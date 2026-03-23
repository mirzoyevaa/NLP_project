"""Поиск в Qdrant и оценка достаточности контекста (отдельно от веб-need_more в agent_openrouter)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from storage.schema import Chunk

from bot_consul.config import orchestrator_settings

logger = logging.getLogger(__name__)


@dataclass
class RAGBundle:
    """Собранный контекст из базы знаний."""

    chunks: List[Chunk] = field(default_factory=list)
    by_source: dict = field(default_factory=dict)  # official | review | channel -> list[Chunk]

    def texts_for_prompt(self, max_chars: int = 12000) -> str:
        parts: List[str] = []
        for st in ("official", "review", "channel"):
            items = self.by_source.get(st) or []
            if not items:
                continue
            label = {
                "official": "Официальные источники",
                "review": "Отзывы",
                "channel": "Telegram и каналы",
            }[st]
            block = [f"### {label}"]
            for c in items:
                url = getattr(c, "url", "") or ""
                block.append(f"- URL: {url}\n  {getattr(c, 'text', '')[:4000]}")
            parts.append("\n".join(block))
        text = "\n\n".join(parts)
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 20] + "\n...(обрезано)..."


class RAGService:
    """Обёртка над QdrantStore с разбиением по типам источников."""

    def __init__(self, store: Optional["QdrantStore"] = None):
        self._store = store

    @property
    def store(self):
        """Ленивый импорт — unit-тесты могут подставить мок без qdrant_client."""
        if self._store is None:
            from storage.store import QdrantStore

            self._store = QdrantStore()
        return self._store

    def retrieve(
        self,
        query: str,
        *,
        country: Optional[str] = None,
        visa_type: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> RAGBundle:
        k = top_k or orchestrator_settings.RAG_TOP_K
        bundle = RAGBundle()

        # Один общий поиск с фильтрами
        chunks = self.store.search(
            query=query,
            country=country,
            visa_type=visa_type,
            top_k=k,
        )

        # Если пусто и указана страна — пробуем без фильтра страны (широкий поиск)
        if not chunks and country:
            logger.info("RAG: повтор без фильтра country")
            chunks = self.store.search(query=query, country=None, visa_type=visa_type, top_k=k)

        if not chunks and visa_type:
            chunks = self.store.search(query=query, country=country, visa_type=None, top_k=k)

        bundle.chunks = chunks
        for c in chunks:
            st = getattr(c, "source_type", None) or "review"
            bundle.by_source.setdefault(st, []).append(c)

        return bundle


def assess_rag_sufficiency(
    bundle: RAGBundle,
    *,
    country_code: str | None = None,
) -> tuple[bool, str]:
    """
    Эвристика «достаточно ли базы».

    Если задан country_code (нормализованный код из профиля), хотя бы один чанк
    должен иметь payload country == этому коду. Иначе считаем, что после
    «широкого» поиска без фильтра подтянулись чужие страны — нужен веб-fallback.
    """
    cfg = orchestrator_settings
    chunks = bundle.chunks

    if len(chunks) < cfg.RAG_MIN_CHUNKS:
        return False, f"chunks<{cfg.RAG_MIN_CHUNKS}"

    if country_code:
        cc = country_code.strip().lower()
        if cc and not any(
            (getattr(c, "country", "") or "").strip().lower() == cc for c in chunks
        ):
            return False, f"no_chunk_for_country={cc}"

    scores = [getattr(c, "score", 0.0) or 0.0 for c in chunks if getattr(c, "score", None) is not None]
    if scores:
        avg = sum(scores) / len(scores)
        if avg < cfg.RAG_MIN_AVG_SCORE:
            return False, f"avg_score={avg:.3f}<{cfg.RAG_MIN_AVG_SCORE}"

    if cfg.RAG_REQUIRE_OFFICIAL:
        officials = bundle.by_source.get("official") or []
        if not officials:
            return False, "require_official_but_missing"

    return True, "ok"
