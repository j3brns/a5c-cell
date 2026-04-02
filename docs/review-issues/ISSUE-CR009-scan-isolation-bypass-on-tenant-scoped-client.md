# SECURITY: TenantScopedDynamoDB.scan() does not enforce tenant isolation

## Seq
862

## Depends on
none

## Problem

`TenantScopedDynamoDB.scan()` and `scan_all()` in
`src/data-access-lib/src/data_access/client.py:333-403` explicitly skip tenant
partition enforcement.  The docstring acknowledges this:

> SECURITY: Scanning is an administrative operation.  Isolation is NOT enforced
> by this method.

This creates a class-level invariant violation: the class is named
`TenantScopedDynamoDB` and every other method enforces tenant isolation, but
`scan`/`scan_all` silently return cross-tenant data.  A developer calling
`db.scan()` on a `TenantScopedDynamoDB` instance may reasonably assume it is
tenant-scoped.

Currently used by `tenant_lifecycle.py:237` (`handle_list` for admin tenant
listing) with an upstream admin check.  A single missing authorization check in
any future call site leaks cross-tenant data.

Additionally, `handle_list` at `tenant_lifecycle.py:207-211` constructs a
`TenantScopedDynamoDB` with a fabricated `tenant_id="system"` to reach the scan
method, which is confusing and fragile.

## Scope

Option A (preferred): Extract `scan`/`scan_all` into a separate
`AdminDynamoDB` class that requires explicit construction with admin context
validation.

Option B: Add a mandatory `require_admin_confirmation: bool` parameter that
must be `True` to call scan, making the authorization requirement visible at
the call site.

In either case:
- Create `_admin_db(caller)` factory in `tenant_api/handler.py` that validates
  admin role at construction time.
- Remove the synthetic `tenant_id="system"` pattern from `handle_list`.
- Update unit tests in `src/data-access-lib/tests/test_dynamodb.py`.

## Test Plan

```bash
uv run pytest src/data-access-lib/tests/ -v
uv run pytest tests/unit/ -k "tenant" -v
make validate-local
```

## Definition of Done

- `TenantScopedDynamoDB` does not expose an unscoped scan method.
- Admin scanning requires explicit admin context.
- No synthetic tenant IDs (`"system"`) used to construct scoped clients.
- All existing admin-list functionality preserved.
- `make validate-local` passes.
