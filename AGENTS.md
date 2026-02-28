# AGENTS.md — gitlab-reviewer

## What is this
Standalone Python service that auto-reviews GitLab MRs using a local LLM
(ollama). Receives GitLab webhooks, fetches diffs, calls the LLM, posts
a structured review comment back to the MR.

## Stack
- Python 3.11+, FastAPI, uvicorn, httpx, pydantic-settings
- ollama (or any OpenAI-compatible endpoint) as LLM backend
- Recommended model: `qwen2.5-coder:32b` (Q4_K_M)

## Structure
```
src/
  config.py         — Settings (pydantic-settings, env vars with GLR_ prefix)
  prompt_engine.py  — Prompt loading, {{include:}} resolution, injection sanitisation
  gitlab_client.py  — GitLab API: get MR, get diffs, post note
  llm_client.py     — OpenAI-compat chat completions (ollama / vllm / llama.cpp)
  reviewer.py       — Orchestration: filter → sanitise → LLM → comment
  webhook.py        — FastAPI routes, HMAC token check, background task dispatch
  main.py           — App factory, wiring, uvicorn entrypoint

prompts/
  system/           — Built-in prompts (version-controlled)
    base.md         — MUST BE FIRST. Role + anti-injection rules + includes code_review
    code_review.md  — General review output format and principles
    security.md     — Security-focused checklist
    performance.md  — Performance checklist
    style.md        — Style / maintainability checklist
  custom/           — User overrides (gitignored, custom/ wins over system/)
    example_team.md — Template for team-specific rules

config.yml          — Which prompts to load, whitelist settings
.env                — Secrets (gitignored)
```

## Development Rules
- All env vars prefixed with `GLR_`
- Never string-interpolate user data (diff/title/description) into the system prompt
- Diff and MR metadata ALWAYS go into the user message turn, not system
- Call `prompt_engine.sanitize_untrusted()` on ALL fields coming from GitLab before use
- `PromptEngine._system_prompt` is assembled once at startup — immutable per request
- Webhook handler returns 200 immediately; review runs in `BackgroundTask`
- Dedup cache is in-memory — restart clears it (acceptable)

## How to run locally
```bash
cp .env.example .env
# Edit .env with your GitLab token and webhook secret

# Install dependencies
pip install -e .

# Run
python -m uvicorn src.main:create_app --factory --reload --port 8000

# Run with docker-compose
docker compose up -d
```

## Pitfalls
- `base.md` MUST be the first prompt in `config.yml` — it contains anti-injection instructions
- Custom prompts in `prompts/custom/` override system ones by name — useful but watch for conflicts
- `{{include:}}` directives are resolved at request time (no startup cache) — circular includes are detected up to depth 8
- ollama `/v1/chat/completions` is available in ollama ≥ 0.1.24; older versions need native `/api/chat`
- The LLM timeout (`GLR_LLM_TIMEOUT`) should be generous (300s+) for large diffs on CPU
- `GLR_LLM_MAX_DIFF_CHARS=32000` is the safety cap — increase only if your model context supports it

## Status
Initial implementation — not yet deployed.
