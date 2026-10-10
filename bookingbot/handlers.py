"""Telegram-facing layer: client booking flow, test-mode payment, reminders, admin.

Handlers depend only on the small update/message interface used here, so tests can
drive the real functions with fake updates and a real SQLite database.
"""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from . import payments, scheduling
from .config import Config
from .db import PastTime, SlotTaken, Store
from .i18n import t
from .seed import day_label, day_start, slots_for_day, time_label

log = logging.getLogger(__name__)

CURRENCY_SYMBOLS = {'RUB': '₽', 'USD': '$', 'EUR': '€'}
DAY_BUTTONS = 7


def lang_of(user) -> str:
    code = (getattr(user, 'language_code', '') or '').lower()
    return 'ru' if code.startswith('ru') else 'en'


def money(minor: int, currency: str) -> str:
    symbol = CURRENCY_SYMBOLS.get(currency, currency)
    return f'{minor // 100:,}'.replace(',', ' ') + f' {symbol}'


def parse_contact(text: str) -> tuple[str, str] | None:
    """«Имя, контакт» — reject garbage: empty parts, digits in the name, contact without digits/@."""
    if not text or ',' not in text:
        return None
    name, contact = (part.strip() for part in text.split(',', 1))
    if not 2 <= len(name) <= 60 or any(ch.isdigit() for ch in name):
        return None
    if not 3 <= len(contact) <= 60 or not any(ch.isdigit() or ch == '@' for ch in contact):
        return None
    return name, contact


class BookingService:
    def __init__(self, store: Store, cfg: Config, clock):
        self.store = store
        self.cfg = cfg
        self.clock = clock
        self.pending: dict[int, dict] = {}
        self.send_text = None  # async (user_id, text) — wired by __main__

    # --- helpers ---------------------------------------------------------
    def _buttons(self, rows):
        return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data) for label, data in row]
                                     for row in rows])

    def _when(self, starts_at: int, lang: str) -> str:
        return f'{day_label(self.cfg, starts_at)} {time_label(self.cfg, starts_at)}'

    def _window_text(self, lang: str) -> str:
        if self.cfg.demo_fast:
            return 'в течение 1 минуты после записи' if lang == 'ru' else 'within 1 minute after booking'
        hours = self.cfg.cancel_hours
        return f'не позднее чем за {hours} ч до визита' if lang == 'ru' else f'at least {hours} h before the visit'

    def _names(self, service, master, lang: str) -> tuple[str, str]:
        pick = (lambda row, ru, en: row[ru] if lang == 'ru' else row[en])
        return pick(service, 'name_ru', 'name_en'), pick(master, 'name_ru', 'name_en')

    def _bookable_rows(self, service, master_id, now, offset):
        """Slots still offered for booking: in the future and with enough consecutive free capacity."""
        need = -(-service['duration_min'] // self.cfg.slot_minutes)
        return [row for row in slots_for_day(self.store, self.cfg, master_id, now, offset)
                if len(self.store.free_slots(master_id, row['starts_at'], service['duration_min'])) == need]

    # --- client flow -----------------------------------------------------
    async def start(self, update, context):
        user, message = update.effective_user, update.effective_message
        lang = lang_of(user)
        await message.reply_text(t(lang, 'start', card=self.cfg.test_card),
                                 reply_markup=self._buttons([[(t(lang, 'book'), 'flow')]]))

    async def callback(self, update, context):
        user, query = update.effective_user, update.callback_query
        await query.answer()
        data, lang = query.data, lang_of(user)
        message = update.effective_message
        if data == 'flow':
            rows = [[(svc['name_ru'] if lang == 'ru' else svc['name_en'], f"svc:{svc['id']}")]
                    for svc in self.store.services()]
            await message.reply_text(t(lang, 'choose_service'), reply_markup=self._buttons(rows))
            return
        if data.startswith('svc:'):
            service_id = int(data.split(':', 1)[1])
            self.pending[user.id] = {'service_id': service_id}
            rows = [[(m['name_ru'] if lang == 'ru' else m['name_en'], f'mst:{service_id}:{m["id"]}')]
                    for m in self.store.masters_for(service_id)]
            await message.reply_text(t(lang, 'choose_master'), reply_markup=self._buttons(rows))
            return
        if data.startswith('mst:'):
            _, service_id, master_id = data.split(':')
            self.pending[user.id] = {'service_id': int(service_id), 'master_id': int(master_id)}
            now = int(self.clock())
            service = self.store.service(int(service_id))
            rows = [[(day_label(self.cfg, day_start(self.cfg, now, offset)), f'day:{service_id}:{master_id}:{offset}')]
                    for offset in range(DAY_BUTTONS)
                    if self._bookable_rows(service, int(master_id), now, offset)]
            if not rows:
                await message.reply_text(t(lang, 'nothing_free'))
                return
            await message.reply_text(t(lang, 'choose_day'), reply_markup=self._buttons(rows))
            return
        if data.startswith('day:'):
            _, service_id, master_id, offset = data.split(':')
            now = int(self.clock())
            service = self.store.service(int(service_id))
            free = self._bookable_rows(service, int(master_id), now, int(offset))
            rows = [[(time_label(self.cfg, row['starts_at']), f"slot:{service_id}:{master_id}:{row['starts_at']}")]
                    for row in free]
            if not rows:
                await message.reply_text(t(lang, 'nothing_free'))
                return
            await message.reply_text(t(lang, 'choose_slot'), reply_markup=self._buttons(rows))
            return
        if data.startswith('slot:'):
            _, service_id, master_id, starts_at = data.split(':')
            if int(starts_at) <= int(self.clock()):
                await message.reply_text(t(lang, 'slot_past'))
                return
            self.pending[user.id] = {'service_id': int(service_id), 'master_id': int(master_id),
                                     'starts_at': int(starts_at)}
            await message.reply_text(t(lang, 'ask_contact'))
            return
        if data.startswith('cancel:'):
            await self._cancel(update, int(data.split(':', 1)[1]))
            return
        if data.startswith('move:'):
            await self._move(update, int(data.split(':', 1)[1]))
            return

    async def text(self, update, context):
        user, message = update.effective_user, update.effective_message
        lang = lang_of(user)
        state = self.pending.get(user.id)
        if not state or 'starts_at' not in state:
            return
        parsed = parse_contact(message.text)
        if parsed is None:
            await message.reply_text(t(lang, 'bad_contact'))
            return
        name, contact = parsed
        service = self.store.service(state['service_id'])
        master = self.store.master(state['master_id'])
        now = int(self.clock())
        try:
            booking_id = self.store.create_booking(
                user.id, service, state['master_id'], state['starts_at'], name, contact, now=now, lang=lang,
                hold_expires_at=scheduling.hold_expires_at(self.cfg, now))
        except SlotTaken:
            await message.reply_text(t(lang, 'slot_taken'))
            return
        except PastTime:
            await message.reply_text(t(lang, 'slot_past'))
            return
        self.pending.pop(user.id, None)
        fields = payments.invoice_fields(
            service, booking_id, self._when(state['starts_at'], lang), lang, self.cfg.currency,
            self.cfg.provider_token, t(lang, 'invoice_title', service=service['name_ru' if lang == 'ru' else 'name_en']),
            t(lang, 'invoice_description',
              service=service['name_ru' if lang == 'ru' else 'name_en'], when=self._when(state['starts_at'], lang)))
        await message.reply_invoice(**fields)

    # --- payment ---------------------------------------------------------
    async def precheckout(self, update, context):
        query = update.pre_checkout_query
        booking_id = payments.booking_id_from_payload(query.invoice_payload or '')
        booking = self.store.booking(booking_id) if booking_id else None
        service = self.store.service(booking['service_id']) if booking else None
        states = (self.store.slot_states(booking['master_id'], booking['starts_at'], service['duration_min'])
                  if booking else [])
        now = int(self.clock())
        held_by_us = bool(states) and all(state['status'] == 'held' and state['booking_id'] == booking['id']
                                          for state in states)
        hold_expired = any(state['hold_expires_at'] is None or now >= state['hold_expires_at']
                           for state in states)
        ok = (booking is not None and booking['status'] == 'pending_payment'
              and query.currency == self.cfg.currency
              and query.total_amount == payments.deposit_minor(service['price_minor'])
              and held_by_us and not hold_expired)
        if ok:
            await query.answer(ok=True)
            return
        reason = t('en' if not booking else booking['lang'], 'amount_mismatch')
        if booking is not None and not held_by_us:
            reason = t(booking['lang'], 'slot_taken')
        elif booking is not None and hold_expired:
            reason = t(booking['lang'], 'hold_expired')
        await query.answer(ok=False, error_message=reason)

    async def paid(self, update, context):
        user, message = update.effective_user, update.effective_message
        lang = lang_of(user)
        payment = message.successful_payment
        booking_id = payments.booking_id_from_payload(payment.invoice_payload or '')
        booking = self.store.booking(booking_id) if booking_id else None
        if booking is None:
            await message.reply_text(t(lang, 'unknown_booking'))
            return
        now = int(self.clock())
        remind_at = scheduling.reminder_times(self.cfg, now, booking['starts_at'])
        ok = self.store.confirm_payment(booking_id, payment.telegram_payment_charge_id,
                                        payment.provider_payment_charge_id, payment.total_amount, now, *remind_at)
        if not ok:
            # Money arrived but no booking could be confirmed (hold expired, slot re-taken):
            # keep the payment trail marked for refund, tell the client honestly, alert the admin.
            self.store.record_refund_due(booking_id, payment.telegram_payment_charge_id,
                                         payment.provider_payment_charge_id, payment.total_amount, now)
            await message.reply_text(t(booking['lang'], 'payment_refund'))
            for admin_id in self.cfg.admin_ids:
                await self.send_text(admin_id, t(booking['lang'], 'payment_refund_admin',
                                                 id=booking_id,
                                                 amount=money(payment.total_amount, self.cfg.currency),
                                                 charge=payment.telegram_payment_charge_id))
            return
        booking = self.store.booking(booking_id)
        service, master = self.store.service(booking['service_id']), self.store.master(booking['master_id'])
        service_name, master_name = self._names(service, master, booking['lang'])
        await message.reply_text(
            t(booking['lang'], 'paid', service=service_name, master=master_name,
              when=self._when(booking['starts_at'], booking['lang']),
              deposit=money(payments.deposit_minor(service['price_minor']), self.cfg.currency),
              price=money(service['price_minor'], self.cfg.currency)),
            reply_markup=self._buttons([[(t(booking['lang'], 'cancel'), f'cancel:{booking_id}'),
                                         (t(booking['lang'], 'move'), f'move:{booking_id}')]]))

    # --- cancel / reschedule --------------------------------------------
    async def _cancel(self, update, booking_id: int):
        user, message = update.effective_user, update.effective_message
        lang = lang_of(user)
        booking = self.store.booking(booking_id)
        if booking is None or booking['user_id'] != user.id:
            await message.reply_text(t(lang, 'unknown_booking'))
            return
        now = int(self.clock())
        if now >= self.cfg.cancel_deadline(booking['created_at'], booking['starts_at']):
            await message.reply_text(t(booking['lang'], 'cancel_refused', window=self._window_text(booking['lang'])))
            return
        self.store.cancel(booking_id, now)
        await message.reply_text(t(booking['lang'], 'cancelled'))

    async def _move(self, update, booking_id: int):
        user, message = update.effective_user, update.effective_message
        lang = lang_of(user)
        booking = self.store.booking(booking_id)
        if booking is None or booking['user_id'] != user.id:
            await message.reply_text(t(lang, 'unknown_booking'))
            return
        now = int(self.clock())
        if now >= self.cfg.cancel_deadline(booking['created_at'], booking['starts_at']):
            await message.reply_text(t(booking['lang'], 'cancel_refused', window=self._window_text(booking['lang'])))
            return
        self.store.cancel(booking_id, now)
        service_id = booking['service_id']
        self.pending[user.id] = {'service_id': service_id}
        rows = [[(m['name_ru' if booking['lang'] == 'ru' else 'name_en'], f'mst:{service_id}:{m["id"]}')]
                for m in self.store.masters_for(service_id)]
        await message.reply_text(t(booking['lang'], 'moved_prompt'), reply_markup=self._buttons(rows))

    # --- maintenance (reminders + expired holds) -------------------------
    async def run_maintenance(self, now: int) -> int:
        self.store.release_expired(now)

        async def send(row, lang, kind):
            service, master = self.store.service(row['service_id']), self.store.master(row['master_id'])
            service_name, master_name = self._names(service, master, lang)
            await self.send_text(row['user_id'], t(lang, 'reminder', service=service_name, master=master_name,
                                                   when=self._when(row['starts_at'], lang)))

        return await scheduling.send_due_reminders(self.store, self.cfg, send, now)

    # --- admin -----------------------------------------------------------
    def _is_admin(self, user) -> bool:
        return user.id in self.cfg.admin_ids

    async def cmd_schedule(self, update, context):
        user, message = update.effective_user, update.effective_message
        lang = lang_of(user)
        if not self._is_admin(user):
            await message.reply_text(t(lang, 'admin_only'))
            return
        now = int(self.clock())
        args = (message.text or '').split()[1:]
        start = day_start(self.cfg, now, 0)
        if args:
            start = _day_from_label(self.cfg, args[0])
        rows = self.store.bookings_between(start, start + 86400)
        if not rows:
            await message.reply_text(t(lang, 'schedule_empty', day=day_label(self.cfg, start)))
            return
        lines = [t(lang, 'schedule_header', day=day_label(self.cfg, start))]
        for row in rows:
            service, master = self.store.service(row['service_id']), self.store.master(row['master_id'])
            service_name, master_name = self._names(service, master, lang)
            lines.append(f'{time_label(self.cfg, row["starts_at"])} — {master_name}: {service_name}, '
                         f'{row["client_name"]} ({row["client_contact"]}), {row["status"]}')
        await message.reply_text('\n'.join(lines))

    async def cmd_manual(self, update, context):
        user, message = update.effective_user, update.effective_message
        lang = lang_of(user)
        if not self._is_admin(user):
            await message.reply_text(t(lang, 'admin_only'))
            return
        parts = (message.text or '').split()
        if len(parts) < 6:
            await message.reply_text(t(lang, 'admin_usage'))
            return
        service = self.store.service_by_slug(parts[1])
        master = self.store.master_by_name(parts[2]) if service else None
        if service is None or master is None:
            await message.reply_text(t(lang, 'admin_usage'))
            return
        starts_at = _slot_from_label(self.cfg, parts[3], parts[4])
        if starts_at is None:
            await message.reply_text(t(lang, 'admin_usage'))
            return
        name, contact = parts[5], parts[6] if len(parts) > 6 else '-'
        now = int(self.clock())
        remind_at = scheduling.reminder_times(self.cfg, now, starts_at)
        try:
            self.store.create_booking(user.id, service, master['id'], starts_at, name, contact, now=now,
                                      lang=lang, status='paid', source='manual', remind_24h_at=remind_at[0],
                                      remind_2h_at=remind_at[1])
        except SlotTaken:
            await message.reply_text(t(lang, 'slot_taken'))
            return
        except PastTime:
            await message.reply_text(t(lang, 'slot_past'))
            return
        await message.reply_text(t(lang, 'manual_ok', service=service['name_ru' if lang == 'ru' else 'name_en'],
                                   master=master['name_ru' if lang == 'ru' else 'name_en'],
                                   when=self._when(starts_at, lang), name=name))

    async def cmd_close(self, update, context):
        user, message = update.effective_user, update.effective_message
        lang = lang_of(user)
        if not self._is_admin(user):
            await message.reply_text(t(lang, 'admin_only'))
            return
        parts = (message.text or '').split()
        master = self.store.master_by_name(parts[3]) if len(parts) > 3 else None
        starts_at = _slot_from_label(self.cfg, parts[1], parts[2]) if master else None
        if starts_at is None:
            await message.reply_text(t(lang, 'admin_usage'))
            return
        self.store.block_slots(master['id'], starts_at, self.cfg.slot_minutes)
        await message.reply_text(t(lang, 'close_ok', master=master['name_ru' if lang == 'ru' else 'name_en'],
                                   when=self._when(starts_at, lang)))

    async def cmd_csv(self, update, context):
        user, message = update.effective_user, update.effective_message
        lang = lang_of(user)
        if not self._is_admin(user):
            await message.reply_text(t(lang, 'admin_only'))
            return
        now = int(self.clock())
        args = (message.text or '').split()[1:]
        start = day_start(self.cfg, now, 0)
        if args:
            start = _day_from_label(self.cfg, args[0])
        rows = self.store.bookings_between(start, start + 86400)
        lines = ['id,service,master,when,name,contact,status,source']
        for row in rows:
            service, master = self.store.service(row['service_id']), self.store.master(row['master_id'])
            lines.append(f'{row["id"]},{service["name_en"]},{master["name_en"]},'
                         f'{self._when(row["starts_at"], "en")},{row["client_name"]},{row["client_contact"]},'
                         f'{row["status"]},{row["source"]}')
        csv_text = '\n'.join(lines) + '\n'
        await message.reply_document(document=csv_text.encode('utf-8'),
                                     filename=t(lang, 'csv_filename', day=day_label(self.cfg, start)))


def _day_from_label(cfg: Config, label: str) -> int:
    from datetime import datetime
    from .seed import tz
    return int(datetime.strptime(label, '%Y-%m-%d').replace(tzinfo=tz(cfg)).timestamp())


def _slot_from_label(cfg: Config, day_label_: str, time_label_: str):
    from datetime import datetime
    from .seed import tz
    try:
        moment = datetime.strptime(f'{day_label_} {time_label_}', '%Y-%m-%d %H:%M').replace(tzinfo=tz(cfg))
    except ValueError:
        return None
    return int(moment.timestamp())
