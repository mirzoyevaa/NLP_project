"""
Сохранение артефактов веб-поиска в ``data/raw/`` (как для ``load_sources.py``).

Используется:
- ``travel_web_agent`` — полный дамп ``sources_travel_*.json``;
- ``bot_consul`` — DDG и сабагенты из оркестратора.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent


def data_raw_dir() -> Path:
    """``storage_zone/data/raw`` — создаётся при необходимости."""
    d = _ROOT / "data" / "raw"
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_curated_dir() -> Path:
    """``storage_zone/data/curated`` — отфильтрованные веб-источники и т.п."""
    d = _ROOT / "data" / "curated"
    d.mkdir(parents=True, exist_ok=True)
    return d


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def ddg_results_for_artifact(
    ddg_results: Optional[List[Any]],
    *,
    limit: int = 40,
    body_max: int = 1500,
) -> List[Dict[str, Any]]:
    """
    Укороченное представление сырого DDG для JSON-артефакта (без раздувания файла).
    """
    if not ddg_results:
        return []
    out: List[Dict[str, Any]] = []
    for r in ddg_results[:limit]:
        if isinstance(r, dict):
            body = r.get("body") or r.get("snippet") or r.get("excerpt") or ""
            if isinstance(body, str) and len(body) > body_max:
                body = body[: body_max - 3] + "..."
            out.append(
                {
                    "title": (r.get("title") or "")[:500],
                    "url": (r.get("url") or "")[:2000],
                    "body": body,
                }
            )
        else:
            out.append({"repr": str(r)[:800]})
    return out


def save_json_artifact(data: Dict[str, Any], *, filename: str) -> Optional[str]:
    """
    Пишет JSON в ``data/raw/<filename>``.
    В консоль — в том же духе, что и в ``travel_web_agent`` (print).
    """
    try:
        path = data_raw_dir() / filename
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"   [✓] JSON saved: {path}", flush=True)
        return str(path)
    except Exception as e:
        print(f"   [warn] Failed to save JSON artifact: {e}")
        return None
