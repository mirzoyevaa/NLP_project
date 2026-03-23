"""
Telegram-бот «Бот-консул»: long polling, вызов оркестратора.

Запуск из каталога bot_consul (как в start_bot.sh) или из storage_zone с PYTHONPATH::

    export PYTHONPATH="..:."
    export TELEGRAM_BOT_TOKEN=...   # или только в .env
    python3 -m bot_consul.telegram_bot

На macOS с pyenv команда ``python`` часто отсутствует — используйте ``python3``
(или: ``pyenv global 3.12.8`` и проверьте ``which python``).

Требуется .env с OPENROUTER_API_KEY и настройками Qdrant (storage).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from html import escape
from pathlib import Path

# storage_zone + каталог с travel_web_agent / search_artifacts (см. start_bot.sh)
_ROOT = Path(__file__).resolve().parent.parent
_PKG_SRC = Path(__file__).resolve().parent
for _p in (_ROOT, _PKG_SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# До любых модулей, тянущих transformers/sentence_transformers — стабильный порядок инициализации бэкенда.
try:
    import torch  # noqa: F401
except ImportError as e:
    raise RuntimeError(
        "Не удалось import torch. Установите PyTorch в venv бота: pip install -r bot_consul/requirements.txt"
    ) from e

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

try:
    from aiogram.types import ErrorEvent
except ImportError:  # старые версии aiogram 3.x
    from aiogram.types.error_event import ErrorEvent
from aiogram.exceptions import TelegramBadRequest

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

from bot_consul.config import orchestrator_settings
from bot_consul.orchestrator import Orchestrator, OrchestratorResult, TurnMode
from bot_consul.session import clear_session
from bot_consul.user_id_log import hash_session_id_for_log
from bot_consul.telegram_format import llm_reply_for_telegram, llm_reply_for_telegram_html

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=_LOG_FORMAT,
)
# Дублирование в файл — чтобы можно было открыть bot.log после запуска в фоне
_LOG_FILE = _ROOT / "bot.log"
try:
    _fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    _fh.setLevel(logging.INFO)
    _fh.setFormatter(logging.Formatter(_LOG_FORMAT))
    logging.getLogger().addHandler(_fh)
except OSError as e:
    logging.getLogger("telegram_consul").warning("Не удалось писать в %s: %s", _LOG_FILE, e)

logging.getLogger("travel_web_agent").setLevel(logging.INFO)
logger = logging.getLogger("telegram_consul")

# Лимит Telegram на одно сообщение
MAX_MESSAGE_LEN = 4096
SAFE_CHUNK = 4000

# Обратная связь по первому полному ответу (callback_data ≤ 64 байт у Telegram)
CB_FEEDBACK_YES = "fb:yes"
CB_FEEDBACK_NO = "fb:no"


def _feedback_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, полезно", callback_data=CB_FEEDBACK_YES),
                InlineKeyboardButton(text="Нет", callback_data=CB_FEEDBACK_NO),
            ]
        ]
    )


# Текст вопроса рядом с кнопками (одно сообщение с reply_markup)
_FEEDBACK_Q_HTML = "\n\n<b>Ответ был полезен?</b>"
_FEEDBACK_Q_PLAIN = "\n\nОтвет был полезен?"


def _append_feedback_question(text: str, *, as_html: bool) -> str:
    """Добавляет вопрос к телу сообщения; укладывается в MAX_MESSAGE_LEN."""
    suffix = _FEEDBACK_Q_HTML if as_html else _FEEDBACK_Q_PLAIN
    max_main = MAX_MESSAGE_LEN - len(suffix)
    if max_main < 0:
        max_main = 0
    base = (text or "")[:max_main]
    return base + suffix


def _session_id(message: Message) -> str:
    """Один контекст на пользователя в личке; в группах — чат+пользователь."""
    if message.chat.type == "private":
        return f"tg:{message.from_user.id}"
    return f"tg:{message.chat.id}:{message.from_user.id}"


def _split_for_telegram(text: str) -> list[str]:
    """Делит длинный ответ на части ≤ SAFE_CHUNK (по возможности по строкам)."""
    text = text or ""
    if len(text) <= MAX_MESSAGE_LEN:
        return [text] if text else [""]

    parts: list[str] = []
    rest = text
    while rest:
        if len(rest) <= SAFE_CHUNK:
            parts.append(rest)
            break
        cut = rest.rfind("\n", 0, SAFE_CHUNK)
        if cut < SAFE_CHUNK // 2:
            cut = SAFE_CHUNK
        chunk = rest[:cut].strip()
        if chunk:
            parts.append(chunk)
        rest = rest[cut:].lstrip()
    return parts if parts else [""]


async def main() -> None:
    token = orchestrator_settings.TELEGRAM_BOT_TOKEN or ""
    if not token.strip():
        logger.error("Задайте TELEGRAM_BOT_TOKEN в .env или переменных окружения.")
        sys.exit(1)

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    @dp.error()
    async def on_errors(event: ErrorEvent) -> None:
        """Пишем необработанные ошибки в bot.log (и stderr)."""
        logger.error(
            "Необработанная ошибка в обработчике: %s",
            event.exception,
            exc_info=(type(event.exception), event.exception, event.exception.__traceback__),
        )

    orch = Orchestrator()

    @dp.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        if message.from_user:
            logger.info(
                "user_hash=%s cmd_start",
                hash_session_id_for_log(_session_id(message)),
            )
        text = (
            "<b>Добро пожаловать!</b>\n\n"
            "Я — <b>Бот-консул</b>: подскажу по визам и документам для поездок, опираясь на "
            "базу знаний и открытые источники.\n\n"
            "<b>Напишите в одном сообщении:</b>\n"
            "• куда едете\n"
            "• ваше гражданство\n"
            "• цель поездки (туризм, работа, учёба и т.д.)\n\n"
            "Чем точнее запрос — тем полезнее ответ. Уточняющие вопросы можно задавать здесь же — "
            "контекст диалога сохраняется.\n\n"
            "<b>Важно:</b> я не консульство и не визовый центр, я не гарантирую получение визы и "
            "не заменяю официальные требования — перед подачей документов сверяйтесь с сайтом "
            "консульства или визового центра.\n\n"
        )
        await message.answer(text)

    @dp.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await message.answer(
            "<b>Как пользоваться</b>\n"
            "• Опишите страну, паспорт (например, РФ) и цель поездки.\n"
            "• Задавайте уточняющие вопросы в одном чате — контекст сохраняется.\n"
            "• /reset — сбросить историю диалога.\n\n"
            "Сверяйте важное с официальными сайтами консульств."
        )

    @dp.message(Command("reset"))
    async def cmd_reset(message: Message) -> None:
        if message.from_user:
            logger.info(
                "user_hash=%s cmd_reset",
                hash_session_id_for_log(_session_id(message)),
            )
        clear_session(_session_id(message))
        await message.answer("Контекст сброшен. Можете описать новый запрос.")

    @dp.callback_query(F.data.in_({CB_FEEDBACK_YES, CB_FEEDBACK_NO}))
    async def on_feedback(callback: CallbackQuery) -> None:
        """Ответ на кнопки «было ли полезно» после первого полного ответа."""
        if not callback.data or not callback.from_user:
            await callback.answer()
            return
        if callback.from_user and callback.message:
            logger.info(
                "user_hash=%s feedback=%s",
                hash_session_id_for_log(_session_id(callback.message)),
                callback.data,
            )
        thanks = (
            "Спасибо, рады помочь!"
            if callback.data == CB_FEEDBACK_YES
            else "Спасибо за честный отзыв — постараемся улучшить ответы."
        )
        await callback.answer(thanks, show_alert=False)
        try:
            if callback.message:
                await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest as e:
            logger.debug("edit_reply_markup after feedback: %s", e)

    # Только обычный текст (не команды с /). Надёжнее, чем ~Command() на всех версиях aiogram.
    @dp.message(F.text & ~F.text.startswith("/"))
    async def on_text(message: Message) -> None:
        if not message.text or not message.from_user:
            return

        sid = _session_id(message)
        user_text = message.text.strip()
        if not user_text:
            return

        uh = hash_session_id_for_log(sid)
        logger.info(
            "user_hash=%s incoming len=%s mode=text",
            uh,
            len(user_text),
        )

        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        loop = asyncio.get_running_loop()

        def _run() -> OrchestratorResult:
            return orch.run_turn(sid, user_text)

        try:
            result: OrchestratorResult = await loop.run_in_executor(None, _run)
        except Exception as e:
            logger.exception(
                "orchestrator failed user_hash=%s: %s",
                uh,
                e,
            )
            await message.answer(
                "Произошла ошибка при обработке. Попробуйте позже или отправьте /reset."
            )
            return

        reply = result.message
        chunks = _split_for_telegram(reply)
        meta = result.meta or {}
        # Флаг из оркестратора: первый содержательный ответ по первичному брифу (один раз)
        mode_val = getattr(result.mode, "value", result.mode)
        is_answer = result.mode == TurnMode.ANSWER or mode_val == "answer"
        fb_meta = meta.get("show_feedback_buttons")
        show_fb = bool(is_answer and fb_meta)
        logger.info(
            "user_hash=%s feedback_ui show=%s phase=%s mode=%s fb_meta=%r",
            uh,
            show_fb,
            meta.get("phase"),
            mode_val,
            fb_meta,
        )

        for i, chunk in enumerate(chunks):
            if not chunk:
                body = "…"
            else:
                try:
                    body = llm_reply_for_telegram_html(chunk)
                except Exception as e:
                    logger.warning("telegram_format failed: %s", e)
                    body = escape(chunk.replace("\x00", ""))
            # Клавиатура на первом chunk — кнопки сразу видны при длинном ответе; к вопросу добавляем текст
            kb = _feedback_keyboard() if (show_fb and i == 0) else None
            if kb:
                body = _append_feedback_question(body, as_html=True)
            try:
                await message.answer(
                    body[:MAX_MESSAGE_LEN],
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
            except TelegramBadRequest as e:
                logger.warning("send HTML failed (chunk %s): %s — fallback plain", i, e)
                plain = llm_reply_for_telegram(chunk)
                if kb:
                    plain = _append_feedback_question(plain, as_html=False)
                await message.answer(
                    plain[:MAX_MESSAGE_LEN],
                    parse_mode=None,
                    reply_markup=kb,
                )
            except Exception as e:
                logger.warning("send failed (chunk %s): %s", i, e)
                raw = (chunk or "…").replace("\x00", "")
                if kb:
                    raw = _append_feedback_question(raw, as_html=False)
                await message.answer(
                    raw[:MAX_MESSAGE_LEN],
                    parse_mode=None,
                    reply_markup=kb,
                )

        # Короткая мета в конце для отладки (можно отключить)
        if result.meta and logger.isEnabledFor(logging.DEBUG):
            await message.answer(escape(str(result.meta)), parse_mode=None)

        logger.info(
            "user_hash=%s turn_done mode=%s",
            uh,
            getattr(result.mode, "value", result.mode),
        )

    # Проверка токена до polling — в логах сразу видно, если токен неверный
    me = await bot.get_me()
    logger.info(
        "Polling стартует: @%s (id=%s). Остановка: Ctrl+C.",
        me.username or "?",
        me.id,
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
