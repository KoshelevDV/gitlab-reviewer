# Python-specific Review Guidelines

Focus on Python-specific concerns in addition to the general review:

**Type Safety**
- Check for missing type annotations (parameters, return types, class fields)
- Warn about `Any` overuse or incorrect Optional handling
- Note where `TypeVar` or `Generic` would improve clarity

**Pythonic Patterns**
- Flag anti-patterns: manual index loops instead of `enumerate()`, `zip()`, comprehensions
- Check for mutable default arguments (e.g., `def f(x=[])`)
- Identify redundant `__init__` that just calls `super().__init__()`
- Suggest dataclasses or NamedTuple where appropriate

**Error Handling**
- Bare `except:` clauses that swallow all exceptions
- `except Exception` used too broadly
- Missing `finally` for resource cleanup (prefer `with` statements)

**Async/Await**
- Blocking I/O calls inside `async def` (e.g., `time.sleep`, `open()` without `aiofiles`)
- Missing `await` on coroutines
- `asyncio.gather()` missed where concurrent execution would help

**Security**
- `eval()` / `exec()` on user input
- Shell injection via `os.system()` or `subprocess` with `shell=True`
- Hardcoded secrets or credentials

**Performance**
- Repeated attribute lookups in tight loops (cache with local var)
- N+1 DB query patterns in ORM usage
- Large list concatenation with `+` instead of `extend()` or `join()`
