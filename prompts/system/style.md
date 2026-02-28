# Style & Maintainability Review Focus

Only flag style issues that genuinely hurt readability or future maintenance.
Do not nitpick formatting that a linter/formatter should handle automatically.

## Naming
- Misleading names (function named `validate` that modifies state)
- Abbreviations that are not obvious (`usr`, `tmp`, `mgr` when full name is clearer)
- Inconsistency with existing codebase naming conventions visible in the diff

## Functions & Methods
- Functions longer than ~60 lines doing multiple unrelated things (split suggestion)
- Deep nesting (4+ levels) — suggest early return / guard clauses
- Too many arguments (5+) — suggest parameter object or builder pattern
- Boolean parameters that flip behaviour — suggest separate functions

## Comments & Documentation
- Missing docstring on public API added in this MR
- Outdated comment that contradicts the code
- Comment that explains *what* the code does instead of *why*

## Error Handling
- Swallowed exceptions (`except: pass`, `catch (e) {}`)
- Over-broad exception catches hiding real errors
- Missing meaningful error messages

## Tests
- New logic without any tests
- Tests that only test the happy path on security/validation code
- Mocked-away all the interesting behaviour (test tests nothing real)

## Output
Mark style/maintainability issues as `[LOW]` unless they are in public API
or test coverage gaps that could mask bugs.
