"""WebSocket chat: streamed replies with cancel (barge-in). Protocol: shared/protocols/chat_ws.md"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.api.deps import authenticate, host_allowed, origin_allowed
from app.api.schemas import ChatRequest
from app.core.errors import AppError, RateLimited
from app.core.streaming import TurnState, run_turn
from app.memory import conversations as repo

log = logging.getLogger("ultron.ws")
router = APIRouter()

WS_POLICY_VIOLATION = 1008
MAX_FRAME_BYTES = 64 * 1024


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket) -> None:
    services = ws.app.state.services
    if not origin_allowed(ws.headers) or not host_allowed(services.settings, ws.headers):
        await ws.close(code=WS_POLICY_VIOLATION)  # a web page, not one of our clients
        return
    # Credentials travel in the upgrade request's Authorization header, never in the URL.
    who = await authenticate(services, ws.headers.get("authorization", ""))
    if who is None or not who.can("chat"):
        await ws.close(code=WS_POLICY_VIOLATION)  # rejected before accept: no session is created
        return
    principal = who.key
    await ws.accept()

    async def send(event: dict) -> None:
        await ws.send_json(event)

    turn: asyncio.Task | None = None
    state = TurnState()
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > MAX_FRAME_BYTES:
                await send({"type": "error", "code": "too_large", "message": "Frame too large."})
                continue
            try:
                msg = _parse(raw)
            except ValueError as exc:
                await send({"type": "error", "code": "invalid_request", "message": str(exc)})
                continue

            kind = msg.get("type")
            if kind == "cancel":
                if (not turn or turn.done()) and isinstance(msg.get("message_id"), int):
                    # The model finished before the operator interrupted: record what was heard.
                    spoken = msg.get("spoken")
                    try:
                        async with services.sessionmaker() as session:
                            saved = await repo.amend_spoken(
                                session,
                                msg["message_id"],
                                spoken if isinstance(spoken, str) else "",
                            )
                        await send(
                            {
                                "type": "amended",
                                "message_id": msg["message_id"],
                                "reply": saved.content if saved else "",
                            }
                        )
                    except AppError as exc:
                        await send({"type": "error", "code": exc.code, "message": exc.message})
                    continue
                if turn and not turn.done():
                    spoken = msg.get("spoken")
                    state.spoken = spoken if isinstance(spoken, str) else None
                    turn.cancel()
                    await asyncio.shield(_wait(turn))
                continue
            if kind != "chat":
                await send({"type": "error", "code": "invalid_request", "message": "Unknown type."})
                continue
            if turn and not turn.done():
                await send({"type": "error", "code": "busy", "message": "A turn is in progress."})
                continue
            try:
                req = ChatRequest.model_validate({k: v for k, v in msg.items() if k != "type"})
                services.limiter.check(principal)
            except ValidationError:
                await send({"type": "error", "code": "invalid_request", "message": "Invalid chat."})
                continue
            except RateLimited as exc:
                await send({"type": "error", "code": exc.code, "message": exc.message, **exc.extra})
                continue

            state = TurnState()
            turn = asyncio.create_task(
                run_turn(
                    services,
                    send,
                    state,
                    text=req.message,
                    conversation_id=req.conversation_id,
                    privacy=req.privacy,
                    voice=req.modality == "voice",
                    project_id=req.project_id,
                )
            )
            turn.add_done_callback(_log_failure)
    except WebSocketDisconnect:
        pass
    finally:
        if turn and not turn.done():
            turn.cancel()  # client vanished: stop generating, persist what existed
            await asyncio.shield(_wait(turn))


async def _wait(task: asyncio.Task) -> None:
    try:
        await task
    except BaseException:  # noqa: BLE001 - failures are logged by the done-callback
        pass


def _parse(raw: str) -> dict:
    import json  # noqa: PLC0415

    try:
        msg = json.loads(raw)
    except ValueError as exc:
        raise ValueError("Frame is not valid JSON.") from exc
    if not isinstance(msg, dict):
        raise ValueError("Frame must be a JSON object.")
    return msg


def _log_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:  # type only: messages can carry user text
        log.error("ws_turn_failed", extra={"exc_type": type(exc).__name__})
