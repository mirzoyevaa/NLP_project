"""Настройки оркестратора (отдельно от storage.config)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Всегда читаем .env из корня storage_zone (рядом с пакетом bot_consul),
# а не из текущей рабочей директории — иначе TELEGRAM_BOT_TOKEN и ключи «пропадают».
_STORAGE_ZONE_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _STORAGE_ZONE_ROOT / ".env"


class OrchestratorSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    OPENROUTER_API_KEY: str = Field(
        default="",
        description="Ключ OpenRouter для облачного LLM",
    )
    OPENROUTER_MODEL: str = Field(
        default="google/gemini-2.0-flash-001",
        description="Модель на OpenRouter",
    )
    #: Резерв: по умолчанию openai/o4-mini — компактная o-серия с reasoning (не «микро»), см. README
    OPENROUTER_FALLBACK_MODELS: str = Field(
        default="openai/o4-mini",
        description="Резерв при ошибке HTTP/таймауте/пустом ответе: id моделей OpenRouter через запятую",
    )
    #: Пусто = не слать reasoning в теле запроса. Для o-серии OpenAI через OpenRouter: low / medium / high.
    OPENROUTER_REASONING_EFFORT: str = Field(
        default="",
        description="Усилие reasoning для поддерживаемых моделей (openai/o*); пусто — отключено",
    )

    OPENROUTER_CHAT_URL: str = Field(
        default="https://openrouter.ai/api/v1/chat/completions",
    )
    LLM_TIMEOUT_SEC: int = Field(default=90)
    LLM_TEMPERATURE: float = Field(default=0.2)

    # RAG
    RAG_TOP_K: int = Field(default=8)
    RAG_MIN_CHUNKS: int = Field(default=1)
    RAG_MIN_AVG_SCORE: float = Field(
        default=0.25,
        description="Если средний score ниже — считаем базу слабой (при наличии score)",
    )
    RAG_REQUIRE_OFFICIAL: bool = Field(
        default=False,
        description="Если True — без чанка source_type=official считаем retrieval недостаточным",
    )

    # Опциональный веб-fallback (короткий DDG из web_fallback.py)
    ENABLE_WEB_FALLBACK: bool = Field(default=False)
    WEB_SEARCH_MAX_RESULTS: int = Field(default=3)
    SAVE_SEARCH_ARTIFACTS_JSON: bool = Field(
        default=True,
        description="Сохранять результаты DDG и веб-сбора в data/raw/*.json",
    )

    # Отобранные по релевантности веб-источники → data/curated/web_sources.jsonl
    WEB_SOURCE_STORE_ENABLED: bool = Field(
        default=True,
        description="Сохранять в хранилище (JSONL) веб-источники после фильтра релевантности",
    )
    WEB_SOURCE_MIN_RELEVANCE: float = Field(
        default=0.28,
        ge=0.0,
        le=1.0,
        description="Порог cosine (нормированные эмбеддинги) или keyword-overlap [0–1]",
    )
    WEB_SOURCE_MAX_PER_TURN: int = Field(
        default=40,
        description="Максимум источников на один ход (после дедуп URL и фильтра)",
    )
    # Если cosine/Jaccard ни у кого не ≥ WEB_SOURCE_MIN_RELEVANCE, в Qdrant не попадало бы ничего —
    # при этом JSON в data/raw есть. Берём топ-N по score без порога (см. web_source_catalog).
    WEB_SOURCE_QDRANT_FALLBACK_TOP_N: int = Field(
        default=8,
        ge=1,
        le=40,
        description="Если после порога релевантности не осталось источников — всё равно upsert в Qdrant топ-N по score",
    )
    WEB_SOURCE_QDRANT_UPSERT: bool = Field(
        default=True,
        description="После фильтра релевантности добавлять чанки в Qdrant (RAG) для следующих запросов",
    )

    # Полный веб-агент (travel_web_agent: 3 сабагента + DDG + fetch)
    # off | always | when_rag_weak (when_rag_weak = устаревший алиас, как always)
    TRAVEL_WEB_MODE: str = Field(
        default="always",
        description="always / when_rag_weak: всегда три сабагента; off: не вызывать",
    )
    FOLLOWUP_SKIP_TRAVEL_WEB: bool = Field(
        default=False,
        description="Если True — при уточняющих сообщениях не вызывать travel_web (только RAG + DDG). "
        "По умолчанию False: сабагенты поиска вызываются и на уточнениях.",
    )

    # Пост-проверка ответа через LangGraph fact-check из travel_web_agent (дорого, доп. вызовы LLM + DDG)
    # По умолчанию включено; отключать только для отладки (в .env ENABLE_TRAVEL_FACT_CHECK=false).
    ENABLE_TRAVEL_FACT_CHECK: bool = Field(default=True)
    FACT_CHECK_MAX_REVERIFY_ATTEMPTS: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Если фактчек вернул REVERIFY (противоречия / нужен новый baseline-поиск) — полный повтор "
        "run_fact_check_gate с новым контекстом, максимум столько раз подряд.",
    )

    # Сессия
    HISTORY_MAX_TURNS: int = Field(default=10)

    # Telegram (@BotFather)
    TELEGRAM_USER_ID_LOG_PEPPER: str = Field(
        default="",
        description="Соль для SHA-256 хеша user id в логах; задайте свой в .env в проде",
    )
    TELEGRAM_BOT_TOKEN: str = Field(
        default="",
        description="Токен бота Telegram",
    )


orchestrator_settings = OrchestratorSettings()
