# PERFORMANCE: SigV4 authoriser does a full DynamoDB table scan on every machine-auth request

## Seq
854

## Depends on
none

## Status
**RESOLVED** (2026-04-02)

## Problem
...
```

## Solution Implemented
1. **GSI Added:** Added `gsi-execution-role-arn` to the `platform-tenants` table in `infra/cdk/lib/platform-storage.ts`.
2. **O(1) Lookup:** Refactored `resolve_sigv4_tenant_binding()` in `src/authoriser/handler.py` to use `table.query()` on the GSI instead of `table.scan()`.
3. **In-Memory Cache:** Implemented a 60s TTL in-memory cache for the ARN→tenant binding to further reduce DynamoDB round-trips.
4. **Verification:** Unit tests updated and passing in `tests/unit/test_authoriser.py`.

## Definition of Done
- [x] GSI exists in CDK definition and passes cfn-guard.
- [x] `resolve_sigv4_tenant_binding` uses `query()` on the GSI.
- [x] In-memory cache with TTL reduces repeated lookups.
- [x] Unit tests mock the GSI query path.
- [x] `make validate-local` passes.
