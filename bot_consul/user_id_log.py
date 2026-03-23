"""Хеширование идентификаторов пользователей для логов (без сырого Telegram user id)."""

from __future__ import annotations

import hashlib

from bot_consul.config import orchestrator_settings


def _pepper() -> str:
    p = (orchestrator_settings.TELEGRAM_USER_ID_LOG_PEPPER or "").strip()
    return p or "bot-consul-default-pepper-set-TELEGRAM_USER_ID_LOG_PEPPER-in-env"


def hash_telegram_user_id(user_id: int) -> str:
    """Короткий стабильный SHA-256 по числовому id (16 hex)."""
    raw = f"tg_uid:{user_id}:{_pepper()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def hash_session_id_for_log(session_id: str) -> str:
    """Хеш session_id (например tg:12345) для логов оркестратора."""
    raw = f"sid:{session_id}:{_pepper()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
