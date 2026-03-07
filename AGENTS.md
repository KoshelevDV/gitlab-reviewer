# AGENTS.md — gitlab-reviewer

## What is this
Standalone Python FastAPI service — automated GitLab MR code review using local LLMs.
Receives GitLab webhooks → fetches diff → sanitises input → calls local LLM → posts
structured review comment to MR. Managed entirely through a Web UI.

## Stack
- Python 3.11+, FastAPI, uvicorn, httpx, pydantic-settings, PyYAML
- **Web UI:** Alpine.js (CDN, no build step), Tailwind CSS (CDN)
  - Design system via CSS custom properties (`--bg`, `--surface`, `--accent`, `--text-1/2/3`)
  - GitHub-inspired dark palette; WCAG AA–compliant contrast on all text
- **LLM backends:** ollama, llama.cpp HTTP server, any OpenAI-compatible endpoint
- **Recommended model:** `qwen2.5-coder:32b` (Q4_K_M, ~20GB)
- **Local tested:** GLM-4.7-Flash-UD-Q4_K_XL via llama-server (AMD GPU Vulkan, port 8080)
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
                        + risk score, walkthrough summary, incremental review, lang detection
                        + SSE streaming (_live_streams, _stream_buffers, register_stream)
                        + _parse_diff_line_map(): unified diff → {new_line: old_line|None}
                        + _build_diff_content_map(): unified diff → {new_line: content}
                        + _annotate_diff_with_line_numbers(): prefixes each diff line with
                          new-file number for LLM ("+  42 | code") — no counting from @@
                        + _is_comment_content(): detects comment-only lines (#, //, /*, *)
                        + in-flight dedup: _in_flight set in QueueManager blocks duplicate
                          enqueue while MR is being processed
                        + processing status: ReviewRecord saved with status='processing'
                          before LLM call; updated on completion via update_review()
  webhook.py          — FastAPI routes, HMAC check, enqueue; Note Hook → slash commands
  slash_commands.py   — /ask, /improve, /summary, /help command parser + executor
  queue_manager.py    — QueueManager: asyncio.Queue + Semaphore, dedup, stats
  db.py               — SQLite via aiosqlite: ReviewRecord (risk_score, mr_version_id),
                        CRUD, stats, recent, get_last_mr_version_id()
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
    rules_api.py      — GET/POST/DELETE /api/v1/rules, GET /api/v1/rules/validate
  rules.py            — Automation Rules engine (FT-6): ActionType, RuleCondition, Rule,
                        RulesConfig, MRContext, RulesEngine, load_rules()
                        Rules evaluated at enqueue time (before diff fetch)
  main.py             — App factory, DI wiring, uvicorn entrypoint

prompts/
  system/             — Built-in prompts (version-controlled)
    base.md           — MUST BE FIRST: role + anti-injection rules + includes code_review
    code_review.md    — Output format and review principles
    security.md       — Security checks (injection, auth, crypto, secrets)
    performance.md    — Performance checks (N+1, O(n²), async blocking)
    style.md          — Style / maintainability
    summary.md        — Walkthrough summary prompt (FT-2)
    lang_python.md    — Python-specific guidelines (FT-4, auto-applied)
    lang_rust.md      — Rust-specific guidelines (FT-4)
    lang_typescript.md — TypeScript/JS-specific guidelines (FT-4)
    lang_go.md        — Go-specific guidelines (FT-4)
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

**501 tests, ~5s** — all pass on Python 3.11 and 3.12.

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
- **Slash commands**: GitLab Note Hook must be enabled separately in webhook config (Triggers → Comments); route is `/webhook/gitlab`
- **SSE streaming**: `register_stream(job_id)` MUST be called BEFORE `enqueue()`, otherwise worker won't find the queue
- **Incremental review**: `get_last_mr_version_id()` queries only `status='posted'` rows — skipped/error reviews don't count
- **Language detection**: threshold is 40% of files — avoids mislabelling polyglot repos
- **PromptEngine init**: accepts `Path | str` — internally converts to Path; old code broke when `str` was passed
- **Route ordering in FastAPI**: literal paths (`/stats/weekly`, `/export.csv`) MUST be registered BEFORE parameterized (`/{review_id}`) — otherwise FastAPI captures them as path params
- **Automation Rules (FT-6)**: `if_files_match` and `if_lines_changed_gt` require diff data not present in webhook payload → conditions with empty `changed_files`/`lines_changed=0` evaluate to False (not True). This is intentional — only `if_author_in` and `if_target_branch` work fully at enqueue time without extra API calls. When skipped, a `DEBUG`-level log is emitted (search for "skipped — changed_files not available").
- **ActionType warning log**: `webhook.py` iterates `engine.evaluate(ctx)` actions and logs `WARNING` for any action type other than `SKIP_REVIEW` (e.g. `add_label`, `assign_reviewer`, `force_full_review`, `notify_webhook`). This is deliberate dead-code guard — implement the action handler, then remove the warning for that type.
- **rules.yml location**: always sibling of `config.yml`; path set via `set_rules_path()` in `main.py`. Rules engine re-reads the file on every webhook call (no caching) — hot-reload friendly
- **rules_api.py**: `_rules_path()` reads the module-level variable from `webhook.py` at call time — do not cache it at import time
- **Inline comment placement**: GitLab Discussions API requires `old_line` for context lines (unchanged lines in the diff) — passing only `new_line` causes comment to be silently misplaced. Use `_parse_diff_line_map()` to determine whether to include `old_line`
- **Comment-snap**: if LLM references a comment-only line (e.g. `# SQL injection`), `_is_comment_content()` + `_build_diff_content_map()` advance the target to the next non-comment line. Regex covers `#`, `//`, `/*`, `*`, `<!--`, `--`
- **Annotated diff format**: diff sent to LLM uses `+NNN | code` format (from `_annotate_diff_with_line_numbers()`). LLM must use the `NNN` number directly — do NOT parse old `@@` hunk offsets. Update `inline_format.md` if the format changes
- **In-flight dedup**: `_in_flight: set[tuple[str, int]]` blocks duplicate enqueue while a worker holds the job. Cleared in `_worker` try/finally. Does NOT survive process restart — by design (stateless between restarts)
- **Processing status cooldown**: `get_last_review_time()` excludes `status IN ('processing', 'skipped')` — otherwise a freshly created processing record triggers the cooldown immediately
- **UI CSS custom properties**: all colors in `index.html` use `var(--xxx)` tokens. Do NOT add new hardcoded Tailwind gray shades — add a token instead to keep the palette consistent
- **Reactive UI (patchConfig)**: `PUT /api/v1/config` returns the full masked config (not `{"status":"ok"}`). `patchConfig()` in `index.html` MUST read the response body and assign it to `this.config` — otherwise saved settings won't reflect in the UI until page reload. Never change the endpoint to return a partial object
- **setActiveProvider**: must patch both `providers[]` and `model.provider_id` in one request. Patching only `providers[].active` leaves `model.provider_id` pointing to the old provider, so the LLM backend keeps using the old one
- **SecretStr + no field_serializer**: `api_key` is `SecretStr` WITHOUT `@field_serializer`. Use `model_dump(mode="json")` in API responses (SecretStr → `"**********"`). Call `.get_secret_value()` only explicitly at LLM call sites. Adding `field_serializer(get_secret_value)` breaks the protection — `model_dump()` will return plaintext everywhere.
- **save_config + SecretStr**: `model_dump(mode="json")` serializes `SecretStr` as `"**********"`. `save_config` must explicitly restore plaintext via `provider.api_key.get_secret_value()` before writing to YAML, otherwise the config file stores masked values.
- **_mask_provider uses mode="json"**: `p.model_dump(mode="json")` returns `"**********"` for SecretStr, then `_mask_provider` replaces it with `"****"`. Changing to `model_dump()` (no mode) would return a `SecretStr` object — not JSON-serializable.
- **Per-role LLMClient cache**: `_role_llm_cache` in `PipelineManager` holds `LLMClient` instances per role. `PipelineManager` is one-shot (one review = one instance). If api_key rotates, the cached client is stale — recreate `PipelineManager`.
- **RoleModelConfig key**: keys in `roles` dict must match `ReviewRole.value` strings exactly (e.g. `"architect"`, `"developer"`, `"tester"`, `"security"`, `"reviewer"`). Wrong key = silently uses global LLM.
- **StrEnum vs str+Enum**: `ReviewRole` and `MemoryCategory` use `StrEnum` (Python 3.11+). Do NOT revert to `class X(str, Enum)` — ruff UP042 flags it and StrEnum is cleaner.
- **E402 in pipeline.py**: module-level imports must appear before any code (including regex compiles). Move `_SLOTS_RE` after all imports, not before.
- **S108 in tests**: never use hardcoded `/tmp/...` paths in test data — use `tmp_path` pytest fixture or `tempfile.mkdtemp()`. Ruff flags it as S108.
- **test_pipeline_v2_true_enables_new_pipeline**: uses `tmp_path` fixture (injected by pytest) to get a real temp dir for `prompts_dir`. Signature must include `tmp_path` parameter.

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
| BATCH B: dry_run, weekly stats, CSV export | v0.11 | ✅ Done |
| FT-2: Walkthrough Summary + Risk Score (0-100) | v0.11 | ✅ Done |
| FT-7: SSE streaming review (GET /api/v1/queue/review/{id}/stream) | v0.11 | ✅ Done |
| FT-3: Incremental review via GitLab MR Versions API | v0.12 | ✅ Done |
| FT-4: Language-aware prompt auto-selection (Python/Rust/TS/Go) | v0.12 | ✅ Done |
| FT-1: Slash commands (/ask, /improve, /summary, /help) | v0.12 | ✅ Done |
| In-flight dedup (race condition fix for parallel webhooks) | v0.13 | ✅ Done |
| Processing status: ReviewRecord saved before LLM call | v0.13 | ✅ Done |
| Accurate inline placement: diff line map + old_line for context lines | v0.13 | ✅ Done |
| Comment-snap: auto-advance off comment-only lines to code statement | v0.13 | ✅ Done |
| GLM-4.7-Flash local provider (llama-server, AMD GPU Vulkan) | v0.13 | ✅ Done |
| UI redesign: CSS custom properties, dark palette, WCAG contrast | v0.13 | ✅ Done |
| Reactive UI: patchConfig returns full config, no page-reload needed | v0.14 | ✅ Done |
| setActiveProvider also updates model.provider_id in one request | v0.14 | ✅ Done |
| Qdrant memory store + docker-compose full stack (profiles: memory, llamacpp) | v0.15 | ✅ Done |
| Per-role LLM model config: assign different model per pipeline_v2 role | v0.15 | ✅ Done |
| api_key: SecretStr (no field_serializer), model_dump(mode="json") in API | v0.15 | ✅ Done |
| URL validator for provider.url (http/https only) | v0.15 | ✅ Done |
| timeout: int = 300 in ModelConfig (configurable) | v0.15 | ✅ Done |
| chore: ruff lint fix — 52 errors fixed (#11, #8, #10) | chore | ✅ Done |
