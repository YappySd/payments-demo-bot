"""Settings from environment variables (see .env.example)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from paybot.core import is_test_token


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    bot_token: str
    provider_token: str
    admin_ids: frozenset[int]
    currency: str
    catalog_path: str
    db_path: str
    test_mode: bool


def load_settings(env=os.environ) -> Settings:
    token = env.get("BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("BOT_TOKEN не задан. Скопируйте .env.example в .env.")
    provider = env.get("PROVIDER_TOKEN", "").strip()
    if not provider:
        raise ConfigError("PROVIDER_TOKEN не задан: @BotFather → бот → Payments → тестовый провайдер.")
    test_mode = is_test_token(provider)
    # A live token means real money. Require an explicit second switch so it never happens by accident.
    if not test_mode and env.get("ALLOW_LIVE_PAYMENTS", "") != "yes":
        raise ConfigError("PROVIDER_TOKEN боевой (не :TEST:). Для реальных платежей добавьте ALLOW_LIVE_PAYMENTS=yes.")
    currency = env.get("CURRENCY", "UAH").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ConfigError("CURRENCY: трёхбуквенный код, например UAH")
    try:
        admins = frozenset(int(x) for x in env.get("ADMIN_IDS", "").replace(" ", "").split(",") if x)
    except ValueError as exc:
        raise ConfigError("ADMIN_IDS: числа через запятую") from exc
    return Settings(
        bot_token=token,
        provider_token=provider,
        admin_ids=admins,
        currency=currency,
        catalog_path=env.get("CATALOG_PATH", "catalog.json"),
        db_path=env.get("DB_PATH", "payments.db"),
        test_mode=test_mode,
    )
