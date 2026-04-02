# BUG: Bootstrap seed records use dual-key attribute naming — silent data divergence across paths

## Seq
860

## Depends on
none

## Problem

`step_post_deploy()` in `scripts/bootstrap.py:582-650` writes tenant records with
**both** snake_case and camelCase attribute names for every field:

```python
# scripts/bootstrap.py:587-613 (extract)
"tenant_id": "platform",
"tenantId": "platform",
"owner_email": owner_email,
"ownerEmail": owner_email,
"created_at": now,
"createdAt": now,
```

Meanwhile `scripts/dev-bootstrap.py:148-201` writes **only** snake_case attributes,
and the tenant API (`tenant_lifecycle.py`) writes **only** camelCase via
`_build_update_expression`.

The authoriser (`src/authoriser/handler.py:157`) compensates with a fallback chain
(`item.get("executionRoleArn") or item.get("execution_role_arn")`), masking the
inconsistency.  Any code path that reads only one naming variant will silently
return `None` depending on whether the record was bootstrap-seeded or API-created.

## Scope

- Standardise all DynamoDB attribute names on camelCase (matching the API layer).
- Remove the dual-key writing from `scripts/bootstrap.py` `step_post_deploy()`.
- Update `scripts/dev-bootstrap.py` tenant/agent/tool fixtures to use camelCase.
- Remove the fallback chain in `src/authoriser/handler.py` `resolve_sigv4_tenant_binding()`.
- Add a unit test asserting all fixture dicts in both bootstrap scripts use the
  canonical attribute names from the domain model.

## Test Plan

```bash
uv run pytest tests/unit/test_bootstrap.py tests/unit/test_dev_bootstrap.py -v
make validate-local
```

## Definition of Done

- All DynamoDB fixture records use a single consistent naming convention.
- Authoriser reads one attribute name, not a fallback chain.
- Existing unit tests pass with updated fixtures.
- `make validate-local` passes.
- No unrelated files changed.
