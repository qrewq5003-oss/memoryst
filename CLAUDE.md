# memoryst — контекст для Claude Code

## Что это

Локальный memory-сервис для SillyTavern. Хранит и извлекает память персонажей из чатов.
FastAPI + SQLite, локальное хранилище, извлечение через внешний LLM API (llm_client.py).

Два клиента к одному API:
- `sillytavern-extension/main.mjs` — основной, вызывает `/memory/store` и `/memory/retrieve`.
  `index.js` — только 16-строчный загрузчик, намеренно заморожен (объяснение внутри файла)
- `app/routes/ui.py` — web UI, вызывает те же сервисы напрямую

## Текущее состояние архитектуры

### Что работает и не трогать без причины
- `app/services/retrieve_service.py` — гибридный ретрив (keyword + entity + опциональный semantic)
- `app/services/summary_service.py` — rolling summary с `summary_source_memory_ids` в metadata
- `app/services/store_service.py` — запись памяти
- `app/services/text_features.py` — текстовые фичи для скоринга
- `app/services/scene_extractor.py` + `app/services/llm_extractor.py` — извлечение по сцене
  через LLM (structured output) с regex-фоллбэком
- `tests/` — 45 Python + 8 mjs тест-файлов, все должны проходить после любых изменений

### Живой код, который легко принять за мёртвый
- **`app/services/llm_extractor.py` — НЕ мёртвый код, не удалять.** Его
  `extract_scene_facts` — основной путь извлечения (`scene_extractor.py:100`), а
  `extract_with_llm` обслуживает эндпоинт `/memory/scene`, который вызывает вкладка
  Tools в web UI (`app/templates/_scripts.html:249`). По `data/server.log` на
  2026-08-01: 1583 успешных вызова и 568 фоллбэков на regex.
  Прежняя редакция этого файла предписывала «удалить как мёртвый код» — указание
  было ошибочным и снято 2026-08-01.

### Известный технический долг
Прежний список (дублирование `extract_memories`/`extract_for_backfill`, веса в
`retrieve_service.py`, async-фикс `_call_embed`, fail-fast для `API_KEY`) закрыт
целиком — проверено 2026-08-01. Актуальный перечень открытых проблем ведётся
не здесь, а в `docs/full_audit_2026-08-01.md` (15 находок, к исправлению не
приступали).

## План доработки — выполнен целиком (проверено 2026-08-01)

Этот раздел — история, а не задание. Все пять этапов реализованы; ниже — куда
смотреть, чтобы не переделывать сделанное.

| Этап | Что было запланировано | Где это сейчас живёт |
|---|---|---|
| 1 | Чистка долга | `extract_memories(mode=...)`, `config/retrieval_weights.yaml` + `retrieval_config.py`, `config.validate_security()` |
| 2 | Raw-таблица сообщений | `chat_messages` + FTS5 в `db.py`, горячий буфер в `chat_buffer_service.py`, `chat_message_repo.py` |
| 3 | Extraction по сцене | `scene_extractor.py` (pre-filter + LLM) и `llm_extractor.py` (structured output) |
| 4 | Консолидация со слиянием фактов | `summary_service._build_summary_metadata` (транзитивная агрегация), `conflict_resolver.py` |
| 5 | Retrieval fallback на raw-историю | `retrieve_service._collect_raw_fallback_results` — оба триггера, авто и ручной |

Следующие работы берутся не отсюда, а из `docs/full_audit_2026-08-01.md`.

## Правила работы с кодом

- Перед удалением кода как «мёртвого» — проверить вызовы grep'ом по `app/`,
  `sillytavern-extension/` **и** `app/templates/`, а не по одному только `app/`.
  Именно пропуск шаблонов породил ошибочное указание удалить `llm_extractor`
- После любых изменений запускать `pytest tests/` — все тесты должны проходить
- `sillytavern-extension/main.mjs` — не ломать совместимость с `/memory/store` и `/memory/retrieve`
- Новые константы/пороги — в конфиг, не хардкодить в логику

## Стек

- Python 3.11+, FastAPI, SQLite, uvicorn
- `llm_client.py` — внешний LLM API (OpenRouter/DeepSeek-совместимый)
- `vector_store.py` — ChromaDB (primary) + JSON fallback. На этой машине `chromadb`
  не установлен, `data/vectors.json` отсутствует — векторный слой фактически не
  используется, а оба его теста в `tests/` помечены skip
- Тесты: pytest (Python), node/vitest (JS extension)

## Что не трогать

- Схему слоёв памяти (episodic/stable/summary) — это ядро архитектуры
- API-контракт `/memory/store` и `/memory/retrieve` — SillyTavern-расширение зависит от них
- `tests/` — только добавлять тесты, не удалять существующие без явной причины
