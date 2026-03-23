"""
Объектное хранилище сырых текстов (§2.2 design-doc).

По ТЗ: «Хранение: объектное хранилище для сырых текстов, векторная БД для индекса»

Назначение:
  - Аудит: что именно попало в индекс (до и после предобработки)
  - Переиндексация без повторного парсинга (load_all → upsert)
  - Отладка: сравнение raw текста с чанками в Qdrant

Структура файловой системы:
  data/raw/
    official/
      germany_tourist_<id>.json   ← один файл = один чанк
    review/
      germany_tourist_<id>.json
    channel/
      general_general_<id>.json

В production объектное хранилище заменяется на S3/MinIO.
Интерфейс класса остаётся тем же — только меняется реализация _path() и I/O.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from storage.config import settings
from storage.schema import Chunk

logger = logging.getLogger(__name__)


class RawStorage:
    """
    Файловое хранилище оригинальных текстов.
    Один Chunk → один JSON-файл.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(settings.RAW_STORAGE_PATH)
        self.root.mkdir(parents=True, exist_ok=True)


    def _path(self, chunk: Chunk) -> Path:
        """data/raw/<source_type>/<country>_<visa_type>_<id>.json"""
        subdir = self.root / chunk.source_type
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir / f"{chunk.country}_{chunk.visa_type}_{chunk.id}.json"


    def save(self, chunk: Chunk) -> Path:
        """Сохраняет чанк в JSON. Перезаписывает если файл уже есть."""
        path = self._path(chunk)
        data = {
            **chunk.to_payload(),
            "id":       chunk.id,
            "saved_at": datetime.now().isoformat(),
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def save_batch(self, chunks: list[Chunk]) -> int:
        """Сохраняет батч. Возвращает число успешно сохранённых."""
        saved = 0
        for chunk in chunks:
            try:
                self.save(chunk)
                saved += 1
            except Exception as e:
                logger.warning("Failed to save chunk %s: %s", chunk.id, e)
        return saved


    def load(self, chunk_id: str) -> Optional[Chunk]:
        """Загружает чанк по id. Ищет по всем поддиректориям."""
        for path in self.root.rglob(f"*_{chunk_id}.json"):
            try:
                return self._parse_file(path)
            except Exception as e:
                logger.warning("Cannot parse %s: %s", path, e)
        return None

    def load_all(
        self,
        source_type: str | None = None,
        country: str | None = None,
        visa_type: str | None = None,
    ) -> list[Chunk]:
        """
        Загружает все чанки из хранилища.
        Используется при переиндексации без повторного парсинга.

        Фильтры:
            source_type — если указан, ищем только в data/raw/<source_type>/
            country     — фильтр по стране
            visa_type   — фильтр по типу визы
        """
        search_root = self.root / source_type if source_type else self.root
        chunks: list[Chunk] = []

        for path in sorted(search_root.rglob("*.json")):
            try:
                chunk = self._parse_file(path)
                if country and chunk.country != country:
                    continue
                if visa_type and chunk.visa_type != visa_type:
                    continue
                chunks.append(chunk)
            except Exception as e:
                logger.warning("Cannot parse %s: %s", path, e)

        logger.info(
            "Loaded %d raw chunks (source_type=%s, country=%s, visa_type=%s)",
            len(chunks),
            source_type or "any",
            country or "any",
            visa_type or "any",
        )
        return chunks

    def _parse_file(self, path: Path) -> Chunk:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Chunk(
            id=data["id"],
            text=data["text"],
            country=data["country"],
            visa_type=data["visa_type"],
            source_type=data["source_type"],
            url=data["url"],
            date=data["date"],
            content_hash=data.get("content_hash", ""),
            version=data.get("version", 1),
        )


    def stats(self) -> dict:
        from collections import defaultdict
        counts: dict[str, int] = defaultdict(int)
        total = 0
        for path in self.root.rglob("*.json"):
            counts[path.parent.name] += 1
            total += 1
        return {"total": total, "by_source_type": dict(counts)}

    def exists(self, chunk_id: str) -> bool:
        return any(True for _ in self.root.rglob(f"*_{chunk_id}.json"))
