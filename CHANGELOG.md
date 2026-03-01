# Changelog

All notable changes to gitlab-reviewer are documented here.

---

## [1.0.0] — 2026-03-01

First stable release. Full-featured, tested, production-ready.

### Features

- **Automatic MR review** — webhook-triggered on MR open/update; posts summary + inline comments via GitLab Discussions API
- **Accurate inline placement** — diff line map (`_parse_diff_line_map`) + `old_line` for context lines; annotated diff format eliminates LLM counting errors; comment-snap advances past comment-only lines
- **Risk Score (0–100)** — deterministic score based on diff size, sensitive paths, and severity keywords; displayed in review header and Web UI
- **Walkthrough Summary** — separate LLM call generates a 3–5 sentence overview of all changes
- **Incremental review** — `repository/compare` API returns real commit delta; only new changes are sent to LLM on subsequent pushes
- **Language-aware prompts** — detects dominant language (Python / Rust / TypeScript / Go) and appends a language-specific supplement prompt
- **Slash commands** — `/ask`, `/improve`, `/summary`, `/help` in MR comments; triggered via GitLab Note Hook
- **SSE streaming** — `GET /api/v1/queue/review/{job_id}/stream` for real-time token delivery; integrates with review UI
- **Processing status** — `ReviewRecord` saved with `status=processing` before LLM call; visible in queue and reviews table immediately
- **In-flight dedup** — `_in_flight: set[tuple[str, int]]` blocks parallel webhook events for the same MR; cleared in worker try/finally
- **Three queue backends** — in-memory (default), Valkey/Redis (distributed), Kafka (high-volume, partition-keyed)
- **Multi-provider LLM** — OpenAI-compat, Ollama, llama.cpp, OpenRouter; runtime fallback on 404; 429 auto-retry with backoff
- **GLM-4.7-Flash local** — llama-server in Podman toolbox, AMD GPU Vulkan (-ngl 999), 32K context, zero rate limits
- **Dry-run mode** — `POST /api/v1/queue/review?dry_run=true` validates MR without queuing
- **Stats & export** — weekly stats (`/stats/weekly`), CSV export (`/export.csv`)
- **Web UI** — dark theme, CSS custom properties design system, sticky topbar with live status, risk score progress bars, log viewer with per-level coloring, reactive config (no page reload)
- **Reactive config** — `PUT /api/v1/config` returns full masked config; frontend applies it directly without a second GET
- **Helm chart** — `helm/gitlab-reviewer/` for Kubernetes deployment
- **Docker Compose** — profiles for memory / Valkey / Kafka backends

### Architecture

- FastAPI async backend; aiosqlite; asyncio.Queue + Semaphore concurrency
- Atomic config write (write temp → rename); secrets injected from env vars, never persisted
- `PromptEngine` with file-based prompt cache and hot-reload
- 17-item code review completed; all issues resolved

### Quality

- **501 tests** — unit + integration across all modules; pytest-asyncio
- **ruff clean** — zero lint errors
- TLS verify passthrough, self-signed GitLab support
- Prompt injection guard in `base.md` + `sanitize_untrusted()`

---

## [0.13] — 2026-03-01

- In-flight dedup (race condition fix for parallel webhooks)
- Processing status saved before LLM call
- Accurate inline comment placement: diff line maps + old_line + comment-snap
- GLM-4.7-Flash local provider
- UI redesign: CSS custom properties, WCAG contrast fixes

## [0.12] — 2026-02-28

- Incremental review via GitLab MR Versions API / compare_commits
- Language-aware prompt auto-selection (Python, Rust, TypeScript, Go)
- Slash commands: /ask, /improve, /summary, /help

## [0.11] — 2026-02-28

- Risk Score (0–100) + Walkthrough Summary
- SSE streaming for real-time review output
- Dry-run mode, weekly stats, CSV export

## [0.10] — 2026-02-28

- Kafka queue backend (aiokafka, KRaft, consumer groups)
- Code review pass: 17/17 issues resolved

## [0.9] — 2026-02-28

- Valkey/Redis distributed queue backend

## [0.1–0.8] — 2026-02-22 to 2026-02-27

- Initial implementation
- Web UI (providers, models, config, logs, reviews, targets)
- Review history SQLite, auto-approve, dashboard
- Inline GitLab Discussion comments
- Branch pattern filtering, author allowlist
- Prometheus metrics, /health endpoint
- Full test suite (154 → 501 tests)
