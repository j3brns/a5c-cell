# SECURITY: Authoriser logs full JWT payload on validation failure — PII leakage

## Seq
864

## Depends on
none

## Problem

`handle_jwt()` in `src/authoriser/handler.py:350-351` logs the full decoded JWT
payload when a required claim is missing:

```python
logger.error("Missing tenantid or appid in token", extra={"payload": payload})
```

Entra ID JWT payloads contain PII: `email`, `name`, `preferred_username`, `oid`
(user object ID), and any custom claims.  Logging the full payload to CloudWatch
creates a PII exposure surface.  If logs are shipped to a less-restricted sink
(central logging account, third-party SIEM), this becomes a GDPR compliance
issue — data must stay in the EU and PII in logs often escapes classification.

Additionally, line 388 uses an f-string instead of structured logging:

```python
logger.warning(f"Invalid JWT: {str(e)}")
```

This is inconsistent with the rest of the codebase (which uses structured kwargs)
and can embed token fragments in the log message string rather than in filterable
structured fields.

## Scope

- Replace `extra={"payload": payload}` with
  `extra={"present_claims": sorted(payload.keys())}` — log which claims exist,
  not their values.
- Replace the f-string on line 388 with structured logging:
  `logger.warning("Invalid JWT", extra={"error": str(e)})`.
- Audit all other `logger.*` calls in `src/authoriser/handler.py` for any that
  could emit token contents, secrets, or PII.

## Test Plan

```bash
uv run pytest tests/unit/ -k "authoriser or authorizer" -v
# Verify no payload values in log output:
grep -r '"payload"' src/authoriser/
make validate-local
```

## Definition of Done

- No JWT payload values appear in any log call in the authoriser.
- Structured logging used consistently (no f-strings in logger calls).
- `make validate-local` passes.
- No unrelated files changed.
