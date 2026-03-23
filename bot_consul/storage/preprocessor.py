"""
Предобработка текста перед индексацией.

По ТЗ §3.1:
  - Нормализация текста (язык, кодировка)
  - Сегментация на смысловые блоки для индексации
  - Удаление навигации, рекламы, мусора (для HTML — на стороне парсера)

По ТЗ §3.2:
  - Размерность векторов фиксирована embedding-моделью (384)
  - Чанки должны быть достаточно длинными для семантики,
    но не слишком длинными (теряется точность поиска)

<<<<<<< HEAD
<<<<<<< HEAD
=======
тут получаем уже извлечённый текст и подготавливает его для векторизации.
>>>>>>> b066bb1 (main_pipline)
=======
тут получаем уже извлечённый текст и подготавливает его для векторизации.
>>>>>>> b066bb1 (main_pipline)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from storage.config import settings
from storage.schema import Chunk, make_chunk_id

def normalize_text(text: str) -> str:
    """
    Нормализация текста перед индексацией:
      - Unicode NFC (склейка составных символов)
      - Замена нескольких пробелов/переносов на одиночные
      - Удаление управляющих символов
      - Обрезка пробелов по краям
    """
<<<<<<< HEAD
<<<<<<< HEAD
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
=======
=======
>>>>>>> b066bb1 (main_pipline)
    # Unicode нормализация
    text = unicodedata.normalize("NFC", text)
    # Управляющие символы (кроме \n, \t)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Множественные пробелы → один
    text = re.sub(r"[ \t]+", " ", text)
    # Множественные переносы → два максимум (абзацы)
<<<<<<< HEAD
>>>>>>> b066bb1 (main_pipline)
=======
>>>>>>> b066bb1 (main_pipline)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_meaningful(text: str, min_length: int | None = None) -> bool:
    """
    Проверяет, что текст достаточно содержателен для индексации.
    Отсекает: навигационные ссылки, копирайты, пустые строки.
    """
    min_len = min_length or settings.MIN_CHUNK_LENGTH
    text = text.strip()
    if len(text) < min_len:
        return False
<<<<<<< HEAD
<<<<<<< HEAD
=======
    # Слишком мало слов — скорее всего заголовок или навигация
>>>>>>> b066bb1 (main_pipline)
=======
    # Слишком мало слов — скорее всего заголовок или навигация
>>>>>>> b066bb1 (main_pipline)
    words = text.split()
    if len(words) < 8:
        return False
    return True


<<<<<<< HEAD
<<<<<<< HEAD
=======
# ── Сегментация ───────────────────────────────────────────────────────────────

>>>>>>> b066bb1 (main_pipline)
=======
# ── Сегментация ───────────────────────────────────────────────────────────────

>>>>>>> b066bb1 (main_pipline)
def split_into_chunks(
    text: str,
    max_length: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    """
    Сегментация текста на смысловые блоки (§3.1).

    Стратегия:
      1. Сначала делим по абзацам (\n\n)
      2. Если абзац длиннее max_length — делим по предложениям
      3. Если предложение длиннее max_length — делим по словам с перекрытием
      4. Склеиваем короткие соседние абзацы чтобы не было слишком мелких чанков

    Перекрытие (overlap) помогает не терять контекст на границах чанков.
    """
    max_len = max_length or settings.MAX_CHUNK_LENGTH
    ovlp = overlap if overlap is not None else settings.CHUNK_OVERLAP
    min_len = settings.MIN_CHUNK_LENGTH

    paragraphs = _split_paragraphs(text)
    raw_chunks: list[str] = []

    for para in paragraphs:
        if len(para) <= max_len:
            raw_chunks.append(para)
        else:
<<<<<<< HEAD
<<<<<<< HEAD
            raw_chunks.extend(_split_by_sentences(para, max_len, ovlp))

=======
=======
>>>>>>> b066bb1 (main_pipline)
            # Длинный абзац → дробим по предложениям
            raw_chunks.extend(_split_by_sentences(para, max_len, ovlp))

    # Склеиваем слишком короткие соседние чанки
<<<<<<< HEAD
>>>>>>> b066bb1 (main_pipline)
=======
>>>>>>> b066bb1 (main_pipline)
    merged = _merge_short_chunks(raw_chunks, min_len, max_len)
    return [c for c in merged if is_meaningful(c)]


def _split_paragraphs(text: str) -> list[str]:
    """Делит текст по двойным переносам строк."""
    return [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]


def _split_by_sentences(text: str, max_len: int, overlap: int) -> list[str]:
    """Делит длинный абзац по предложениям с перекрытием."""
<<<<<<< HEAD
<<<<<<< HEAD
=======
    # Паттерн конца предложения (русский + английский)
>>>>>>> b066bb1 (main_pipline)
=======
    # Паттерн конца предложения (русский + английский)
>>>>>>> b066bb1 (main_pipline)
    sentence_end = re.compile(r"(?<=[.!?])\s+(?=[А-ЯA-Z\d«\"\(])")
    sentences = sentence_end.split(text)

    chunks: list[str] = []
    current = ""
<<<<<<< HEAD
<<<<<<< HEAD
    prev_sentence = "" 
=======
    prev_sentence = ""  # для перекрытия
>>>>>>> b066bb1 (main_pipline)
=======
    prev_sentence = ""  # для перекрытия
>>>>>>> b066bb1 (main_pipline)

    for sent in sentences:
        candidate = (prev_sentence + " " + sent).strip() if prev_sentence else sent
        if len(current) + len(sent) + 1 <= max_len:
            current = (current + " " + sent).strip() if current else sent
        else:
            if current:
                chunks.append(current)
<<<<<<< HEAD
<<<<<<< HEAD
=======
            # Перекрытие: берём хвост предыдущего чанка
>>>>>>> b066bb1 (main_pipline)
=======
            # Перекрытие: берём хвост предыдущего чанка
>>>>>>> b066bb1 (main_pipline)
            overlap_text = current[-overlap:] if len(current) > overlap else current
            current = (overlap_text + " " + sent).strip() if overlap_text else sent
        prev_sentence = sent

    if current:
        chunks.append(current)

    return chunks


def _merge_short_chunks(
    chunks: list[str], min_len: int, max_len: int
) -> list[str]:
    """Склеивает слишком короткие соседние чанки."""
    if not chunks:
        return []
    merged: list[str] = [chunks[0]]
    for chunk in chunks[1:]:
        last = merged[-1]
        if len(last) < min_len and len(last) + len(chunk) + 1 <= max_len:
            merged[-1] = last + " " + chunk
        else:
            merged.append(chunk)
    return merged


<<<<<<< HEAD
<<<<<<< HEAD
@dataclass
class PreparedText:
    """Результат предобработки одного сырого текста."""
    chunks: list[str]      
    original_length: int 
    chunks_count: int    
=======
=======
>>>>>>> b066bb1 (main_pipline)
# ── Главная точка входа ───────────────────────────────────────────────────────

@dataclass
class PreparedText:
    """Результат предобработки одного сырого текста."""
    chunks: list[str]       # нормализованные сегменты, готовые к векторизации
    original_length: int    # длина исходного текста (символов)
    chunks_count: int       # число получившихся чанков
<<<<<<< HEAD
>>>>>>> b066bb1 (main_pipline)
=======
>>>>>>> b066bb1 (main_pipline)


def preprocess(text: str) -> PreparedText:
    """
    Полный пайплайн предобработки одного текста.
    Вызывается перед векторизацией.
    """
    normalized = normalize_text(text)
    chunks = split_into_chunks(normalized)
    return PreparedText(
        chunks=chunks,
        original_length=len(text),
        chunks_count=len(chunks),
    )


def preprocess_to_chunks(
    text: str,
    url: str,
    country: str,
    visa_type: str,
    source_type: str,
    date: str,
<<<<<<< HEAD
<<<<<<< HEAD
=======
    id_offset: int = 0,
>>>>>>> b066bb1 (main_pipline)
=======
    id_offset: int = 0,
>>>>>>> b066bb1 (main_pipline)
) -> list[Chunk]:
    """
    Предобрабатывает текст и возвращает готовые Chunk-объекты.
    Используется парсерами: они передают сырой текст,
    получают список Chunk, готовых к upsert в QdrantStore.
<<<<<<< HEAD
<<<<<<< HEAD
    
    id формируется детерминированно как make_chunk_id(url, index).
=======

    id_offset — смещение индекса для уникальности ID при нескольких вызовах
    с одним URL.
>>>>>>> b066bb1 (main_pipline)
=======

    id_offset — смещение индекса для уникальности ID при нескольких вызовах
    с одним URL.
>>>>>>> b066bb1 (main_pipline)
    """
    prepared = preprocess(text)
    return [
        Chunk(
<<<<<<< HEAD
<<<<<<< HEAD
            id=make_chunk_id(url, i),
=======
            id=make_chunk_id(url, id_offset + i),
>>>>>>> b066bb1 (main_pipline)
=======
            id=make_chunk_id(url, id_offset + i),
>>>>>>> b066bb1 (main_pipline)
            text=chunk_text,
            country=country,
            visa_type=visa_type,
            source_type=source_type,
            url=url,
            date=date,
        )
        for i, chunk_text in enumerate(prepared.chunks)
<<<<<<< HEAD
<<<<<<< HEAD
    ]
=======
    ]
>>>>>>> b066bb1 (main_pipline)
=======
    ]
>>>>>>> b066bb1 (main_pipline)
