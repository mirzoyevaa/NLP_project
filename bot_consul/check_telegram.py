#!/usr/bin/env python3
"""
Быстрая проверка: токен из .env, ответ Telegram getMe (без polling).

Запуск из каталога storage_zone:
    PYTHONPATH=. python3 check_telegram.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

from bot_consul.config import orchestrator_settings


async def main() -> None:
    token = (orchestrator_settings.TELEGRAM_BOT_TOKEN or "").strip()
    if not token:
        print(
            "ОШИБКА: TELEGRAM_BOT_TOKEN пуст.\n"
            f"  Ожидается файл: {_ROOT / '.env'}\n"
            "  Добавьте строку: TELEGRAM_BOT_TOKEN=... (от @BotFather)",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
    except ImportError as e:
        print("ОШИБКА: не установлен aiogram. Выполните: pip install -r requirements.txt", file=sys.stderr)
        raise SystemExit(1) from e

    bot = Bot(token=token, default=DefaultBotProperties())
    try:
        me = await bot.get_me()
        print(f"OK: бот @{me.username or '?'} (id={me.id}) — токен принят Telegram.")
    except Exception as e:
        print(
            "ОШИБКА: Telegram отклонил токен или нет сети:\n",
            repr(e),
            file=sys.stderr,
        )
        sys.exit(2)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
