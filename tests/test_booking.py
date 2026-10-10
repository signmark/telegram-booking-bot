"""Real-handler tests for demo №4: fake updates, real SQLite, no network."""
from __future__ import annotations

import sys
import threading
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bookingbot.config import Config
from bookingbot.db import PastTime, SlotTaken, Store
from bookingbot.handlers import BookingService, parse_contact
from bookingbot.payments import deposit_minor
from bookingbot.seed import day_start, seed

BASE = int(datetime(2026, 10, 12, 12, 0, tzinfo=ZoneInfo('Europe/Moscow')).timestamp())


class FakeMessage:
    def __init__(self, text=None, payment=None):
        self.text = text
        self.successful_payment = payment
        self.sent = []

    async def reply_text(self, value, **kw):
        self.sent.append(('text', value, kw))

    async def reply_invoice(self, **fields):
        self.sent.append(('invoice', fields, {}))

    async def reply_document(self, **kw):
        self.sent.append(('document', kw, {}))


class FakeCallback:
    def __init__(self, data):
        self.data = data
        self.answers = []

    async def answer(self, *args, **kw):
        self.answers.append((args, kw))


class FakePreCheckout:
    def __init__(self, payload, amount, currency):
        self.invoice_payload = payload
        self.total_amount = amount
        self.currency = currency
        self.answers = []

    async def answer(self, ok=False, error_message=None):
        self.answers.append((ok, error_message))


def user(user_id=7, lang='ru'):
    return SimpleNamespace(id=user_id, full_name='Анна', username='anna', language_code=lang)


def update(message=None, *, u=None, callback=None, precheckout=None):
    return SimpleNamespace(effective_user=u or user(), effective_message=message,
                           callback_query=callback, pre_checkout_query=precheckout)


def buttons_of(entry):
    markup = entry[2].get('reply_markup')
    if markup is None:
        return []
    return [(button.text, button.callback_data) for row in markup.inline_keyboard for button in row]


def texts(sent):
    return [entry[1] for entry in sent if entry[0] == 'text']


class BookingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = BASE
        self.cfg = self.make_cfg()
        self.store = Store(Path(self.tmp.name) / 'db.sqlite3')
        seed(self.store, self.cfg, self.now)
        self.bot = BookingService(self.store, self.cfg, lambda: self.now)
        self.reminders = []

        async def send_text(user_id, text):
            self.reminders.append((user_id, text))

        self.bot.send_text = send_text

    def make_cfg(self, demo_fast=False):
        return Config(bot_token='123:TEST', provider_token='TESTPROVIDER',
                      test_card='0000 0000 0000 0002', admin_ids=frozenset({99}),
                      db_path=Path(self.tmp.name) / 'db.sqlite3', demo_fast=demo_fast,
                      cancel_hours=12, hold_minutes=15, seed_days=7)

    # --- helpers ---------------------------------------------------------
    def slot(self, day_offset=3, hour=11):
        return day_start(self.cfg, self.now, day_offset) + hour * 3600

    async def flow_to_invoice(self, u=None, *, bot=None, master_id=1, starts_at=None,
                              contact='Анна, +7 900 000-00-00', message=None):
        bot = bot or self.bot
        u = u or user()
        starts_at = starts_at or self.slot()
        message = message or FakeMessage()
        offset = (day_start(self.cfg, starts_at, 0) - day_start(self.cfg, self.now, 0)) // 86400
        for data in ('flow', f'svc:1', f'mst:1:{master_id}', f'day:1:{master_id}:{offset}',
                     f'slot:1:{master_id}:{starts_at}'):
            await bot.callback(update(message, u=u, callback=FakeCallback(data)), None)
        message.text = contact
        await bot.text(update(message, u=u), None)
        return message

    async def pay(self, u=None, booking_id=0, amount=45000, *, bot=None, message=None):
        bot = bot or self.bot
        message = message or FakeMessage()
        # attribute names match telegram.SuccessfulPayment (the real interface)
        message.successful_payment = SimpleNamespace(
            invoice_payload=f'booking:{booking_id}', total_amount=amount,
            currency='RUB', telegram_payment_charge_id='tg-1', provider_payment_charge_id='pv-1')
        await bot.paid(update(message, u=u or user()), None)
        return message

    async def click(self, data, u=None, message=None):
        await self.bot.callback(update(message or FakeMessage(), u=u or user(),
                                       callback=FakeCallback(data)), None)

    # --- start / disclaimer ---------------------------------------------
    async def test_start_shows_demo_disclaimer_with_configured_test_card(self):
        for lang in ('ru', 'en'):
            message = FakeMessage()
            await self.bot.start(update(message, u=user(lang=lang)), None)
            text = texts(message.sent)[0]
            with self.subTest(lang=lang):
                self.assertIn('ДЕМО' if lang == 'ru' else 'DEMO', text)
                self.assertIn(self.cfg.test_card, text)  # card comes from config, not hardcoded
                self.assertIn('30%', text)
        message = FakeMessage()
        await self.bot.start(update(message, u=user()), None)
        self.assertTrue(any(data == 'flow' for _, data in buttons_of(message.sent[0])))

    # --- client flow to invoice -----------------------------------------
    async def test_full_flow_sends_invoice_with_30_percent_deposit(self):
        message = await self.flow_to_invoice()
        invoice = [entry for entry in message.sent if entry[0] == 'invoice']
        self.assertEqual(1, len(invoice))
        fields = invoice[0][1]
        booking = self.store.bookings_for_user(7)[0]
        self.assertEqual(f'booking:{booking["id"]}', fields['payload'])
        self.assertEqual('RUB', fields['currency'])
        self.assertEqual(self.cfg.provider_token, fields['provider_token'])
        self.assertEqual(45000, fields['prices'][0]['amount'])  # 30% of 150 000 minor (1 500 ₽)
        self.assertEqual(45000, deposit_minor(150000))
        self.assertIn('ДЕМО', fields['description'])
        self.assertEqual('pending_payment', booking['status'])
        self.assertEqual('Анна', booking['client_name'])

    async def test_contact_filter_rejects_garbage(self):
        for bad in ('Анна', 'Анна7, +7 900', ', +7 900', 'Анна, xxx', 'А1, +7 900', 'Анна, '):
            with self.subTest(bad=bad):
                self.assertIsNone(parse_contact(bad))
        message = await self.flow_to_invoice(contact='Мусор123, вообще фигня')
        self.assertFalse([entry for entry in message.sent if entry[0] == 'invoice'])
        self.assertIn('Не разобрал', texts(message.sent)[-1])
        self.assertFalse(self.store.bookings_for_user(7))

    # --- payment ---------------------------------------------------------
    async def test_successful_payment_confirms_booking_and_offers_cancel_move(self):
        message = await self.flow_to_invoice()
        booking = self.store.bookings_for_user(7)[0]
        await self.pay(booking_id=booking['id'], message=message)
        booking = self.store.booking(booking['id'])
        self.assertEqual('paid', booking['status'])
        self.assertEqual('booked', self.store.slot_states(1, booking['starts_at'], 60)[0]['status'])
        reply = texts(message.sent)[-1]
        self.assertIn('1 500 ₽', reply)
        data = [callback for _, callback in buttons_of(message.sent[-1])]
        self.assertEqual([f'cancel:{booking["id"]}', f'move:{booking["id"]}'], data)

    async def test_precheckout_refuses_vanished_slot_and_wrong_amount(self):
        await self.flow_to_invoice()
        booking = self.store.bookings_for_user(7)[0]
        good = FakePreCheckout(f'booking:{booking["id"]}', 45000, 'RUB')
        await self.bot.precheckout(update(precheckout=good), None)
        self.assertEqual((True, None), good.answers[-1])
        wrong = FakePreCheckout(f'booking:{booking["id"]}', 45001, 'RUB')
        await self.bot.precheckout(update(precheckout=wrong), None)
        self.assertFalse(wrong.answers[-1][0])
        self.assertIn('не сходится', wrong.answers[-1][1])
        self.store.release_expired(self.now + 3600)  # the hold expired — the slot is gone
        gone = FakePreCheckout(f'booking:{booking["id"]}', 45000, 'RUB')
        await self.bot.precheckout(update(precheckout=gone), None)
        self.assertFalse(gone.answers[-1][0])

    async def test_precheckout_refuses_expired_hold(self):
        # Probe: pay at minute 16, before the 30s sweeper runs — must still refuse.
        await self.flow_to_invoice()
        booking = self.store.bookings_for_user(7)[0]
        query = FakePreCheckout(f'booking:{booking["id"]}', 45000, 'RUB')
        self.now += 16 * 60
        await self.bot.precheckout(update(precheckout=query), None)
        self.assertFalse(query.answers[-1][0])
        self.assertIn('не удержан', query.answers[-1][1])

    async def test_payment_after_hold_release_is_refunded_not_confirmed(self):
        message = await self.flow_to_invoice()
        booking = self.store.bookings_for_user(7)[0]
        self.store.release_expired(self.now + 3600)  # booking cancelled, slots free
        await self.pay(booking_id=booking['id'], message=message)
        reply = texts(message.sent)[-1]
        self.assertIn('будет возвращена', reply)
        self.assertNotIn('Вы записаны', reply)
        self.assertFalse(buttons_of(message.sent[-1]))  # no cancel/move for a non-booking
        rows = self.store.payments_for(booking['id'])
        self.assertEqual(1, len(rows))
        self.assertEqual('refund_due', rows[0]['status'])
        self.assertEqual('tg-1', rows[0]['telegram_charge_id'])
        self.assertEqual(45000, rows[0]['amount_minor'])
        self.assertEqual('cancelled', self.store.booking(booking['id'])['status'])
        self.assertTrue(any('ВОЗВРАТ' in text for _, text in self.reminders))  # admin alerted

    async def test_payment_after_slot_rebooked_is_refunded_not_confirmed(self):
        message = await self.flow_to_invoice()
        booking = self.store.bookings_for_user(7)[0]
        self.store.release_expired(self.now + 3600)
        service = self.store.service(1)
        other = self.store.create_booking(22, service, 1, booking['starts_at'], 'Гость', '+7 911',
                                         now=self.now, hold_expires_at=self.now + 900)
        self.assertTrue(self.store.confirm_payment(other, 'tg-2', 'pv-2', 45000, self.now, 0, 0))
        await self.pay(booking_id=booking['id'], message=message)
        reply = texts(message.sent)[-1]
        self.assertIn('будет возвращена', reply)
        self.assertNotIn('Вы записаны', reply)
        self.assertFalse(buttons_of(message.sent[-1]))
        self.assertEqual('refund_due', self.store.payments_for(booking['id'])[-1]['status'])
        self.assertEqual('paid', self.store.booking(other)['status'])  # the other guest keeps the slot
        self.assertTrue(any('ВОЗВРАТ' in text for _, text in self.reminders))

    async def test_double_booking_same_slot_is_impossible(self):
        starts_at = self.slot()
        service = self.store.service(1)
        wins, losses = [], []
        barrier = threading.Barrier(2)

        def worker(uid):
            store = Store(Path(self.tmp.name) / 'db.sqlite3')
            barrier.wait()
            try:
                store.create_booking(uid, service, 1, starts_at, 'Клиент', '+7 900', now=self.now,
                                     hold_expires_at=self.now + 900)
                wins.append(uid)
            except SlotTaken:
                losses.append(uid)

        threads = [threading.Thread(target=worker, args=(uid,)) for uid in (11, 12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(1, len(wins))
        self.assertEqual(1, len(losses))

    # --- reminders -------------------------------------------------------
    async def test_reminders_fire_once_and_not_after_cancel(self):
        message = await self.flow_to_invoice()
        booking = self.store.bookings_for_user(7)[0]
        await self.pay(booking_id=booking['id'], message=message)
        self.assertEqual(0, await self.bot.run_maintenance(self.now + 1 * 3600))
        self.assertEqual(2, await self.bot.run_maintenance(booking['starts_at'] - 3600))
        self.assertEqual(2, len(self.reminders))
        self.assertIn('Напоминание', self.reminders[0][1])
        self.assertEqual(0, await self.bot.run_maintenance(booking['starts_at'] + 3600))
        # a cancelled booking sends nothing
        message2 = await self.flow_to_invoice(starts_at=self.slot(3, 15))
        booking2 = self.store.bookings_for_user(7)[-1]
        await self.pay(booking_id=booking2['id'], message=message2)
        await self.click(f'cancel:{booking2["id"]}', message=message2)
        self.assertIn('отменена', texts(message2.sent)[-1])
        self.assertEqual(0, await self.bot.run_maintenance(booking2['starts_at'] + 3600))
        self.assertEqual(2, len(self.reminders))

    async def test_demo_reminders_come_minutes_after_payment(self):
        self.bot.cfg = self.make_cfg(demo_fast=True)
        self.cfg = self.bot.cfg
        message = await self.flow_to_invoice()
        booking = self.store.bookings_for_user(7)[0]
        await self.pay(booking_id=booking['id'], message=message)
        self.assertEqual(0, await self.bot.run_maintenance(self.now + 30))
        self.assertEqual(2, await self.bot.run_maintenance(self.now + 125))
        self.assertEqual(2, len(self.reminders))
        self.assertEqual(0, await self.bot.run_maintenance(self.now + 200))

    # --- cancel / reschedule / holds ------------------------------------
    async def test_cancel_frees_slots_before_deadline_and_refuses_after(self):
        message = await self.flow_to_invoice(starts_at=self.slot(3, 11))
        booking = self.store.bookings_for_user(7)[0]
        await self.pay(booking_id=booking['id'], message=message)
        await self.click(f'cancel:{booking["id"]}', message=message)
        self.assertIn('отменена', texts(message.sent)[-1])
        self.assertEqual('open', self.store.slot_states(1, booking['starts_at'], 60)[0]['status'])
        # 12h deadline: an appointment 36h out cancels fine; 11h out is refused
        message2 = await self.flow_to_invoice(starts_at=self.slot(2, 15))
        booking2 = [row for row in self.store.bookings_for_user(7)
                    if row['starts_at'] == self.slot(2, 15)][0]
        await self.pay(booking_id=booking2['id'], message=message2)
        self.now = booking2['starts_at'] - 11 * 3600
        await self.click(f'cancel:{booking2["id"]}', message=message2)
        self.assertIn('не позднее', texts(message2.sent)[-1])
        self.assertEqual('paid', self.store.booking(booking2['id'])['status'])

    async def test_demo_cancel_window_is_compressed(self):
        self.bot.cfg = self.make_cfg(demo_fast=True)
        self.cfg = self.bot.cfg
        message = await self.flow_to_invoice()
        booking = self.store.bookings_for_user(7)[0]
        await self.pay(booking_id=booking['id'], message=message)
        self.now += 90  # past the one-minute demo window
        await self.click(f'cancel:{booking["id"]}', message=message)
        self.assertIn('1 минуты', texts(message.sent)[-1])

    async def test_reschedule_cancels_and_restarts_flow(self):
        message = await self.flow_to_invoice()
        booking = self.store.bookings_for_user(7)[0]
        await self.pay(booking_id=booking['id'], message=message)
        await self.click(f'move:{booking["id"]}', message=message)
        self.assertIn('Запись отменена', texts(message.sent)[-1])
        self.assertEqual('cancelled', self.store.booking(booking['id'])['status'])
        self.assertEqual('open', self.store.slot_states(1, booking['starts_at'], 60)[0]['status'])
        self.assertTrue([data for _, data in buttons_of(message.sent[-1]) if data.startswith('mst:')])

    async def test_expired_hold_releases_slots_and_cancels_booking(self):
        await self.flow_to_invoice()
        booking = self.store.bookings_for_user(7)[0]
        starts_at = booking['starts_at']
        self.assertEqual('held', self.store.slot_states(1, starts_at, 60)[0]['status'])
        self.assertEqual(1, self.store.release_expired(self.now + 16 * 60))
        self.assertEqual('open', self.store.slot_states(1, starts_at, 60)[0]['status'])
        self.assertEqual('cancelled', self.store.booking(booking['id'])['status'])

    # --- past slots (review round) ---------------------------------------
    async def test_past_slots_are_not_shown(self):
        self.now = self.slot(3, 15)  # exactly on the grid — the boundary case
        message = FakeMessage()
        await self.bot.callback(update(message, u=user(), callback=FakeCallback('day:1:1:0')), None)
        labels = [label for label, _ in buttons_of(message.sent[0])]
        self.assertNotIn('14:30', labels)  # 30 minutes ago — gone
        self.assertNotIn('10:00', labels)  # long past — gone
        self.assertIn('15:30', labels)  # in 30 minutes — still bookable
        self.assertIn('16:00', labels)  # future slots of the same day are still offered

    async def test_slot_callback_refuses_past_time(self):
        past = self.slot(3, 11)
        self.now = past + 3600  # the button sat in an old message
        message = FakeMessage()
        await self.bot.callback(update(message, u=user(), callback=FakeCallback(f'slot:1:1:{past}')), None)
        self.assertIn('прошло', texts(message.sent)[-1])
        self.assertFalse(self.bot.pending)
        self.assertFalse(self.store.bookings_for_user(7))

    def test_create_booking_refuses_past_time(self):
        past = self.slot(3, 11)
        service = self.store.service(1)
        with self.assertRaises(PastTime):
            self.store.create_booking(7, service, 1, past, 'Клиент', '+7 900', now=past + 60,
                                      hold_expires_at=past + 900)

    async def test_day_without_future_slots_is_not_shown(self):
        self.now = self.slot(3, 20) + 30 * 60  # 20:30 — the workday is over
        message = FakeMessage()
        await self.bot.callback(update(message, u=user(), callback=FakeCallback('mst:1:1')), None)
        data = [callback for _, callback in buttons_of(message.sent[0])]
        self.assertNotIn('day:1:1:0', data)  # today has nothing left
        self.assertIn('day:1:1:1', data)  # tomorrow is offered

    # --- admin -----------------------------------------------------------
    async def test_admin_manual_booking_schedule_and_csv(self):
        admin = user(99)
        manual = FakeMessage('/manual haircut Анна 2026-10-15 12:00 Иван +7 900 000-00-01')
        await self.bot.cmd_manual(update(manual, u=admin), None)
        self.assertIn('создана', texts(manual.sent)[-1])
        schedule = FakeMessage('/schedule 2026-10-15')
        await self.bot.cmd_schedule(update(schedule, u=admin), None)
        self.assertIn('Иван', texts(schedule.sent)[-1])
        csv_message = FakeMessage('/csv 2026-10-15')
        await self.bot.cmd_csv(update(csv_message, u=admin), None)
        document = [entry for entry in csv_message.sent if entry[0] == 'document'][0][1]
        csv_text = document['document'].decode('utf-8')
        self.assertIn('haircut', csv_text)
        self.assertIn('Иван', csv_text)
        self.assertIn('manual', csv_text)

    async def test_admin_close_slot_makes_it_unbookable(self):
        admin = user(99)
        starts_at = self.slot(3, 13)  # 2026-10-15 13:00
        close = FakeMessage('/close 2026-10-15 13:00 Анна')
        await self.bot.cmd_close(update(close, u=admin), None)
        self.assertIn('закрыт', texts(close.sent)[-1])
        service = self.store.service(1)
        with self.assertRaises(SlotTaken):
            self.store.create_booking(7, service, 1, starts_at, 'Клиент', '+7 900', now=self.now,
                                      hold_expires_at=self.now + 900)

    async def test_admin_commands_reject_non_admin(self):
        for command in ('cmd_schedule', 'cmd_manual', 'cmd_close', 'cmd_csv'):
            message = FakeMessage('/x')
            await getattr(self.bot, command)(update(message, u=user()), None)
            with self.subTest(command=command):
                self.assertIn('только для администратора', texts(message.sent)[-1])

    async def test_english_flow_uses_english_texts(self):
        message = await self.flow_to_invoice(user(lang='en'), contact='Anna, +44 7700 900000')
        invoice = [entry for entry in message.sent if entry[0] == 'invoice'][0][1]
        self.assertIn('DEMO', invoice['description'])
        self.assertIn('prepayment', invoice['title'])


if __name__ == '__main__':
    unittest.main()
