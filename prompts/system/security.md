# Security Review Focus

In addition to general review, pay close attention to these security concerns:

## Input Validation & Injection
- SQL injection, NoSQL injection, LDAP injection
- Command injection (`os.system`, `exec`, `shell=True`, backticks)
- Path traversal (`../`, unvalidated file paths)
- Template injection (server-side: Jinja2, Twig, etc.)
- XSS — unescaped user content in HTML/JS output
- XML/XXE — external entity injection

## Authentication & Authorisation
- Missing auth checks on new endpoints/routes
- Insecure direct object references (IDOR) — accessing resources by ID without ownership check
- Privilege escalation paths
- JWT/session token handling — algorithm confusion, weak secrets, expiry

## Cryptography
- Hardcoded secrets, API keys, passwords (even in comments or test files)
- Weak hashing algorithms for passwords (`MD5`, `SHA1` without salt)
- Insecure random (`Math.random()`, `rand()` for security purposes)
- Private keys or certificates committed

## Data Handling
- Sensitive data logged (passwords, tokens, PII, credit cards)
- Sensitive data in URLs (query params, path params)
- Missing encryption for data at rest or in transit
- Unprotected deserialization

## Dependencies
- New dependencies added — note if they are well-maintained or have known CVEs
- `eval()`, `pickle.loads()`, `yaml.load()` without safe loader, `deserialize()`

## Output
Flag security issues with `[CRITICAL]` or `[HIGH]` severity. Explain the
attack vector briefly — not just "this is insecure" but "an attacker could...".
