# Team-Specific Rules (example — copy and edit)
#
# Place your custom prompts here. Files in prompts/custom/ override
# files of the same name in prompts/system/.
# Reference this file in config.yml under prompts.system: [... "example_team"]
# or use {{include: example_team}} inside another prompt.

## Project Conventions

- All HTTP handlers must validate input with Pydantic or equivalent.
- Database models must not be returned directly from API endpoints — use DTOs.
- Every new endpoint must have a corresponding integration test.
- Migrations must be reversible (include `downgrade()` / `down()` method).

## Language-Specific Rules

### Python
- Use `from __future__ import annotations` in all new files.
- No `print()` in production code — use `logging`.
- Type hints required on all public functions.

### Go
- Errors must be wrapped with `fmt.Errorf("... %w", err)`.
- No `panic()` outside of `main()` or `init()`.

## Out of Scope
- Do NOT comment on indentation or trailing whitespace — enforced by CI.
- Do NOT flag `TODO` comments — tracked in issues separately.
