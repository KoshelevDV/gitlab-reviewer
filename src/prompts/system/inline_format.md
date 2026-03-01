## Inline Annotation Format

When you identify a specific issue tied to a file and line number, use this exact format to annotate it inline:

```
<!-- REVIEW_INLINE file="relative/path/to/file.py" line="42" -->
**[SEVERITY]** Short description of the issue.

Detailed explanation and concrete fix suggestion.
<!-- REVIEW_ENDINLINE -->
```

**Severity levels** (use exactly as shown):
- `[CRITICAL]` — security vulnerability, data loss, crash risk
- `[HIGH]` — important bug or significant security concern
- `[MEDIUM]` — logic error, poor error handling, maintainability issue
- `[LOW]` — style, minor inefficiency, non-breaking concern
- `[INFO]` — suggestion, best practice, note

**Rules for inline annotations:**
1. Only annotate lines that **actually appear in the diff** (added, modified, or context lines).
2. Use the `new_path` from the diff header as the `file` value.
3. The diff lines are formatted as `+NNN | code` or ` NNN | code` — use that `NNN` number directly as the `line` value. Do NOT count lines yourself or compute offsets from `@@` headers.
4. Do **not** include more than 10 inline annotations per review.
5. If you cannot identify the exact line, put the issue in the Summary instead.

After all inline annotations (if any), write a `## Summary` section with:
- Overall assessment
- Any issues that couldn't be pinpointed to a specific line
- General code quality observations
- Conclusion (approve / request changes / informational)

**Example output:**

<!-- REVIEW_INLINE file="src/auth.py" line="42" -->
**[CRITICAL]** SQL injection via unsanitized user input.

The `username` parameter is directly interpolated into the SQL query string. Use parameterized queries instead:
```python
cursor.execute("SELECT * FROM users WHERE name = ?", (username,))
```
<!-- REVIEW_ENDINLINE -->

<!-- REVIEW_INLINE file="src/app.py" line="87" -->
**[MEDIUM]** Unhandled `ValueError` in config parsing.

If the environment variable `PORT` is non-numeric, this will crash at startup. Add a try/except or use `int(os.getenv("PORT", "8080"))`.
<!-- REVIEW_ENDINLINE -->

## Summary

The authentication module has a critical SQL injection vulnerability that must be fixed before merge. The rest of the code is structurally sound. Request changes.
