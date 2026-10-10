"""Test-mode Telegram Payments: 30% prepayment, payload carries the booking id."""
from __future__ import annotations

DEPOSIT_PERCENT = 30


def deposit_minor(price_minor: int) -> int:
    return price_minor * DEPOSIT_PERCENT // 100


def invoice_fields(service, booking_id: int, when_text: str, lang: str, currency: str,
                   provider_token: str, title: str, description: str) -> dict:
    return {
        'title': title,
        'description': description,
        'payload': f'booking:{booking_id}',
        'provider_token': provider_token,
        'currency': currency,
        'prices': [{'label': service['name_ru'] if lang == 'ru' else service['name_en'],
                    'amount': deposit_minor(service['price_minor'])}],
    }


def invoice_payload(booking_id: int) -> str:
    return f'booking:{booking_id}'


def booking_id_from_payload(payload: str) -> int | None:
    if not payload.startswith('booking:'):
        return None
    tail = payload.split(':', 1)[1]
    return int(tail) if tail.isdigit() else None
