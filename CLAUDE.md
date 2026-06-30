# memoryst — контекст для Claude Code

## Что это

Локальный memory-сервис для SillyTavern. Хранит и извлекает память персонажей из чатов.
FastAPI + SQLite, локальное хранилище, извлечение через внешний LLM API (llm_client.py).

Два клиента к одному API:
- `sillytavern-extension/index.js` — основной, вызывает `/memory/store` и `/memory/retrieve`
- `app/routes/ui.py` — web UI, вызывает те же сервисы напрямую

## Текущее состояние архитектуры

### Что работает и не трогать без причины
- `app/services/retrieve_service.py` — гибридный ретрив (keyword + entity + опциональный semantic)
- `app/services/summary_service.py` — rolling summary с `summary_source_memory_ids` в metadata
- `app/services/store_service.py` — запись памяти
- `app/services/text_features.py` — текстовые фичи для скоринга
- `tests/` — 29 Python + 6 mjs тест-файлов, все должны проходить после любых изменений

### Известный технический долг (устранять по плану, не точечно)
- `extract_memories` и `extract_for_backfill` в `extractor.py` — дублирующие функции,
  объединить в одну с параметром `mode: Literal["live", "backfill"]`
- `app/services/llm_extractor.py` и эндпоинт `/memory/scene` — мёртвый код,
  ничего в кодовой базе их не вызывает. Удалить.
- 24 магические константы весов в `retrieve_service.py` — вынести в конфиг
- `_call_embed` в `vector_store.py` — синхронный `httpx.post` в async-стеке, заменить на AsyncClient
- `API_KEY` пустая строка по умолчанию — добавить fail-fast если хост не 127.0.0.1

## План доработки (этапы, выполнять строго по порядку)

### Этап 1 — Чистка долга
Дублирование extraction, удаление мёртвого llm_extractor, вынос весов в конфиг,
async fix, security. После этого этапа все существующие тесты должны проходить.

### Этап 2 — Raw-таблица сообщений
Новая SQLite-таблица `chat_messages` с FTS5-индексом. Горячий буфер: последние 4
сообщения чата (только role=user/assistant, без OOC и системных) живут вне базы,
попадают в таблицу при появлении 5-го. UUID как первичный ключ, не порядковый индекс.

### Этап 3 — Extraction по сцене
Заменить построчную regex-классификацию на LLM-вызов по всей сцене целиком.
Structured output (json_schema), не regex-парсинг JSON. Каждый извлечённый факт
получает `source_message_ids: list[str]` — ссылки на UUID из chat_messages.
Regex-маркеры остаются только как pre-filter "стоит ли звать LLM".

### Этап 4 — Консолидация со слиянием фактов
`_build_summary_metadata` агрегирует `source_message_ids` transitively
(summary → memories → raw messages). Логика обнаружения конфликтующих фактов
перед LLM-консолидацией.

### Этап 5 — Retrieval fallback на raw-историю
Два триггера: автоматический (confidence score ниже порога) и ручной (пользователь
запрашивает детали). FTS5-поиск по `chat_messages`. Результаты из raw помечаются
отдельно от консолидированной памяти.

## Правила работы с кодом

- Не менять несколько этапов одновременно — каждый этап отдельная сессия
- После каждого этапа запускать `pytest tests/` — все тесты должны проходить
- Extraction-логику не трогать до завершения Этапа 1 (чистка дублирования)
- `sillytavern-extension/index.js` — не ломать совместимость с `/memory/store` и `/memory/retrieve`
- Новые константы/пороги — в конфиг, не хардкодить в логику

## Стек

- Python 3.11+, FastAPI, SQLite, uvicorn
- `llm_client.py` — внешний LLM API (OpenRouter/DeepSeek-совместимый)
- `vector_store.py` — ChromaDB (primary) + JSON fallback
- Тесты: pytest (Python), node/vitest (JS extension)

## Что не трогать

- Схему слоёв памяти (episodic/stable/summary) — это ядро архитектуры
- API-контракт `/memory/store` и `/memory/retrieve` — SillyTavern-расширение зависит от них
- `tests/` — только добавлять тесты, не удалять существующие без явной причины
