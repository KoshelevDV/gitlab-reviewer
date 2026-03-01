## Inline Comment Format

For each finding, output an inline annotation **in addition to** your summary.
Use the following XML-comment format so the system can post findings directly on the relevant lines:

```
<!-- REVIEW_INLINE file="path/to/file.py" line="42" -->
**[CRITICAL]** SQL injection vulnerability: user input is interpolated directly into the query string.
Use parameterised queries: `cursor.execute("SELECT * FROM users WHERE username = ?", (username,))`
<!-- REVIEW_ENDINLINE -->
```

Rules:
- `file` — exact path as it appears in the diff (e.g. `src/user_service.py`)
- `line` — line number in the **new** file where the issue appears
- Severity prefix: `[CRITICAL]`, `[HIGH]`, `[MEDIUM]`, `[LOW]`, or `[INFO]`
- Keep each inline comment focused on **one** issue, ≤ 5 sentences
- Produce **up to 10** inline comments per review
- After all inline blocks, write a `## Summary` section with your overall assessment
