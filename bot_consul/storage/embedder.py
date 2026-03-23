"""
Embedding-модель для векторизации текста.

По ТЗ §3.2:
  - Multilingual модель (русский + английский)
  - Векторы нормализованы для cosine similarity
  - Батч-векторизация для эффективной индексации
  - При росте объёма — возможно доменное дообучение на визовой лексике

Singleton через @lru_cache: модель загружается один раз при первом вызове
и живёт до завершения процесса.
"""
import logging
from functools import lru_cache

<<<<<<< HEAD
<<<<<<< HEAD

try:
    import torch
=======
=======
>>>>>>> b066bb1 (main_pipline)
# Загрузить PyTorch до transformers/sentence_transformers — иначе у Hugging Face иногда частичная инициализация
# и в логах «GenerationMixin» / «Could not import sentence_transformers» при том что пакеты стоят.
try:
    import torch  # noqa: F401
<<<<<<< HEAD
>>>>>>> b066bb1 (main_pipline)
=======
>>>>>>> b066bb1 (main_pipline)
except ImportError as e:
    raise RuntimeError(
        "Пакет torch (PyTorch) не импортируется. Переустановите зависимости в .venv бота: "
        "pip install -r requirements.txt (нужен torch>=2.1 для текущей ветки transformers)."
    ) from e

from langchain_huggingface import HuggingFaceEmbeddings

from storage.config import settings

logger = logging.getLogger(__name__)

VECTOR_SIZE = 1024


@lru_cache(maxsize=1)
def get_embedder() -> HuggingFaceEmbeddings:
    logger.info(
        "Loading embedding model: %s (device=%s)",
        settings.EMBEDDING_MODEL,
        settings.EMBEDDING_DEVICE,
    )
    model = HuggingFaceEmbeddings(
    model_name=settings.EMBEDDING_MODEL,
    model_kwargs={"device": settings.EMBEDDING_DEVICE},
    encode_kwargs={"normalize_embeddings": True},)
    logger.info("Embedding model ready, vector_size=%d", VECTOR_SIZE)
    return model


def embed_text(text: str) -> list[float]:
    return get_embedder().embed_query(text)

def embed_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    return get_embedder().embed_documents(texts)
