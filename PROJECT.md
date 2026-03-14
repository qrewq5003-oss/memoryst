 # Идея

Проект — это внешний memory service для SillyTavern, рассчитанный в первую очередь на личное использование в длинных ролевых и сюжетных чатах. Сервис должен помогать хранить, отбирать и возвращать значимую память: устойчивые факты, динамику отношений, важные события, обещания, конфликты и повторяющиеся темы. Основной клиентский сценарий — SillyTavern на Android, при этом тяжёлая логика памяти выносится на Steam Deck как локальный внешний хост. Первая версия должна быть простой, управляемой и полезной на практике, без декоративного оверинжиниринга и без ложной “подготовки к будущему”.

# Архитектура

## Общая схема

Система состоит из двух основных частей:

1. **Клиент**
   - SillyTavern на Android
   - отвечает за UI, чат и вызовы memory API

2. **Внешний memory service**
   - работает на Steam Deck
   - принимает запросы от клиента
   - хранит память
   - извлекает новые memory items
   - ищет релевантные записи
   - формирует memory block для prompt

## Роли компонентов

### Клиентский слой
Клиент не должен содержать основную memory-логику. Его задача:
- перед генерацией отправлять текущий ввод и недавний контекст на memory service
- получать релевантный memory block
- после обмена сообщениями отправлять новый кусок диалога на сохранение

### Memory service
Memory service отвечает за:
- приём и обработку запросов
- rule-based extraction
- дедупликацию
- хранение памяти
- retrieval и scoring
- ручное редактирование памяти
- форматирование памяти для prompt

## Внутренние модули сервиса

### API layer
Минимальный набор endpoint'ов:
- `POST /memory/retrieve`
- `POST /memory/store`
- `GET /memory/list`
- `PATCH /memory/{id}`
- `POST /memory/{id}/pin`
- `POST /memory/{id}/archive`
- `DELETE /memory/{id}`

### Domain layer
Основные модули:
- `extractor`
- `deduper`
- `retriever`
- `scorer`
- `formatter`
- `editor`

Их задача — разделять ответственность, а не создавать искусственную сложность.

### Storage layer
На первом этапе используется SQLite как основной источник истины.

Основная сущность:
- `Memory`

Поля памяти:
- `id`
- `chat_id`
- `character_id`
- `type`
- `content`
- `normalized_content`
- `importance`
- `created_at`
- `updated_at`
- `last_accessed_at`
- `access_count`
- `pinned`
- `archived`
- `metadata_json`

## Типы памяти в v1
- `profile`
- `relationship`
- `event`

## Retrieval в v1
Без embeddings и без vector DB. Поиск строится на:
- keyword overlap
- entity overlap
- importance
- recency
- pinned boost

## Ручное управление памятью
В первой версии поддерживаются:
- просмотр списка записей
- редактирование текста
- изменение типа
- pin / unpin
- archive / unarchive
- delete
- ручное создание записи

## Концептуальная модель памяти

Несмотря на то, что в v1 используется единая operational model и единая таблица памяти, архитектурно память рассматривается как **концептуальная модель из трёх смысловых слоёв**:

1. **Episodic**
   - конкретные события, сцены, эпизоды

2. **Stable**
   - устойчивые факты, предпочтения, отношения, повторяющиеся значимые состояния

3. **Aggregated**
   - будущие summaries, сводки и уплотнённые представления длинной истории

Эта модель фиксируется как принцип проектирования, чтобы не смешивать сырые события, стабильные факты и агрегированные представления в одну неразличимую массу. В v1 она не обязана выражаться отдельными таблицами или отдельной подсистемой.

# Текущее состояние

Реализованы этапы 1–5. Система полностью функциональна.

**Реализованный стек:**
- Python + FastAPI
- SQLite (через sqlite3)
- Jinja2 templates + встроенный web UI
- Pydantic v2 schemas

**Структура проекта:**
```
app/
  main.py              # FastAPI app, роутеры, static
  config.py            # конфигурация через env
  db.py                # SQLite подключение и init
  schemas.py           # Pydantic модели
  repositories/
    memory_repo.py     # CRUD операции
  services/
    extractor.py       # rule-based extraction (RU/EN)
    formatter.py       # форматирование memory block
    store_service.py   # store logic с дедупликацией
    retrieve_service.py # retrieval scoring
  routes/
    memory_api.py      # API endpoints
    ui.py              # web UI endpoints
  templates/
    base.html
    memories.html
  static/
    styles.css
```

**API endpoints:**
- `GET /health` — health check
- `POST /memory/create` — создать запись
- `GET /memory/list` — список с фильтрами
- `GET /memory/{id}` — получить по ID
- `PATCH /memory/{id}` — обновить
- `POST /memory/{id}/pin` — pin/unpin
- `POST /memory/{id}/archive` — archive/unarchive
- `DELETE /memory/{id}` — удалить
- `POST /memory/store` — store из сообщений (auto-extract)
- `POST /memory/retrieve` — retrieve релевантных записей

**Web UI:**
- `/ui` — просмотр и фильтрация памяти
- Create/Edit/Delete формы
- Pin/unpin, archive/unarchive кнопки
- Встроен в сервис (не отдельный frontend)

**Services:**
- `extractor` — rule-based, русские + английские маркеры
- `formatter` — форматирование memory block
- `store_service` — extract + dedup + store
- `retrieve_service` — scoring (keyword/entity overlap, importance, recency, pinned floor)

**Схема БД:**
- Таблица `memories` со всеми индексами
- Типы: profile, relationship, event
- Слои: episodic, stable
- Источники: auto, manual

**Решения:**
- memory service — внешний HTTP-сервис
- SQLite как основное хранилище
- extraction в v1 — rule-based (без LLM)
- retrieval — scoring без embeddings/vector DB
- встроенный web UI для ручного управления
- трёхслойная концептуальная модель (episodic, stable, aggregated)

# Изменения

- 2026-03-14
  Сформирована базовая идея внешнего memory service для SillyTavern с разделением ролей между Android-клиентом и Steam Deck как внешним локальным хостом.

- 2026-03-14
  Зафиксировано, что v1 использует внешний HTTP API, SQLite, rule-based extraction и ручное управление памятью.

- 2026-03-14
  Подтверждено, что будущие направления расширения (vector DB, embeddings, graph memory, summaries, emotional markers) не входят в MVP, но должны учитываться на уровне чистых модульных границ.

- 2026-03-14
  Исключены `MemoryRelation` и `MemoryState` как пустые таблицы без текущей логики. Вместо этого оставлен `metadata_json` и зафиксирован принцип расширяемой архитектуры без декоративного каркаса.

- 2026-03-14
  Добавлена концептуальная модель памяти из трёх слоёв: episodic, stable, aggregated.

- 2026-03-14
  **Этап 1:** Инфраструктура проекта и инициализация SQLite.
  - app/main.py, app/config.py, app/db.py
  - requirements.txt, README.md
  - Таблица memories со всеми индексами
  - GET /health endpoint

- 2026-03-14
  **Этап 2:** Schemas и repository.
  - app/schemas.py — Pydantic модели
  - app/repositories/memory_repo.py — CRUD операции
  - Все request/response схемы

- 2026-03-14
  **Этап 3:** API routes.
  - app/routes/memory_api.py — CRUD endpoints
  - POST /memory/create, GET /memory/list, GET /memory/{id}, PATCH /memory/{id}
  - POST /memory/{id}/pin, POST /memory/{id}/archive, DELETE /memory/{id}

- 2026-03-14
  **Этап 4:** Store и retrieve services.
  - app/services/extractor.py — rule-based extraction (RU/EN маркеры)
  - app/services/formatter.py — форматирование memory block
  - app/services/store_service.py — extract + dedup + store
  - app/services/retrieve_service.py — scoring (keyword/entity overlap, importance, recency, pinned floor)
  - POST /memory/store, POST /memory/retrieve endpoints

- 2026-03-14
  **Этап 5:** Встроенный web UI.
  - app/routes/ui.py — UI routes
  - app/templates/base.html, memories.html
  - app/static/styles.css
  - Jinja2 templates, python-multipart
  - UI: просмотр, фильтры, create, edit, pin, archive, delete

# Открытые вопросы

- Нужен ли ручной merge memory-записей уже после базового CRUD, или это задача следующего этапа.
- Каким будет минимальный формат `metadata_json` в v1 (сейчас entities + keywords).
- Как именно нормализовать entities и keywords в первой rule-based версии, чтобы не переусложнить extractor.
- Требуется ли интеграция с SillyTavern как клиентом или UI достаточно для личного использования.

# Следующий шаг

Базовый функционал реализован. Возможные направления развития:

1. **Стабилизация и полировка**
   - Улучшение UI (удобство, pagination)
   - Тесты для critical paths
   - Documentation для API

2. **Улучшение extraction**
   - Более точные rule-based маркеры
   - LLM-assisted extraction (опционально)

3. **Улучшение retrieval**
   - Более сложная scoring формула
   - Entity linking
   - Graph memory (опционально)

4. **Интеграция с SillyTavern**
   - Настройка клиента для вызова API
   - Автоматический store/retrieve цикл

5. **Дополнительные возможности**
   - Merge memory записей
   - Summaries для длинной истории
   - Emotional markers (опционально)
