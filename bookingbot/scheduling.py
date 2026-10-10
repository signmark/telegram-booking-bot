"""Reminders, holds and demo time compression.

Reminders are stored as absolute times on the booking at payment, so a restart
catches up naturally: anything due is sent once (idempotent flags in the DB).
"""
from __future__ import annotations

from .config import Config


def reminder_times(cfg: Config, now: int, starts_at: int) -> tuple[int, int]:
    if cfg.demo_fast:
        first, second = cfg.DEMO_REMIND_SECONDS
        return now + first, now + second
    first, second = cfg.remind_offsets()
    return starts_at - first, starts_at - second


def hold_expires_at(cfg: Config, now: int) -> int:
    return now + cfg.hold_minutes * 60


async def send_due_reminders(store, cfg, send_message, now) -> int:
    """Send every due reminder exactly once; used by the scheduler and at startup."""
    sent = 0
    for row, kind in store.reminders_due(now):
        if not store.mark_reminder(row['id'], kind):
            continue
        lang = row['lang']
        await send_message(row, lang, kind)
        sent += 1
    return sent
