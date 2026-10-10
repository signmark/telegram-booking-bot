"""Offline mutation check: every deliberate corruption must make its test fail."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = [
    ('double_booking_guard', 'bookingbot/db.py',
     "if db.execute('SELECT changes()').fetchone()[0] != len(ts):",
     'if False:',
     'test_double_booking_same_slot_is_impossible'),
    ('reminder_not_sent', 'bookingbot/scheduling.py',
     '        await send_message(row, lang, kind)\n        sent += 1',
     '        sent += 1',
     'test_reminders_fire_once_and_not_after_cancel'),
    ('cancel_after_deadline_allowed', 'bookingbot/handlers.py',
     "        if now >= self.cfg.cancel_deadline(booking['created_at'], booking['starts_at']):\n"
     "            await message.reply_text(t(booking['lang'], 'cancel_refused', window=self._window_text(booking['lang'])))\n"
     '            return\n'
     '        self.store.cancel(booking_id, now)\n'
     "        await message.reply_text(t(booking['lang'], 'cancelled'))",
     '        self.store.cancel(booking_id, now)\n'
     "        await message.reply_text(t(booking['lang'], 'cancelled'))",
     'test_cancel_frees_slots_before_deadline_and_refuses_after'),
    ('deposit_math_wrong', 'bookingbot/payments.py',
     'return price_minor * DEPOSIT_PERCENT // 100',
     'return price_minor * 20 // 100',
     'test_full_flow_sends_invoice_with_30_percent_deposit'),
    ('hold_auto_release_removed', 'bookingbot/db.py',
     'AND hold_expires_at<?',
     'AND hold_expires_at<0',
     'test_expired_hold_releases_slots_and_cancels_booking'),
    ('precheckout_ignores_hold_expiry', 'bookingbot/handlers.py',
     'hold_expired = any(state[\'hold_expires_at\'] is None or now >= state[\'hold_expires_at\']\n'
     '                           for state in states)',
     'hold_expired = False',
     'test_precheckout_refuses_expired_hold'),
    ('paid_ignores_confirm_failure', 'bookingbot/handlers.py',
     '        if not ok:',
     '        if False:',
     'test_payment_after_hold_release_is_refunded_not_confirmed'),
    ('past_slots_listing_filter', 'bookingbot/seed.py',
     "    return [row for row in store.open_slots(master_id, start, start + 86400) if row['starts_at'] > now]",
     '    return store.open_slots(master_id, start, start + 86400)',
     'test_past_slots_are_not_shown'),
    ('slot_callback_past_guard', 'bookingbot/handlers.py',
     '            if int(starts_at) <= int(self.clock()):',
     '            if False:',
     'test_slot_callback_refuses_past_time'),
    ('create_booking_past_guard', 'bookingbot/db.py',
     '        if starts_at <= now:\n            raise PastTime()',
     '        if False:\n            raise PastTime()',
     'test_create_booking_refuses_past_time'),
    ('day_visibility_filter', 'bookingbot/handlers.py',
     '                    if self._bookable_rows(service, int(master_id), now, offset)]',
     '                    if True]',
     'test_day_without_future_slots_is_not_shown'),
]


def main():
    failed = []
    for name, rel, before, after, test in MUTATIONS:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            for folder in ('bookingbot', 'tests'):
                shutil.copytree(ROOT / folder, dest / folder,
                                ignore=shutil.ignore_patterns('__pycache__'))
            target = dest / rel
            old = target.read_text(encoding='utf-8')
            pairs = zip(before, after) if isinstance(before, list) else [(before, after)]
            for old_text, new_text in pairs:
                if old.count(old_text) != 1:
                    raise RuntimeError(f'{name}: mutation target missing ({old.count(old_text)})')
                old = old.replace(old_text, new_text, 1)
            target.write_text(old, encoding='utf-8')
            result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                                     '-k', test, '-q'], cwd=dest, capture_output=True, text=True)
            print(f'{name}: {"RED" if result.returncode else "GREEN (BAD)"}')
            if not result.returncode:
                failed.append(name)
    if failed:
        raise SystemExit('survived: ' + ', '.join(failed))


if __name__ == '__main__':
    main()
