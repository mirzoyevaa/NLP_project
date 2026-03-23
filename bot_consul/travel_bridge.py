"""
Мост между оркестратором и travel_web_agent.py (бывший agent_openrouter):
три сабагента с веб-поиском (официальные источники, отзывы, практика).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Optional

from bot_consul.session import VisaProfile

logger = logging.getLogger(__name__)


def visa_profile_to_travel_profile(vp: VisaProfile) -> Dict[str, Any]:
    """Формат travel_profile, ожидаемый сабагентами в travel_web_agent."""
    return {
        "destination": vp.destination or "",
        "country": vp.country or "",
        "passportCountry": vp.passport_country or "",
        "nationality": vp.passport_country or "",
        "citizenship": vp.passport_country or "",
        "purpose": vp.purpose or "",
        "datesOrMonth": vp.dates_or_month or "",
    }


def run_travel_web_subagents(
    travel_profile: Dict[str, Any],
    *,
    today_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Запускает official_docs_subagent, reviews_subagent, practical_recs_subagent.
    Тяжёлый вызов (много HTTP + LLM).
    """
    # Ленивый импорт: не тянуть langgraph/ddgs при unit-тестах guardrails
    from travel_web_agent import (
        official_docs_subagent,
        practical_recs_subagent,
        reviews_subagent,
    )

    today_iso = today_iso or date.today().isoformat()
    dest = travel_profile.get("destination") or travel_profile.get("country") or "?"
    logger.info(
        "travel_web: запуск трёх сабагентов (destination=%s, passport=%s)",
        dest,
        travel_profile.get("passportCountry") or "?",
    )
    logger.info("travel_web: [1/3] official_docs_subagent …")
    official = official_docs_subagent(today_iso=today_iso, travel_profile=travel_profile)
    logger.info("travel_web: [2/3] reviews_subagent …")
    reviews = reviews_subagent(today_iso=today_iso, travel_profile=travel_profile)
    logger.info("travel_web: [3/3] practical_recs_subagent …")
    practical = practical_recs_subagent(today_iso=today_iso, travel_profile=travel_profile)
    logger.info("travel_web: сабагенты завершены")
    return {
        "official": official,
        "reviews": reviews,
        "practical": practical,
    }


def format_travel_web_for_prompt(materials: Dict[str, Any]) -> str:
    """Текст для промпта LLM."""
    lines: list[str] = []
    o = materials.get("official") or {}
    r = materials.get("reviews") or {}
    p = materials.get("practical") or {}
    if o.get("summary"):
        lines.append(
            "### Веб-сбор: официальные/авторитетные сведения\n" + str(o.get("summary", ""))
        )
    if r.get("summary"):
        lines.append("### Веб-сбор: опыт путешественников\n" + str(r.get("summary", "")))
    if p.get("summary"):
        lines.append(
            "### Веб-сбор: практические советы (только важное для поездки; без общих чеклистов)\n"
            + str(p.get("summary", ""))
        )
    return "\n\n".join(lines)
