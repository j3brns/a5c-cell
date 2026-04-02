# BUG: Reserved platform tenant cannot be updated via API — admin lockout

## Seq
861

## Depends on
none

## Problem

`_canonical_tenant_id()` in `src/tenant_api/handler.py:241-242` unconditionally
rejects `"platform"` as a reserved tenant ID:

```python
_RESERVED_TENANT_IDS = frozenset({"platform", "admin", "root", "system", "stub"})

def _canonical_tenant_id(value: Any) -> str:
    ...
    if normalized in _RESERVED_TENANT_IDS:
        raise ValueError("tenantId is reserved")
```

This is correct for external callers but blocks **all** API paths including
admin-initiated operations.  The bootstrap script creates the `platform` tenant
by writing directly to DynamoDB (bypassing API validation), but any subsequent
update through the API — tier change, budget update, status change — will be
rejected with `"tenantId is reserved"` even for Platform.Admin callers.

The `system_caller_for_tenant` helper in `tenant_lifecycle.py:95-103` constructs
a system identity but does not exempt reserved IDs from tenant ID validation.
The EventBridge provisioning handler (`handle_tenant_provisioning_event`)
also calls `_canonical_tenant_id(detail.get("tenantId"))`, meaning any
provisioning event for the platform tenant will fail.

## Scope

- Add an internal validation path (e.g. `_validate_tenant_id_internal`) that
  skips the reserved-ID check for system/admin callers operating on existing
  records.
- Routes that **create** tenants continue to reject reserved IDs.
- Routes that **read/update/delete** existing tenants allow Platform.Admin to
  reference reserved IDs.
- Document this distinction in ADR-016 (platform internal tenant).

## Test Plan

```bash
uv run pytest tests/unit/ -k "tenant" -v
make validate-local
```

## Definition of Done

- Platform.Admin can update the `platform` tenant record via PATCH/PUT.
- Tenant creation still rejects reserved IDs for all callers.
- Provisioning events for the platform tenant are handled correctly.
- Unit tests cover both paths (create-reject, update-allow).
- `make validate-local` passes.
