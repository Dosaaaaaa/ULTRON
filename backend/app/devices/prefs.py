"""The switches that decide how much ULTRON asks before acting. Owner-level (one set for everything:
the desktop window, voice and the phone), changeable from the desktop panel or the phone's Settings.

- call_confirm (default ON): "call Asha" only asks the phone to show a confirmation. Turned OFF,
  the phone places the call itself after a short cancellable countdown, and only for a full number
  or exactly one matching contact; anything unclear still falls back to asking.
- reminder_guess (default OFF): a reminder with an unclear time ("tomorrow", "at 5") asks what you
  meant. Turned ON, sensible defaults are used instead (09:00 for a day, the next 5 o'clock for
  "at 5").
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.errors import AppError
from app.core.services import Services
from app.db.models import Preference, utcnow

DEFAULTS: dict[str, bool] = {"call_confirm": True, "reminder_guess": False}


class InvalidPreference(AppError):
    status_code = 422
    code = "invalid_preference"


async def get_all(services: Services) -> dict[str, bool]:
    async with services.sessionmaker() as session:
        rows = (await session.scalars(select(Preference))).all()
    values = dict(DEFAULTS)
    values.update({r.key: bool(r.value) for r in rows if r.key in DEFAULTS})
    return values


async def get(services: Services, key: str) -> bool:
    return (await get_all(services))[key]


async def update(services: Services, changes: dict) -> dict[str, bool]:
    bad = [k for k in changes if k not in DEFAULTS]
    if bad:
        raise InvalidPreference(f"Unknown setting: {bad[0]}. Known: {sorted(DEFAULTS)}.")
    if any(not isinstance(v, bool) for v in changes.values()):
        raise InvalidPreference("Settings are on/off (true or false).")
    async with services.sessionmaker() as session:
        for key, value in changes.items():
            row = await session.get(Preference, key)
            if row is None:
                session.add(Preference(key=key, value=value, updated_at=utcnow()))
            else:
                row.value, row.updated_at = value, utcnow()
        await session.commit()
    return await get_all(services)
