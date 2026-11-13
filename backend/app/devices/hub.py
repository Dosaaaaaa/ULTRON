"""Push to devices. Every item is written to the outbox first and removed only when the device
acknowledges it, so a phone that is asleep, out of range or restarting misses nothing."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import OutboxItem, utcnow
from app.devices.registry import DeviceRegistry

log = logging.getLogger("ultron.push")
Send = Callable[[dict], Awaitable[None]]
MAX_PENDING_PER_DEVICE = 200
KEEP_DELIVERED = timedelta(days=1)
KEEP_UNDELIVERED = timedelta(days=14)


def frame(item: OutboxItem) -> dict[str, Any]:
    return {
        "type": "push",
        "id": item.id,
        "kind": item.kind,
        "payload": item.payload,
        "created_at": item.created_at.isoformat(),
    }


class PushHub:
    def __init__(self, sessionmaker: async_sessionmaker, registry: DeviceRegistry) -> None:
        self._sm = sessionmaker
        self._registry = registry
        self._live: dict[str, set[Send]] = {}
        self._closers: dict[Send, Callable[[], Awaitable[None]]] = {}

    # ------------------------------------------------------------------ connections
    def attach(self, device_id: str, send: Send, close: Callable[[], Awaitable[None]]) -> None:
        self._live.setdefault(device_id, set()).add(send)
        self._closers[send] = close

    def detach(self, device_id: str, send: Send) -> None:
        self._live.get(device_id, set()).discard(send)
        self._closers.pop(send, None)

    async def disconnect(self, device_id: str) -> int:
        """Close every live socket of a device (used when it is revoked). Returns how many."""
        sends = list(self._live.get(device_id, ()))
        for send in sends:
            closer = self._closers.get(send)
            self.detach(device_id, send)
            if closer is not None:
                try:
                    await closer()
                except Exception:  # noqa: BLE001 - already gone is fine
                    log.info("close_failed", extra={"device": device_id})
        return len(sends)

    def connected(self, device_id: str) -> bool:
        return bool(self._live.get(device_id))

    # ------------------------------------------------------------------ queue
    async def enqueue(self, device_id: str, kind: str, payload: dict) -> str:
        async with self._sm() as session:
            pending = (
                await session.scalars(
                    select(OutboxItem.id).where(
                        OutboxItem.device_id == device_id, OutboxItem.delivered_at.is_(None)
                    )
                )
            ).all()
            if (
                len(pending) >= MAX_PENDING_PER_DEVICE
            ):  # an absent device must not grow this forever
                oldest = await session.scalar(
                    select(OutboxItem)
                    .where(OutboxItem.device_id == device_id, OutboxItem.delivered_at.is_(None))
                    .order_by(OutboxItem.created_at)
                    .limit(1)
                )
                if oldest is not None:
                    await session.delete(oldest)
            item = OutboxItem(device_id=device_id, kind=kind, payload=payload, created_at=utcnow())
            session.add(item)
            await session.commit()
            await session.refresh(item)
        await self._push(device_id, frame(item))
        return item.id

    async def enqueue_for(self, cap: str, kind: str, payload: dict) -> int:
        """To every un-revoked device that holds `cap`. Returns how many devices were addressed."""
        devices = await self._registry.with_capability(cap)
        for d in devices:
            await self.enqueue(d.id, kind, payload)
        return len(devices)

    async def pending(self, device_id: str) -> list[dict]:
        async with self._sm() as session:
            rows = await session.scalars(
                select(OutboxItem)
                .where(OutboxItem.device_id == device_id, OutboxItem.delivered_at.is_(None))
                .order_by(OutboxItem.created_at)
            )
            return [frame(r) for r in rows]

    async def ack(self, device_id: str, item_id: str) -> bool:
        async with self._sm() as session:
            item = await session.get(OutboxItem, item_id)
            if item is None or item.device_id != device_id:  # a device can only ack its own items
                return False
            if item.delivered_at is None:
                item.delivered_at = utcnow()
                await session.commit()
            return True

    async def purge(self) -> int:
        now = utcnow()
        async with self._sm() as session:
            res = await session.execute(
                delete(OutboxItem).where(
                    (
                        OutboxItem.delivered_at.is_not(None)
                        & (OutboxItem.delivered_at < now - KEEP_DELIVERED)
                    )
                    | (OutboxItem.created_at < now - KEEP_UNDELIVERED)
                )
            )
            await session.commit()
            return res.rowcount or 0

    async def _push(self, device_id: str, message: dict) -> None:
        for send in list(self._live.get(device_id, ())):
            try:
                await send(message)
            except Exception:  # noqa: BLE001 - a dead socket must not lose the item; it stays queued
                self.detach(device_id, send)
                log.info("push_failed", extra={"device": device_id})
