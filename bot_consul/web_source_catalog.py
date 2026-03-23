"""
Сбор веб-источников (travel_web + DDG), фильтр релевантности, сохранение в JSONL.

Релевантность:
  1) cosine между эмбеддингом якорного запроса (вопрос + профиль) и эмбеддингом текста источника;
  2) при ошибке эмбеддера — Jaccard по токенам (простой запасной путь).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from bot_consul.config import orchestrator_settings
from bot_consul.session import VisaProfile

logger = logging.getLogger(__name__)

_CURATED_FILENAME = "web_sources.jsonl"


@dataclass
class WebSourceRecord:
    """Один внешний источник для оценки и сохранения."""

    url: str
    title: str = ""
    excerpt: str = ""
    kind: str = ""  # official | reviews | practical | ddg

    def as_text_for_embedding(self, max_chars: int = 6000) -> str:
        parts = [self.url, self.title, self.excerpt]
        return "\n".join(p for p in parts if p)[:max_chars]


def _tokenize(s: str) -> set[str]:
    s = re.sub(r"[^\w\s]", " ", s.lower(), flags=re.UNICODE)
    return {w for w in s.split() if len(w) > 2}


def _keyword_relevance(anchor: str, doc: str) -> float:
    """Jaccard по токенам, значение [0, 1]."""
    a, b = _tokenize(anchor), _tokenize(doc)
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _cosine_dense(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def build_relevance_anchor(user_query: str, profile: VisaProfile) -> str:
    """Текст «запрос пользователя + профиль» для сравнения с источниками."""
    parts = [
        user_query.strip(),
        profile.destination or "",
        profile.country or "",
        profile.passport_country or "",
        profile.purpose or "",
        profile.visa_type or "",
    ]
    return " ".join(p for p in parts if p).strip()


def collect_from_travel_meta(materials: Optional[Dict[str, Any]]) -> List[WebSourceRecord]:
    """Извлекает страницы из official / reviews / practical.

    Если ``pages`` пуст (fetch не удался), но есть ``summary`` — добавляем синтетический URL,
    чтобы текст всё равно мог попасть в Qdrant (раньше в облаке не было записей при «только summary»).
    """
    if not materials:
        return []
    out: List[WebSourceRecord] = []
    for bucket in ("official", "reviews", "practical"):
        block = materials.get(bucket) or {}
        for page in block.get("pages") or []:
            url = (page.get("url") or "").strip()
            if not url:
                continue
            ex = (page.get("excerpt") or "")[:8000]
            out.append(WebSourceRecord(url=url, title="", excerpt=ex, kind=bucket))
        if not (block.get("pages") or []):
            summary = (block.get("summary") or "").strip()
            if len(summary) >= 80:
                h = hashlib.md5(f"{bucket}:{summary[:2000]}".encode()).hexdigest()[:16]
                pseudo_url = f"https://travel-web.internal/summary/{bucket}/{h}"
                out.append(
                    WebSourceRecord(
                        url=pseudo_url,
                        title=f"Travel web ({bucket})",
                        excerpt=summary[:8000],
                        kind=bucket,
                    )
                )
    return out


def collect_from_ddg(results: List[Dict[str, Any]]) -> List[WebSourceRecord]:
    out: List[WebSourceRecord] = []
    for r in results or []:
        href = (r.get("href") or r.get("url") or "").strip()
        if not href:
            continue
        out.append(
            WebSourceRecord(
                url=href,
                title=str(r.get("title") or "")[:500],
                excerpt=str(r.get("body") or "")[:8000],
                kind="ddg",
            )
        )
    return out


def dedupe_by_url(sources: List[WebSourceRecord]) -> List[WebSourceRecord]:
    seen: set[str] = set()
    uniq: List[WebSourceRecord] = []
    for s in sources:
        key = s.url.split("#")[0].rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(s)
    return uniq


def score_sources(
    anchor: str,
    sources: List[WebSourceRecord],
) -> List[Tuple[WebSourceRecord, float]]:
    """Возвращает пары (источник, score)."""
    if not sources:
        return []
    texts = [s.as_text_for_embedding() for s in sources]
    try:
        from storage.embedder import embed_batch, embed_text

        qv = embed_text(anchor[:8000])
        mat = embed_batch(texts)
        scores = [_cosine_dense(qv, row) for row in mat]
    except Exception as e:
        logger.warning("embedder unavailable for relevance (%s) — keyword fallback", e)
        scores = [_keyword_relevance(anchor, t) for t in texts]
    return list(zip(sources, scores))


def filter_by_relevance(
    anchor: str,
    sources: List[WebSourceRecord],
    *,
    threshold: Optional[float] = None,
    max_items: Optional[int] = None,
) -> List[Tuple[WebSourceRecord, float]]:
    """Оставляет источники с score >= threshold, сортирует по убыванию score."""
    th = threshold if threshold is not None else orchestrator_settings.WEB_SOURCE_MIN_RELEVANCE
    cap = max_items if max_items is not None else orchestrator_settings.WEB_SOURCE_MAX_PER_TURN
    scored = score_sources(anchor, sources)
    passed = [(s, sc) for s, sc in scored if sc >= th]
    passed.sort(key=lambda x: -x[1])
    return passed[:cap]


def _kind_to_source_type(kind: str) -> str:
    """Маппинг сабагентов/DDG → payload source_type в Qdrant."""
    return {
        "official": "official",
        "reviews": "review",
        "practical": "channel",
<<<<<<< HEAD
        "ddg": "ddg",
=======
        "ddg": "channel",
>>>>>>> b066bb1 (main_pipline)
    }.get((kind or "").lower(), "channel")


def build_cloud_dataset_key(profile: VisaProfile, *, date_iso: str) -> str:
    """
    Ключ датасета для payload Qdrant: ``{country}_{passport}_{YYYY-MM-DD}``
    (пример: ``australia_Казахстан_2026-03-19``).
    """
    from datetime import date

    c = (profile.country or "general").strip().lower().replace(" ", "_") or "general"
    pc = (profile.passport_country or "unknown").strip() or "unknown"
    pc = re.sub(r"\s+", "_", pc)
    d = (date_iso or "").strip()[:10]
    if len(d) < 10:
        d = date.today().isoformat()
    return f"{c}_{pc}_{d}"


def _qdrant_canonical_url(kind: str, src_url: str) -> str:
    """
    Сводит синтетические URL summary к виду ``json://official_material.summary`` и т.п.
    Обычные http(s) оставляет как есть (обрезка по лимиту Telegram/Qdrant).
    """
    u = (src_url or "").strip()
    if "travel-web.internal/summary/official/" in u:
        return "json://official_material.summary"
    if "travel-web.internal/summary/reviews/" in u:
        return "json://reviews_material.summary"
    if "travel-web.internal/summary/practical/" in u:
        return "json://practical_material.summary"
    if u.lower().startswith("json://"):
        return u[:2048]
    # DDG / веб: явный json-идентификатор сниппета (если когда-либо без URL)
    if not u or u == "#":
        k = (kind or "ddg").lower()
        if k == "ddg":
            return "json://ddg_web.snippet"
        return f"json://{k}_material.summary"
    return u[:2048]


def upsert_filtered_sources_to_qdrant(
    profile: VisaProfile,
    filtered: List[Tuple[WebSourceRecord, float]],
    *,
    today_iso: Optional[str] = None,
) -> Optional[Dict[str, int]]:
    """
    Записывает отфильтрованные веб-источники в Qdrant как Chunk (дедуп по content_hash в store.upsert).

    Payload в облаке: text, page_content, country, visa_type, source_type, url (в т.ч. json://…),
    date, dataset, passport_country, purpose, destination_raw.
    """
    if not orchestrator_settings.WEB_SOURCE_QDRANT_UPSERT or not filtered:
        return None
    from datetime import date

    from bot_consul.profile import normalize_visa_type_for_store
    from storage.schema import Chunk, make_chunk_id
    from storage.store import QdrantStore

    country = (profile.country or "").strip() or "general"
    vt = normalize_visa_type_for_store(profile.visa_type) or "general"
    day = (today_iso or "").strip()[:10]
    if len(day) < 10:
        day = date.today().isoformat()
    dataset = build_cloud_dataset_key(profile, date_iso=day)
    passport = (profile.passport_country or "").strip()
    purpose = (profile.purpose or "").strip()
    dest_raw = (profile.destination or "").strip()

    chunks: List[Chunk] = []
<<<<<<< HEAD
    for i, (src, _sc) in enumerate(filtered):
=======
    for src, _sc in filtered:
>>>>>>> b066bb1 (main_pipline)
        body = "\n\n".join(p for p in (src.title, src.excerpt) if p).strip()
        if not body:
            body = src.url
        if len(body) > 12000:
            body = body[:12000]
        canon_url = _qdrant_canonical_url(src.kind, src.url)
        chunks.append(
            Chunk(
<<<<<<< HEAD
                id=make_chunk_id(src.url, i),
=======
                id=make_chunk_id(src.url, 0),
>>>>>>> b066bb1 (main_pipline)
                text=body,
                country=country,
                visa_type=vt,
                source_type=_kind_to_source_type(src.kind),
                url=canon_url,
                date=day,
                page_content=body,
                dataset=dataset,
                passport_country=passport,
                purpose=purpose,
                destination_raw=dest_raw,
            )
        )

    try:
        store = QdrantStore()
        stats = store.upsert(chunks)
        logger.info("web_source_catalog: Qdrant upsert %s", stats)
        return stats
    except Exception as e:
        logger.warning("web_source_catalog: Qdrant upsert failed: %s", e)
        return None


def append_curated_jsonl(
    path: Path,
    rows: List[Dict[str, Any]],
) -> Optional[str]:
    """Дописывает строки JSON в файл. Возвращает путь или None."""
    if not rows:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        logger.info("web_source_catalog: сохранено %s записей в %s", len(rows), path)
        return str(path)
    except Exception as e:
        logger.warning("web_source_catalog: не удалось записать %s: %s", path, e)
        return None


def persist_filtered_sources(
    *,
    session_id: str,
    user_query: str,
    profile: VisaProfile,
    travel_meta: Optional[Dict[str, Any]],
    ddg_results: List[Dict[str, Any]],
    today_iso: str = "",
) -> Optional[str]:
    """
    Собирает источники, фильтрует по релевантности, дописывает в data/curated/web_sources.jsonl.

    session_id — обезличенный id (например tg:123); для логов храним только hash.
    """
    if not orchestrator_settings.WEB_SOURCE_STORE_ENABLED and not orchestrator_settings.WEB_SOURCE_QDRANT_UPSERT:
        return None

    raw: List[WebSourceRecord] = []
    raw.extend(collect_from_travel_meta(travel_meta))
    raw.extend(collect_from_ddg(ddg_results))
    raw = dedupe_by_url(raw)
    if not raw:
        return None

    anchor = build_relevance_anchor(user_query, profile)
    filtered = filter_by_relevance(anchor, raw)
    if not filtered and raw:
        # Порог мог отсечь всё (низкое пересечение с якорём), хотя источники есть — иначе Qdrant Cloud пустой.
        scored = score_sources(anchor, raw)
        scored.sort(key=lambda x: -x[1])
        n = max(1, orchestrator_settings.WEB_SOURCE_QDRANT_FALLBACK_TOP_N)
        filtered = scored[: min(n, len(scored))]
        logger.info(
            "web_source_catalog: порог %.3f отсёк все источники — fallback в Qdrant: топ-%s по score (лучший score=%.4f)",
            orchestrator_settings.WEB_SOURCE_MIN_RELEVANCE,
            len(filtered),
            filtered[0][1] if filtered else 0.0,
        )
    if not filtered:
        logger.info(
            "web_source_catalog: нет источников для Qdrant (пустой travel+DDG). anchor len=%s",
            len(anchor),
        )
        return None

    sid_hash = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    from search_artifacts import data_curated_dir

    curated = data_curated_dir()

    path = curated / _CURATED_FILENAME

    upsert_filtered_sources_to_qdrant(
        profile,
        filtered,
        today_iso=today_iso or None,
    )

    out_path: Optional[str] = None
    if orchestrator_settings.WEB_SOURCE_STORE_ENABLED:
        rows: List[Dict[str, Any]] = []
        for src, sc in filtered:
            rows.append(
                {
                    "saved_at_utc": ts,
                    "session_id_hash": sid_hash,
                    "user_query": user_query[:2000],
                    "destination": profile.destination,
                    "passport_country": profile.passport_country,
                    "purpose": profile.purpose,
                    "country_code": profile.country,
                    "kind": src.kind,
                    "url": src.url,
                    "title": src.title,
                    "excerpt": src.excerpt[:4000],
                    "relevance_score": round(float(sc), 4),
                }
            )
        out_path = append_curated_jsonl(path, rows)

    return out_path
