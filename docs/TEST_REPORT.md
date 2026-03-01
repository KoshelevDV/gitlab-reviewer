# Integration Test Report — gitlab-reviewer

**Date:** 2026-03-01  
**GitLab instance:** https://gitlab.fgdevelop.tech  
**Test project:** `testauto/test-reviewer` (id=35)  
**Reviewer:** running locally on `http://10.0.30.18:8000`  
**LLM:** OpenRouter `google/gemma-3-27b-it:free` (131K context)  
**Commit tested:** `53c9ef9`

---

## Results Summary

| # | Test | Status | Details |
|---|------|--------|---------|
| 1 | Webhook delivery (real event) | ✅ | MR creation triggers webhook; reviewer enqueues and processes automatically |
| 2 | OpenRouter — gemma-3-27b-it:free | ✅ | Reviews posted; 429 rate-limits handled via retry (4 attempts, 5–30s backoff) |
| 3 | Local Ollama / llama.cpp | N/A | Ollama not running; no Docker container |
| 4 | Rate limiting — cooldown | ✅ | `review_cooldown_minutes=2`; repeated requests within window → `skipped` |
| 5 | Deduplication (diff hash) | ✅ | Same diff hash → immediate skip; confirmed in logs |
| 6 | Queue concurrency | ✅ | `max_concurrent=3`; MR!2, !3 processed in parallel |
| 7 | Language-aware prompts | ✅ | Python → `lang_python.md`, TypeScript → `lang_typescript.md`, Go → `lang_go.md` |
| 8 | Slash command `/summary` | ✅ | Note hook received; bot replied with `<!-- slash-command:summary -->` response |
| 9 | Dry run (`dry_run: true` in body) | ✅ | Returns `{"status":"dry_run", mr_title, ...}`; no DB record created |
| 10 | Weekly stats | ✅ | `GET /api/v1/reviews/stats/weekly` — correct aggregation (total/posted/skipped/errors) |
| 11 | CSV export | ✅ | `GET /api/v1/reviews/export.csv` — correct headers and data |
| 12 | SSE Streaming | ✅ | `stream: true` → `stream_url`; live token chunks confirmed via curl |
| 13 | Incremental review | ✅ | After new commit: badge `📦 Incremental review — only changes since version N` |
| 14 | Project-level webhook | ✅ | `projects/35/hooks` — GitLab test event → `201 Created` |
| 15 | Group-level webhook | ✅ | `groups/127/hooks` — available on this GitLab EE instance (plan=None/trial) |

**Overall: 14/14 ✅, 1 N/A (Ollama)**

---

## Test MRs Created

| MR | Branch | Language | Risk Score | Inline | Notes |
|----|--------|----------|-----------|--------|-------|
| !1 | `feature/test-review` | Python | 98/100 🔴 | 7 | SQLi, CMDi, path traversal, hardcoded creds, timing attack |
| !2 | `feature/ts-review` | TypeScript | 100/100 🔴 | 5 | SQLi, XSS, hardcoded creds, insecure random |
| !3 | `feature/go-review` | Go | 49/100 🟡 | 2 | RCE via exec.Command |
| !4 | `feature/sse-test` | Python | 100/100 🔴 | 5–8 | SSE streaming test |
| !5 | `feature/sse-test-*` | Python | — | — | Live SSE streaming captured |

---

## Bugs Found

### BUG-8: `dry_run=true` as query param is silently ignored
- **Reproduce:** `POST /api/v1/queue/review?dry_run=true` (body without `dry_run`)
- **Expected:** `{"status": "dry_run", ...}`
- **Actual:** `{"status": "queued", "job_id": N}` — job enqueued as normal
- **Root cause:** `dry_run` field is in `TriggerBody` Pydantic model (body), not a query param. No validation error raised for unknown query params.
- **Fix:** Either add `dry_run` as a proper query param via FastAPI `Query()`, or document clearly that it must be in the JSON body.

### BUG-9: SSE buffer replay sometimes misses tokens
- **Reproduce:** Connect to `stream_url` 1–2 seconds after POST — some initial tokens lost
- **Root cause:** `_stream_buffers` stores tokens, but if the job is very fast (small diff + cached LLM), it may complete and clear the buffer before client connects
- **Severity:** LOW — only affects late-connecting clients; `register_stream()` before enqueue is correct

---

## Warnings (Open from CODE_REVIEW.md)

| ID | Issue | Status |
|----|-------|--------|
| WARN-4 | Sequential HTTP in `test_connection` | 🔲 Open |
| WARN-6 | KeyError fallback in `llm_client.py` | 🔲 Open |
| WARN-7 | `import json` inside generator function | 🔲 Open |
| STYLE-1 | Duplicated dedup code in backends | 🔲 Open |

---

## Rate Limiting Notes

OpenRouter free tier (`google/gemma-3-27b-it:free`) hits 429 regularly.  
Retry config: 4 attempts, exponential 5–30s backoff — handles it cleanly.  
Alternative if rate limits are problematic:
- `nousresearch/hermes-3-llama-3.1-405b:free` (131K ctx)
- `qwen/qwen3-coder:free` (262K ctx) — but hosted on Venice, frequent rate limits
- Deploy local Ollama with `qwen2.5-coder:7b` or `deepseek-coder:6.7b`

---

## Infrastructure

- **Webhook URL:** `http://10.0.30.18:8000/webhook/gitlab` (internal IP, reachable from GitLab)
- **External IP** `193.124.92.78:8000` — NOT reachable (blocked externally)
- **Group hooks:** available on this EE instance (no Premium license error)
- **Project hooks:** always available, currently active on `test-reviewer`
- **Secret:** `GLR_WEBHOOK_SECRET` in `/opt/projects/gitlab-reviewer/.env`

---

## Recommendations

1. **Fix BUG-8** — add `dry_run` as `Query()` param OR document clearly it's body-only
2. **Local Ollama** — deploy `ollama/ollama` Docker container with `qwen2.5-coder:7b` as fallback when OpenRouter rate-limits
3. **Cooldown** — current 2 min is fine for testing; set to 10–15 min in production
4. **Group webhook** — already configured on `testauto` group; covers all new projects automatically
5. **SSE** — works, but consider increasing buffer retention time for slow clients
