# memoryst — контекст для Claude Code

## Что это

Локальный memory-сервис для SillyTavern. Хранит и извлекает память персонажей из чатов.
FastAPI + SQLite, локальное хранилище, извлечение через внешний LLM API (llm_client.py).

Два клиента к одному API:
- `sillytavern-extension/main.mjs` — основной. Зовёт `/memory/retrieve` (на `MESSAGE_SENT`),
  `/memory/store` (на `CHARACTER_MESSAGE_RENDERED`), плюс `/memory/version` (хэндшейк),
  `/memory/trackers` и `/memory/audit`. Рядом ещё семь модулей, не зависящих от ST:
  `audit.mjs`, `settings.mjs`, `settings-ui.mjs`, `trackers.mjs`, `lore-anchors.mjs`,
  `scope.mjs`, `version.mjs` — они и покрыты mjs-тестами.
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
- `tests/` — 56 Python + 8 mjs тест-файлов на 2026-09-20 (`ls tests/test_*.py | wc -l`),
  все должны проходить после любых изменений

### Живой код, который легко принять за мёртвый
- **`app/services/llm_extractor.py` — НЕ мёртвый код, не удалять.** Его
  `extract_scene_facts` — основной путь извлечения (`scene_extractor.py:100`), а
  `extract_with_llm` обслуживает эндпоинт `/memory/scene`, который вызывает вкладка
  Tools в web UI (`app/templates/_scripts.html:249`). По `data/server.log` на
  2026-08-01: 1583 успешных вызова и 568 фоллбэков на regex.
  Прежняя редакция этого файла предписывала «удалить как мёртвый код» — указание
  было ошибочным и снято 2026-08-01.

### Известный технический долг
Два прежних списка закрыты целиком, оба — история, а не задание:
- дублирование `extract_memories`/`extract_for_backfill`, веса в `retrieve_service.py`,
  async-фикс `_call_embed`, fail-fast для `API_KEY` — закрыто 2026-08-01;
- 15 находок `docs/full_audit_2026-08-01.md` — **все закрыты 2026-08-02**, перечень
  коммитов в шапке самого файла. Прежняя редакция этого раздела утверждала обратное.

Актуальный перечень открытых проблем — `docs/extension_audit_2026-09-20.md`.
Три, которые стоит чинить первыми:
1. ~~Ни на одном `fetch` в расширении нет `AbortController`~~ — **закрыто 2026-09-20,
   коммит `9da6825`.** Всё идёт через `sillytavern-extension/http.mjs`. При правке
   бюджетов: `storeTimeoutMs` обязан быть больше `SCENE_LLM_TIMEOUT` из `app/config.py`,
   иначе клиент рвёт извлечение сцены, которое вот-вот бы завершилось.
2. ~~UI-роутер не защищён при `allow_origins=["*"]`~~ — **закрыто 2026-09-20, коммит
   `65be121`.** CORS — allowlist (`config.CORS_ALLOW_ORIGINS` / `CORS_ALLOW_ORIGIN_REGEX`,
   по умолчанию лупбек), `/ui` висит на `auth.require_same_origin`. Две вещи, которые
   легко сломать обратно: `require_api_key` на `/ui` вешать **нельзя** — браузерная
   форма не умеет слать заголовок; и `/memory` намеренно **не** под same-origin — его
   зовут кросс-доменно из SillyTavern. Это CSRF-защита, не аутентификация.
3. ~~Штатная кнопка ST «Install Extension» не может поставить расширение~~ — **закрыто
   2026-09-20, коммит `d3a8531`.** Ставится по URL репозитория **с веткой `extension`**
   (в диалоге установки есть поле для ветки); в её корне лежит расширение. Ветка —
   публикация, сама не обновляется: см. правило про `publish_extension_branch` ниже.

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

Следующие работы берутся не отсюда, а из `docs/extension_audit_2026-09-20.md`.

## Правила работы с кодом

- Перед удалением кода как «мёртвого» — проверить вызовы grep'ом по `app/`,
  `sillytavern-extension/` **и** `app/templates/`, а не по одному только `app/`.
  Именно пропуск шаблонов породил ошибочное указание удалить `llm_extractor`
- После любых изменений запускать **обе** сюиты: `pytest tests/` и
  `node --test tests/*.mjs`. CI гоняет ещё и `scripts/run_retrieval_eval.py` как gate —
  правка весов ретрива без прогона eval'а завалит пайплайн
- `sillytavern-extension/main.mjs` — не ломать совместимость с `/memory/store` и
  `/memory/retrieve`. При изменении контракта поднимать `PROTOCOL_VERSION` в
  `app/version.py` **и** `MEMORY_PROTOCOL_VERSION` в `sillytavern-extension/version.mjs`
- Новые константы/пороги — в конфиг, не хардкодить в логику
- Менял `sillytavern-extension/` — после коммита прогнать
  `python -m scripts.stamp_extension_build`, затем
  `python -m scripts.publish_extension_branch --push`. Ветка `extension` — это то, что
  клонирует кнопка «Install Extension» в ST, и она не обновляется сама.
  `tests/test_extension_branch_publish.py` падает, если ветка отстала
- **Закрыл задачу — отметь это в том документе, где она описана.** Не в коммите,
  не только здесь: правка идёт в сам файл с задачей. Минимум — строка в шапке
  раздела или пункта: что закрыто, когда, каким коммитом. Если находка оказалась
  ошибочной — так и написать, а не молча удалить.
  Отмечать надо в **двух** местах, иначе толку нет:
  1. документ, где задача описана (`docs/*.md`, план, список находок);
  2. указатель, который на него ведёт — раздел «Известный технический долг» здесь.
     Файл со статусом «закрыто», на который отсюда ведёт ссылка «к исправлению не
     приступали», хуже, чем отсутствие ссылки.

  Это не бюрократия: `docs/full_audit_2026-08-01.md` был закрыт целиком 2026-08-02,
  а `CLAUDE.md` ещё полтора месяца отправлял читать его как список открытых проблем.
  Каждая сессия, начинавшаяся с этого файла, получала задание чинить починенное.

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
