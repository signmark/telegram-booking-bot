"""Ru/en texts; the demo disclaimer is intentionally loud — no real money moves in test mode."""
from __future__ import annotations

TEXT = {
    'ru': {
        'start': (
            'Здравствуйте! Это ДЕМО-бот салона красоты «Craft Podium Demo» (вымышленный салон).\n'
            'ДЕМО-РЕЖИМ: оплата тестовая — карта {card} , '
            'настоящие деньги НЕ списываются.\n\n'
            'Выберите услугу, мастера и время; предоплата 30% — тестовая, как и весь бот.'),
        'book': 'Записаться',
        'choose_service': 'Выберите услугу:',
        'choose_master': 'Выберите мастера:',
        'choose_day': 'Выберите день:',
        'choose_slot': 'Выберите время:',
        'ask_contact': 'Напишите имя и контакт в формате: Имя, телефон (или @username).',
        'bad_contact': 'Не разобрал контакт. Нужно: Имя, телефон (или @username). Пример: Анна, +7 900 000-00-00.',
        'invoice_title': '{service} — предоплата 30%',
        'invoice_description': (
            'ДЕМО-ОПЛАТА (тестовая карта, деньги не списываются). '
            'Предоплата 30% за «{service}» к {when}. Остальное — на месте.'),
        'paid': (
            'Оплата прошла (ДЕМО — деньги не списаны).\n'
            'Вы записаны: {service}, мастер {master}, {when}.\n'
            'Предоплата 30%: {deposit} из {price}.'),
        'cancel': 'Отменить',
        'move': 'Перенести',
        'cancelled': 'Запись отменена, время снова свободно.',
        'cancel_refused': 'Отменить или перенести можно {window}. Пожалуйста, свяжитесь с администратором.',
        'moved_prompt': 'Запись отменена. Выберите новое время:',
        'reminder': 'Напоминание: {service} у мастера {master} — {when}.',
        'nothing_free': 'На это время ничего не свободно. Выберите другой слот.',
        'slot_taken': 'К сожалению, это время уже заняли.',
        'hold_expired': 'Слот больше не удержан. Выберите время заново.',
        'slot_past': 'Это время уже прошло. Выберите будущее время.',
        'payment_refund': (
            'Оплата прошла, но слот уже не удержан — запись не создана. '
            'Предоплата будет возвращена. Приносим извинения.'),
        'payment_refund_admin': (
            'ТРЕБУЕТСЯ ВОЗВРАТ: оплата без записи. Бронь #{id}, {amount}, charge {charge}.'),
        'amount_mismatch': 'Сумма не сходится с записью.',
        'unknown_booking': 'Запись не найдена.',
        'admin_only': 'Команда только для администратора.',
        'admin_usage': 'Использование: /schedule [ГГГГ-ММ-ДД] · /manual <услуга> <мастер> <ГГГГ-ММ-ДД ЧЧ:ММ> <Имя> <контакт> · /close <ГГГГ-ММ-ДД> <ЧЧ:ММ> <мастер> · /csv [ГГГГ-ММ-ДД]',
        'schedule_empty': 'На {day} записей нет.',
        'schedule_header': 'Расписание на {day}:',
        'manual_ok': 'Ручная запись создана: {service}, {master}, {when}, {name}.',
        'close_ok': 'Слот закрыт: {master}, {when}.',
        'csv_filename': 'bookings-{day}.csv',
        'free_slots': 'Свободно: ',
    },
    'en': {
        'start': (
            'Hello! This is a DEMO beauty salon bot, «Craft Podium Demo» (a fictional salon).\n'
            'DEMO MODE: payment is test-only — card {card} ; '
            'no real money is charged.\n\n'
            'Pick a service, master and time; the 30% prepayment is test-only, like the whole bot.'),
        'book': 'Book',
        'choose_service': 'Choose a service:',
        'choose_master': 'Choose a master:',
        'choose_day': 'Choose a day:',
        'choose_slot': 'Choose a time:',
        'ask_contact': 'Send your name and contact as: Name, phone (or @username).',
        'bad_contact': 'Could not read the contact. Format: Name, phone (or @username). Example: Anna, +44 7700 900000.',
        'invoice_title': '{service} — 30% prepayment',
        'invoice_description': (
            'DEMO PAYMENT (test card, no real money). '
            '30% prepayment for «{service}» on {when}. The rest is paid on site.'),
        'paid': (
            'Payment received (DEMO — no money was charged).\n'
            'Booked: {service} with {master}, {when}.\n'
            '30% prepayment: {deposit} of {price}.'),
        'cancel': 'Cancel',
        'move': 'Reschedule',
        'cancelled': 'Booking cancelled, the slot is free again.',
        'cancel_refused': 'Cancelling or rescheduling is possible {window}. Please contact the administrator.',
        'moved_prompt': 'Booking cancelled. Choose a new time:',
        'reminder': 'Reminder: {service} with {master} — {when}.',
        'nothing_free': 'Nothing is free at that time. Choose another slot.',
        'slot_taken': 'Sorry, that time has just been taken.',
        'hold_expired': 'The slot is no longer held. Please choose a time again.',
        'slot_past': 'That time is in the past. Please choose a future time.',
        'payment_refund': (
            'The payment went through but the slot is no longer held — no booking was created. '
            'Your prepayment will be refunded. Sorry about that.'),
        'payment_refund_admin': (
            'REFUND REQUIRED: payment without a booking. Booking #{id}, {amount}, charge {charge}.'),
        'amount_mismatch': 'The amount does not match the booking.',
        'unknown_booking': 'Booking not found.',
        'admin_only': 'This command is for administrators only.',
        'admin_usage': 'Usage: /schedule [YYYY-MM-DD] · /manual <service> <master> <YYYY-MM-DD HH:MM> <Name> <contact> · /close <YYYY-MM-DD> <HH:MM> <master> · /csv [YYYY-MM-DD]',
        'schedule_empty': 'No bookings on {day}.',
        'schedule_header': 'Schedule for {day}:',
        'manual_ok': 'Manual booking created: {service}, {master}, {when}, {name}.',
        'close_ok': 'Slot closed: {master}, {when}.',
        'csv_filename': 'bookings-{day}.csv',
        'free_slots': 'Available: ',
    },
}


def t(lang: str, key: str, **kw) -> str:
    return TEXT['en' if lang == 'en' else 'ru'][key].format(**kw)
