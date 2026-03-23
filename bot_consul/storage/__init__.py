"""
Зона: Хранилище — публичный API.
<<<<<<< HEAD
=======

ВАЖНО: __init__.py намеренно не импортирует QdrantStore и embedder —
они тянут тяжёлые зависимости (qdrant_client, sentence_transformers).
Импортируйте их явно там, где нужно:

    from storage.store import QdrantStore
    from storage.embedder import embed_text, VECTOR_SIZE

Лёгкие модули (schema, preprocessor, raw_storage, quality)
можно импортировать и отсюда:

    from storage import Chunk, preprocess_to_chunks, RawStorage
>>>>>>> b066bb1 (main_pipline)
"""

from storage.schema import (
    Chunk,
    make_chunk_id,
<<<<<<< HEAD
=======
    make_content_hash,
>>>>>>> b066bb1 (main_pipline)
    SOURCE_TYPES,
    COUNTRY_CODES,
    VISA_TYPE_CODES,
)
<<<<<<< HEAD

from storage.store import QdrantStore

from storage.embedder import embed_text, embed_batch, VECTOR_SIZE

__all__ = [
    "Chunk", "make_chunk_id",
    "SOURCE_TYPES", "COUNTRY_CODES", "VISA_TYPE_CODES",
    "QdrantStore",
    "embed_text", "embed_batch", "VECTOR_SIZE",
]
=======
from storage.preprocessor import (
    preprocess,
    preprocess_to_chunks,
    normalize_text,
    split_into_chunks,
    PreparedText,
)
from storage.raw_storage import RawStorage
from storage.quality import (
    CoverageReport,
    build_coverage_report,
    TARGET_COUNTRIES,
    REQUIRED_SOURCE_TYPES,
)

__all__ = [
    "Chunk", "make_chunk_id", "make_content_hash",
    "SOURCE_TYPES", "COUNTRY_CODES", "VISA_TYPE_CODES",
    "preprocess", "preprocess_to_chunks",
    "normalize_text", "split_into_chunks", "PreparedText",
    "RawStorage",
    "CoverageReport", "build_coverage_report",
    "TARGET_COUNTRIES", "REQUIRED_SOURCE_TYPES",
]
>>>>>>> b066bb1 (main_pipline)
