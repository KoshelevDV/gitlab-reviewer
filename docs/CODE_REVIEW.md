# Code Review — gitlab-reviewer

> Дата: 2026-03-01  
> Проверено: все модули src/ + tests/  
> Тестов до ревью: 361 / 361 ✅

---

## 🔴 Критические проблемы

### BUG-1 · Двойной маршрут `/health` — реальная проверка никогда не срабатывает

**Файл:** `src/webhook.py:81`, `src/main.py:124`

`make_webhook_router()` регистрирует `GET /health` (простая заглушка).  
В `main.py` после него регистрируется `health_router` из `api/health.py` — тоже `GET /health`.  
FastAPI отдаёт **первый** совпадающий маршрут. Итог: полноценная проверка DB + queue + config **никогда не вызывается**.

```python
# main.py
app.include_router(make_webhook_router())  # ← регистрирует GET /health (stub)
app.include_router(health_router)          # ← GET /health скрыт предыдущим
```

**Фикс:** удалить `GET /health` из `webhook.py`.

---

### BUG-2 · `tls_verify=false` в конфиге игнорируется

**Файл:** `src/gitlab_client.py:45`, `src/config.py:57`

`GitLabConfig.tls_verify` присутствует в конфиге и UI, но `GitLabClient.__init__` никогда не передаёт `verify=...` в `httpx.AsyncClient`. Self-signed GitLab → `SSLError`, пользователь не понимает почему.

```python
# gitlab_client.py — сейчас
self._client = httpx.AsyncClient(
    headers={...},
    timeout=timeout,
    # verify не передаётся!
)
```

**Фикс:** принять `tls_verify: bool = True` в конструкторе и передать в httpx.

---

### BUG-3 · Env-var секреты уведомлений записываются в `config.yml`

**Файл:** `src/config.py:_inject_secrets`, `src/config.py:save_config`

`GLR_NOTIFY_WEBHOOK_URL`, `GLR_TELEGRAM_BOT_TOKEN`, `GLR_TELEGRAM_CHAT_ID` устанавливаются напрямую в поля модели (не в private attrs), поэтому `model_dump()` → `save_config()` записывает их в yaml.  
После первого сохранения конфига через UI токен бота окажется в `config.yml`.

```python
# _inject_secrets — сейчас (записывается в yml)
self.notifications.telegram_bot_token = tbt  # ← станет частью dump

# save_config — чистит только gitlab-секреты:
data.get("gitlab", {}).pop("webhook_secret", None)
# notifications.telegram_bot_token НЕ чистится
```

**Фикс:** аналогично `gitlab.webhook_secret` — обнулять перед записью в yaml.

---

## 🟠 Высокие проблемы

### BUG-4 · `datetime.utcnow()` deprecated — падает на Python 3.12+

**Файл:** `src/db.py:list_diff_hashes`

```python
cutoff = datetime.utcnow() - timedelta(hours=hours)  # DeprecationWarning → RuntimeWarning в 3.12
```

Кроме того, `list_diff_hashes()` открывает **отдельное** `aiosqlite.connect(self._path)` вместо использования `self._db`. Это второе соединение не имеет `row_factory` и нарушает транзакционную изоляцию при WAL mode.

**Фикс:** использовать `datetime.now(UTC)` и `self._db`.

---

### BUG-5 · Несчитанный счётчик инъекций в `sanitize_untrusted()`

**Файл:** `src/prompt_engine.py:sanitize_untrusted`

```python
stripped_count += result.count(pat.pattern)  # pat.pattern — это строка regex, не то что нашли!
```

`result.count("\\bIGNORE\\s+...")` всегда возвращает 0. Счётчик инъекций всегда 0 → `logger.warning` никогда не срабатывает.

**Фикс:**
```python
matches = pat.findall(result)
stripped_count += len(matches)
result = pat.sub("[REDACTED]", result)
```

---

### BUG-6 · Delayed requeue tasks не отслеживаются — утечка при shutdown

**Файл:** `src/reviewer.py:_do_review`

```python
asyncio.create_task(_delayed_requeue(self._queue, job, remaining_secs))
```

Задача создаётся без сохранения ссылки. При shutdown (`queue.drain()`) она не отменяется. Если накопится много кулдаунов, задачи висят в фоне. GC может их прибить, или они выживут и будут пытаться enqueue после drain.

**Фикс:** хранить в `Reviewer` список `_pending_requeue_tasks`, отменять при shutdown.

---

### BUG-7 · Webhook: нет ограничения размера тела запроса

**Файл:** `src/webhook.py`

```python
body: dict[str, Any] = await request.json()  # читает всё тело без лимита
```

Злоумышленник отправляет 100MB JSON → OOM. FastAPI не ставит лимит по умолчанию.

**Фикс:** проверять `request.headers.get("content-length")` и отклонять > N KB.

---

## 🟡 Средние проблемы

### WARN-1 · Prompt cache никогда не инвалидируется

**Файл:** `src/prompt_engine.py:_read_file`

`self._cache: dict[str, str]` заполняется при первом чтении и никогда не очищается. Если пользователь редактирует `.md` файл через UI (в будущем), изменения применятся только после рестарта.

**Фикс:** при горячей перезагрузке конфига (`POST /api/v1/config/reload`) сбрасывать `prompts._cache`.

---

### WARN-2 · `/drain` убивает воркеры без возможности перезапуска

**Файл:** `src/api/queue_api.py:drain_queue`

После `POST /api/v1/queue/drain` воркеры останавливаются насовсем (`self._workers.clear()`). Единственный способ восстановить — рестарт сервиса.

**Фикс:** добавить `POST /api/v1/queue/start` или после drain сразу перезапускать воркеры с тем же `review_fn`.

---

### WARN-3 · Тип `Reviewer.__init__` несовместим с Valkey/Kafka backend

**Файл:** `src/reviewer.py:Reviewer.__init__`, `src/reviewer.py:_delayed_requeue`

```python
class Reviewer:
    def __init__(self, prompts: PromptEngine, queue: QueueManager) -> None:
        # queue: QueueManager, но может быть ValkeyQueueManager / KafkaQueueManager
```

Работает за счёт duck typing, но mypy/pyright будут ругаться при строгой проверке.

**Фикс:** ввести `Protocol` или `TypeAlias` для типа очереди.

---

### WARN-4 · `gitlab_client.test_connection()` — 2 последовательных HTTP-запроса

**Файл:** `src/gitlab_client.py:test_connection`

Version и user fetched последовательно. Можно ускорить в 2x с `asyncio.gather`.

---

### WARN-5 · Webhook не валидирует `project_id` / `mr_iid` типы

**Файл:** `src/webhook.py`

```python
project_id = body.get("project", {}).get("id")  # может быть None, str или int
mr_iid = attrs.get("iid")                       # та же история
```

При `project_id=None` и `mr_iid=None` HTTPException вернётся, но если `project_id=""` (пустая строка) — пройдёт дальше и упадёт в GitLab клиенте с непонятной ошибкой.

---

### WARN-6 · `chat()` в `LLMClient` глотает все `KeyError`

**Файл:** `src/llm_client.py:chat`

```python
except (httpx.HTTPStatusError, KeyError):
    # fallback to ollama native
```

`KeyError` может прийти не из `data["choices"][0]["message"]["content"]`, а из другого места. Fallback на ollama native — а если провайдер вообще не ollama? Тогда второй запрос `/api/chat` тоже упадёт с 404, но с менее понятным сообщением.

**Фикс:** разделить — ловить `KeyError` отдельно, логировать структуру ответа.

---

### WARN-7 · `import json` внутри генератора `chat_stream`

**Файл:** `src/llm_client.py:chat_stream`

```python
async for line in resp.aiter_lines():
    if ...:
        import json  # ← импорт на каждой итерации
```

Python кэширует модули, так что это не катастрофа, но плохой стиль.

---

## 🟢 Низкие / стиль

### STYLE-1 · Дублирующийся код деdup-логики в трёх backend'ах

`QueueManager`, `ValkeyQueueManager`, `KafkaQueueManager` все содержат одинаковый код:
```python
def _is_seen(self, key: tuple) -> bool: ...
def is_already_seen(...) -> bool: ...
def mark_seen(...) -> None: ...
async def load_seen_from_db(...) -> int: ...
```

**Рефакторинг:** вынести в `Deduplicator` mixin или standalone-класс.

---

### STYLE-2 · `_find_target()` поддерживает только `group` по `id == project_id`

**Файл:** `src/reviewer.py:_find_target`

```python
if t.type == "group" and t.id == project_id:
    return t
```

Реальный GitLab group id ≠ project_id. Таргет типа `group` никогда не матчится.  
(Вероятно, намерение было искать по namespace, но логика не реализована.)

---

### STYLE-3 · Нет retry для HTTP-вызовов GitLab/LLM

Временные сетевые ошибки (503, ConnectionError) сразу пишутся в `error` статус. Один retry с jitter существенно снизил бы noise.

---

## Итоговая таблица

| ID | Серьёзность | Файл | Описание | Статус |
|----|-------------|------|----------|--------|
| BUG-1 | 🔴 Critical | webhook.py + main.py | Двойной /health, реальная проверка не работает | ✅ Fixed |
| BUG-2 | 🔴 Critical | gitlab_client.py | tls_verify игнорируется | ✅ Fixed |
| BUG-3 | 🔴 Critical | config.py | Env-var секреты пишутся в config.yml | ✅ Fixed |
| BUG-4 | 🟠 High | db.py | datetime.utcnow() deprecated + лишнее соединение | ✅ Fixed |
| BUG-5 | 🟠 High | prompt_engine.py | Счётчик инъекций всегда 0 | ✅ Fixed |
| BUG-6 | 🟠 High | reviewer.py | Untracked delayed requeue tasks | ✅ Fixed |
| BUG-7 | 🟠 High | webhook.py | Нет лимита размера тела | ✅ Fixed |
| WARN-1 | 🟡 Medium | prompt_engine.py | Кэш промптов не инвалидируется | ✅ Fixed (Q-4) |
| WARN-2 | 🟡 Medium | queue_api.py | /drain необратим | ✅ Fixed (Q-3: /queue/start) |
| WARN-3 | 🟡 Medium | reviewer.py | Тип queue: QueueManager неверный | ✅ Fixed (QueueLike Protocol) |
| WARN-4 | 🟡 Medium | gitlab_client.py | Sequential requests в test_connection | ✅ Fixed (asyncio.gather) |
| WARN-5 | 🟡 Medium | webhook.py | Слабая валидация project_id/mr_iid | ✅ Fixed |
| WARN-6 | 🟡 Medium | llm_client.py | KeyError fallback слишком широкий | ✅ Fixed (split except) |
| WARN-7 | 🟢 Low | llm_client.py | import json внутри генератора | ✅ Fixed (module-level) |
| STYLE-1 | 🟢 Low | backends/* | Дублирующийся dedup-код | ✅ Fixed (DedupCache) |
| STYLE-2 | 🟢 Low | reviewer.py | group target matching не работает | ✅ Fixed (Q-5) |
| STYLE-3 | 🟢 Low | — | Нет retry на HTTP-ошибки | ✅ Fixed (Q-7 tenacity) |

**Итого:** 17/17 закрыто ✅
