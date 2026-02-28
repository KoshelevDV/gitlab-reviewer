# gitlab-reviewer

Automated GitLab MR code reviewer powered by a **local LLM via ollama**.  
No data leaves your infrastructure.

---

## Features

- 🤖 Automatic review comment on every MR open / update
- 🛡️ Prompt injection prevention (diff content never touches system prompt)
- 📝 Composable prompt system — split reviews by concern, use `{{include:}}` to assemble
- 🔒 Webhook HMAC verification
- 🔁 Dedup cache — no duplicate comments on identical diffs
- 🚫 Draft / whitelist / max-files filters
- 🧪 Dry-run mode (log without posting)
- 🐳 Docker Compose + Helm chart

---

## Quick Start

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env — set GLR_GITLAB_URL, GLR_GITLAB_TOKEN, GLR_WEBHOOK_SECRET

# 2. Pull the recommended model
ollama pull qwen2.5-coder:32b

# 3. Start
docker compose up -d
```

Configure a GitLab webhook:
- **URL:** `http://your-server:8000/webhook/gitlab`
- **Secret:** same value as `GLR_WEBHOOK_SECRET`
- **Trigger:** Merge request events

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GLR_GITLAB_URL` | `https://gitlab.com` | GitLab base URL |
| `GLR_GITLAB_TOKEN` | **required** | Personal/project token (`api` scope) |
| `GLR_WEBHOOK_SECRET` | **required** | Webhook secret token |
| `GLR_OLLAMA_URL` | `http://localhost:11434` | Ollama base URL |
| `GLR_OLLAMA_MODEL` | `qwen2.5-coder:32b` | Model name |
| `GLR_LLM_TIMEOUT` | `300` | LLM request timeout (seconds) |
| `GLR_LLM_MAX_DIFF_CHARS` | `32000` | Max diff characters sent to LLM |
| `GLR_LLM_TEMPERATURE` | `0.2` | LLM temperature (lower = more focused) |
| `GLR_DRY_RUN` | `false` | Log review, do not post to GitLab |
| `GLR_MAX_FILES_PER_REVIEW` | `50` | Skip review if MR touches more files |
| `GLR_DIFF_CACHE_TTL` | `3600` | Dedup cache TTL in seconds |
| `GLR_LOG_LEVEL` | `info` | Log level |

---

## Prompt System

Prompts live in `prompts/system/` (built-in) and `prompts/custom/` (your overrides).  
`config.yml` controls which prompts are loaded and in what order.

### Composing prompts

```yaml
# config.yml
prompts:
  system:
    - base         # always first — anti-injection rules
    - security     # security checks
    - performance  # performance checks
    # - my_team    # prompts/custom/my_team.md
```

Inside a prompt file, use `{{include: other_prompt}}` to pull in another file:

```markdown
<!-- prompts/custom/my_team.md -->
## Team Rules
...

{{include: style}}   ← pulls in prompts/system/style.md
```

Files in `prompts/custom/` override same-named files in `prompts/system/`.

### Anti-Injection Architecture

```
GitLab Webhook
      │
      ▼
sanitize_untrusted(diff)   ← strips model control tokens, truncates
      │
      ▼
┌─────────────────────────────────────┐
│  system turn: sealed prompt         │  ← assembled from files, never modified
│  • Role definition                  │
│  • Anti-injection rules             │
│  • Review guidelines                │
├─────────────────────────────────────┤
│  user turn: structured data only    │  ← diff goes here, clearly delimited
│  • MR metadata (sanitised)          │
│  • Diff content (sanitised)         │
└─────────────────────────────────────┘
      │
      ▼
   ollama / vllm / llama.cpp
      │
      ▼
  GitLab MR comment
```

---

## Docker

```bash
docker build -t gitlab-reviewer .
docker run -d --env-file .env -p 8000:8000 gitlab-reviewer
```

## Docker Compose

```bash
docker compose up -d          # starts gitlab-reviewer + ollama
docker compose logs -f        # follow logs
```

## Helm

```bash
helm install gitlab-reviewer ./helm/gitlab-reviewer \
  --set secrets.gitlabToken=glpat-xxx \
  --set secrets.webhookSecret=mysecret \
  --set env.GLR_GITLAB_URL=https://gitlab.example.com \
  --set env.GLR_OLLAMA_URL=http://ollama:11434

# Upgrade
helm upgrade gitlab-reviewer ./helm/gitlab-reviewer --reuse-values

# With custom values file
helm install gitlab-reviewer ./helm/gitlab-reviewer -f my-values.yaml
```

---

## Recommended Models

| Model | Size (Q4) | Notes |
|-------|-----------|-------|
| `qwen2.5-coder:32b` | ~20 GB | Best balance — code-specialist, fast |
| `qwen2.5-coder:72b` | ~45 GB | Maximum quality, needs ~50 GB RAM |
| `deepseek-r1:32b` | ~20 GB | Deep reasoning, slower, great explanations |
| `codestral:22b` | ~14 GB | Fast, good for high-volume reviews |

---

---

# gitlab-reviewer (RU)

Автоматическое ревью GitLab MR с помощью **локальной LLM через ollama**.  
Данные не покидают вашу инфраструктуру.

## Возможности

- 🤖 Автоматический комментарий-ревью при каждом открытии / обновлении MR
- 🛡️ Защита от prompt injection — содержимое diff никогда не попадает в системный промпт
- 📝 Составные промпты с `{{include:}}` — разбивайте по смыслу, склеивайте в один
- 🔒 Проверка подписи webhook (constant-time HMAC)
- 🔁 Дедупликация — не дублирует ревью на идентичный diff
- 🚫 Фильтры: черновики, whitelist авторов/проектов, лимит файлов
- 🧪 Dry-run режим

## Быстрый старт

```bash
cp .env.example .env
# Заполни GLR_GITLAB_URL, GLR_GITLAB_TOKEN, GLR_WEBHOOK_SECRET

ollama pull qwen2.5-coder:32b

docker compose up -d
```

Настрой webhook в GitLab:
- **URL:** `http://your-server:8000/webhook/gitlab`
- **Secret Token:** значение `GLR_WEBHOOK_SECRET`
- **Триггер:** Merge request events

## Система промптов

```
prompts/
  system/           ← встроенные (в git)
    base.md         ← ВСЕГДА первый — роль + анти-инъекция + включает code_review
    code_review.md  ← формат и принципы ревью
    security.md     ← проверки безопасности
    performance.md  ← проверки производительности
    style.md        ← стиль и поддерживаемость
  custom/           ← твои переопределения (gitignored)
    example_team.md ← шаблон командных правил
```

Управление через `config.yml`:

```yaml
prompts:
  system:
    - base
    - security
    # - performance
    # - my_team   # prompts/custom/my_team.md
```

Внутри файла промпта:
```markdown
{{include: style}}   ← вставляет prompts/system/style.md
```

Файлы в `prompts/custom/` имеют приоритет над `prompts/system/` при совпадении имени.
