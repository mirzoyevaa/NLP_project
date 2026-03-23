"""
Конфигурация зоны Хранилище.
Читается из переменных окружения / .env файла.
Другие зоны работают через интерфейс QdrantStore и не должны
зависеть от деталей подключения к Qdrant напрямую.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Тот же корень, что и у bot_consul: storage_zone/.env (не зависит от cwd)
_STORAGE_ZONE_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _STORAGE_ZONE_ROOT / ".env"


class StorageSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    QDRANT_URL: str = Field(default = "https://1a52f6e5-15f6-4d8d-9168-0c4ca6887771.europe-west3-0.gcp.cloud.qdrant.io",  description='url')
    QDRANT_API_KEY: str = Field(default="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIn0.MOrvRW05eaj9OVXqzJddFTafoLSMUaBwgMJTE_TbZM8", description='api')
    QDRANT_COLLECTION: str = Field(
        default="consul_knowledge",
        description="consul_knowledge",
    )

    EMBEDDING_MODEL: str = Field(
        default="BAAI/bge-m3",
        description="Модель эмбеддингов",
    )
    EMBEDDING_DEVICE: str = Field(
        default="cpu",
        description="Устройство для эмбеддингов: cpu / cuda / mps",
    )
    EMBEDDING_BATCH_SIZE: int = Field(
        default=32,
        description="Размер батча для генерации эмбеддингов",
    )
    EMBEDDING_NORMALIZE: bool = Field(
        default=True,
        description="Нормализовать эмбеддинги перед записью/поиском",
    )

    SEARCH_TOP_K: int = Field(
        default=5,
        description="Количество результатов поиска по умолчанию",
    )
    SEARCH_SCORE_THRESHOLD: float = Field(
        default=0.2,
        description="Минимальный score для возврата результата",
    )

    STALENESS_DAYS: int = Field(
        default=180,
        description="Порог устаревания источников в днях",
    )

    RAW_STORAGE_PATH: str = Field(
        default="data/raw",
        description="Папка для сырых JSON и промежуточных выгрузок",
    )

    MIN_CHUNK_LENGTH: int = Field(
        default=80,
        description="Минимальная длина чанка в символах",
    )
    MAX_CHUNK_LENGTH: int = Field(
        default=1500,
        description="Максимальная длина чанка в символах",
    )
    CHUNK_OVERLAP: int = Field(
        default=100,
        description="Перекрытие между чанками в символах",
    )

    DEFAULT_VISA_TYPE: str = Field(
        default="tourist",
        description="Значение visa_type по умолчанию",
    )
    DEFAULT_DATASET: str = Field(
        default="default_dataset",
        description="Имя датасета по умолчанию",
    )

    LOG_LEVEL: str = Field(
        default="INFO",
        description="Уровень логирования",
    )


settings = StorageSettings()
