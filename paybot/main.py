"""Entry point:  python -m paybot.main"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

from paybot.bot import build_router
from paybot.config import ConfigError, load_settings
from paybot.service import PaymentService
from paybot.storage import Storage

log = logging.getLogger("paybot")


async def amain() -> None:
    load_dotenv()
    settings = load_settings()
    storage = Storage(settings.db_path)
    try:
        storage.load_catalog(settings.catalog_path)
    except FileNotFoundError as exc:
        raise ConfigError(f"Нет {settings.catalog_path}: скопируйте catalog.example.json в catalog.json") from exc
    service = PaymentService(storage, settings.currency, datetime.now)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    async def notify_admin(text: str) -> None:
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(admin_id, text)
            except Exception as exc:  # noqa: BLE001 - a blocked admin chat must not break payments
                log.warning("Не удалось уведомить %s: %s", admin_id, type(exc).__name__)

    dp = Dispatcher()
    dp.include_router(build_router(service, settings.provider_token, settings.admin_ids, settings.test_mode, notify_admin))
    log.info("Бот оплаты запущен, режим: %s", "ТЕСТОВЫЙ" if settings.test_mode else "БОЕВОЙ")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        storage.close()


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    try:
        asyncio.run(amain())
    except ConfigError as exc:
        sys.exit(f"Ошибка настройки: {exc}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
