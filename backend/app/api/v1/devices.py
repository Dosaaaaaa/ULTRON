"""Phone pairing, push, reminders and notifications.

Owner endpoints (owner token only): create a pairing code, list/revoke devices, create/list/cancel
reminders, send a notification. Device endpoints: pairing (the code is the credential) and the push
socket (the device token, needs the `notify` capability).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import ssl
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.api.deps import (
    Principal,
    ServicesDep,
    authenticate,
    device_can,
    host_allowed,
    origin_allowed,
)
from app.core.errors import AppError, RateLimited
from app.db.models import DEVICE_CAPS, DEVICE_KINDS, Device
from app.devices import prefs
from app.devices import reminders as reminder_service
from app.devices.hub import frame
from app.devices.registry import Who

log = logging.getLogger("ultron.devices")


def tls_fingerprint(path) -> str | None:
    """SHA-256 of the served certificate, as colon-separated hex (what Android shows too)."""
    if path is None:
        return None
    try:
        der = ssl.PEM_cert_to_DER_cert(Path(path).read_text())
    except (OSError, ValueError):
        return None
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


router = APIRouter(tags=["devices"])
WS_POLICY_VIOLATION = 1008
IdPath = str
# The owner, or a paired device that was given the `reminders` power.
CanRemind = Annotated[Who, Depends(device_can("reminders"))]
CanChat = Annotated[Who, Depends(device_can("chat"))]


class PairingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capabilities: list[Literal["chat", "notify", "call", "reminders"]] = Field(
        default_factory=lambda: ["chat", "notify"]
    )


class PairBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=4, max_length=20)
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["phone", "tablet", "desktop", "other"] = "phone"


class ReminderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=300)
    due_at: datetime


class NotifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=80)
    body: str = Field(default="", max_length=400)


def _device_out(d: Device, services) -> dict:
    return {
        "id": d.id,
        "name": d.name,
        "kind": d.kind,
        "capabilities": list(d.capabilities),
        "created_at": d.created_at.isoformat(),
        "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
        "revoked": d.revoked_at is not None,
        "connected": services.hub.connected(d.id),
    }


# ------------------------------------------------------------------------- pairing
@router.post("/devices/pairing-code")
async def create_pairing_code(body: PairingRequest, services: ServicesDep, _: Principal) -> dict:
    code, expires = services.devices.new_code(body.capabilities)
    return {
        "code": code,
        "expires_at": expires.isoformat(),
        "capabilities": body.capabilities,
        # null when the backend is not served over TLS (loopback only); the phone should refuse that
        "tls_fingerprint_sha256": tls_fingerprint(services.settings.tls_cert_file),
    }


@router.post("/devices/pair")
async def pair(body: PairBody, services: ServicesDep, request: Request) -> dict:
    """Unauthenticated on purpose: the single-use code the owner made is the credential. Strictly
    rate limited (5 tries a minute for everyone together), because the code is short."""
    if not host_allowed(services.settings, request.headers):
        raise AppError("Host not served.")
    if services.pair_limiter is not None:
        services.pair_limiter.check("pair")
    device, token = await services.devices.pair(body.code, body.name, body.kind)
    return {
        "device_id": device.id,
        "token": token,  # shown once; only its hash is kept
        "capabilities": list(device.capabilities),
        "name": device.name,
    }


@router.get("/devices")
async def list_devices(services: ServicesDep, _: Principal) -> list[dict]:
    return [_device_out(d, services) for d in await services.devices.list()]


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device(device_id: IdPath, services: ServicesDep, _: Principal) -> Response:
    await services.devices.revoke(device_id)
    await services.hub.disconnect(device_id)  # a revoked phone must not stay connected
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------------- reminders
@router.post("/reminders", status_code=status.HTTP_201_CREATED)
async def create_reminder(body: ReminderBody, services: ServicesDep, _: CanRemind) -> dict:
    r = await reminder_service.create(services, body.text, body.due_at)
    return _reminder_out(r)


@router.get("/reminders")
async def list_reminders(services: ServicesDep, _: CanRemind, all: bool = False) -> list[dict]:
    return [_reminder_out(r) for r in await reminder_service.listing(services, include_done=all)]


@router.delete("/reminders/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_reminder(reminder_id: IdPath, services: ServicesDep, _: CanRemind) -> Response:
    await reminder_service.cancel(services, reminder_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _reminder_out(r) -> dict:
    return {
        "id": r.id,
        "text": r.text,
        "due_at": r.due_at.isoformat(),
        "status": r.status,
        "fired_at": r.fired_at.isoformat() if r.fired_at else None,
    }


# ------------------------------------------------------------------------- preferences
class PreferencesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_confirm: StrictBool | None = None  # strict: "yes" or 1 are not accepted as on
    reminder_guess: StrictBool | None = None


@router.get("/preferences")
async def get_preferences(services: ServicesDep, _: CanChat) -> dict:
    return await prefs.get_all(services)


@router.patch("/preferences")
async def patch_preferences(body: PreferencesPatch, services: ServicesDep, _: CanChat) -> dict:
    """The owner, or a paired phone with `chat`, may switch how much ULTRON asks before acting."""
    return await prefs.update(services, body.model_dump(exclude_none=True))


# ------------------------------------------------------------------------- notify
@router.post("/notify")
async def notify(body: NotifyBody, services: ServicesDep, _: Principal) -> dict:
    sent = await services.hub.enqueue_for("notify", "notify", body.model_dump())
    return {"devices": sent}


# ------------------------------------------------------------------------- push socket
@router.websocket("/ws/device")
async def device_socket(ws: WebSocket) -> None:
    """The phone's long-lived connection. The server pushes `{"type":"push",...}`; the phone replies
    `{"type":"ack","id":...}` for each item it has shown. Anything unacknowledged is re-sent on the
    next connection."""
    services = ws.app.state.services
    if not origin_allowed(ws.headers) or not host_allowed(services.settings, ws.headers):
        await ws.close(code=WS_POLICY_VIOLATION)
        return
    who = await authenticate(services, ws.headers.get("authorization", ""))
    if who is None or who.device_id is None or not who.can("notify"):
        await ws.close(code=WS_POLICY_VIOLATION)  # only a paired device with `notify`
        return
    await ws.accept()
    lock = asyncio.Lock()

    async def send(message: dict) -> None:
        async with lock:  # one writer at a time
            await ws.send_json(message)

    hub = services.hub
    hub.attach(who.device_id, send, lambda: ws.close(code=WS_POLICY_VIOLATION))
    try:
        for item in await hub.pending(who.device_id):
            await send(item)
        while True:
            raw = await ws.receive_text()
            if len(raw) > 4096:
                await send({"type": "error", "code": "too_large"})
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                await send({"type": "error", "code": "invalid_request"})
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "ack" and isinstance(msg.get("id"), str):
                await hub.ack(who.device_id, msg["id"])
            elif msg.get("type") == "ping":
                await send({"type": "pong"})
            try:
                services.limiter.check(who.key)
            except RateLimited:
                await send({"type": "error", "code": "rate_limited"})
    except WebSocketDisconnect:
        pass
    finally:
        hub.detach(who.device_id, send)


__all__ = ["DEVICE_CAPS", "DEVICE_KINDS", "frame", "router"]
