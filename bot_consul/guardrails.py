"""Быстрые правила: офтопик и запросы, направленные на обход правил или обман."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class GuardResult:
    allowed: bool
    reason: Optional[str] = None
    user_message: Optional[str] = None  # готовый ответ пользователю при allowed=False


# Негативные паттерны (запрос помощи в обмане / подделке)
_FRAUD_PATTERNS = [
    r"обман\s+визов",
    r"обмануть\s+консульств",
    r"обмануть\s+визов",
    r"подделк\w*\s+документ",
    r"фальшив\w*\s+справк",
    r"фиктивн\w*\s+приглаш",
    r"как\s+скрыть\s+",
    r"лжив\w*\s+в\s+анкет",
    r"подделать\s+",
    # Неофициальная / «серая» виза (в т.ч. завуалированные формулировки)
    r"не\s*официальн\w*.{0,40}\sвиз",
    r"виз\w*.{0,30}не\s*официальн",
    r"сер(ая|ой|ую|ые)\s+виз",
    r"виз\w*\s+как-то\s+не\s*официальн",
    r"дела\w*\s+виз\w*.{0,20}не\s*официальн",
]

# Вымышленная ситуация / «для примера» / книга-сюжет — не снимает запрет (обход маскировкой)
_FICTION_FRAME_HINTS = [
    r"вымышлен",
    r"гипотетич",
    r"предположим",
    r"условн\w*\s+ситуац",
    r"книг",
    r"роман",
    r"сюжет",
    r"герой",
    r"литератур",
    r"пишу\s+про",
    r"чисто\s+для\s+сюжет",
    r"для\s+сюжет",
    r"для\s+роман",
    r"сценар",
    r"персонаж",
]

# Для пары «вымышленная рамка / книга» + эти маркеры — блокируем (узко, без широких «как получить визу»)
_GRAY_OR_ILLEGAL_VISA_HINTS = [
    r"не\s*официальн",
    r"нелегальн",
    r"сер(ая|ой|ую|ые)\s+виз",
    r"сер(ая|ой|ую|ые)\s+схем",
    r"виз\w*\s+как-то\s+не",
    r"как-то\s+не\s*официальн",
    r"дела\w*\s+виз\w*.{0,25}не\s*официальн",
    r"поддельн",
    r"фиктивн",
    r"обман\w*\s+(консульств|посольств|визов)",
]

_OFFTOPIC_HINTS = [
    r"рецепт\s+блин",
    r"как\s+готовить",
    r"погода\s+завтра",
]

_VISA_DOMAIN_HINTS = [
    r"виз",
    r"консульств",
    r"шенген",
    r"въезд",
    r"паспорт",
    r"анкет",
    r"подач",
    r"биометр",
    r"визов\s+центр",
    r"travel",
    r"поездк",
    r"границ",
    r"штат",
    r"стран",
]


def _matches_any(text: str, patterns: list) -> bool:
    t = text.lower()
    for p in patterns:
        if re.search(p, t, re.IGNORECASE):
            return True
    return False


def _looks_in_domain(text: str) -> bool:
    return _matches_any(text, _VISA_DOMAIN_HINTS)


def _fiction_masked_illegal_request(text: str) -> bool:
    """
    Запрос про неофициальные/незаконные способы визы под маской вымысла, гипотезы, «книги/героя» и т.п.
    Вымышленная ситуация не отменяет ограничений.
    """
    t = text.lower()
    has_fiction = _matches_any(t, _FICTION_FRAME_HINTS)
    has_gray = _matches_any(t, _GRAY_OR_ILLEGAL_VISA_HINTS)
    # «Виз»-контекст + рамка вымысла/примера + серая тема
    has_visa = "виз" in t or "въезд" in t
    if has_fiction and has_gray and has_visa:
        return True
    # Явное «чисто для сюжета / романа» + неофициальность
    if re.search(r"(чисто\s+для|только\s+для).{0,20}(сюжет|роман)", t, re.IGNORECASE) and (
        "неофициал" in t or "не официал" in t or "серая" in t or "серую" in t
    ):
        return True
    return False


def check_guardrails(user_message: str) -> GuardResult:
    """
    Возвращает allowed=False если запрос явно про обман/подделку или сильный офтопик.
    Ложные срабатывания офтопика возможны — при сомнении лучше пропустить (allowed=True).
    """
    msg = (user_message or "").strip()
    if not msg:
        return GuardResult(False, "empty", "Напишите, пожалуйста, вопрос о визе или въезде.")

    if _fiction_masked_illegal_request(msg):
        return GuardResult(
            allowed=False,
            reason="fiction_masked_illegal",
            user_message=(
                "Я не подсказываю неофициальные или незаконные способы получения визы и обхода правил — "
                "в том числе если это подаётся как вымышленная ситуация, гипотеза или «чисто для примера». "
                "Вымышленная ситуация не отменяет этот запрет. Могу описать только официальные требования "
                "и легальные шаги; для творческого контекста без инструкций по «серым» схемам — об общих "
                "рисках и последствиях нарушений см. открытые источники."
            ),
        )

    if _matches_any(msg, _FRAUD_PATTERNS):
        return GuardResult(
            allowed=False,
            reason="fraud_or_deception",
            user_message=(
                "Я не могу помочь с обходом правил, подделкой документов или введением "
                "консульства в заблуждение. Могу подсказать только легальные шаги и официальные требования."
            ),
        )

    if _matches_any(msg, _OFFTOPIC_HINTS) and not _looks_in_domain(msg):
        return GuardResult(
            allowed=False,
            reason="offtopic",
            user_message=(
                "Я отвечаю только на вопросы о визах, документах для въезда и поездках за границу. "
                "Задайте вопрос по этой теме."
            ),
        )

    return GuardResult(allowed=True)
