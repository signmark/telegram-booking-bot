"""Configuration from the server environment; nothing here is invented at runtime.

Env contract (demo №4): TELEGRAM_BOT_TOKEN, PAYMENT_PROVIDER_TOKEN, BOT_USERNAME,
TEST_CARD_NUMBER, CURRENCY (default RUB), ADMIN_IDS, DB_PATH, DEMO_FAST,
CANCEL_HOURS, HOLD_MINUTES, SEED_DAYS.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    bot_token: str
    provider_token: str
    test_card: str
    admin_ids: frozenset[int]
    db_path: Path
    bot_username: str = ''
    currency: str = 'RUB'
    demo_fast: bool = False
    cancel_hours: int = 12
    hold_minutes: int = 15
    seed_days: int = 14
    slot_minutes: int = 30
    work_start_hour: int = 10
    work_end_hour: int = 20
    tz_name: str = 'Europe/Moscow'

    # Demo mode compresses time so a visitor sees reminders, cancel and refusal within minutes:
    # reminders arrive 1 and 2 minutes after payment, and the cancel window closes 1 minute after booking.
    DEMO_REMIND_SECONDS = (60, 120)
    DEMO_CANCEL_SECONDS = 60

    @classmethod
    def from_env(cls, environ: dict | None = None) -> 'Config':
        env = os.environ if environ is None else environ

        def need(key: str) -> str:
            value = env.get(key, '').strip()
            if not value:
                raise ValueError(f'{key} is required')
            return value

        admin_ids = frozenset(
            int(part) for part in env.get('ADMIN_IDS', '').replace(' ', '').split(',') if part)
        return cls(
            bot_token=need('TELEGRAM_BOT_TOKEN'),
            provider_token=need('PAYMENT_PROVIDER_TOKEN'),
            # The provider is Smart Glocal Test; its test card number is not assumed here —
            # the deployer verifies it on a live test invoice and puts it in the environment.
            test_card=need('TEST_CARD_NUMBER'),
            admin_ids=admin_ids,
            db_path=Path(env.get('DB_PATH', 'data/bookings.sqlite3')),
            bot_username=env.get('BOT_USERNAME', '').strip().lstrip('@'),
            currency=env.get('CURRENCY', 'RUB').strip().upper() or 'RUB',
            demo_fast=env.get('DEMO_FAST', '').strip() in {'1', 'true', 'yes'},
            cancel_hours=int(env.get('CANCEL_HOURS', '12')),
            hold_minutes=int(env.get('HOLD_MINUTES', '15')),
            seed_days=int(env.get('SEED_DAYS', '14')),
            tz_name=env.get('TZ_NAME', 'Europe/Moscow').strip() or 'Europe/Moscow',
        )

    def remind_offsets(self) -> tuple[int, int]:
        """Seconds before the appointment when reminders fire; demo: after payment instead."""
        return (24 * 3600, 2 * 3600) if not self.demo_fast else self.DEMO_REMIND_SECONDS

    def cancel_deadline(self, created_at: int, starts_at: int) -> int:
        """Last moment the client may cancel or reschedule; demo: one minute after booking."""
        return created_at + self.DEMO_CANCEL_SECONDS if self.demo_fast else starts_at - self.cancel_hours * 3600
