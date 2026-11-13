"""Pairing and per-device tokens.

A device is paired once with a short-lived, single-use code that the OWNER creates (with the owner
token). The device then holds its own token, shown exactly once; only its SHA-256 is stored. A
device can be revoked at any time and then stops working at once. A device holds only the
capabilities it was paired with (default: chat and notify), and never the owner's powers.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.errors import AppError, NotFound
from app.db.models import DEVICE_CAPS, DEVICE_KINDS, Device, utcnow

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
CODE_LENGTH = 8
CODE_TTL = timedelta(minutes=5)
MAX_ACTIVE_CODES = 5
MAX_DEVICES = 10
DEFAULT_CAPS = ("chat", "notify")
SEEN_EVERY = timedelta(seconds=60)


class PairingFailed(AppError):
    status_code = 400
    code = "pairing_failed"


@dataclass(frozen=True)
class Who:
    """The authenticated caller: the owner (everything) or one paired device (its capabilities)."""

    key: str  # stable, non-secret; used for rate limiting
    owner: bool
    device_id: str | None = None
    caps: frozenset[str] = frozenset()

    def can(self, cap: str) -> bool:
        return self.owner or cap in self.caps


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalise_code(code: str) -> str:
    return "".join(c for c in (code or "").upper() if c.isalnum())


@dataclass
class _Code:
    expires: datetime
    caps: tuple[str, ...]


@dataclass
class DeviceRegistry:
    sessionmaker: async_sessionmaker
    clock: Callable[[], datetime] = utcnow
    _codes: dict[str, _Code] = field(default_factory=dict)
    _cache: dict[str, Who] = field(default_factory=dict)  # token hash -> caller
    _seen: dict[str, datetime] = field(default_factory=dict)

    # ------------------------------------------------------------------ pairing
    def new_code(self, caps: list[str] | None = None) -> tuple[str, datetime]:
        wanted = tuple(dict.fromkeys(caps if caps else DEFAULT_CAPS))
        bad = [c for c in wanted if c not in DEVICE_CAPS]
        if bad:
            raise PairingFailed(f"Unknown capability: {bad[0]}. Choose from {list(DEVICE_CAPS)}.")
        now = self.clock()
        self._codes = {c: v for c, v in self._codes.items() if v.expires > now}
        if len(self._codes) >= MAX_ACTIVE_CODES:
            raise PairingFailed("Too many pairing codes are waiting; use or let them expire.")
        code = "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))
        expires = now + CODE_TTL
        self._codes[code] = _Code(expires, wanted)
        return f"{code[:4]}-{code[4:]}", expires

    async def pair(self, code: str, name: str, kind: str) -> tuple[Device, str]:
        """Consume a code (once) and create the device. Returns the device and its token."""
        name = " ".join((name or "").split())[:80]
        if not name or kind not in DEVICE_KINDS:
            raise PairingFailed("A device name and a valid kind are required.")
        entry = self._codes.pop(normalise_code(code), None)  # single use, even if it then fails
        if entry is None or entry.expires <= self.clock():
            raise PairingFailed("That pairing code is wrong or has expired.")
        token = secrets.token_urlsafe(32)
        async with self.sessionmaker() as session:
            live = (await session.scalars(select(Device).where(Device.revoked_at.is_(None)))).all()
            if len(live) >= MAX_DEVICES:
                raise PairingFailed(f"At most {MAX_DEVICES} devices can be paired; revoke one.")
            device = Device(
                name=name,
                kind=kind,
                token_hash=token_hash(token),
                capabilities=list(entry.caps),
                created_at=self.clock(),
            )
            session.add(device)
            await session.commit()
            await session.refresh(device)
        return device, token

    # ------------------------------------------------------------------ use
    async def verify(self, token: str) -> Who | None:
        """The caller a device token stands for, or None (unknown, revoked)."""
        if not token:
            return None
        digest = token_hash(token)
        who = self._cache.get(digest)
        if who is None:
            async with self.sessionmaker() as session:
                device = await session.scalar(select(Device).where(Device.token_hash == digest))
            if device is None or device.revoked_at is not None:
                return None
            who = Who(f"dev-{device.id}", False, device.id, frozenset(device.capabilities))
            self._cache[digest] = who
        await self._touch(who.device_id)
        return who

    async def _touch(self, device_id: str | None) -> None:
        now = self.clock()
        last = self._seen.get(device_id) if device_id else None
        if device_id is None or (last is not None and now - last < SEEN_EVERY):
            return
        self._seen[device_id] = now
        async with self.sessionmaker() as session:
            device = await session.get(Device, device_id)
            if device is not None:
                device.last_seen_at = now
                await session.commit()

    # ------------------------------------------------------------------ management
    async def list(self) -> list[Device]:
        async with self.sessionmaker() as session:
            rows = await session.scalars(select(Device).order_by(Device.created_at))
            return list(rows)

    async def with_capability(self, cap: str) -> list[Device]:
        return [d for d in await self.list() if d.revoked_at is None and cap in d.capabilities]

    async def revoke(self, device_id: str) -> None:
        async with self.sessionmaker() as session:
            device = await session.get(Device, device_id)
            if device is None:
                raise NotFound("No such device.")
            if device.revoked_at is None:
                device.revoked_at = self.clock()
                await session.commit()
        self._cache = {h: w for h, w in self._cache.items() if w.device_id != device_id}
