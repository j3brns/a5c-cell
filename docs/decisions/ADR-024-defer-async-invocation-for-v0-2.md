# ADR-024: Defer Async Invocation for v0.2

## Status: Accepted
## Date: 2026-04-26

## Context
ADR-005 and ADR-010 described the intended AgentCore-native async path: agents use
`app.add_async_task` and `app.complete_async_task`, the bridge returns `202 Accepted`,
and clients poll or receive a webhook when the job reaches a terminal state.

The checked-in v0.2 bridge only created `pending` job records for async agents. It did
not submit a complete native async execution, persist results, or transition job status
to `completed` or `failed`. That behavior was worse than unsupported: clients received
a job id that could never complete through the platform path.

## Decision
Async invocation is not part of the v0.2 supported contract.

For v0.2:
- agent manifests and registration accept only `sync` and `streaming`
- the bridge rejects async-mode agent records with `UNSUPPORTED_INVOCATION_MODE`
- the OpenAPI invoke route does not advertise `202 Accepted`
- job polling and webhook delivery remain as supporting surfaces for terminal job
  records, not as an async execution backend

## Consequences
- The platform no longer creates dead `pending` jobs for async invokes.
- Sync and streaming behavior is unchanged.
- AgentCore-native async can be reintroduced later only with an owned completion path:
  runtime submission, status transition, result persistence, webhook eventing, and
  operability evidence.

## Alternatives Rejected
- Keep returning `202 Accepted`: preserves a broken client contract.
- Poll inside the bridge Lambda until completion: conflicts with long-running async
  duration and Lambda limits.
- Reintroduce an SQS async runner: contradicts ADR-010 and routes execution outside
  the AgentCore-native model.
