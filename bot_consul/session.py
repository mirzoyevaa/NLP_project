"""Состояние диалога (in-memory, для одного процесса бота)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class TurnMessage:
    role: str  # "user" | "assistant"
    content: str
    ts: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


@dataclass
class VisaProfile:
    """Обезличенный профиль для RAG/промпта (без Telegram ID)."""

    destination: Optional[str] = None
    country: Optional[str] = None  # нормализованный код страны (germany, …) если известен
    dates_or_month: Optional[str] = None
    passport_country: Optional[str] = None
    purpose: Optional[str] = None
    visa_type: Optional[str] = None  # tourist, business, …

    def missing_required(self) -> List[str]:
        required = ["destination", "passport_country", "purpose"]
        out: List[str] = []
        for key in required:
            v = getattr(self, key, None)
            if v is None or (isinstance(v, str) and not v.strip()):
                out.append(key)
        return out


@dataclass
class SessionState:
    session_id: str
    history: List[TurnMessage] = field(default_factory=list)
    profile: VisaProfile = field(default_factory=VisaProfile)
    pending_clarification_fields: List[str] = field(default_factory=list)
    #: True после первого успешного полного ответа с первичным шаблоном (страна+паспорт+цель собраны).
    initial_brief_done: bool = False
    #: Ключ направления (country / нормализация), для которого уже отдан первичный обзор; при смене страны — снова «первый» ответ.
    brief_country_key: Optional[str] = None
    #: Показали ли уже кнопки «полезно» за текущий первичный обзор (сброс при смене направления / /reset).
    feedback_buttons_shown: bool = False

    def add_user(self, text: str) -> None:
        self.history.append(TurnMessage(role="user", content=text))

    def add_assistant(self, text: str) -> None:
        self.history.append(TurnMessage(role="assistant", content=text))

    def recent_context(self, max_turns: int) -> str:
        """Склеивает последние реплики для экстрактора (без персональных идентификаторов)."""
        tail = self.history[-max_turns * 2 :] if max_turns else self.history
        lines = []
        for m in tail:
            lines.append(f"{m.role}: {m.content}")
        return "\n".join(lines)

    def prior_dialog_for_prompt(self, max_turns: int) -> str:
        """
        Диалог без последней реплики (текущее сообщение пользователя задаётся отдельно в промпте).
        Пусто, если в чате только одно сообщение пользователя.
        """
        if len(self.history) < 2:
            return ""
        prior = self.history[:-1]
        tail = prior[-max_turns * 2 :] if max_turns else prior
        lines = [f"{m.role}: {m.content}" for m in tail]
        return "\n".join(lines)


# Глобальное хранилище сессий (MVP: один процесс)
_sessions: Dict[str, SessionState] = {}


def get_session(session_id: str) -> SessionState:
    if session_id not in _sessions:
        _sessions[session_id] = SessionState(session_id=session_id)
    return _sessions[session_id]


def clear_session(session_id: str) -> None:
    _sessions.pop(session_id, None)


def merge_visa_profile(base: VisaProfile, new: VisaProfile) -> None:
    """Заполняет пустые поля base из new; непустые в new перезаписывают."""
    for field in (
        "destination",
        "country",
        "dates_or_month",
        "passport_country",
        "purpose",
        "visa_type",
    ):
        v = getattr(new, field, None)
        if v is not None and (not isinstance(v, str) or v.strip()):
            setattr(base, field, v)
