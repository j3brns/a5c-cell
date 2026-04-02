# BUG: dev-bootstrap .env.test tenant IDs do not match DynamoDB fixtures

## Seq
863

## Depends on
none

## Problem

`write_env_test()` in `scripts/dev-bootstrap.py:375-377` writes:

```
BASIC_TENANT_ID=t-basic-001
PREMIUM_TENANT_ID=t-premium-001
ADMIN_TENANT_ID=t-basic-001
```

But the DynamoDB tenant fixtures at lines 166-200 seed:

```python
"tenant_id": "t-test-001",   # basic
"tenant_id": "t-test-002",   # premium
```

Any test that reads `BASIC_TENANT_ID` or `PREMIUM_TENANT_ID` from `.env.test`
and attempts a DynamoDB lookup will get a 404 — the IDs don't exist as seeded
records.

Additionally, line 374 generates a new `SCOPED_TOKEN_SIGNING_KEY` on every run
of dev-bootstrap but does not seed a corresponding SSM parameter or Secrets
Manager secret in LocalStack.  Any Lambda that reads this key at runtime will
not find a matching value, and the key changes on every `make dev`, silently
invalidating previously-issued scoped tokens.

## Scope

- Fix `.env.test` to use `t-test-001` / `t-test-002` matching the fixtures.
- Seed `SCOPED_TOKEN_SIGNING_KEY` as an SSM parameter
  (`/platform/config/scoped-token-signing-key`) in LocalStack during bootstrap.
- Consider making the signing key deterministic in dev mode (e.g. fixed value
  or derived from a stable seed) so tokens survive re-bootstraps.
- Update `ADMIN_TENANT_ID` to a valid admin fixture or the `platform` tenant.

## Test Plan

```bash
uv run python scripts/dev-bootstrap.py
# Verify .env.test IDs match DynamoDB:
grep TENANT_ID .env.test
uv run pytest tests/unit/test_dev_bootstrap.py -v
make validate-local
```

## Definition of Done

- `.env.test` tenant IDs match seeded DynamoDB fixture records.
- Signing key is seeded in LocalStack SSM for runtime consumption.
- `uv run pytest tests/unit/test_dev_bootstrap.py` passes.
- `make validate-local` passes.
