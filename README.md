# gitlab-reviewer

Automated GitLab MR code reviewer powered by **local LLMs** (ollama, llama.cpp, or any OpenAI-compatible endpoint).  
No data leaves your infrastructure. Everything configured through a **Web UI**.

---

## Features

- 🤖 Automatic review on every MR open / update
- 🌐 **Web UI** — manage providers, models, GitLab targets, view logs (v0.2+)
- 🔌 **Multi-provider** — ollama, llama.cpp HTTP server, any OpenAI-compatible API
- 📋 **Model picker** — browse models from provider, no manual typing
- 🎛️ Model settings — temperature, context size, max tokens (tunable in UI)
- 🛡️ Prompt injection prevention — diff never touches the system prompt
- 📝 Composable prompts — split by concern, assemble with `{{include:}}`
- 🗂️ Review targets — select groups, projects, branches; auto-approve option
- 🔒 Webhook HMAC verification
- ⚡ **3 queue backends** — `memory` (default), `valkey` (multi-instance), `kafka` (high-volume)
- 🔁 Dedup cache — no duplicate comments on identical diffs
- 📜 Live logs in browser + review history
- 💬 **Inline diff comments** — review posted as positioned discussions (v0.5+)
- 🔔 **Notifications** — Slack, Telegram, or generic JSON webhook (v0.6+)
- 🧪 Dry-run mode
- 🐳 Docker Compose (profiles: `valkey`, `kafka`) + Helm chart
- 💾 Config file as single source of truth — every UI action writes to `config.yml`
- 📊 **Risk Score** — deterministic 0-100 MR risk badge (no extra LLM call)
- 📝 **Walkthrough Summary** — 3-5 sentence MR overview before inline comments
- 🔄 **Incremental review** — only reviews changed files since last version (MR Versions API)
- 🌍 **Language-aware prompts** — auto-detects Python/Rust/TypeScript/Go, applies specific guidelines
- 💬 **Slash commands** — post `/ask`, `/improve`, `/summary`, `/help` in MR comments
- 📡 **SSE streaming** — watch the review generate in real time (`stream=true`)
- 📍 **Accurate inline placement** — diff line map ensures comments land on exact code lines; auto-snaps off comment-only lines to the next code statement
- ⏳ **Processing status** — `status=processing` record created before LLM call; visible in `/api/v1/queue` and Web UI
- 🛡️ **In-flight dedup** — prevents duplicate reviews when two webhook events arrive before the first review completes
- 🎨 **Redesigned Web UI** — consistent design system (CSS custom properties, GitHub-inspired dark palette, WCAG-compliant contrast, risk score visualization)

---

## Quick Start (v0.1 — config file)

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env — set GLR_GITLAB_TOKEN, GLR_WEBHOOK_SECRET

# 2. Pull the recommended model
ollama pull qwen2.5-coder:32b

# 3. Start
docker compose up -d
```

Configure a GitLab webhook:
- **URL:** `http://your-server:8000/webhook/gitlab`
- **Secret:** value of `GLR_WEBHOOK_SECRET`
- **Trigger:** Merge request events

> Starting from **v0.2**, open `http://your-server:8000/ui/` to configure everything visually.

---

## LLM Providers

The service supports any **OpenAI-compatible** endpoint. Configure in `config.yml` or Web UI.

### Supported provider types

| Type | Software | Model list endpoint |
|------|----------|-------------------|
| `ollama` | [Ollama](https://ollama.ai) | `GET /api/tags` |
| `llamacpp` | [llama.cpp HTTP server](https://github.com/ggml-org/llama.cpp/tree/master/tools/server) | `GET /v1/models` |
| `openai_compat` | vllm, LM Studio, etc. | `GET /v1/models` |

### Recommended models

| Model | Size (Q4) | Notes |
|-------|-----------|-------|
| `qwen2.5-coder:32b` | ~20 GB | Best balance — code-specialist, fast |
| `qwen2.5-coder:72b` | ~45 GB | Maximum quality |
| `deepseek-r1:32b` | ~20 GB | Deep reasoning, detailed explanations |
| `codestral:22b` | ~14 GB | Fast, good for high-volume |

---

## Configuration

### config.yml (full reference)

```yaml
providers:
  - id: ollama-local
    name: "Local Ollama"
    type: ollama              # ollama | llamacpp | openai_compat
    url: http://localhost:11434
    api_key: ""
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
  context_size: null          # null = model default
  max_tokens: 4096

gitlab:
  url: https://gitlab.example.com
  auth_type: token            # token | basic
  # Secrets → env vars only: GLR_GITLAB_TOKEN, GLR_GITLAB_PASSWORD
  tls_verify: true
  webhook_secret: ""          # → env GLR_WEBHOOK_SECRET

review_targets:
  - type: group               # group | project | all
    id: "mygroup"
    branches:
      pattern: "main,develop,release/*"   # glob, comma = OR
      protected_only: false
    auto_approve: false
    prompts:
      system: [base, security]   # optional per-target override
  - type: project
    id: "123"
    branches:
      pattern: "*"
      protected_only: true
    auto_approve: true

queue:
  backend: memory             # memory | valkey
  max_concurrent: 3
  max_queue_size: 100
  valkey_url: redis://localhost:6379

cache:
  backend: memory             # memory | valkey
  ttl: 3600
  valkey_url: redis://localhost:6379

prompts:
  system: [base, security]

ui:
  enabled: true
  log_buffer_lines: 1000

server:
  host: 0.0.0.0
  port: 8000
  log_level: info
```

### Environment variables (secrets only)

| Variable | Description |
|----------|-------------|
| `GLR_GITLAB_TOKEN` | GitLab personal/project token (`api` scope) |
| `GLR_GITLAB_PASSWORD` | GitLab password (basic auth only) |
| `GLR_WEBHOOK_SECRET` | Webhook secret token |

Secrets are **never** written to `config.yml`. All other settings live in the config file.

---

## Prompt System

Prompts live in `prompts/system/` (built-in) and `prompts/custom/` (your overrides).

### Composition via `{{include:}}`

```yaml
# config.yml
prompts:
  system:
    - base         # always first — anti-injection rules (includes code_review)
    - security     # security checks
    # - performance
    # - my_team    # prompts/custom/my_team.md
```

Inside a prompt file:
```markdown
{{include: style}}   ← pulls in prompts/system/style.md
```

Files in `prompts/custom/` override same-named files in `prompts/system/`.

### Anti-Injection Architecture

```
GitLab Webhook
      │
      ▼
sanitize_untrusted(all GitLab fields)   ← strips control tokens, truncates
      │
      ▼
┌─────────────────────────────────────────┐
│  system turn: assembled from files      │  ← immutable per request
│  • Anti-injection rules (from base.md)  │
│  • Review guidelines                    │
├─────────────────────────────────────────┤
│  user turn: structured + sanitised data │  ← diff goes here only
│  • MR metadata (sanitised)              │
│  • Diff content (sanitised, capped)     │
└─────────────────────────────────────────┘
      │
   LLM (ollama / llama.cpp / ...)
      │
  GitLab MR comment
```

---

## Queue & Concurrency

```
Webhook → enqueue(project_id, mr_iid)
               │
         QueueManager (memory | valkey | kafka)
         ├── dedup check (diff_hash cache) → skip if identical
         ├── max_queue_size limit
         └── Semaphore(max_concurrent)
               │
         ReviewWorker × N
               │
         LLM → GitLab comment → persist to history
```

### Queue backends

| Backend | Use case | External dep | Docker profile |
|---------|----------|-------------|----------------|
| `memory` | Dev / single instance, &lt;50 MR/day | None | — |
| `valkey` | Prod multi-instance, &lt;1000 MR/day | `redis>=4.2` | `valkey` |
| `kafka` | High-volume, &gt;1000 MR/day, audit trail | `aiokafka>=0.10` | `kafka` |

**memory** — `asyncio.Queue` + `asyncio.Semaphore`, zero deps.  
**valkey** — `LPUSH`/`BRPOP` via `redis.asyncio`, globally unique job IDs via `INCR`, cross-instance latest-wins supersede.  
**kafka** — `AIOKafkaProducer`/`Consumer`, partition key `project_id:mr_iid` (same MR → same partition → ordered), consumer group for load balancing.

```bash
# Start with Valkey
docker compose --profile valkey up -d

# Start with Kafka (KRaft, no ZooKeeper)
docker compose --profile kafka up -d
```

```yaml
# config.yml
queue:
  backend: valkey          # or kafka
  valkey_url: redis://valkey:6379
  # kafka_brokers: kafka:9092
```

---

## Slash Commands

Post slash commands in any MR comment to interact with the reviewer:

| Command | Description |
|---------|-------------|
| `/ask <question>` | Ask anything about the MR diff |
| `/improve [path]` | Get improvement suggestions (optionally scoped to a file) |
| `/summary` | Generate a concise MR walkthrough |
| `/help` | List available commands |

**Setup:** Enable **Comments** trigger in your GitLab webhook settings (in addition to Merge request events).  
The bot replies as a new MR note within seconds.

**Example:**
```
/ask What's the risk of this database migration?
/improve src/auth/login.py
/summary
```

---

## Streaming Reviews

To watch a review generate in real time:

```bash
# 1. Trigger with stream=true
curl -X POST http://localhost:8000/api/v1/queue/review \
  -H "Content-Type: application/json" \
  -d '{"project_id": 42, "mr_iid": 7, "stream": true}'
# → {"status": "queued", "job_id": 123, "stream_url": "/api/v1/queue/review/123/stream"}

# 2. Connect to SSE stream
curl -N http://localhost:8000/api/v1/queue/review/123/stream
# → data: {"text": "## Code Review\n\n"}
# → data: {"text": "Looking at the changes..."}
# → event: done
# → data: {}
```

---

## Docker

```bash
docker build -t gitlab-reviewer .
docker run -d --env-file .env -p 8000:8000 \
  -v $(pwd)/config.yml:/app/config.yml \
  -v $(pwd)/prompts:/app/prompts:ro \
  gitlab-reviewer
```

## Docker Compose

```bash
docker compose up -d    # starts gitlab-reviewer + ollama
docker compose logs -f
```

## Helm

```bash
helm install gitlab-reviewer ./helm/gitlab-reviewer \
  --set secrets.gitlabToken=glpat-xxx \
  --set secrets.webhookSecret=mysecret \
  --set env.GLR_GITLAB_URL=https://gitlab.example.com \
  --set env.GLR_OLLAMA_URL=http://ollama:11434

helm upgrade gitlab-reviewer ./helm/gitlab-reviewer --reuse-values
```

---

## Roadmap

| Version | Status | What |
|---------|--------|------|
| **v0.1** | ✅ Done | Webhook, LLM, prompts, dedup, docker |
| **v0.2–v0.8** | ✅ Done | Web UI, live logs, review history, auto-approve, inline comments, notifications |
| **v0.9** | ✅ Done | Valkey distributed queue (redis.asyncio, LPUSH/BRPOP, cross-instance supersede) |
| **v0.10** | ✅ Done | Kafka queue (aiokafka, KRaft, consumer groups, partition-keyed ordering) |
| **v0.11** | ✅ Done | Risk Score, Walkthrough Summary, SSE streaming, dry-run, weekly stats, CSV export |
| **v0.12** | ✅ Done | Incremental review, language-aware prompts, slash commands |
| **v0.13** | ✅ Done | In-flight dedup (race condition fix), processing status, GLM local provider, accurate inline placement, UI redesign |

See [ROADMAP.md](ROADMAP.md) and [docs/CODE_REVIEW.md](docs/CODE_REVIEW.md) for full details.

---

---

# gitlab-reviewer (RU)

Автоматическое ревью GitLab MR с помощью **локальной LLM**.  
Данные не покидают вашу инфраструктуру. Всё настраивается через **Web UI**.

## Возможности

- 🤖 Авто-ревью при каждом открытии / обновлении MR
- 🌐 **Web UI** — провайдеры, модели, цели ревью, логи
- 🔌 **Мультипровайдер** — ollama, llama.cpp, любой OpenAI-compatible
- 📋 Выбор модели из списка (без ручного ввода)
- 🎛️ Настройка модели: temperature, context size, max tokens
- 🛡️ Защита от prompt injection
- 📝 Составные промпты с `{{include:}}`
- 🗂️ Цели ревью: группы, проекты, ветки, auto-approve
- ⚡ **3 бэкенда очереди** — `memory`, `valkey`, `kafka`
- 🔁 Дедупликация по hash диффа
- 📊 **Risk Score** — детерминированный скор 0–100 без LLM
- 📝 **Walkthrough Summary** — краткий обзор MR перед комментариями
- 🔄 **Инкрементальное ревью** — только изменения с последней версии
- 🌍 **Language-aware промпты** — автодетект Python/Rust/TS/Go
- 💬 **Slash-команды** — `/ask`, `/improve`, `/summary` прямо в комментарии
- 📡 **SSE стриминг** — наблюдать за генерацией в реальном времени
- 📍 **Точные inline-комментарии** — diff line map; авто-сдвиг с комментарий-строк на код
- ⏳ **Статус processing** — виден в очереди пока идёт генерация
- 🛡️ **In-flight dedup** — защита от дублей при параллельных webhook-событиях
- 🎨 **Переработанный Web UI** — единая дизайн-система, WCAG-совместимый контраст
- 💾 `config.yml` — единый источник истины

## Быстрый старт

```bash
cp .env.example .env
# Заполнить GLR_GITLAB_TOKEN, GLR_WEBHOOK_SECRET

ollama pull qwen2.5-coder:32b
docker compose up -d
```

Webhook в GitLab:
- URL: `http://server:8000/webhook/gitlab`
- Secret: `GLR_WEBHOOK_SECRET`
- Trigger: Merge request events

С версии **v0.2**: `http://server:8000/ui/` — вся настройка через браузер.

## Промпты

Система `{{include:}}` позволяет разбить промпты по смыслу и собрать в один:

```yaml
prompts:
  system:
    - base         # всегда первым — анти-инъекция
    - security     # проверки безопасности
    # - performance
    # - my_team    # prompts/custom/my_team.md (ваши правила)
```

Внутри файла промпта:
```markdown
{{include: style}}   ← подтягивает prompts/system/style.md
```

## Очередь и конкурентность (RU)

```yaml
queue:
  backend: memory    # или valkey / kafka
  max_concurrent: 3  # сколько ревью одновременно
```

| Бэкенд | Когда | Доп. зависимость |
|--------|-------|-----------------|
| `memory` | Dev / один инстанс | — |
| `valkey` | Прод, несколько инстансов | `pip install redis` |
| `kafka` | Высокая нагрузка (>1000 MR/день) | `pip install aiokafka` |

## Дорожная карта

| Версия | Статус | Что сделано |
|--------|--------|-------------|
| v0.1 | ✅ Done | Webhook, LLM, промпты, docker |
| v0.2–0.8 | ✅ Done | Web UI, логи, история, авто-апрув, уведомления |
| v0.9 | ✅ Done | Valkey распределённая очередь |
| v0.10 | ✅ Done | Kafka очередь (KRaft, aiokafka) |
| v0.11 | ✅ Done | Risk Score, Walkthrough Summary, SSE, dry-run, статистика |
| v0.12 | ✅ Done | Инкрементальное ревью, lang-aware промпты, slash-команды |
| v0.13 | ✅ Done | In-flight dedup, статус processing, точные inline-комментарии, новый UI |
