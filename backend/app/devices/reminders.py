"""Reminders: stored, survive restarts, and delivered to the paired devices when due."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.core.errors import AppError, NotFound
from app.core.services import Services
from app.db.models import Reminder, utcnow

log = logging.getLogger("ultron.reminders")
MAX_PENDING = 200
MAX_AHEAD = timedelta(days=366)
GIVE_UP_AFTER = timedelta(hours=24)  # nobody was reachable for a day: mark it missed, stop retrying
LOOP_SECONDS = 10


class InvalidReminder(AppError):
    status_code = 422
    code = "invalid_reminder"


async def create(services: Services, text: str, due_at: datetime) -> Reminder:
    text = " ".join((text or "").split())[:300]
    if not text:
        raise InvalidReminder("A reminder needs some text.")
    now = utcnow()
    if due_at.tzinfo is None:
        raise InvalidReminder(
            "The time must include a timezone (for example 2026-11-13T09:00:00Z)."
        )
    if due_at < now - timedelta(seconds=30):
        raise InvalidReminder("That time has already passed.")
    if due_at > now + MAX_AHEAD:
        raise InvalidReminder("Reminders can be at most a year ahead.")
    async with services.sessionmaker() as session:
        n = await session.scalar(
            select(func.count()).select_from(Reminder).where(Reminder.status == "pending")
        )
        if (n or 0) >= MAX_PENDING:
            raise InvalidReminder(f"At most {MAX_PENDING} reminders can be waiting.")
        reminder = Reminder(text=text, due_at=due_at, created_at=now)
        session.add(reminder)
        await session.commit()
        await session.refresh(reminder)
        return reminder


async def listing(services: Services, include_done: bool = False) -> list[Reminder]:
    async with services.sessionmaker() as session:
        stmt = select(Reminder).order_by(Reminder.due_at)
        if not include_done:
            stmt = stmt.where(Reminder.status == "pending")
        return list((await session.scalars(stmt)).all())


async def cancel(services: Services, reminder_id: str) -> None:
    async with services.sessionmaker() as session:
        reminder = await session.get(Reminder, reminder_id)
        if reminder is None:
            raise NotFound("No such reminder.")
        if reminder.status == "pending":
            reminder.status = "cancelled"
            await session.commit()


async def fire_due(services: Services, now: datetime | None = None) -> int:
    """Deliver every reminder that is due. Returns how many were delivered or given up on."""
    now = now or utcnow()
    done = 0
    async with services.sessionmaker() as session:
        due = (
            await session.scalars(
                select(Reminder).where(Reminder.status == "pending", Reminder.due_at <= now)
            )
        ).all()
        for r in due:
            sent = await services.hub.enqueue_for(
                "notify",
                "reminder",
                {"id": r.id, "text": r.text, "due_at": r.due_at.isoformat()},
            )
            if sent == 0 and now - r.due_at < GIVE_UP_AFTER:
                continue  # no device to tell yet: try again on the next pass
            r.status, r.fired_at = "fired", now
            done += 1
        await session.commit()
    return done


async def run_loop(services: Services) -> None:
    while True:
        try:
            await fire_due(services)
            await services.hub.purge()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the scheduler must keep running
            log.error("reminder_loop_error", extra={"exc_type": type(exc).__name__})
        await asyncio.sleep(LOOP_SECONDS)


@contextlib.asynccontextmanager
async def running(services: Services):
    task = asyncio.create_task(run_loop(services), name="reminders")
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
