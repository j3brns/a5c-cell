# REFACTOR: bridge/handler.py is a 1,908-line monolith despite existing submodules

## Seq
871

## Depends on
none

## Problem

`src/bridge/handler.py` (1,908 lines, 58 functions) is the largest source file
in the codebase.  The bridge package already has the right modular structure
(`discovery_service.py`, `config_provider.py`, `runtime_invoker.py`,
`runtime_orchestrator.py`, `invocation_engine.py`), but `handler.py` never
fully delegated to them.  It still contains:

| Concern | Approx lines | Should live in |
|---------|-------------|---------------|
| Global client init + lazy singletons | ~100 | `clients.py` |
| SSM config fetch + caching | ~60 | `config_provider.py` (exists, 130 lines) |
| Distributed locking (acquire/release) | ~60 | `lock_manager.py` or shared `ops_control` |
| Failover trigger logic | ~60 | `failover.py` |
| Tenant/agent record lookup | ~100 | `discovery_service.py` (exists, 228 lines) |
| Execution role resolution + STS assume | ~80 | `role_resolver.py` |
| Runtime invocation: real, mock, sync, streaming, async | ~600 | `runtime_invoker.py` (exists, 154 lines) |
| Invocation logging + metrics | ~200 | `telemetry.py` |
| HTTP routing + Lambda entry | ~150 | stays in `handler.py` |

The `runtime_invoker.py` (154 lines) handles orchestration but the actual
invocation implementations (`invoke_real_runtime` at ~350 lines,
`invoke_mock_runtime` at ~60 lines, `handle_sync_invocation`,
`handle_streaming_invocation`, `handle_async_invocation`) remain in `handler.py`.

## Scope

Move function groups into the modules listed above.  Target `handler.py` at
~400 lines (routing, Lambda entry point, glue between submodules).

Priority order:
1. Move `invoke_real_runtime`, `invoke_mock_runtime`, `handle_sync_invocation`,
   `handle_streaming_invocation`, `handle_async_invocation` into
   `runtime_invoker.py` (~600 lines moved).
2. Move `log_invocation`, `emit_invocation_metrics`, `emit_bedrock_throttle_metric`
   into `telemetry.py` (~200 lines moved).
3. Move `resolve_tenant_execution_role_arn`, `assume_tenant_role`,
   `_validate_execution_role_arn` into `role_resolver.py` (~80 lines moved).
4. Move `acquire_lock`, `release_lock`, `trigger_failover` into
   `failover.py` (~120 lines moved).

## Test Plan

```bash
uv run pytest tests/unit/ -k "bridge" -v
uv run python -c "from src.bridge import handler"  # verify imports resolve
make validate-local
```

## Definition of Done

- `bridge/handler.py` is under 500 lines.
- Each extracted module has a single clear responsibility.
- All existing unit tests pass (with import path updates if needed).
- `make validate-local` passes.
- No functional changes — refactor only.
