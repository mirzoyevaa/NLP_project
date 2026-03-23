"""
Опциональное дополнение контекста через веб-поиск (DuckDuckGo).

Диагностика в том же стиле, что и ``travel_web_agent.search_web``:
``print`` с префиксами ``[🔍]``, ``[✓ Search result received]`` (как в fact-check узлах).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bot_consul.config import orchestrator_settings

logger = logging.getLogger(__name__)


def _save_ddg_results_json(
    query: str,
    today_iso: Optional[str],
    results: List[Dict[str, Any]],
) -> Optional[str]:
    """Дамп сниппетов DDG в ``data/raw/ddg_search_<ts>.json``."""
    if not orchestrator_settings.SAVE_SEARCH_ARTIFACTS_JSON or not results:
        return None
    try:
        from search_artifacts import save_json_artifact, utc_now_iso
    except ImportError:
        logger.warning("search_artifacts недоступен — JSON не сохранён")
        return None

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    payload = {
        "kind": "ddg_snippets",
        "generated_at_utc": utc_now_iso(),
        "query": query,
        "today_iso": today_iso,
        "max_results": orchestrator_settings.WEB_SEARCH_MAX_RESULTS,
        "results": [
            {
                "title": r.get("title"),
                "body": r.get("body"),
                "href": r.get("href"),
            }
            for r in results
        ],
    }
    return save_json_artifact(payload, filename=f"ddg_search_{ts}.json")


def fetch_web_snippets_pair(
    query: str,
    *,
    today_iso: Optional[str] = None,
    force: bool = False,
) -> tuple[str, List[Dict[str, Any]]]:
    """
    Короткая выдача для промпта + сырые сниппеты (для сохранения в хранилище).

    Возвращает (текст для промпта, список результатов DDG).
    """
    if not force and not orchestrator_settings.ENABLE_WEB_FALLBACK:
        return "", []

    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except ImportError:
            print("   [warn] ddgs/duckduckgo_search не установлены — веб-fallback пропущен")
            logger.warning("Установите: pip install ddgs или duckduckgo-search")
            return "", []

    q = query.strip()
    if today_iso and len(today_iso) >= 4:
        y = today_iso[:4]
        if y and y not in q:
            q = f"{q} {y}"

    print(f"   [🔍]: {q}")

    try:
        with DDGS() as ddgs:
            results = list(
                ddgs.text(q, max_results=orchestrator_settings.WEB_SEARCH_MAX_RESULTS)
            )
    except Exception as e:
        print(f"   [warn] Web search failed: {e}")
        logger.warning("Web search failed: %s", e)
        return "", []

    if not results:
        print("   [⚠] DDG: Nothing found (0 results)")
        return "", []

    lines = []
    for r in results:
        lines.append(
            f"Title: {r.get('title', '')}\nText: {r.get('body', '')}\nSource: {r.get('href', '')}"
        )
    out = "\n\n".join(lines)

    print("   [✓ Search result received]")
    _save_ddg_results_json(q, today_iso, results)
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("DDG полный блок для промпта (%s симв.):\n%s", len(out), out[:12000])

    return out, results


def fetch_web_snippets(
    query: str,
    *,
    today_iso: Optional[str] = None,
    force: bool = False,
) -> str:
    """
    Короткая выдача для промпта. Требует пакет ddgs (duckduckgo-search).

    force=True — вызвать при слабом RAG даже если ENABLE_WEB_FALLBACK=false
    (чтобы не оставлять пользователя без внешних сниппетов).
    """
    text, _ = fetch_web_snippets_pair(query, today_iso=today_iso, force=force)
    return text
