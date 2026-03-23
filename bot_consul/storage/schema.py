"""
Схема документа (чанка) — единственное определение модели данных.

Используется:
  - Парсингом: создание чанков из сырых текстов
  - Хранилищем: сохранение и поиск в Qdrant
  - RAG-зоной: чтение при формировании контекста

По ТЗ §3.1 метаданные каждого документа:
  - страна, тип визы      → фильтрация при поиске
  - тип источника         → official | review | channel
  - дата                  → staleness check, версионирование
  - URL                   → показывается пользователю как ссылка на источник
  - content_hash          → дедупликация по содержимому (§3.3)
  - version               → версионирование документов по дате (§3.2)
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Optional


<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
SOURCE_TYPES = ("official", "review", "channel", "ddg")
=======
SOURCE_TYPES = ("official", "review", "channel")
>>>>>>> b066bb1 (main_pipline)
=======
SOURCE_TYPES = ("official", "review", "channel")
>>>>>>> b066bb1 (main_pipline)
=======
SOURCE_TYPES = ("official", "review", "channel", "ddg")
>>>>>>> bffe1d0 (storage: fix deduplication, config, and consistency issues - schema: add australia, south_africa to COUNTRY_CODES - schema: add page_content, dataset, passport_country, purpose, destination_raw fields to Chunk - config: add SEARCH_SCORE_THRESHOLD_OFFICIAL field - embedder: fix HuggingFaceEmbeddings initialization - store: replace hardcoded score_threshold=0.35 with settings - quality: switch duplicate detection from content_hash to URL - web_source_catalog: fix chunk ID generation with enumerate - __init__: remove unused exports - add .gitignore)

COUNTRY_CODES = (
    "germany", "france", "spain", "czechia", "italy",
    "thailand", "uae", "uk", "usa", "serbia", "georgia",
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
    "turkey", "australia", "south_africa", "general",
=======
    "turkey", "general",
>>>>>>> b066bb1 (main_pipline)
=======
    "turkey", "general",
>>>>>>> b066bb1 (main_pipline)
=======
    "turkey", "australia", "south_africa", "general",
>>>>>>> bffe1d0 (storage: fix deduplication, config, and consistency issues - schema: add australia, south_africa to COUNTRY_CODES - schema: add page_content, dataset, passport_country, purpose, destination_raw fields to Chunk - config: add SEARCH_SCORE_THRESHOLD_OFFICIAL field - embedder: fix HuggingFaceEmbeddings initialization - store: replace hardcoded score_threshold=0.35 with settings - quality: switch duplicate detection from content_hash to URL - web_source_catalog: fix chunk ID generation with enumerate - __init__: remove unused exports - add .gitignore)
)

VISA_TYPE_CODES = (
    "tourist", "business", "student", "work", "transit", "general",
)


def make_chunk_id(url: str, index: int) -> str:
    """
    Стабильный ID точки Qdrant. Раньше использовалось int от 16 hex-цифр — большие числа
    ломали JSON/валидацию ID в Qdrant Cloud («not a valid point ID»). UUID из MD5 — безопасно.
    """
    h = hashlib.md5(f"{url}:{index}".encode()).hexdigest()
    return str(uuid.UUID(h))


def make_content_hash(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:32]


@dataclass
class Chunk:
    """
    Единица хранения знаний.

    Жизненный цикл:
      1. Парсер создаёт Chunk из сырого текста
      2. Препроцессор нормализует и сегментирует
      3. QdrantStore векторизует и сохраняет в Qdrant
      4. RawStorage сохраняет оригинал на диск
      5. Retriever возвращает Chunk с заполненным score при поиске
    """

    id: str
    text: str
    country: str
    visa_type: str
    source_type: str
    url: str
    date: str


    content_hash: str = field(default="")
    version: int = field(default=1)

    score: float = field(default=0.0)

    # Расширенный payload для Qdrant Cloud (веб-ингест, совместимость с экспортом JSON)
    page_content: str = ""
    dataset: str = ""
    passport_country: str = ""
    purpose: str = ""
    destination_raw: str = ""

    def __post_init__(self) -> None:
        if not self.content_hash:
            self.content_hash = make_content_hash(self.text)
        if not (self.page_content or "").strip():
            self.page_content = self.text

    def is_stale(self, max_days: int = 180) -> bool:
        """True если источник старше max_days дней."""
        try:
            return (date.today() - date.fromisoformat(self.date)).days > max_days
        except ValueError:
            return False

    def days_old(self) -> Optional[int]:
        """Возраст источника в днях. None если дата невалидна."""
        try:
            return (date.today() - date.fromisoformat(self.date)).days
        except ValueError:
            return None

    def next_version(self, new_text: str, new_date: str) -> "Chunk":
        """
        Создаёт новую версию чанка с обновлённым текстом.
        ID не меняется — это тот же документ, новая версия.
        """
        return Chunk(
            id=self.id,
            text=new_text,
            country=self.country,
            visa_type=self.visa_type,
            source_type=self.source_type,
            url=self.url,
            date=new_date,
            version=self.version + 1,
            page_content=new_text,
            dataset=self.dataset,
            passport_country=self.passport_country,
            purpose=self.purpose,
            destination_raw=self.destination_raw,
        )

    def to_payload(self) -> Dict[str, Any]:
        """
        Словарь для хранения в Qdrant payload.
        id и score не включаются: id — отдельное поле Qdrant, score — вычисляется.

        Формат согласован с облачным экспортом: text + page_content + профиль + dataset + json:// URL для summary.
        """
        pc = (self.page_content or "").strip() or self.text
        return {
            "text": self.text,
            "page_content": pc,
            "country": self.country,
            "visa_type": self.visa_type,
            "source_type": self.source_type,
            "url": self.url,
            "date": self.date,
            "dataset": self.dataset,
            "passport_country": self.passport_country,
            "purpose": self.purpose,
            "destination_raw": self.destination_raw,
            "content_hash": self.content_hash,
            "version": self.version,
        }

    @classmethod
    def from_payload(
        cls,
        chunk_id: str,
        payload: dict,
        score: float = 0.0,
    ) -> "Chunk":
        """Восстановление Chunk из payload Qdrant."""
        text = payload.get("text", "")
        pc = payload.get("page_content") or text
        return cls(
            id=chunk_id,
            text=text,
            country=payload.get("country", ""),
            visa_type=payload.get("visa_type", ""),
            source_type=payload.get("source_type", "unknown"),
            url=payload.get("url", ""),
            date=payload.get("date", ""),
            content_hash=payload.get("content_hash", ""),
            version=payload.get("version", 1),
            score=score,
            page_content=pc,
            dataset=payload.get("dataset", "") or "",
            passport_country=payload.get("passport_country", "") or "",
            purpose=payload.get("purpose", "") or "",
            destination_raw=payload.get("destination_raw", "") or "",
        )

    def __repr__(self) -> str:
        return (
            f"Chunk(id={self.id!r}, country={self.country!r}, "
            f"visa_type={self.visa_type!r}, source_type={self.source_type!r}, "
            f"date={self.date!r}, version={self.version}, score={self.score:.3f})"
        )
