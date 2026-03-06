# ROADMAP — gitlab-reviewer

> Статус: `✅ done` · `🔄 in progress` · `📋 planned` · `💡 optional`

---

## Текущее состояние (MVP — v0.1)

- ✅ FastAPI webhook receiver (HMAC auth)
- ✅ GitLab API client (fetch diff, post MR note)
- ✅ OpenAI-compatible LLM client (ollama / vllm / llama.cpp)
- ✅ Prompt engine: `{{include:}}` композиция, `sanitize_untrusted()`
- ✅ Встроенные промпты: base, code_review, security, performance, style
- ✅ Dedup-кэш по hash диффа (in-memory)
- ✅ Фильтры: draft, whitelist авторов/проектов, лимит файлов
- ✅ Dry-run режим
- ✅ Dockerfile (multi-stage), docker-compose, Helm chart

---

## Phase 1 — Web UI foundation (v0.2)

**Цель:** управлять сервисом через браузер, без редактирования файлов вручную.

### 1.1 Сервинг Web UI из FastAPI

- [ ] Статические файлы из `src/ui/static/` → route `/ui/`
- [ ] Стек: **Alpine.js + HTMX** (CDN, без сборки), Tailwind CSS (CDN)
- [ ] Структура: одностраничное приложение с вкладками

```
src/ui/
  static/
    index.html       — оболочка SPA, Alpine.js + HTMX
    components/      — переиспользуемые фрагменты (navbar, cards)
  router.py          — FastAPI роуты для UI (GET /ui/, статика)
```

### 1.2 Config API

```
GET  /api/v1/config          → текущий конфиг (секреты замаскированы: ****)
PUT  /api/v1/config          → обновить + записать config.yml на диск
POST /api/v1/config/reload   → горячий рестарт (перечитать config.yml без остановки)
GET  /api/v1/config/schema   → JSON Schema для валидации в UI
```

- config.yml становится **единым источником истины** — env vars переопределяют только секреты
- При записи: атомарный write (temp file → rename), backup предыдущей версии

### 1.3 Управление LLM-провайдерами

**Новая секция config.yml:**
```yaml
providers:
  - id: ollama-local
    name: "Local Ollama"
    type: ollama          # ollama | llamacpp | openai_compat
    url: http://localhost:11434
    api_key: ""           # опционально, для openai_compat
    active: true
  - id: llamacpp-server
    name: "llama.cpp HTTP"
    type: llamacpp
    url: http://localhost:8080
    active: false

model:
  provider_id: ollama-local
  name: qwen2.5-coder:32b
  temperature: 0.2
  context_size: null    # null = дефолт модели
  max_tokens: 4096
```

**API:**
```
GET    /api/v1/providers                         → список провайдеров
POST   /api/v1/providers                         → добавить
PUT    /api/v1/providers/{id}                    → обновить
DELETE /api/v1/providers/{id}                    → удалить
POST   /api/v1/providers/{id}/test               → тест соединения + версия
GET    /api/v1/providers/{id}/models             → список моделей у провайдера (live запрос)
GET    /api/v1/providers/{id}/models/{name}/info → детали модели (context window, params)
```

**UI — вкладка "Providers":**
- Список провайдеров (имя, тип, URL, статус online/offline)
- Добавить провайдер (форма: тип → URL → тест)
- Для активного провайдера: дропдаун с моделями (подгружаются через API, не руками)
- Слайдеры: temperature, context_size, max_tokens (с дефолтами от модели)
- Кнопка "Save & Apply"

**Разрешение моделей по типу провайдера:**
```
ollama:         GET /api/tags               → .models[].name
llama.cpp:      GET /v1/models              → .data[].id
openai_compat:  GET /v1/models              → .data[].id (+ api_key header)
```

---

## Phase 2 — Логи и история ревью (v0.3)

### 2.1 Live логи в UI

- [ ] In-memory circular buffer (последние N строк, настраивается)
- [ ] Кастомный logging handler → пишет в буфер
- [ ] WebSocket endpoint: `GET /ws/logs`
  - Client подключается → получает backlog последних N строк → затем stream новых
- [ ] UI — вкладка "Logs":
  - Окно терминала (monospace), авто-скролл
  - Фильтр по уровню (INFO / WARNING / ERROR)
  - Кнопка "Clear", toggle "Follow"
  - Цветовая подсветка по уровню

### 2.2 История ревью

- [ ] `ReviewRecord` — модель: id, project_id, mr_iid, mr_title, author, timestamp, diff_hash, review_text, status (skipped/posted/error), skip_reason
- [ ] Хранилище: **SQLite** через `aiosqlite` (простой вариант, без отдельного сервиса)
- [ ] API:
  ```
  GET  /api/v1/reviews               → список (пагинация, фильтр по проекту/статусу)
  GET  /api/v1/reviews/{id}          → детали (полный текст ревью, diff hash)
  ```
- [ ] UI — вкладка "Reviews":
  - Таблица: время, проект, MR, автор, статус
  - Клик → открывает полный текст ревью
  - Фильтры и пагинация

---

## Phase 3 — GitLab конфигурация через UI (v0.4)

### 3.1 Настройки соединения с GitLab

**Новая секция config.yml:**
```yaml
gitlab:
  url: https://gitlab.example.com
  auth_type: token          # token | basic
  token: ""                 # или из env GLR_GITLAB_TOKEN (секрет)
  username: ""              # для auth_type: basic
  # password — только через env GLR_GITLAB_PASSWORD, никогда в файле
  webhook_secret: ""        # или из env GLR_WEBHOOK_SECRET
  tls_verify: true          # false для self-signed сертификатов
```

**API:**
```
POST /api/v1/gitlab/test              → тест соединения (version + current_user)
GET  /api/v1/gitlab/groups            → список групп (с пагинацией)
GET  /api/v1/gitlab/groups/{id}/projects → проекты в группе
GET  /api/v1/gitlab/projects          → список проектов (доступных токену)
GET  /api/v1/gitlab/projects/{id}/branches → ветки проекта (с флагом protected)
```

**UI — вкладка "GitLab":**
- Форма: URL, тип авторизации (токен / логин+пароль), поле токена/пароля (input type=password)
- Кнопка "Test Connection" → показывает версию GitLab и текущего пользователя
- TLS verify toggle (для self-signed)

### 3.2 Цели ревью (Review Targets)

**Расширенная секция config.yml:**
```yaml
review_targets:
  - type: group             # group | project | all
    id: "mygroup"           # namespace/path или числовой id
    branches:
      pattern: "main,develop,release/*"  # glob, запятая = OR
      protected_only: false
    auto_approve: false
    prompts:
      system: [base, security]   # переопределить промпты для этой цели
  - type: project
    id: "123"
    branches:
      pattern: "*"
      protected_only: true
    auto_approve: true
```

**UI — вкладка "Review Targets":**
- Список целей с кнопками Add / Edit / Delete
- Форма добавления:
  - Тип: Group / Project / All
  - Для Group: дропдаун из `/api/v1/gitlab/groups`
  - Для Project: поиск из `/api/v1/gitlab/projects`
  - Ветки: input glob + checkbox "Only protected", picker веток из API
  - Toggle "Auto-approve when review passes"
  - Переопределение промптов (мультиселект)

---

## Phase 4 — Очередь и конкурентность (v0.5)

### 4.1 Review Queue (in-memory, simple)

- [ ] `QueueManager` — обёртка над `asyncio.Queue` + `asyncio.Semaphore`
- [ ] Настройка в config.yml:
  ```yaml
  queue:
    backend: memory
    max_concurrent: 3          # сколько ревью одновременно
    max_queue_size: 100        # отбросить если очередь переполнена
  ```
- [ ] Webhook handler → `QueueManager.enqueue(project_id, mr_iid, event_data)`
- [ ] Worker-пул: N воркеров из `asyncio.gather`, каждый дёргает из очереди
- [ ] Dedup до постановки в очередь: если diff_hash в кэше → skip
- [ ] UI — вкладка "Queue":
  - Счётчики: ожидание / выполняется / завершено / ошибки
  - Live обновление (polling каждые 2s или WebSocket)
  - Список активных ревью (project, MR, сколько ждёт)

**API:**
```
GET  /api/v1/queue           → { pending: N, active: N, done: N, errors: N }
POST /api/v1/queue/drain     → отменить все ожидающие (graceful)
```

### 4.2 Улучшенный dedup-кэш

- [ ] Хранить: `(project_id, mr_iid, diff_hash)` → `timestamp`
- [ ] Не только по diff_hash — учитывать project_id + mr_iid (разные MR могут иметь одинаковый diff)
- [ ] Персистентный кэш в SQLite (та же БД что история ревью)

---

## Phase 5 — Внешние бэкенды (v0.6, опционально)

> Нужно только при: несколько инстансов сервиса, или объём > 100 MR/час

### 5.1 Valkey (Redis-compatible) как очередь и кэш

- [ ] Абстракция `QueueBackend` + `CacheBackend` (интерфейс)
- [ ] Реализации: `MemoryQueue` / `ValkeyQueue`, `MemoryCache` / `ValkeyCache`
- [ ] config.yml:
  ```yaml
  queue:
    backend: valkey
    max_concurrent: 5
    valkey_url: redis://localhost:6379
    valkey_queue_key: glr:queue
  cache:
    backend: valkey
    valkey_url: redis://localhost:6379
    valkey_key_prefix: glr:diff:
    ttl: 3600
  ```
- [ ] Valkey-очередь: `LPUSH` при постановке, `BRPOP` в воркере (блокирующий pop)
- [ ] Distributed lock (SETNX) для защиты от двойного ревью при нескольких инстансах
- [ ] docker-compose добавляет Valkey service (опционально через profile)

### 5.2 Kafka (💡 optional, high-volume)

> Только если > 1000 MR/день и нужна надёжная доставка с ретраями

- [ ] Webhook → publish event to Kafka topic `gitlab.mr.events`
- [ ] Consumer group читает из Kafka, fanout по инстансам
- [ ] Библиотека: `aiokafka`

---

## Архитектура итоговой конфигурации

### config.yml (полная схема)

```yaml
providers:
  - id: string               # уникальный id
    name: string             # отображаемое имя
    type: ollama|llamacpp|openai_compat
    url: string              # base URL
    api_key: string          # опционально
    active: bool

model:
  provider_id: string
  name: string               # имя модели у провайдера
  temperature: float         # 0.0–2.0
  context_size: int|null     # null = дефолт модели
  max_tokens: int

gitlab:
  url: string
  auth_type: token|basic
  # token/password → только env vars (GLR_GITLAB_TOKEN, GLR_GITLAB_PASSWORD)
  tls_verify: bool
  webhook_secret: ""         # → env GLR_WEBHOOK_SECRET

review_targets:
  - type: group|project|all
    id: string
    branches:
      pattern: string        # glob, comma-separated
      protected_only: bool
    auto_approve: bool
    prompts:
      system: [string]       # переопределение промптов

queue:
  backend: memory|valkey
  max_concurrent: int
  max_queue_size: int
  valkey_url: string

cache:
  backend: memory|valkey
  ttl: int
  valkey_url: string

prompts:
  system: [string]           # глобальные промпты (если не переопределены в target)

ui:
  enabled: bool
  log_buffer_lines: int      # сколько строк хранить в памяти для UI

server:
  host: string
  port: int
  log_level: string
```

### Env vars (секреты, не в файле)

| Переменная | Описание |
|-----------|---------|
| `GLR_GITLAB_TOKEN` | GitLab personal/project token |
| `GLR_GITLAB_PASSWORD` | GitLab пароль (basic auth) |
| `GLR_WEBHOOK_SECRET` | Webhook secret |

---

## Схема компонентов (целевая)

```
                     ┌─────────────────────────────────────────────────────┐
                     │                  gitlab-reviewer                     │
                     │                                                     │
GitLab Webhook ──────┤ POST /webhook/gitlab                                │
                     │    ↓ HMAC verify                                    │
                     │    ↓ check review_targets                           │
                     │    ↓ fetch diff → dedup check                       │
                     │    ↓ enqueue(project_id, mr_iid)                    │
                     │         ↓                                            │
                     │   QueueManager (memory | Valkey)                    │
                     │         ↓ Semaphore(max_concurrent)                 │
                     │   ReviewWorker                                       │
                     │     ↓ fetch full diff                               │
                     │     ↓ sanitize_untrusted()                          │
                     │     ↓ build_user_message()                          │
                     │     ↓ LLMClient.chat(system_prompt, user_msg)       │
                     │     ↓ post comment / auto-approve                   │
                     │     ↓ save ReviewRecord → SQLite                    │
                     │                                                     │
Browser ─────────────┤ GET /ui/                                            │
                     │   Providers tab → GET /api/v1/providers             │
                     │   Model picker  → GET /api/v1/providers/{id}/models │
                     │   GitLab tab    → POST /api/v1/gitlab/test          │
                     │   Targets tab   → GET /api/v1/gitlab/groups         │
                     │   Logs tab      → WS /ws/logs                       │
                     │   Queue tab     → GET /api/v1/queue                 │
                     └─────────────────────────────────────────────────────┘
                                          │
                              ┌───────────┴───────────┐
                        Ollama / llama.cpp         Valkey (opt)
                        (LLM backend)              (queue + cache)
```

---

## Выбор бэкенда очереди

| Бэкенд | Когда использовать | Конфиг |
|--------|-------------------|--------|
| `memory` | Dev/single instance, <50 MR/день | `queue.backend: memory` |
| `valkey` | Prod multi-instance, <1000 MR/день | `queue.backend: valkey` + profile |
| `kafka` | High-volume, >1000 MR/день, audit trail | `queue.backend: kafka` + profile |

---

## Версии и приоритеты

| Версия | Фаза | Ключевые фичи | Приоритет |
|--------|------|--------------|-----------|
| v0.1 | MVP | Webhook, LLM, промпты, dedup | ✅ Done |
| v0.2 | Web UI | Провайдеры, модели, config API | ✅ Done |
| v0.3 | Logs+History | Live логи WS ✅, история ревью SQLite ✅ | ✅ Done |
| v0.4 | GitLab UI | Соединение ✅, группы/проекты/ветки ✅, auto-approve ✅ | ✅ Done |
| v0.5 | Queue+Inline | Конкурентность ✅, queue status ✅, dedup persistence ✅ | ✅ Done |
| v0.5b | Inline Comments | GitLab Discussion API, parse_review_sections, fallback | ✅ Done |
| v0.5c | Filters | Branch pattern (glob, comma-sep), author allowlist/skip | ✅ Done |
| v0.5d | Targets API + Retry | CRUD /api/v1/targets, POST /reviews/{id}/retry, UI Targets tab | ✅ Done |
| v0.6 | Observability | GET /metrics (Prometheus), GET /health, queue gauge instrumentation | ✅ Done |
| v0.7 | CI + Notifications | Blocking ruff+pytest CI, Slack/Telegram/Generic notifications UI | ✅ Done |
| v0.7b | Notifications UI | Web UI tab + /api/v1/notifications/test endpoint | ✅ Done |
| v0.7c | File Filters | file_exclude globs (global + per-target), vendor/lock defaults | ✅ Done |
| v0.7d | Cooldown | review_cooldown_minutes per MR, per-target override | ✅ Done |
| v0.8 | Debounce + Dedup | Latest-wins supersede, diff hash dedup in reviewer, delayed retry | ✅ Done |
| v0.9 | Valkey | Distributed queue+cache, multi-instance | ✅ Done |
| v0.10 | Kafka | High-volume event streaming (aiokafka, KRaft docker-compose profile) | ✅ Done |
| v0.11 | Qdrant Memory | MemoryStore (vector search), MemoryConfig, integration in review_job_v2 | ✅ Done |
| v0.11 | docker-compose v2 | Qdrant + llama-server profiles, docker-compose.full.yml full stack | ✅ Done |

---

## Следующие шаги (v0.12+)

- [x] **Per-role model config** — назначить разную LLM-модель каждой роли pipeline_v2 (feat/v2-per-role-model)
- [ ] **Memory UI** — вкладка "Memory" в Web UI: просмотр/удаление паттернов по проекту
- [ ] **Memory seeding** — при первом старте залить историю из SQLite в Qdrant (bootstrap)
- [ ] **REVIEW_HISTORY category** — сохранять историю ревью по файлу (не только паттерны)
- [ ] **Inline recall** — при ревью файла X, recall истории именно этого файла (`query=file_path`)
- [ ] **llama-server GPU profiles** — отдельные compose профили для CUDA/ROCm/Vulkan
- [ ] **Helm chart обновление** — добавить Qdrant sidecar/dependency в values.yaml
