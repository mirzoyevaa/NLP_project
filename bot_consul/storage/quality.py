"""
Метрики качества базы знаний (§3.3 design-doc).

По ТЗ три группы метрик:

  1. Покрытие (§3.3):
     «доля целевых направлений, по которым в базе присутствуют чанки
      всех трёх типов — официальные источники, отзывы/тг-каналы»

  2. Актуальность (§3.3):
     «частота обновления источников; время с момента изменения на сайте
      до появления в индексе. Нет источников старше, чем полгода.»

  3. Консистентность (§3.3):
     «дедупликация одинаковых фрагментов; выявление противоречий
      между источниками (официальные vs отзывы)»

Используется:
  - В скрипте scripts/knowledge_report.py
  - В Prometheus-метриках (через pipeline/metrics.py)
  - В ручном ревью командой данных
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage.store import QdrantStore

logger = logging.getLogger(__name__)

# Целевые страны — должны быть покрыты по ТЗ
TARGET_COUNTRIES = frozenset({
    "germany", "france", "spain", "czechia", "italy",
    "thailand", "uae", "uk", "usa", "serbia", "georgia", "turkey",
})

REQUIRED_SOURCE_TYPES = frozenset({"official", "review", "channel"})

_SCROLL_BATCH = 100


# ══════════════════════════════════════════════════════════════════════════════
# Отчёт о качестве
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CoverageReport:
    """
    Сводный отчёт о качестве базы знаний.
    Содержит данные по всем трём группам метрик ТЗ §3.3.
    """

    # ── Покрытие ──────────────────────────────────────────────────────────────
    total_chunks: int = 0
    # Страны с тремя типами источников (official + review + channel)
    countries_full: list[str] = field(default_factory=list)
    # Страны с 1–2 типами источников
    countries_partial: list[str] = field(default_factory=list)
    # Целевые страны без данных вообще
    countries_missing: list[str] = field(default_factory=list)
    # Доля целевых стран с полным покрытием (0.0–1.0)
    coverage_score: float = 0.0

    # ── Актуальность ──────────────────────────────────────────────────────────
    fresh_chunks: int = 0
    stale_chunks: int = 0
    stale_ratio: float = 0.0
    # Возраст самого старого источника в днях
    oldest_source_days: int = 0
    # URL источников, которым нужно обновление
    sources_needing_update: list[str] = field(default_factory=list)

    # ── Консистентность ───────────────────────────────────────────────────────
    # Число чанков с одинаковым content_hash (дубликаты)
    duplicate_hashes: int = 0
    source_type_counts: dict[str, int] = field(default_factory=dict)

    # ── Интерфейс ─────────────────────────────────────────────────────────────

    def is_healthy(self) -> bool:
        """
        Базовая проверка здоровья базы знаний.
        Критерии — из ТЗ §3.3:
          - coverage_score ≥ 0.5 (хотя бы половина целевых стран покрыта)
          - stale_ratio < 0.3 (менее 30% устаревших)
          - oldest_source_days ≤ 180 (нет источников старше полугода)
        """
        return (
            self.coverage_score >= 0.5
            and self.stale_ratio < 0.3
            and self.oldest_source_days <= 180
        )

    def as_dict(self) -> dict:
        return {
            "coverage": {
                "total_chunks":            self.total_chunks,
                "countries_full_coverage": self.countries_full,
                "countries_partial":       self.countries_partial,
                "countries_missing":       self.countries_missing,
                "coverage_score":          self.coverage_score,
            },
            "freshness": {
                "fresh_chunks":           self.fresh_chunks,
                "stale_chunks":           self.stale_chunks,
                "stale_ratio":            self.stale_ratio,
                "oldest_source_days":     self.oldest_source_days,
                "sources_needing_update": self.sources_needing_update,
            },
            "consistency": {
                "duplicate_hashes":  self.duplicate_hashes,
                "source_type_counts": self.source_type_counts,
            },
        }

    def summary(self) -> str:
        status = "✅ OK" if self.is_healthy() else "⚠️  NEEDS ATTENTION"
        missing = ", ".join(self.countries_missing) or "—"
        return (
            f"{status}\n"
            f"Чанков: {self.total_chunks}  "
            f"(устаревших: {self.stale_chunks}, {self.stale_ratio:.0%})\n"
            f"Покрытие: {len(self.countries_full)}/{len(TARGET_COUNTRIES)} "
            f"стран ({self.coverage_score:.0%})\n"
            f"Без данных: {missing}\n"
            f"Самый старый источник: {self.oldest_source_days} дней\n"
            f"Дубликаты content_hash: {self.duplicate_hashes}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Построение отчёта
# ══════════════════════════════════════════════════════════════════════════════

def build_coverage_report(
    store: "QdrantStore",
    staleness_days: int = 180,
) -> CoverageReport:
    """
    Строит полный отчёт за один scroll-проход по коллекции.
    Не делает дополнительных запросов к Qdrant.

    Аргументы:
        store          — экземпляр QdrantStore
        staleness_days — порог устаревания (по умолчанию 180 = полгода)
    """
    from collections import defaultdict

    cutoff = (date.today() - timedelta(days=staleness_days)).isoformat()

    country_sources: dict[str, set]  = defaultdict(set)
    source_counts:   dict[str, int]  = defaultdict(int)
    hash_count:      dict[str, int]  = defaultdict(int)
    url_dates:       dict[str, str]  = {}

    report = CoverageReport()
    oldest_days = 0

    # Один scroll-проход по всей коллекции
    offset = None
    while True:
        records, next_offset = store.client.scroll(
            collection_name=store.collection,
            limit=_SCROLL_BATCH,
            offset=offset,
            with_payload=["country", "source_type", "date", "url", "content_hash"],
            with_vectors=False,
        )
        for rec in records:
            p = rec.payload or {}
            country   = p.get("country", "unknown")
            src_type  = p.get("source_type", "unknown")
            rec_date  = p.get("date", "9999-01-01")
            url       = p.get("url", "")
            c_hash    = p.get("content_hash", "")

            report.total_chunks += 1
            country_sources[country].add(src_type)
            source_counts[src_type] += 1

            if c_hash:
                hash_count[c_hash] += 1

            if rec_date < cutoff:
                report.stale_chunks += 1

            # Возраст источника
            try:
                age = (date.today() - date.fromisoformat(rec_date)).days
                if age > oldest_days:
                    oldest_days = age
            except ValueError:
                pass

            # Свежесть по URL
            if url and (url not in url_dates or rec_date > url_dates[url]):
                url_dates[url] = rec_date

        if next_offset is None:
            break
        offset = next_offset

    # ── Покрытие ──────────────────────────────────────────────────────────────
    for country in TARGET_COUNTRIES:
        types = country_sources.get(country, set())
        if REQUIRED_SOURCE_TYPES.issubset(types):
            report.countries_full.append(country)
        elif types:
            report.countries_partial.append(country)
        else:
            report.countries_missing.append(country)

    report.coverage_score = round(
        len(report.countries_full) / len(TARGET_COUNTRIES), 3
    )

    # ── Актуальность ──────────────────────────────────────────────────────────
    report.fresh_chunks = report.total_chunks - report.stale_chunks
    report.stale_ratio = round(
        report.stale_chunks / report.total_chunks, 3
    ) if report.total_chunks else 0.0
    report.oldest_source_days = oldest_days
    report.sources_needing_update = [
        url for url, last_date in url_dates.items()
        if last_date < cutoff
    ]

    # ── Консистентность ───────────────────────────────────────────────────────
    report.duplicate_hashes = sum(cnt - 1 for cnt in hash_count.values() if cnt > 1)
    report.source_type_counts = dict(source_counts)

    return report
