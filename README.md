# Бот-Консул

Telegram-ассистент для визовых консультаций граждан России. Агрегирует актуальную информацию об оформлении виз из официальных источников консульств, пользовательских отзывов и Telegram-каналов.

## Что умеет

- Формирует чек-лист документов под конкретную страну, тип визы и цель поездки
- Даёт пошаговые инструкции по подаче
- Ссылается на официальные сайты консульств и визовых центров
- Предупреждает о типичных ошибках
- Чётко разграничивает официальную политику и пользовательский опыт
- При слабом покрытии базы знаний — автоматически подтягивает свежие данные через веб-поиск

## Архитектура

```
Пользователь (Telegram)
        │
        ▼
  Orchestrator
  ┌─────────────────────────────────────────────┐
  │ 1. Guardrails (фильтрация нежелательных запросов)
  │ 2. Извлечение визового профиля              │
  │    (страна, тип визы, гражданство, цель)    │
  │ 3. Уточнение при неполном профиле           │
  │ 4. RAG-поиск в Qdrant                       │
  │ 5. Оценка достаточности контекста           │
  │ 6. Веб-поиск (DuckDuckGo) при необходимости │
  │ 7. Генерация ответа (LLM)                   │
  │ 8. Опциональная проверка фактов             │
  └─────────────────────────────────────────────┘
        │
        ▼
  Ответ пользователю
```

### Слои системы

| Слой | Модули | Ответственность |
|------|--------|-----------------|
| **Storage** | `schema.py`, `store.py`, `embedder.py`, `preprocessor.py` | Схема данных, векторная БД, эмбеддинги, нарезка текста |
| **RAG** | `rag_service.py` | Поиск с фильтрацией, ослабление фильтров, оценка достаточности |
| **Orchestration** | `orchestrator.py` | Пайплайн диалогового хода, управление состоянием |
| **Fallback** | `web_fallback.py` | Веб-поиск при недостаточном RAG-контексте |

## Технический стек

| Компонент | Технология |
|-----------|-----------|
| Векторная БД | [Qdrant](https://qdrant.tech/) |
| Модель эмбеддингов | [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3) (dim=1024, русский + английский) |
| Веб-поиск | DuckDuckGo (`ddgs`) |
| Платформа | Telegram Bot API |
| Язык | Python 3.11+ |

## Установка

```bash
git clone https://github.com/mirzoyevaa/NLP_project.git
cd NLP_project
pip install -r requirements.txt
```

Создайте `.env` файл:

```env
TELEGRAM_BOT_TOKEN=<ваш токен>
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=<ключ, если используется>
QDRANT_COLLECTION=bot_consul
EMBEDDING_DEVICE=cpu
ENABLE_WEB_FALLBACK=true
SAVE_SEARCH_ARTIFACTS_JSON=false
```

Запустите Qdrant (Docker):

```bash
docker run -p 6333:6333 qdrant/qdrant
```

Запустите бота:

```bash
python -m bot_consul
```

## RAG: как работает поиск

1. Запрос пользователя векторизуется моделью `BAAI/bge-m3`.
2. В Qdrant выполняется поиск с фильтрами по `country`, `visa_type`, `source_type`.
3. Если результатов недостаточно — фильтры поэтапно ослабляются (*filter relaxation*).
4. `assess_rag_sufficiency()` проверяет четыре эвристики:
   - достаточное число чанков
   - упоминание нужной страны в результатах
   - score выше минимального порога
   - наличие хотя бы одного официального источника
5. При провале любой проверки — активируется DuckDuckGo.

## Обновление базы знаний

```python
from bot_consul.storage.store import QdrantStore
from bot_consul.storage.preprocessor import preprocess_to_chunks

store = QdrantStore()

# Удалить устаревшие записи (старше 180 дней)
store.delete_stale_records()

# Добавить новый документ
chunks = preprocess_to_chunks(
    text=document_text,
    country="Germany",
    visa_type="Schengen",
    source_type="official",
    url="https://example.com/germany-visa"
)
store.upsert(chunks)

# Посмотреть статистику покрытия
print(store.coverage_stats())
```

## Структура проекта

```
bot_consul/
├── orchestrator.py        # Главный пайплайн
├── rag_service.py         # RAG: поиск и оценка достаточности
├── web_fallback.py        # Веб-поиск через DuckDuckGo
└── storage/
    ├── schema.py          # Dataclass Chunk, хеширование, версионирование
    ├── store.py           # CRUD-обёртка над Qdrant
    ├── embedder.py        # Singleton BAAI/bge-m3
    └── preprocessor.py    # Нормализация и нарезка текста
```

## Метрики

- **RAG hit rate** — доля запросов, обработанных без веб-поиска
- **Helpfulness rating** — оценка полезности от пользователей
- **Staleness rate** — доля устаревших чанков (> 180 дней)
- **Web fallback rate** — доля запросов, потребовавших DuckDuckGo
- **Repeat usage** — возвращаемость пользователей
