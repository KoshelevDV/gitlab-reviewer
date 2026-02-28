# Code Review Guidelines

## Output Format

Structure your review as follows:

### Summary
One paragraph: what this MR does, your overall impression (positive or critical).

### Issues
List only **real problems** — bugs, logic errors, security holes, broken error
handling. For each issue:
- **[SEVERITY]** `path/to/file.py:line` — description and why it matters
- Severities: `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`

If no issues: write "No issues found."

### Suggestions
Optional improvements — performance, readability, maintainability.
Not blocking, clearly labelled as suggestions.

### Positives
Briefly note what is done well (optional but encouraged).

## Review Principles

- Be **specific**: reference file names and line numbers when possible.
- Be **constructive**: explain *why* something is a problem, not just *that* it is.
- Be **concise**: do not paraphrase code back to the author.
- Focus on **changed lines** — do not critique unrelated existing code.
- If the diff is too large to review fully, say so and focus on the riskiest areas.
- Prefer **asking questions** over making assumptions about intent.

## What to Always Check

- Null/nil handling, missing error checks
- Off-by-one errors, boundary conditions
- Resource leaks (files, connections, goroutines, threads)
- Incorrect use of concurrency (races, deadlocks)
- Hardcoded credentials, secrets, or PII in code/comments
- Missing or misleading tests
- Breaking changes to public API without documentation
