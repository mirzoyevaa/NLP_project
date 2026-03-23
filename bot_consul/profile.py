"""Извлечение профиля визы/поездки из текста (обезличенный запрос для RAG/LLM)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from langchain_core.messages import HumanMessage

from bot_consul.llm_client import OpenRouterChat
from storage.schema import VISA_TYPE_CODES

from bot_consul.session import VisaProfile


def normalize_visa_type_for_store(raw: str | None) -> str | None:
    """Приводит произвольную строку к одному из VISA_TYPE_CODES или None."""
    if not raw:
        return None
    t = raw.lower()
    for code in VISA_TYPE_CODES:
        if code in t or t in code:
            return code
    if "турист" in t or "tourism" in t:
        return "tourist"
    if "бизнес" in t or "business" in t:
        return "business"
    if "учеб" in t or "student" in t:
        return "student"
    if "работ" in t or "work" in t:
        return "work"
    if "транзит" in t or "transit" in t:
        return "transit"
    return "general"


def _extract_json_object(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


# Карта нормализации страны к кодам из storage.schema.COUNTRY_CODES (нижний регистр)
_COUNTRY_ALIASES = {
    "германия": "germany",
    "germany": "germany",
    "франция": "france",
    "france": "france",
    "испания": "spain",
    "spain": "spain",
    "чехия": "czechia",
    "czechia": "czechia",
    "италия": "italy",
    "italy": "italy",
    "таиланд": "thailand",
    "thailand": "thailand",
    "оаэ": "uae",
    "uae": "uae",
    "великобритания": "uk",
    "uk": "uk",
    "сша": "usa",
    "usa": "usa",
    "сербия": "serbia",
    "serbia": "serbia",
    "грузия": "georgia",
    "georgia": "georgia",
    "турция": "turkey",
    "turkey": "turkey",
    "юар": "south_africa",
    "южная африка": "south_africa",
    "south africa": "south_africa",
    "south_africa": "south_africa",
    "rsa": "south_africa",
}


def normalize_country_from_destination(destination: str | None) -> str | None:
    """Публичная обёртка для нормализации страны под фильтр Qdrant."""
    return _normalize_country_key(destination)


def _normalize_country_key(destination: str | None) -> str | None:
    if not destination:
        return None
    key = destination.strip().lower()
    # простое совпадение по первому слову / alias
    for alias, code in _COUNTRY_ALIASES.items():
        if alias in key or key in alias:
            return code
    # латиница одним словом
    w = re.sub(r"[^a-zа-яё\-]", " ", key).split()
    if w:
        cand = w[0].lower()
        if cand in _COUNTRY_ALIASES.values():
            return cand
    return None


def parse_visa_profile(
    user_text: str,
    *,
    dialog_context: str = "",
    llm: OpenRouterChat | None = None,
) -> Tuple[VisaProfile, List[str]]:
    """
    Извлекает VisaProfile и список отсутствующих обязательных полей.
    """
    llm = llm or OpenRouterChat()
    combined = f"{dialog_context}\n\nПоследнее сообщение:\n{user_text}".strip()

    prompt = f"""Извлеки профиль для консультации по визе и въезду.

Верни ТОЛЬКО JSON с ключами:
- destination: string | null  (город или страна назначения)
- datesOrMonth: string | null  (даты или месяц/год поездки)
- passportCountry: string | null  (страна паспорта / гражданства, например Россия)
- purpose: string | null  (туризм, бизнес, учёба, транзит и т.д.)
- visaType: string | null  (если пользователь указал: туристическая, шенген, рабочая; иначе null)

Если поле не указано — null.

Текст:
{combined}
"""
    raw = llm.invoke([HumanMessage(content=prompt)]).content
    data = _extract_json_object(raw)

    profile = VisaProfile(
        destination=(data.get("destination") or "").strip() or None,
        dates_or_month=(data.get("datesOrMonth") or "").strip() or None,
        passport_country=(data.get("passportCountry") or "").strip() or None,
        purpose=(data.get("purpose") or "").strip() or None,
        visa_type=(data.get("visaType") or "").strip() or None,
    )
    profile.country = _normalize_country_key(profile.destination)

    missing = profile.missing_required()
    return profile, missing


QUESTIONS_MAP = {
    "destination": "Куда вы едете (страна или город в стране)?",
    "passport_country": "Какое у вас гражданство / страна паспорта?",
    "purpose": "Цель поездки (туризм, бизнес, учёба, транзит и т.д.)?",
}
