"""Entry point: wires the booking service into python-telegram-bot and the job queue."""
from __future__ import annotations

import logging
import time

from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, MessageHandler,
                          PreCheckoutQueryHandler, filters)

from .config import Config
from .db import Store
from .handlers import BookingService
from .seed import seed


def configure_logging():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s %(message)s')
    for name in ('httpx', 'httpcore'):
        logging.getLogger(name).setLevel(logging.WARNING)


def main():
    configure_logging()
    cfg = Config.from_env()
    store = Store(cfg.db_path, slot_minutes=cfg.slot_minutes)
    seed(store, cfg, int(time.time()))
    service = BookingService(store, cfg, time.time)
    app = Application.builder().token(cfg.bot_token).build()

    async def send_text(user_id, text):
        await app.bot.send_message(chat_id=user_id, text=text)

    service.send_text = send_text

    app.add_handler(CommandHandler('start', service.start))
    app.add_handler(CommandHandler('schedule', service.cmd_schedule))
    app.add_handler(CommandHandler('manual', service.cmd_manual))
    app.add_handler(CommandHandler('close', service.cmd_close))
    app.add_handler(CommandHandler('csv', service.cmd_csv))
    app.add_handler(CallbackQueryHandler(service.callback))
    app.add_handler(PreCheckoutQueryHandler(service.precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, service.paid))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, service.text))

    async def maintenance(context):
        await service.run_maintenance(int(time.time()))

    app.job_queue.run_repeating(maintenance, first=5, interval=30)
    app.run_polling()


if __name__ == '__main__':
    main()
