# gitlab-reviewer — Ideas & Backlog

> Источник: анализ экосистемы (PR-Agent/Qodo, CodeRabbit, Bito, Reviewpad, Copilot Review,
> genai-code-review, GitLab Duo) + best practices automated code review.
> Дата: 2026-03-01

---

## Обзор схожих проектов

| Инструмент | Stars | Ключевые фичи |
|-----------|-------|--------------|
| **PR-Agent** (Qodo) | 14k+ | /review /improve /ask /describe команды в MR; PR Compression; config.toml правила; мультиплатформа |
| **CodeRabbit** | commercial | Walkthrough summary, nitpick mode, learnable patterns, чат с MR, file-level summaries, auto-unapprove при изменениях |
| **Bito** | 1k+ | VSCode + CI интеграция, объяснение кода, генерация тестов, Q&A по коду |
| **Reviewpad** | 900+ | YAML automation rules, auto-assign reviewers, custom labels, merge policies |
| **genai-code-review** | 800+ | GitHub Actions, per-file review, обычный GPT |
| **GitLab Duo** | built-in | Root cause analysis, объяснение MR, code suggestions прямо в IDE |
| **CodiumAI** | commercial | Impact analysis, test generation, behaviour analysis |

### Интересные подходы из открытых источников

- **PR-Agent**: "PR Compression" — умная компрессия диффа сохраняет контекст без потери важного;
  slash-команды в комментариях (`/review`, `/ask почему это O(n²)?`) — интерактивное ревью
- **CodeRabbit**: learnable patterns — система запоминает "не флагай X в этом репо" и применяет к будущим MR;
  auto-unapprove — если MR изменился после апрува, снимает одобрение автоматически
- **Reviewpad**: правила на YAML — "если изменён security/ dir, добавь label `security-review`, уведоми @security-team"
- **CodiumAI**: impact analysis — "этот MR может сломать X, Y, Z компоненты" на основе call graph
- **GitLab Duo**: Review Summary в одном предложении + объяснение зачем MR нужен для нетехников

---

## User Stories

### Взаимодействие с ревью

**US-01**  
Как разработчик я хочу написать `/ask почему это может вызвать N+1 запрос?` в комментарии к MR  
чтобы получить мгновенный ответ от LLM в контексте именно этого диффа.

**US-02**  
Как разработчик я хочу написать `/improve auth/login.py` в комментарии  
чтобы получить конкретные предложения рефакторинга для конкретного файла.

**US-03**  
Как автор MR я хочу получать краткое резюме ревью в одном абзаце (Walkthrough Summary)  
чтобы быстро понять что нашёл ревьюер, не читая всё.

**US-04**  
Как тимлид я хочу ставить 👍 или 👎 под конкретным комментарием ревью  
чтобы учить систему что полезно, а что — шум.

**US-05**  
Как разработчик я хочу видеть прогресс генерации ревью в реальном времени (streaming)  
чтобы не ждать 60 секунд без фидбека.

---

### Качество ревью

**US-06**  
Как администратор я хочу настраивать разные промпты для разных языков (Rust, Python, Go)  
чтобы ревью было специфично для технологии — Rust проверяет ownership, Python — type hints.

**US-07**  
Как security engineer я хочу отдельный промпт "compliance check" который ищет hardcoded secrets,  
отсутствие license headers и небезопасные зависимости  
чтобы не пропустить их в код review.

**US-08**  
Как разработчик я хочу получать предложения тестов ("Consider testing X edge case")  
чтобы повысить покрытие не думая "а что ещё стоит протестировать?".

**US-09**  
Как тимлид я хочу видеть "Impact Analysis" — какие другие файлы/компоненты может затронуть MR  
чтобы заранее понимать риск и назначать дополнительных ревьюеров.

**US-10**  
Как разработчик я хочу получать incremental review — только на changed files с момента предыдущего ревью  
чтобы не перечитывать комментарии к незменённому коду.

---

### Автоматизация и политики

**US-11**  
Как тимлид я хочу задавать YAML-правила вида "если файл в `security/` → уведомить @security-team"  
чтобы автоматизировать routing MR-ов без ручного вмешательства.

**US-12**  
Как DevOps-инженер я хочу чтобы MR с находкой `[CRITICAL]` блокировал merge через GitLab approval rules  
а не просто оставлял комментарий, которые можно проигнорировать  
чтобы критические баги не попадали в main.

**US-13**  
Как тимлид я хочу auto-unapprove MR если он изменился после апрува  
чтобы новый коммит не прокрался незаметно.

**US-14**  
Как DevOps-инженер я хочу автоматически назначать ревьюеров из CODEOWNERS  
при обнаружении изменений в sensitive-файлах  
чтобы не забывать вручную добавлять нужных людей.

**US-15**  
Как PM я хочу получать автоматический CHANGELOG из закрытых MR за неделю  
чтобы иметь историю изменений для релизных заметок.

---

### Аналитика и наблюдаемость

**US-16**  
Как тимлид я хочу видеть dashboard с метриками: среднее время ревью, топ-авторы по числу находок,  
самые "опасные" директории  
чтобы принимать данно-обоснованные решения о качестве кода.

**US-17**  
Как DevOps-инженер я хочу видеть стоимость каждого ревью (токены × цена)  
чтобы контролировать расходы при использовании облачных LLM.

**US-18**  
Как разработчик я хочу видеть в истории ревью счёт риска MR (0–100)  
чтобы быстро фильтровать "горячие" MR в очереди.

**US-19**  
Как DevOps-инженер я хочу экспортировать историю ревью в CSV / JSONL  
чтобы анализировать тренды в BI-инструментах.

**US-20**  
Как разработчик я хочу получать digest "что нашли за неделю" раз в неделю в Telegram/Slack  
чтобы видеть паттерны ошибок без захода в UI.

---

## Feature Tickets

---

**FT-1: Slash-команды в MR-комментариях**

**Описание:**  
Пользователь пишет `/ask <вопрос>`, `/improve <файл>`, `/review` в комментарии GitLab MR.  
Сервис получает webhook `note` события, парсит команду, выполняет LLM-запрос с контекстом диффа,  
постит ответ как thread в том же MR.

**Acceptance criteria:**
- [ ] Webhook handler обрабатывает `Note Hook` события от GitLab
- [ ] Парсинг команд: `/review`, `/ask <text>`, `/improve [<file>]`, `/summary`
- [ ] `/ask` — LLM отвечает только на вопрос, с контекстом всего диффа
- [ ] `/improve <file>` — LLM даёт конкретные улучшения только для указанного файла
- [ ] `/summary` — краткое резюме MR для нетехников (без оценки качества)
- [ ] Команды работают только для авторизованных пользователей (конфиг whitelist)
- [ ] Ответы постятся как ответ на комментарий (GitLab discussion reply)

**Priority:** High  
**Effort:** L

---

**FT-2: Walkthrough Summary + Risk Score**

**Описание:**  
Перед детальными замечаниями — краткое резюме: что делает MR, какие файлы затронуты,  
и риск-скор (0–100) основанный на: размер диффа, наличие CRITICAL/HIGH, изменения в security/  
директориях, новые зависимости.

**Acceptance criteria:**
- [ ] Новый промпт `summary.md` генерирует 3–5 предложений о цели MR
- [ ] Risk score считается детерминированно (без LLM): размер + severity + touched paths
- [ ] Summary и score отображаются в начале review comment (до inline findings)
- [ ] Risk score сохраняется в `ReviewRecord` (новое поле `risk_score: int`)
- [ ] UI: в таблице Reviews добавлена колонка Risk Score с цветовой индикацией
- [ ] Фильтрация в `/api/v1/reviews` по `risk_score_min`

**Priority:** High  
**Effort:** M

---

**FT-3: Incremental Review (только что изменилось)**

**Описание:**  
При повторном push в тот же MR — ревью только diff между предыдущей и текущей версией.  
Использует GitLab MR Versions API для получения diff между версиями.

**Acceptance criteria:**
- [ ] При enqueue — проверять есть ли предыдущий review для этого MR
- [ ] Если есть — fetching `GET /projects/:id/merge_requests/:iid/versions` → последние 2 версии
- [ ] Diff между версиями через `GET /diffs?from=<base>&to=<head>`
- [ ] В prompts — добавить контекст "это incremental review, предыдущие проблемы: [список]"
- [ ] Comment: помечать "Incremental review (changes since last review)"
- [ ] Опция `review_targets.incremental_only: true` в конфиге
- [ ] Fallback на full review если предыдущего нет

**Priority:** Medium  
**Effort:** L

---

**FT-4: Language-Aware Prompt Selection**

**Описание:**  
Автоматически добавлять language-специфичный промпт на основе расширений файлов в диффе.  
Rust MR → промпт про ownership/lifetimes. Python → type hints, async gotchas. JS → XSS, prototype pollution.

**Acceptance criteria:**
- [ ] Детекция языков из FileDiff.new_path расширений (`.rs`, `.py`, `.ts`, `.go`, `.java`, etc.)
- [ ] Маппинг: extension → prompt name (configurable в config.yml)
- [ ] Промпты `prompts/system/lang_rust.md`, `lang_python.md`, etc.
- [ ] Если MR смешанный (py + ts) — добавляются оба языковых промпта
- [ ] В `review_targets` можно pinned-переопределить language prompts
- [ ] UI: в Settings показывать какие language prompts включены для текущего провайдера

**Priority:** Medium  
**Effort:** M

---

**FT-5: Reviewer Feedback Learning (Thumbs Up/Down)**

**Описание:**  
После получения ревью разработчик может реагировать на конкретный комментарий эмодзи:  
👍 = useful, 👎 = noise. Сервис через GitLab Award Emoji API собирает сигналы,  
адаптирует промпты и фильтры (минимум сигналов для значимости: 5+).

**Acceptance criteria:**
- [ ] Polling GitLab Award Emoji на posted review comments (периодически или по webhook)
- [ ] Хранение feedback в новой таблице `review_feedback(review_id, gitlab_note_id, rating, user)`
- [ ] Reporting в UI: "X% комментариев полезны" по автору / промпту / проекту
- [ ] Интеграция в prompt: "Прошлые паттерны которые пользователи отметили как noise: [список]"
- [ ] `/api/v1/feedback` — REST эндпоинт для просмотра статистики
- [ ] Автоматическая инвалидация системного промпта при изменении feedback-данных

**Priority:** Low  
**Effort:** XL

---

**FT-6: Automation Rules (Reviewpad-style YAML)** ✅ Done

**Описание:**  
Пользователь описывает политики в `rules.yml`: условия (touched files, author, MR size)  
и действия (add label, assign reviewer, skip review, notify).

**Acceptance criteria:**
- [x] Новый файл `rules.yml` рядом с `config.yml`
- [x] Условия: `if_files_match: [security/**, *.env]`, `if_author_in: [...]`, `if_lines_changed > 500`
- [x] Действия: `add_label`, `assign_reviewer`, `skip_review`, `notify_webhook`, `force_full_review`
- [x] Правила оцениваются при enqueue, до фетча диффа
- [x] Правила применяются независимо от review_targets (это meta-слой)
- [x] UI: вкладка "Rules" с YAML-редактором и валидацией

**Реализовано:** `src/rules.py`, `src/api/rules_api.py`, интеграция в `src/webhook.py` + `src/main.py`, UI-вкладка "⚙️ Rules" в `index.html`, 28 тестов в `tests/test_rules.py`.

**Priority:** Medium  
**Effort:** XL

---

**FT-7: Streaming Review in Web UI**

**Описание:**  
При запуске ревью вручную из UI (POST /api/v1/queue/review) — отображать текст ревью  
по мере генерации через SSE/WebSocket. Пользователь не ждёт 60с пустой страницы.

**Acceptance criteria:**
- [ ] LLMClient.chat_stream() уже реализован — использовать его
- [ ] Новый WebSocket endpoint `GET /ws/review/{job_id}`
- [ ] При старте ревью — возвращать job_id, UI подключается к WS
- [ ] Chunk'и текста стримятся в браузер и отображаются в real-time
- [ ] По завершении — WS закрывается, финальный текст сохраняется в БД
- [ ] Fallback: если браузер не поддерживает WS — polling каждые 2s

**Priority:** Medium  
**Effort:** M

---

## Быстрые улучшения (< 1 дня каждое)

| # | Описание | Effort |
|---|----------|--------|
| Q-1 | `/api/v1/queue/review` — dry_run параметр | XS |
| Q-2 | `GET /api/v1/reviews/{id}/diff` — вернуть diff_hash + файлы из ревью | XS |
| Q-3 | `POST /api/v1/queue/start` — перезапуск воркеров после drain (BUG WARN-2) | S |
| Q-4 | Prompt cache invalidation при `POST /api/v1/config/reload` | S |
| Q-5 | `review_targets.type=group` — правильный матч по namespace (сейчас сломан) | S |
| Q-6 | `GET /api/v1/stats/weekly` — агрегат за неделю по статусам | S |
| Q-7 | HTTP retry (httpx + tenacity) для GitLab и LLM вызовов (3 попытки, exp backoff) | S |
| Q-8 | `POST /webhook/gitlab` — reject > 512 KB без чтения тела (уже сделано) | Done |
| Q-9 | MR URL кликабельна в review comment (уже есть mr_url) | XS |
| Q-10 | Экспорт истории ревью в CSV через `GET /api/v1/reviews/export.csv` | M |

---

## Архитектурные улучшения

### Dedup Mixin
Вынести общий dedup-код (is_already_seen / mark_seen / _is_seen / load_seen_from_db)  
из QueueManager / ValkeyQueueManager / KafkaQueueManager в shared `DeduplicatorMixin`.  
Или standalone `DedupCache` класс который все три backend-а инстанцируют.

### Protocol-based типизация
`QueueLike` Protocol уже добавлен в reviewer.py.  
Следующий шаг: `src/backends/protocol.py` с полным Protocol,  
и все три backend явно помечены как `class ValkeyQueueManager(QueueLike):`.

### Retry Middleware
`httpx` + `tenacity` для all outbound calls (GitLab API + LLM):
```python
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
async def _with_retry(fn):
    return await fn()
```

### Config Hot Reload
При `POST /api/v1/config/reload`:
1. Перезагрузить `_config`
2. Инвалидировать `PromptEngine._cache` (сейчас не делается)
3. Пересоздать QueueManager если `backend` изменился
4. Обновить `Reviewer` ссылку на новый queue

---

## Prioritized Backlog

| Priority | Item | Effort | Impact | Статус |
|----------|------|--------|--------|--------|
| 🔴 P0 | FT-1: Slash-команды в MR | L | 🔥 Core UX | ✅ Done (`fe3c2aa`) |
| 🔴 P0 | FT-2: Walkthrough Summary + Risk Score | M | 🔥 Core UX | ✅ Done (`94ad3e4`) |
| 🟠 P1 | Q-7: HTTP retry (tenacity) | S | Reliability | ✅ Done (`d1cbb1b`) |
| 🟠 P1 | Q-5: group target matching fix | S | Bug fix | ✅ Done |
| 🟠 P1 | Q-3: queue restart after drain | S | Bug fix | ✅ Done |
| 🟠 P1 | Q-4: prompt cache invalidation | S | Correctness | ✅ Done |
| 🟠 P1 | FT-7: Streaming review in UI | M | UX | ✅ Done (`e1de6b3`) |
| 🟡 P2 | FT-3: Incremental review | L | Efficiency | ✅ Done (`1d22cf7`) |
| 🟡 P2 | FT-4: Language-aware prompts | M | Quality | ✅ Done (`8afedc1`) |
| 🟡 P2 | Q-10: CSV export | M | Analytics | ✅ Done (`8969dd7`) |
| 🟡 P2 | Q-1: dry_run review trigger | S | DX | ✅ Done (`8969dd7`) |
| 🟡 P2 | Q-6: Weekly stats API | S | Analytics | ✅ Done (`8969dd7`) |
| 🟢 P3 | FT-5: Reviewer Feedback Learning | XL | Long-term | 🔲 Open |
| 🟢 P3 | FT-6: Automation Rules (YAML) | XL | Power users | ✅ Done |
| 🟢 P3 | #11: ruff lint fix (52 errors) | XS | Code quality | ✅ Done (`chore/ruff-lint-fix`) |
| 🟢 P3 | #8: Q-9 test assertions (MR link at start) | XS | Test quality | ✅ Done |
| 🟢 P3 | #10: `_safe_mr_title` type annotation `str\|None` | XS | Type safety | ✅ Done |

**Открытые CODE_REVIEW items:** WARN-4, WARN-6, WARN-7, STYLE-1 (все Low/Medium)

---

## 🆕 New Feature Ideas (2026-03-07)

---

**FT-8: Auto-Unapprove on New Commit**

Когда MR изменился после апрува — автоматически снять одобрение через GitLab API.
Detect: новый push event на уже-approved MR.

AC:
- [ ] При push event — проверять есть ли approved review для этого MR
- [ ] Если есть → GitLab API DELETE /approvals
- [ ] Событие логируется + опциональное уведомление автору

Priority: P1 | Effort: S | Impact: high

---

**FT-9: Smart Diff Compression для огромных MR**

MR с diff > `max_diff_lines` усекается умно: приоритет security/, core/, изменённым файлам.
Добавляет "N files omitted" предупреждение в review comment.

AC:
- [ ] `review_targets.max_diff_lines: int | null` в config (null = без лимита)
- [ ] Алгоритм ранжирования файлов: security/ > tests/ > docs/ > other
- [ ] Усечённые файлы → summary "File X: omitted (N lines), review manually"
- [ ] Тест с diff > max_diff_lines

Priority: P1 | Effort: M | Impact: high

---

**FT-12: Dependency Vulnerability Scanning (OSV.dev)**

При изменении requirements.txt / go.mod / package.json / Cargo.toml в MR — проверять через OSV.dev API на known CVEs.

AC:
- [ ] Detect dependency files in diff by filename pattern
- [ ] `GET https://api.osv.dev/v1/query` per new/changed dependency
- [ ] Findings включаются в SECURITY role результаты
- [ ] Fail-open: если OSV недоступен → warning, не блокировка
- [ ] Configurable: `security.dependency_scan: true|false`

Priority: P1 | Effort: M | Impact: high

---

**FT-13: Pattern Suppression / Noise Dictionary**

Per-project `suppress.yml` с паттернами подавления: findings matching suppressed patterns убираются из output.

AC:
- [ ] Новый файл `suppress.yml` рядом с `config.yml`
- [ ] Паттерны: `text_contains`, `file_matches`, `role`
- [ ] Filtering после LLM-ответа, до публикации комментария
- [ ] `/api/v1/suppress` CRUD endpoints
- [ ] UI: вкладка Suppress с редактором
- [ ] Пополнение через `/learn <quote>` slash command

Priority: P1 | Effort: M | Impact: high

---

**BUG-8: dry_run как query param в POST /api/v1/queue/review**

`dry_run` в теле запроса работает, в query param (`?dry_run=true`) — игнорируется.

AC:
- [ ] `dry_run: bool = Query(False)` в endpoint, с приоритетом над телом
- [ ] Тест: GET-подобный вызов с ?dry_run=true

Priority: P1 | Effort: XS | Impact: low

---

**BUG-9: SSE Buffer Race Condition**

Клиент, подключившийся через 1-2с после старта генерации, пропускает начальные токены.

AC:
- [ ] TTL на `_stream_buffers`: не чистить сразу, а через 60с после завершения
- [ ] Replay при подключении: клиент получает buffered chunks с начала
- [ ] Тест: E2E-3 late-connect scenario

Priority: P1 | Effort: S | Impact: medium

---

**FT-14: CHANGELOG Generation**

`GET /api/v1/changelog?from=YYYY-MM-DD&to=YYYY-MM-DD` — LLM группирует закрытые MR по типу.

AC:
- [ ] Агрегация MR из ReviewRecord за период
- [ ] LLM prompt: группировать по feat/fix/perf/security
- [ ] Возвращать markdown, Content-Disposition: attachment для скачивания
- [ ] Опциональный GitLab API fetch для MR description

Priority: P2 | Effort: M | Impact: medium

---

**FT-16: GitHub PR Support**

Добавить `github_client.py` с поддержкой GitHub Webhooks + GitHub Reviews API.

AC:
- [ ] Webhook path `/webhook/github`, secret validation (`X-Hub-Signature-256`)
- [ ] `github_client.py`: get_pr_diff, post_pr_review_comment
- [ ] Config: `providers.type: github`
- [ ] README: GitHub setup section

Priority: P2 | Effort: XL | Impact: high

---

**FT-17: MR Description Enhancement (/describe)**

`/describe` slash command → LLM генерирует улучшенное описание MR и обновляет его через API.

AC:
- [ ] Slash command `/describe` в `slash_commands.py`
- [ ] LLM prompt: "Generate a clear MR description based on diff"
- [ ] PUT `/projects/:id/merge_requests/:iid` с обновлённым description
- [ ] Оригинальное описание сохраняется в БД перед заменой

Priority: P2 | Effort: S | Impact: medium

---

**FT-18: Review Cost Tracking**

Для cloud LLM провайдеров — считать стоимость каждого ревью.

AC:
- [ ] `ReviewRecord.cost_usd: float | None` — новое поле + миграция
- [ ] `config.yml`: `model.price_per_1k_input_tokens`, `price_per_1k_output_tokens`
- [ ] Dashboard: стоимость за день/неделю, топ-5 дорогих проектов
- [ ] `/api/v1/stats/cost` endpoint

Priority: P2 | Effort: M | Impact: medium

---

**FT-19: Webhook Dead-Letter Queue (Retry)**

Failed jobs → retry queue с exponential backoff. UI кнопка "Retry".

AC:
- [ ] `review_failed` table: job_id, error, retry_count, next_retry_at
- [ ] Retry worker: exponential backoff (1m → 5m → 15m → give up)
- [ ] UI: вкладка Failed с кнопкой Retry
- [ ] `POST /api/v1/reviews/{id}/retry` endpoint

Priority: P2 | Effort: M | Impact: medium

---

**FT-20: GitLab Severity Labels**

Автоматически ставить label на MR по максимальной находке: `glr::critical`, `glr::high`, `glr::clean`.

AC:
- [ ] `review_targets.labels.enabled: true`
- [ ] `review_targets.labels.prefix: "glr"` (configurable)
- [ ] GitLab API: POST /labels (create if not exists), PUT /merge_requests (add label)
- [ ] Тест с mock GitLab

Priority: P2 | Effort: S | Impact: medium

---

## 🔧 New Refactoring Ideas (2026-03-07)

---

**RFT-1: Cross-Instance Dedup via Valkey SET NX EX**

⚠️ Production bug: in-memory dedup не работает при replicas > 1 в k8s.

AC:
- [ ] Заменить `DedupCache._seen` на Valkey `SET NX EX` atomic operation
- [ ] Ключ: `glr:dedup:{project_id}:{mr_iid}:{diff_hash}`, TTL = cooldown
- [ ] Fallback к in-memory если Valkey недоступен
- [ ] Тест: параллельный enqueue одного job → только один проходит

Priority: P1 | Effort: M | Impact: high

---

**RFT-2: OpenTelemetry Distributed Tracing**

Один trace = один review job: webhook → enqueue → fetch_diffs → LLM_call → post_comment.

AC:
- [ ] `opentelemetry-sdk` + `opentelemetry-instrumentation-fastapi` + `opentelemetry-instrumentation-httpx`
- [ ] OTLP export: `OTEL_EXPORTER_OTLP_ENDPOINT` env var
- [ ] Span attrs: `review_id`, `project_id`, `mr_iid`, `role`, `model`
- [ ] docker-compose profile `otel` с Jaeger

Priority: P2 | Effort: M | Impact: medium

---

**RFT-4: LLM Token Pre-Flight Check**

Подсчёт токенов до отправки в LLM. Если diff > context_size × 0.85 → Smart Compression.

AC:
- [ ] tiktoken для OpenAI-compat, `/api/tokenize` для ollama
- [ ] `config.yml`: `model.context_size: 32768` (уже есть, использовать)
- [ ] При overflow: автоматический trigger FT-9 или truncate с warning
- [ ] Метрика `review_token_count` в Prometheus

Priority: P1 | Effort: M | Impact: high

---

**RFT-7: Graceful SIGTERM / k8s Shutdown**

AC:
- [ ] `signal.signal(SIGTERM, _shutdown_handler)` в `main.py`
- [ ] При SIGTERM: stop accepting (503), drain in-flight jobs (timeout 120s), flush Prometheus
- [ ] `SIGTERM_GRACE_PERIOD: int = 120` env var
- [ ] k8s `terminationGracePeriodSeconds: 150` в helm values

Priority: P2 | Effort: M | Impact: medium

---

**RFT-9: Pydantic v2 Audit — SecretStr model_dump**

Аудит всех `model_dump()` без `mode="json"` — потенциальный leak SecretStr.

AC:
- [ ] grep всех `model_dump(` без `mode=`
- [ ] Добавить mypy strict check на SecretStr handling
- [ ] bandit scan в CI: `bandit -r src/ -ll`

Priority: P1 | Effort: S | Impact: high

---

**RFT-10: API Rate Limiting на Webhook**

AC:
- [ ] `slowapi` + `limiter = Limiter(key_func=get_remote_address)`
- [ ] `@limiter.limit("100/minute")` на `/webhook/gitlab`
- [ ] 429 ответ с `Retry-After` header
- [ ] Тест: 101 запрос → последний возвращает 429

Priority: P2 | Effort: S | Impact: medium

---

## 🧪 New E2E Test Ideas (2026-03-07)

---

**E2E-1: Full Webhook → Review → GitLab Comment**

Главный happy path сквозного теста.

AC:
- [ ] `httpx.AsyncClient(transport=ASGITransport(app=app))`
- [ ] `respx` mock для GitLab API (get_mr, get_diffs, post_mr_note)
- [ ] Mock LLM → детерминированный ответ
- [ ] Assert: `post_mr_note` вызван с comment содержащим Risk Score

Priority: P1 | Effort: M | Impact: high

---

**E2E-2: Slash Commands Full Cycle**

Note Hook webhook → parse → LLM → reply comment.

AC:
- [ ] Note Hook payload с `/summary`
- [ ] Mock GitLab `get_diffs` + `post_mr_note`
- [ ] Assert: reply-comment запостен в GitLab

Priority: P1 | Effort: M | Impact: high

---

**E2E-3: SSE Streaming Integration**

AC:
- [ ] POST /api/v1/queue/review → stream_url
- [ ] Подключиться к SSE endpoint, собрать chunks
- [ ] Assert: chunks → финальный text == DB ReviewRecord.review_text
- [ ] Late-connect scenario: подключиться через 0.5s → не пропустить токены (BUG-9)

Priority: P2 | Effort: M | Impact: medium

---

**E2E-4: Queue Backend Contract Tests (Testcontainers)**

AC:
- [ ] `testcontainers-python` для Valkey (redis-compatible)
- [ ] Параметризованный pytest fixture: `[MemoryBackend, ValkeyBackend]`
- [ ] Один набор тестов: enqueue + dequeue + dedup + stats

Priority: P2 | Effort: M | Impact: medium

---

**E2E-5: Rules Engine Post-Diff Integration**

После реализации RFT-R07 (post-diff rules evaluation):

AC:
- [ ] Webhook → diff fetch (mock files) → rules(if_files_match security/**) → skip
- [ ] Assert: если match → enqueue не вызван; если no match → вызван

Priority: P1 | Effort: S | Impact: high (после RFT-R07)
