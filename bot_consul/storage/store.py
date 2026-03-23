"""
QdrantStore — единственная точка доступа к векторной БД.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointIdsList,
    PointStruct,
    VectorParams,
)

from storage.config import settings
from storage.embedder import VECTOR_SIZE, embed_batch, embed_text
from storage.schema import Chunk

logger = logging.getLogger(__name__)

_SCROLL_BATCH = 100
_UPSERT_BATCH = 64


class QdrantStore:
    """
    CRUD-обёртка над Qdrant.
    Все операции с векторной БД — только через этот класс.
    """

    def __init__(self, url=None, api_key=None, collection=None):
        qdrant_url = url or settings.QDRANT_URL
        qdrant_api_key = api_key or settings.QDRANT_API_KEY

        # было: raise RuntimeError если нет ключа
        # стало: подключаемся без ключа для localhost
        if qdrant_api_key:
            self.client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        else:
            self.client = QdrantClient(url=qdrant_url)

        self.collection = collection or settings.QDRANT_COLLECTION
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        """Создаёт коллекцию и payload-индексы, если их нет."""
        existing = {c.name for c in self.client.get_collections().collections}

        if self.collection not in existing:
            logger.info(
                "Creating Qdrant collection '%s' (dim=%d, metric=cosine)",
                self.collection,
                VECTOR_SIZE,
            )
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(
                    size=VECTOR_SIZE,
                    distance=Distance.COSINE,
                ),
            )

        self._ensure_payload_indexes()

    def _ensure_payload_indexes(self) -> None:
        """
        Создаёт индексы для полей, по которым фильтруем.
        В Qdrant Cloud фильтрация по keyword без индекса часто падает.
        """
        keyword_fields = [
            "country",
            "visa_type",
            "source_type",
            "dataset",
            "seed_name",
            "passport_country",
            "purpose",
            "destination_raw",
        ]

        for field in keyword_fields:
            try:
                self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception as e:
                logger.debug("Payload index for %s skipped: %s", field, e)

    def upsert(self, chunks: List[Chunk]) -> Dict[str, int]:
        """
        Добавляет/обновляет чанки с дедупликацией и версионированием.
        """
        if not chunks:
            return {"inserted": 0, "updated": 0, "skipped": 0}

        total_stats = {"inserted": 0, "updated": 0, "skipped": 0}

        for i in range(0, len(chunks), _UPSERT_BATCH):
            batch = chunks[i:i + _UPSERT_BATCH]
            to_write = self._classify(batch, total_stats)
            if not to_write:
                continue

            vectors = embed_batch([c.text for c in to_write])
            points = [
                PointStruct(id=c.id, vector=v, payload=c.to_payload())
                for c, v in zip(to_write, vectors)
            ]
            self.client.upsert(
                collection_name=self.collection,
                points=points,
                wait=True,
            )

        logger.info(
            "upsert done — inserted=%d updated=%d skipped=%d",
            total_stats["inserted"],
            total_stats["updated"],
            total_stats["skipped"],
        )
        return total_stats

    def _classify(
        self,
        chunks: List[Chunk],
        stats: Dict[str, int],
    ) -> List[Chunk]:
        """
        Классифицирует чанки в батче: insert / update / skip.
        """
        ids = [c.id for c in chunks]
        existing = {}

        try:
            records = self.client.retrieve(
                collection_name=self.collection,
                ids=ids,
                with_payload=True,
                with_vectors=False,
            )
            for rec in records:
                existing[str(rec.id)] = rec.payload or {}
        except Exception as e:
            logger.warning("retrieve() failed, will insert all: %s", e)

        to_write = []

        for chunk in chunks:
            prev = existing.get(str(chunk.id))
            if prev is None:
                stats["inserted"] += 1
                to_write.append(chunk)
            elif prev.get("url") == chunk.url:
                stats["skipped"] += 1
            else:
                stats["updated"] += 1
                to_write.append(
                    Chunk(
                        id=chunk.id,
                        text=chunk.text,
                        country=chunk.country,
                        visa_type=chunk.visa_type,
                        source_type=chunk.source_type,
                        url=chunk.url,
                        date=chunk.date,
                        version=int(prev.get("version", 1)) + 1,
                        page_content=chunk.page_content,
                        dataset=chunk.dataset,
                        passport_country=chunk.passport_country,
                        purpose=chunk.purpose,
                        destination_raw=chunk.destination_raw,
                    )
                )

        return to_write

    def upsert_incremental(self, chunks: List[Chunk]) -> Dict[str, int]:
        return self.upsert(chunks)

    def rebuild(self, chunks: List[Chunk]) -> Dict[str, int]:
        logger.warning("FULL REBUILD: dropping collection '%s'", self.collection)
        self.client.delete_collection(self.collection)
        self._ensure_collection()
        stats = self.upsert(chunks)
        logger.info("Rebuild complete: %s", stats)
        return stats

    def search(
        self,
        query: str,
        country: Optional[str] = None,
        visa_type: Optional[str] = None,
        source_types: Optional[List[str]] = None,
        top_k: Optional[int] = None,
        score_threshold: Optional[float] = None,
        dataset: Optional[str] = None,
        seed_name: Optional[str] = None,
    ) -> List[Chunk]:
        """
        Семантический поиск + фильтры по метаданным.
        """
        vector = embed_text(query)
        must = []

        if country:
            must.append(
                FieldCondition(
                    key="country",
                    match=MatchValue(value=country),
                )
            )

        if visa_type:
            must.append(
                FieldCondition(
                    key="visa_type",
                    match=MatchValue(value=visa_type),
                )
            )

        if source_types:
            must.append(
                FieldCondition(
                    key="source_type",
                    match=MatchAny(any=source_types),
                )
            )

        if dataset:
            must.append(
                FieldCondition(
                    key="dataset",
                    match=MatchValue(value=dataset),
                )
            )

        if seed_name:
            must.append(
                FieldCondition(
                    key="seed_name",
                    match=MatchValue(value=seed_name),
                )
            )

        results = self.client.search(
            collection_name=self.collection,
            query_vector=vector,
            query_filter=Filter(must=must) if must else None,
            limit=top_k if top_k is not None else settings.SEARCH_TOP_K,
            score_threshold=(
                score_threshold
                if score_threshold is not None
                else settings.SEARCH_SCORE_THRESHOLD
            ),
            with_payload=True,
            with_vectors=False,
        )

        return [
            Chunk.from_payload(str(r.id), r.payload or {}, score=r.score)
            for r in results
        ]

    def search_official_only(
        self,
        query: str,
        country: Optional[str] = None,
        top_k: int = 3,
    ) -> List[Chunk]:
        return self.search(
            query=query,
            country=country,
            source_types=["official"],
            top_k=top_k,
            score_threshold=settings.SEARCH_SCORE_THRESHOLD_OFFICIAL
        )

    def search_by_source_type(
        self,
        query: str,
        source_type: str,
        country: Optional[str] = None,
        top_k: int = 3,
    ) -> List[Chunk]:
        return self.search(
            query=query,
            country=country,
            source_types=[source_type],
            top_k=top_k,
            score_threshold=settings.SEARCH_SCORE_THRESHOLD_OFFICIAL
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Удаление
    # ══════════════════════════════════════════════════════════════════════════

    def delete_stale(self, max_days: Optional[int] = None) -> int:
        max_days = max_days or settings.STALENESS_DAYS
        cutoff = (date.today() - timedelta(days=max_days)).isoformat()

        stale_ids = []
        offset = None

        while True:
            records, next_offset = self.client.scroll(
                collection_name=self.collection,
                limit=_SCROLL_BATCH,
                offset=offset,
                with_payload=["date"],
                with_vectors=False,
            )

            for rec in records:
                rec_date = (rec.payload or {}).get("date")
                if rec_date and rec_date < cutoff:
                    stale_ids.append(rec.id)

            if next_offset is None:
                break
            offset = next_offset

        if stale_ids:
            for i in range(0, len(stale_ids), _SCROLL_BATCH):
                self.client.delete(
                    collection_name=self.collection,
                    points_selector=PointIdsList(
                        points=stale_ids[i:i + _SCROLL_BATCH]
                    ),
                    wait=True,
                )

        logger.info(
            "Deleted %d stale chunks (cutoff=%s, max_days=%d)",
            len(stale_ids),
            cutoff,
            max_days,
        )
        return len(stale_ids)

    def delete_by_url(self, url: str) -> int:
        ids_to_delete = []
        offset = None

        while True:
            records, next_offset = self.client.scroll(
                collection_name=self.collection,
                limit=_SCROLL_BATCH,
                offset=offset,
                with_payload=["url"],
                with_vectors=False,
            )

            for rec in records:
                if (rec.payload or {}).get("url") == url:
                    ids_to_delete.append(rec.id)

            if next_offset is None:
                break
            offset = next_offset

        if ids_to_delete:
            self.client.delete(
                collection_name=self.collection,
                points_selector=PointIdsList(points=ids_to_delete),
                wait=True,
            )
            logger.info("Deleted %d chunks for url=%s", len(ids_to_delete), url)

        return len(ids_to_delete)

    def delete_by_dataset(self, dataset: str) -> int:
        ids_to_delete = []
        offset = None

        while True:
            records, next_offset = self.client.scroll(
                collection_name=self.collection,
                limit=_SCROLL_BATCH,
                offset=offset,
                with_payload=["dataset"],
                with_vectors=False,
            )

            for rec in records:
                if (rec.payload or {}).get("dataset") == dataset:
                    ids_to_delete.append(rec.id)

            if next_offset is None:
                break
            offset = next_offset

        if ids_to_delete:
            for i in range(0, len(ids_to_delete), _SCROLL_BATCH):
                self.client.delete(
                    collection_name=self.collection,
                    points_selector=PointIdsList(
                        points=ids_to_delete[i:i + _SCROLL_BATCH]
                    ),
                    wait=True,
                )

        logger.info("Deleted %d chunks for dataset=%s", len(ids_to_delete), dataset)
        return len(ids_to_delete)

    # ══════════════════════════════════════════════════════════════════════════
    # Утилиты и статистика
    # ══════════════════════════════════════════════════════════════════════════

    def count(self) -> int:
        result = self.client.count(
            collection_name=self.collection,
            exact=True,
        )
        return int(result.count)

    def get_by_id(self, chunk_id: str) -> Optional[Chunk]:
        records = self.client.retrieve(
            collection_name=self.collection,
            ids=[chunk_id],
            with_payload=True,
            with_vectors=False,
        )
        if not records:
            return None

        rec = records[0]
        return Chunk.from_payload(str(rec.id), rec.payload or {})

    def coverage_stats(self) -> Dict:
        from collections import defaultdict

        cutoff = (date.today() - timedelta(days=settings.STALENESS_DAYS)).isoformat()
        country_sources = defaultdict(set)
        source_counts = defaultdict(int)
        dataset_counts = defaultdict(int)

        total = 0
        stale = 0
        offset = None

        while True:
            records, next_offset = self.client.scroll(
                collection_name=self.collection,
                limit=_SCROLL_BATCH,
                offset=offset,
                with_payload=["country", "source_type", "date", "dataset", "seed_name"],
                with_vectors=False,
            )

            for rec in records:
                p = rec.payload or {}
                total += 1

                country = p.get("country", "unknown")
                source_type = p.get("source_type", "unknown")
                dataset = p.get("dataset") or p.get("seed_name") or "unknown"

                country_sources[country].add(source_type)
                source_counts[source_type] += 1
                dataset_counts[dataset] += 1

                if p.get("date") and p.get("date") < cutoff:
                    stale += 1

            if next_offset is None:
                break
            offset = next_offset

        full_coverage = [
            c for c, types in country_sources.items()
            if {"official", "review", "channel"}.issubset(types)
        ]

        return {
            "total_chunks": total,
            "fresh_chunks": total - stale,
            "stale_chunks": stale,
            "stale_ratio": round(float(stale) / float(total), 3) if total else 0,
            "source_type_counts": dict(source_counts),
            "dataset_counts": dict(dataset_counts),
            "countries_full_coverage": sorted(full_coverage),
            "countries_with_data": sorted(country_sources.keys()),
            "coverage_score": round(
                float(len(full_coverage)) / float(max(len(country_sources), 1)),
                3,
            ),
        }

    def freshness_report(self) -> List[Dict]:
        cutoff = (date.today() - timedelta(days=settings.STALENESS_DAYS)).isoformat()
        url_info = {}
        offset = None

        while True:
            records, next_offset = self.client.scroll(
                collection_name=self.collection,
                limit=_SCROLL_BATCH,
                offset=offset,
                with_payload=[
                    "url",
                    "date",
                    "source_type",
                    "country",
                    "dataset",
                    "seed_name",
                ],
                with_vectors=False,
            )

            for rec in records:
                p = rec.payload or {}
                url = p.get("url", "")
                rec_date = p.get("date", "")

                if not url:
                    continue

                prev = url_info.get(url)
                if prev is None or rec_date > prev["last_updated"]:
                    url_info[url] = {
                        "url": url,
                        "last_updated": rec_date,
                        "source_type": p.get("source_type", ""),
                        "country": p.get("country", ""),
                        "dataset": p.get("dataset") or p.get("seed_name", ""),
                    }

            if next_offset is None:
                break
            offset = next_offset

        report = []
        for info in url_info.values():
            try:
                age = (date.today() - date.fromisoformat(info["last_updated"])).days
            except Exception:
                age = -1

            report.append(
                {
                    **info,
                    "age_days": age,
                    "is_stale": bool(info["last_updated"]) and info["last_updated"] < cutoff,
                }
            )

        return sorted(report, key=lambda x: x["age_days"], reverse=True)