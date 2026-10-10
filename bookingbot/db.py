"""SQLite storage: the double-booking guarantee lives here, at the database level.

A booking takes N consecutive 30-minute slots with one conditional UPDATE inside a
BEGIN IMMEDIATE transaction: it succeeds only if every slot was free (rowcount check).
Two concurrent callers cannot both reserve the same slot, across processes too.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS services(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT UNIQUE NOT NULL,
  name_ru TEXT NOT NULL,
  name_en TEXT NOT NULL,
  duration_min INTEGER NOT NULL,
  price_minor INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS masters(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name_ru TEXT NOT NULL,
  name_en TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS master_services(
  master_id INTEGER NOT NULL,
  service_id INTEGER NOT NULL,
  PRIMARY KEY (master_id, service_id)
);
CREATE TABLE IF NOT EXISTS slots(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  master_id INTEGER NOT NULL,
  starts_at INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  hold_expires_at INTEGER,
  booking_id INTEGER,
  UNIQUE (master_id, starts_at)
);
CREATE TABLE IF NOT EXISTS bookings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  service_id INTEGER NOT NULL,
  master_id INTEGER NOT NULL,
  starts_at INTEGER NOT NULL,
  end_at INTEGER NOT NULL,
  client_name TEXT NOT NULL,
  client_contact TEXT NOT NULL,
  status TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'payment',
  lang TEXT NOT NULL DEFAULT 'ru',
  created_at INTEGER NOT NULL,
  remind_24h_at INTEGER,
  remind_2h_at INTEGER,
  remind_24h_sent INTEGER NOT NULL DEFAULT 0,
  remind_2h_sent INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  booking_id INTEGER NOT NULL,
  telegram_charge_id TEXT,
  provider_charge_id TEXT,
  amount_minor INTEGER NOT NULL,
  status TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
"""


class SlotTaken(RuntimeError):
    pass


class PastTime(RuntimeError):
    """The requested booking time is already in the past."""


class Store:
    def __init__(self, path, slot_minutes: int = 30):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.slot_minutes = slot_minutes
        self.db = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=30000')
        self.db.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield self.db
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    # --- catalogue -------------------------------------------------------
    def add_service(self, slug, name_ru, name_en, duration_min, price_minor) -> int:
        with self.tx() as db:
            db.execute('INSERT INTO services(slug,name_ru,name_en,duration_min,price_minor) VALUES(?,?,?,?,?)',
                       (slug, name_ru, name_en, duration_min, price_minor))
            return db.execute('SELECT id FROM services WHERE slug=?', (slug,)).fetchone()['id']

    def add_master(self, name_ru, name_en, service_ids) -> int:
        with self.tx() as db:
            db.execute('INSERT INTO masters(name_ru,name_en) VALUES(?,?)', (name_ru, name_en))
            master_id = db.execute('SELECT id FROM masters WHERE name_ru=?', (name_ru,)).fetchone()['id']
            for service_id in service_ids:
                db.execute('INSERT OR IGNORE INTO master_services(master_id,service_id) VALUES(?,?)',
                           (master_id, service_id))
            return master_id

    def services(self):
        return self.db.execute('SELECT * FROM services ORDER BY id').fetchall()

    def service(self, service_id):
        return self.db.execute('SELECT * FROM services WHERE id=?', (service_id,)).fetchone()

    def service_by_slug(self, slug):
        return self.db.execute('SELECT * FROM services WHERE slug=?', (slug,)).fetchone()

    def master(self, master_id):
        return self.db.execute('SELECT * FROM masters WHERE id=?', (master_id,)).fetchone()

    def master_by_name(self, name):
        lowered = name.strip().lower()
        for row in self.db.execute('SELECT * FROM masters WHERE active=1').fetchall():
            if lowered in (row['name_ru'].lower(), row['name_en'].lower()):
                return row
        return None

    def masters_for(self, service_id):
        return self.db.execute(
            'SELECT m.* FROM masters m JOIN master_services ms ON ms.master_id=m.id '
            'WHERE ms.service_id=? AND m.active=1 ORDER BY m.id', (service_id,)).fetchall()

    # --- slots -----------------------------------------------------------
    def ensure_slots(self, master_id, starts_at_list) -> None:
        with self.tx() as db:
            for starts_at in starts_at_list:
                db.execute('INSERT OR IGNORE INTO slots(master_id,starts_at) VALUES(?,?)', (master_id, starts_at))

    def _slot_ts(self, starts_at, duration_min):
        n = -(-duration_min // self.slot_minutes)  # ceil
        return [starts_at + i * self.slot_minutes * 60 for i in range(n)]

    def open_slots(self, master_id, day_start, day_end):
        return self.db.execute(
            "SELECT * FROM slots WHERE master_id=? AND starts_at>=? AND starts_at<? AND status='open' "
            'ORDER BY starts_at', (master_id, day_start, day_end)).fetchall()

    def _reserve(self, db, master_id, starts_at, duration_min, status, booking_id, hold_expires_at) -> None:
        ts = self._slot_ts(starts_at, duration_min)
        marks = ','.join('?' * len(ts))
        db.execute(
            f"UPDATE slots SET status=?, hold_expires_at=?, booking_id=? "
            f"WHERE master_id=? AND starts_at IN ({marks}) AND status='open'",
            (status, hold_expires_at, booking_id, master_id, *ts))
        if db.execute('SELECT changes()').fetchone()[0] != len(ts):
            raise SlotTaken()

    def reserve(self, master_id, starts_at, duration_min, status, booking_id, hold_expires_at=None) -> None:
        """The DB-level double-booking guard: all-or-nothing on free slots."""
        with self.tx() as db:
            self._reserve(db, master_id, starts_at, duration_min, status, booking_id, hold_expires_at)

    def block_slots(self, master_id, starts_at, duration_min) -> int:
        ts = self._slot_ts(starts_at, duration_min)
        marks = ','.join('?' * len(ts))
        with self.tx() as db:
            db.execute(f"UPDATE slots SET status='blocked' WHERE master_id=? AND starts_at IN ({marks}) "
                       "AND status='open'", (master_id, *ts))
            return db.execute('SELECT changes()').fetchone()[0]

    def slot_states(self, master_id, starts_at, duration_min):
        ts = self._slot_ts(starts_at, duration_min)
        marks = ','.join('?' * len(ts))
        return self.db.execute(
            f'SELECT starts_at,status,booking_id,hold_expires_at FROM slots '
            f'WHERE master_id=? AND starts_at IN ({marks})',
            (master_id, *ts)).fetchall()

    def free_slots(self, master_id, starts_at, duration_min):
        return [row for row in self.slot_states(master_id, starts_at, duration_min) if row['status'] == 'open']

    # --- bookings --------------------------------------------------------
    def create_booking(self, user_id, service, master_id, starts_at, name, contact, *, now, lang='ru',
                       status='pending_payment', source='payment', hold_expires_at=None,
                       remind_24h_at=None, remind_2h_at=None) -> int:
        if starts_at <= now:
            raise PastTime()
        end_at = starts_at + service['duration_min'] * 60
        with self.tx() as db:
            db.execute(
                'INSERT INTO bookings(user_id,service_id,master_id,starts_at,end_at,client_name,client_contact,'
                'status,source,lang,created_at,remind_24h_at,remind_2h_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (user_id, service['id'], master_id, starts_at, end_at, name, contact, status, source, lang, now,
                 remind_24h_at, remind_2h_at))
            booking_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            # Booking row and slot reservation commit atomically: SlotTaken rolls the insert back.
            self._reserve(db, master_id, starts_at, service['duration_min'],
                          'booked' if status == 'paid' else 'held', booking_id, hold_expires_at)
        return booking_id

    def booking(self, booking_id):
        return self.db.execute('SELECT * FROM bookings WHERE id=?', (booking_id,)).fetchone()

    def bookings_for_user(self, user_id):
        return self.db.execute("SELECT * FROM bookings WHERE user_id=? AND status IN ('pending_payment','paid') "
                               'ORDER BY starts_at', (user_id,)).fetchall()

    def bookings_between(self, from_ts, to_ts):
        return self.db.execute('SELECT * FROM bookings WHERE starts_at>=? AND starts_at<? '
                               "AND status IN ('pending_payment','paid') ORDER BY starts_at",
                               (from_ts, to_ts)).fetchall()

    def confirm_payment(self, booking_id, telegram_charge_id, provider_charge_id, amount_minor,
                        now, remind_24h_at, remind_2h_at) -> bool:
        with self.tx() as db:
            db.execute("UPDATE bookings SET status='paid', remind_24h_at=?, remind_2h_at=? "
                       'WHERE id=? AND status=\'pending_payment\'',
                       (remind_24h_at, remind_2h_at, booking_id))
            if db.execute('SELECT changes()').fetchone()[0] != 1:
                return False
            db.execute("UPDATE slots SET status='booked' WHERE booking_id=? AND status='held'", (booking_id,))
            db.execute('INSERT INTO payments(booking_id,telegram_charge_id,provider_charge_id,amount_minor,'
                       'status,created_at) VALUES(?,?,?,?,?,?)',
                       (booking_id, telegram_charge_id, provider_charge_id, amount_minor, 'paid', now))
            return True

    def record_refund_due(self, booking_id, telegram_charge_id, provider_charge_id,
                          amount_minor, now) -> None:
        """Money arrived but no booking could be confirmed: keep the trail, flag it for refund."""
        with self.tx() as db:
            db.execute('INSERT INTO payments(booking_id,telegram_charge_id,provider_charge_id,amount_minor,'
                       'status,created_at) VALUES(?,?,?,?,?,?)',
                       (booking_id, telegram_charge_id, provider_charge_id, amount_minor, 'refund_due', now))

    def payments_for(self, booking_id):
        return self.db.execute('SELECT * FROM payments WHERE booking_id=? ORDER BY id',
                               (booking_id,)).fetchall()

    def cancel(self, booking_id, now, statuses=('pending_payment', 'paid')) -> bool:
        marks = ','.join('?' * len(statuses))
        with self.tx() as db:
            db.execute(f"UPDATE bookings SET status='cancelled' WHERE id=? AND status IN ({marks})",
                       (booking_id, *statuses))
            if db.execute('SELECT changes()').fetchone()[0] != 1:
                return False
            db.execute("UPDATE slots SET status='open', booking_id=NULL, hold_expires_at=NULL "
                       "WHERE booking_id=?", (booking_id,))
            return True

    def release_expired(self, now) -> int:
        """Held slots whose payment window expired: free them and cancel the bookings."""
        with self.tx() as db:
            rows = db.execute("SELECT DISTINCT booking_id FROM slots WHERE status='held' "
                              'AND hold_expires_at IS NOT NULL AND hold_expires_at<?', (now,)).fetchall()
            count = 0
            for row in rows:
                db.execute("UPDATE bookings SET status='cancelled' WHERE id=? AND status='pending_payment'",
                           (row['booking_id'],))
                count += db.execute('SELECT changes()').fetchone()[0]
                db.execute("UPDATE slots SET status='open', booking_id=NULL, hold_expires_at=NULL "
                           "WHERE booking_id=? AND status='held'", (row['booking_id'],))
            return count

    # --- reminders -------------------------------------------------------
    def reminders_due(self, now):
        due = []
        for row in self.db.execute("SELECT * FROM bookings WHERE status='paid'").fetchall():
            if row['remind_24h_at'] is not None and not row['remind_24h_sent'] and row['remind_24h_at'] <= now:
                due.append((row, '24h'))
            if row['remind_2h_at'] is not None and not row['remind_2h_sent'] and row['remind_2h_at'] <= now:
                due.append((row, '2h'))
        return due

    def mark_reminder(self, booking_id, kind) -> bool:
        """Idempotent: only the caller that flips 0→1 sends."""
        column = 'remind_24h_sent' if kind == '24h' else 'remind_2h_sent'
        with self.tx() as db:
            db.execute(f'UPDATE bookings SET {column}=1 WHERE id=? AND {column}=0', (booking_id,))
            return db.execute('SELECT changes()').fetchone()[0] == 1
