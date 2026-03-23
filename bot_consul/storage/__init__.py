"""
Зона: Хранилище — публичный API.
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
=======
=======
>>>>>>> b066bb1 (main_pipline)

ВАЖНО: __init__.py намеренно не импортирует QdrantStore и embedder —
они тянут тяжёлые зависимости (qdrant_client, sentence_transformers).
Импортируйте их явно там, где нужно:

    from storage.store import QdrantStore
    from storage.embedder import embed_text, VECTOR_SIZE

Лёгкие модули (schema, preprocessor, raw_storage, quality)
можно импортировать и отсюда:

    from storage import Chunk, preprocess_to_chunks, RawStorage
<<<<<<< HEAD
>>>>>>> b066bb1 (main_pipline)
=======
>>>>>>> b066bb1 (main_pipline)
=======
>>>>>>> bffe1d0 (storage: fix deduplication, config, and consistency issues - schema: add australia, south_africa to COUNTRY_CODES - schema: add page_content, dataset, passport_country, purpose, destination_raw fields to Chunk - config: add SEARCH_SCORE_THRESHOLD_OFFICIAL field - embedder: fix HuggingFaceEmbeddings initialization - store: replace hardcoded score_threshold=0.35 with settings - quality: switch duplicate detection from content_hash to URL - web_source_catalog: fix chunk ID generation with enumerate - __init__: remove unused exports - add .gitignore)
"""

from storage.schema import (
    Chunk,
    make_chunk_id,
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
=======
    make_content_hash,
>>>>>>> b066bb1 (main_pipline)
=======
    make_content_hash,
>>>>>>> b066bb1 (main_pipline)
=======
>>>>>>> bffe1d0 (storage: fix deduplication, config, and consistency issues - schema: add australia, south_africa to COUNTRY_CODES - schema: add page_content, dataset, passport_country, purpose, destination_raw fields to Chunk - config: add SEARCH_SCORE_THRESHOLD_OFFICIAL field - embedder: fix HuggingFaceEmbeddings initialization - store: replace hardcoded score_threshold=0.35 with settings - quality: switch duplicate detection from content_hash to URL - web_source_catalog: fix chunk ID generation with enumerate - __init__: remove unused exports - add .gitignore)
    SOURCE_TYPES,
    COUNTRY_CODES,
    VISA_TYPE_CODES,
)
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
=======
>>>>>>> bffe1d0 (storage: fix deduplication, config, and consistency issues - schema: add australia, south_africa to COUNTRY_CODES - schema: add page_content, dataset, passport_country, purpose, destination_raw fields to Chunk - config: add SEARCH_SCORE_THRESHOLD_OFFICIAL field - embedder: fix HuggingFaceEmbeddings initialization - store: replace hardcoded score_threshold=0.35 with settings - quality: switch duplicate detection from content_hash to URL - web_source_catalog: fix chunk ID generation with enumerate - __init__: remove unused exports - add .gitignore)

from storage.store import QdrantStore

from storage.embedder import embed_text, embed_batch, VECTOR_SIZE
<<<<<<< HEAD

__all__ = [
    "Chunk", "make_chunk_id",
    "SOURCE_TYPES", "COUNTRY_CODES", "VISA_TYPE_CODES",
    "QdrantStore",
    "embed_text", "embed_batch", "VECTOR_SIZE",
]
=======
=======
>>>>>>> b066bb1 (main_pipline)
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
=======
>>>>>>> bffe1d0 (storage: fix deduplication, config, and consistency issues - schema: add australia, south_africa to COUNTRY_CODES - schema: add page_content, dataset, passport_country, purpose, destination_raw fields to Chunk - config: add SEARCH_SCORE_THRESHOLD_OFFICIAL field - embedder: fix HuggingFaceEmbeddings initialization - store: replace hardcoded score_threshold=0.35 with settings - quality: switch duplicate detection from content_hash to URL - web_source_catalog: fix chunk ID generation with enumerate - __init__: remove unused exports - add .gitignore)

__all__ = [
    "Chunk", "make_chunk_id",
    "SOURCE_TYPES", "COUNTRY_CODES", "VISA_TYPE_CODES",
<<<<<<< HEAD
    "preprocess", "preprocess_to_chunks",
    "normalize_text", "split_into_chunks", "PreparedText",
    "RawStorage",
    "CoverageReport", "build_coverage_report",
    "TARGET_COUNTRIES", "REQUIRED_SOURCE_TYPES",
]
<<<<<<< HEAD
>>>>>>> b066bb1 (main_pipline)
=======
>>>>>>> b066bb1 (main_pipline)
=======
    "QdrantStore",
    "embed_text", "embed_batch", "VECTOR_SIZE",
]
>>>>>>> bffe1d0 (storage: fix deduplication, config, and consistency issues - schema: add australia, south_africa to COUNTRY_CODES - schema: add page_content, dataset, passport_country, purpose, destination_raw fields to Chunk - config: add SEARCH_SCORE_THRESHOLD_OFFICIAL field - embedder: fix HuggingFaceEmbeddings initialization - store: replace hardcoded score_threshold=0.35 with settings - quality: switch duplicate detection from content_hash to URL - web_source_catalog: fix chunk ID generation with enumerate - __init__: remove unused exports - add .gitignore)
