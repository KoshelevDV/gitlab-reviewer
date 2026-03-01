# AGENTS.md — gitlab-reviewer

## What is this
Standalone Python FastAPI service — automated GitLab MR code review using local LLMs.
Receives GitLab webhooks → fetches diff → sanitises input → calls local LLM → posts
structured review comment to MR. Managed entirely through a Web UI.

## Stack
- Python 3.11+, FastAPI, uvicorn, httpx, pydantic-settings, PyYAML
- **Web UI:** Alpine.js + HTMX (CDN, no build step), Tailwind CSS (CDN)
- **LLM backends:** ollama, llama.cpp HTTP server, any OpenAI-compatible endpoint
- **Recommended model:** `qwen2.5-coder:32b` (Q4_K_M, ~20GB)
- **Storage:** SQLite via aiosqlite (review history + persistent dedup cache)
- **Queue backends:** memory (default) · valkey/redis (`redis>=4.2`) · kafka (`aiokafka>=0.10`)
- Dockerfile multi-stage, docker-compose (profiles: valkey, kafka), Helm chart

## Structure (current + planned)

```
src/
  config.py           — Settings (pydantic-settings), config.yml loader + writer
  prompt_engine.py    — Prompt loading, {{include:}} resolution, sanitize_untrusted()
  gitlab_client.py    — GitLab API: get_mr, get_diffs, post_mr_note, list groups/projects/branches
  llm_client.py       — OpenAI-compat chat (ollama / llama.cpp / openai_compat), model listing
  reviewer.py         — Orchestration: filter → sanitise → LLM → comment → persist
  webhook.py          — FastAPI routes, HMAC check, enqueue (not BackgroundTask anymore)
  queue_manager.py    — QueueManager: asyncio.Queue + Semaphore, dedup, stats
  db.py               — SQLite via aiosqlite: ReviewRecord, CRUD, stats, recent
  ui/
    router.py         — FastAPI routes for UI static files                           [PLANNED v0.2]
    static/
      index.html      — SPA shell (Alpine.js + HTMX)
      components/     — navbar, cards, log viewer
  api/
    config.py         — /api/v1/config CRUD                                         [PLANNED v0.2]
    providers.py      — /api/v1/providers CRUD + model listing                      [PLANNED v0.2]
    gitlab.py         — /api/v1/gitlab test/groups/projects/branches                [PLANNED v0.4]
    reviews.py        — /api/v1/reviews (list, stats, recent, get by id)
    queue_api.py      — /api/v1/queue status + drain
    logs_api.py       — GET /api/v1/logs + WebSocket /ws/logs
  main.py             — App factory, DI wiring, uvicorn entrypoint

prompts/
  system/             — Built-in prompts (version-controlled)
    base.md           — MUST BE FIRST: role + anti-injection rules + includes code_review
    code_review.md    — Output format and review principles
    security.md       — Security checks (injection, auth, crypto, secrets)
    performance.md    — Performance checks (N+1, O(n²), async blocking)
    style.md          — Style / maintainability
  custom/             — User overrides (gitignored; custom/ wins over system/)
    example_team.md   — Template for team-specific rules

config.yml            — Single source of truth (see schema below)
.env                  — Secrets only (GITLAB_TOKEN, GITLAB_PASSWORD, WEBHOOK_SECRET)
ROADMAP.md            — Phased feature plan
```

## config.yml Full Schema

```yaml
providers:
  - id: string              # unique id
    name: string            # display name
    type: ollama|llamacpp|openai_compat
    url: string             # base URL
    api_key: ""             # optional
    active: true

model:
  provider_id: string
  name: string              # model name at the provider
  temperature: 0.2
  context_size: null        # null = model default
  max_tokens: 4096

gitlab:
  url: https://gitlab.example.com
  auth_type: token|basic
  # Secrets → env GLR_GITLAB_TOKEN / GLR_GITLAB_PASSWORD only (never in file)
  tls_verify: true
  webhook_secret: ""        # → env GLR_WEBHOOK_SECRET

review_targets:
  - type: group|project|all
    id: string
    branches:
      pattern: "main,develop"   # glob, comma = OR
      protected_only: false
    auto_approve: false
    prompts:
      system: [base, security]  # optional per-target override

queue:
  backend: memory|valkey|kafka
  max_concurrent: 3
  max_queue_size: 100
  valkey_url: redis://localhost:6379        # Valkey backend
  kafka_brokers: localhost:9092             # Kafka backend (comma-separated)
  kafka_topic: glr.mr.events               # Kafka backend
  kafka_group_id: glr-reviewers            # Kafka backend

cache:
  backend: memory|valkey
  ttl: 3600
  valkey_url: redis://localhost:6379

prompts:
  system: [base, security]   # global default

ui:
  enabled: true
  log_buffer_lines: 1000

server:
  host: 0.0.0.0
  port: 8000
  log_level: info
```

## Development Rules

### Injection Prevention (non-negotiable)
- System prompt assembled from files at startup — **immutable per request**
- Diff / MR title / description / author → ALWAYS in the **user** message turn, never system
- Call `prompt_engine.sanitize_untrusted()` on **every** field arriving from GitLab
- `base.md` MUST be listed first in any prompt configuration

### Config Management
- `config.yml` is the single source of truth
- Secrets (tokens, passwords) → env vars only, never written to config.yml
- Config writes: atomic (write temp → rename) + backup previous version
- Hot reload must not drop in-flight reviews

### Queue
- Webhook handler → `QueueManager.enqueue()` only — never run review inline
- Dedup check before enqueue: `(project_id, mr_iid, diff_hash)` → skip if cached
- `max_concurrent` controls the Semaphore — never spawn unbounded tasks

### Web UI
- Alpine.js + HTMX only — no npm, no build step
- All config changes go through `/api/v1/config` (validate → write → reload)
- Secrets are never returned by the API (masked as `****`)
- Log viewer: WebSocket `/ws/logs`, monospace, colour-coded by level

### API versioning
- All new endpoints under `/api/v1/`
- Webhook stays at `/webhook/gitlab` (no version prefix — GitLab-configured URL)

## Provider Model Resolution

```python
# ollama
GET /api/tags            → .models[].name
# llama.cpp / openai_compat
GET /v1/models           → .data[].id
# Model info (context window)
# ollama: GET /api/show → .model_info."llama.context_length"
# llama.cpp: GET /v1/models/{id} or props endpoint
```

## Tests

```bash
pip install -e ".[test]"
pytest tests/ -q               # run all tests
pytest tests/ -v               # verbose
pytest tests/ --cov=src --cov-report=term-missing   # with coverage
```

### Test structure

```
tests/
  conftest.py                 — shared fixtures (isolate config, db, app, prompts_dir)
  test_prompt_engine.py       — PromptEngine: sanitize_untrusted (18 injection cases),
                                {{include:}} resolution, circular include, custom override
  test_db.py                  — Database: CRUD, filters, pagination, stats, recent
  test_queue_manager.py       — QueueManager: enqueue, dedup, concurrency, error counter
  test_gitlab_client.py       — GitLabClient: mock httpx via respx; MR info, diffs,
                                groups, branches, approve, post_note
  test_llm_client.py          — LLMClient: chat (system/user turns), fallback to /api/chat,
                                model listing (ollama/llamacpp), api_key, model info
  test_reviewer.py            — Reviewer: draft filter, empty diffs, happy path, auto-approve
                                (CRITICAL/HIGH blocking), error handling, db persistence
  test_webhook.py             — Webhook: HMAC auth, event filtering, payload validation
  test_api/
    test_config_api.py        — /api/v1/config: read, write, secret masking, reload, schema
    test_providers_api.py     — /api/v1/providers: CRUD, test connection, model listing
    test_reviews_api.py       — /api/v1/reviews: list, filter, paginate, stats, recent, get by id
```

**154 tests, ~1.5s** — all pass on Python 3.11 and 3.12.

### Key testing decisions
- `respx` mocks all httpx calls (no real network in tests)
- `aiosqlite` with temp file — real SQLite, no mocking of the DB layer
- App fixture wires singletons directly (not via lifespan) because `ASGITransport` in httpx
  does not trigger ASGI lifespan startup events
- `_isolate_config` autouse fixture — each test gets its own temp `config.yml`
- `pytest-asyncio` in `auto` mode — all async test functions work without decorators

## How to run

```bash
cp .env.example .env
# Fill: GLR_GITLAB_TOKEN, GLR_WEBHOOK_SECRET

pip install -e .
python -m uvicorn src.main:create_app --factory --reload

# Or docker
docker compose up -d
```

GitLab webhook:
- URL: `http://server:8000/webhook/gitlab`
- Secret: `GLR_WEBHOOK_SECRET`
- Trigger: Merge request events

After v0.2: open `http://server:8000/ui/` to configure everything.

## Pitfalls

- `base.md` MUST be first in prompt lists — contains anti-injection instructions
- ollama `/v1/chat/completions` requires ollama ≥ 0.1.24; older → native `/api/chat` (auto-fallback)
- LLM timeout: 300s+ for large diffs on CPU/iGPU
- `context_size` must fit both the system prompt AND the diff — don't set it below 8192
- Atomic config write: write to `.config.yml.tmp` then `os.rename()` — avoids corrupt config on crash
- Valkey dedup: in-memory per-instance, seeded from DB at startup — NOT cross-instance; upgrade to SET NX EX for full cross-instance dedup
- Kafka supersede: works per-partition (same MR → same partition → same consumer) — reliable without Redis
- `_delayed_requeue` tasks must be tracked in `Reviewer._requeue_tasks` and cancelled at shutdown via `reviewer.cancel_pending()`
- Health check MUST be from `api/health.py` (full DB+queue+config checks) — don't use the old webhook stub
- `save_config()` strips `notifications.telegram_bot_token/chat_id/webhook_url` — they come from env-vars only
- `tls_verify=False` → passed to `httpx.AsyncClient(verify=False)` in `GitLabClient`

## Status

| Phase | Version | Status |
|-------|---------|--------|
| MVP: webhook + LLM + prompts | v0.1 | ✅ Done |
| Web UI: providers, models, config API | v0.2 | ✅ Done |
| Live logs WebSocket (`/ws/logs`) | v0.2 | ✅ Done |
| GitLab browse API (groups/projects/branches) | v0.2 | ✅ Done |
| Queue + concurrency (asyncio.Queue + Semaphore) | v0.2 | ✅ Done |
| Review history SQLite (`db.py`, `/api/v1/reviews`) | v0.3 | ✅ Done |
| Reviews tab in Web UI (table, modal, stats, pagination) | v0.3 | ✅ Done |
| Auto-approve via GitLab API (CRITICAL/HIGH check) | v0.4 | ✅ Done |
| Dashboard — review stats + recent history | v0.4 | ✅ Done |
| docker-compose Valkey profile | v0.4 | ✅ Done |
| Valkey distributed queue + cache backend | v0.9 | ✅ Done |
| Kafka high-volume distributed queue backend | v0.10 | ✅ Done |
| Code review / bug fixes (7 bugs fixed, see docs/CODE_REVIEW.md) | v0.10.1 | ✅ Done |
