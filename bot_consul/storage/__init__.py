"""
Зона: Хранилище — публичный API.
"""

from storage.schema import (
    Chunk,
    make_chunk_id,
    SOURCE_TYPES,
    COUNTRY_CODES,
    VISA_TYPE_CODES,
)

from storage.store import QdrantStore

from storage.embedder import embed_text, embed_batch, VECTOR_SIZE

__all__ = [
    "Chunk", "make_chunk_id",
    "SOURCE_TYPES", "COUNTRY_CODES", "VISA_TYPE_CODES",
    "QdrantStore",
    "embed_text", "embed_batch", "VECTOR_SIZE",
]