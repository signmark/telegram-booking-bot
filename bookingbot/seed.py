"""Demo catalogue for the fictional salon «Craft Podium Demo» and its slot grid."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Config
from .db import Store

SERVICES = (
    ('haircut', 'Мужская стрижка', "Men's haircut", 60, 150000),
    ('coloring', 'Окрашивание', 'Colouring', 120, 450000),
    ('manicure', 'Маникюр', 'Manicure', 90, 250000),
    ('shave', 'Классическое бритьё', 'Classic shave', 45, 120000),
)
MASTERS = (
    ('Анна', 'Anna', ('haircut', 'coloring')),
    ('Михаил', 'Michael', ('haircut', 'shave')),
    ('Ольга', 'Olga', ('manicure', 'coloring')),
)


def tz(cfg: Config) -> ZoneInfo:
    return ZoneInfo(cfg.tz_name)


def day_start(cfg: Config, now: int, offset_days: int = 0) -> int:
    local = datetime.fromtimestamp(now, tz(cfg)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((local + timedelta(days=offset_days)).timestamp())


def day_label(cfg: Config, ts: int) -> str:
    return datetime.fromtimestamp(ts, tz(cfg)).strftime('%Y-%m-%d')


def time_label(cfg: Config, ts: int) -> str:
    return datetime.fromtimestamp(ts, tz(cfg)).strftime('%H:%M')


def seed(store: Store, cfg: Config, now: int) -> None:
    service_ids = {}
    for slug, name_ru, name_en, duration_min, price_minor in SERVICES:
        row = store.service_by_slug(slug)
        service_ids[slug] = row['id'] if row else store.add_service(slug, name_ru, name_en, duration_min, price_minor)
    for name_ru, name_en, slugs in MASTERS:
        if store.master_by_name(name_ru) is None:
            store.add_master(name_ru, name_en, [service_ids[s] for s in slugs])
    for master in store.db.execute('SELECT id FROM masters WHERE active=1').fetchall():
        store.ensure_slots(master['id'], grid(cfg, now))


def grid(cfg: Config, now: int, days: int | None = None) -> list[int]:
    """Slot starts for the seeding horizon: every work day, work hours, slot_minutes step."""
    out = []
    for offset in range(days if days is not None else cfg.seed_days):
        base = day_start(cfg, now, offset)
        minute = cfg.work_start_hour * 60
        while minute < cfg.work_end_hour * 60:
            out.append(base + minute * 60)
            minute += cfg.slot_minutes
    return out


def slots_for_day(store: Store, cfg: Config, master_id: int, now: int, offset_days: int):
    start = day_start(cfg, now, offset_days)
    store.ensure_slots(master_id, grid(cfg, start, days=1))
    # Past slots are never offered: a stale message must not lead to a 10:00 booking at 15:40.
    return [row for row in store.open_slots(master_id, start, start + 86400) if row['starts_at'] > now]
