# ADR-019: Tenant Execution Role Account Boundary

## Status: Accepted
## Date: 2026-04-20

## Context

Issue `#5` originates from security finding **ENG-03**: the Bridge Lambda can
assume `arn:aws:iam::*:role/platform-tenant-*-execution-role`, which is too
broad unless cross-account tenant execution is explicitly supported and fully
threat-modeled.

The current repository contains a **partial** cross-account lane, but it is not
coherent end-to-end:

- the Bridge validates that `executionRoleArn.account == tenant.accountId`
  before `sts:AssumeRole`
- `TenantStack` creates a tenant execution role in the current stack/account and
  stores that exact ARN in SSM
- the authoriser's SigV4 path binds machine callers to a tenant through the
  exact stored `executionRoleArn`
- the Terraform vended-account module creates a different cross-account trust
  shape that requires `sts:ExternalId`
- the Bridge call path does not currently pass `ExternalId`
- the Terraform vended-account role name does not match the Bridge IAM allow
  pattern
- tenant create/provision paths currently accept arbitrary `accountId` values
  without an allow-list contract
- no supported repo path writes Terraform vended-account outputs back into the
  control-plane `executionRoleArn`/SSM source of truth

That means the repository does **not** currently have a safe, operable
`cross-account allow-list` implementation. The only boundary that is coherent
today is the same-account path.

## Decision

The platform adopts **`same-account only`** as the current tenant
execution-role account-boundary decision.

This means:

1. `tenant.accountId` must equal the platform home account for the supported
   tenant execution-role path.
2. `executionRoleArn` in tenant metadata and
   `/platform/tenants/{tenantId}/execution-role-arn` must resolve to that same
   home account.
3. The Bridge must assume only same-account tenant execution roles for the
   current supported design.
4. The existing any-account wildcard in Bridge IAM is treated as a defect to be
   remediated, not as an approval of cross-account operation.

## Explicit Vocabulary

Use exactly these terms:

- **`same-account only`**
  - Supported current model.
  - Tenant `accountId`, tenant `executionRoleArn`, and Bridge `sts:AssumeRole`
    target must all be in the home account.

- **`cross-account allow-list`**
  - Not supported by the current implementation.
  - Reserved for a future successor ADR plus implementation that adds:
    explicit approved-account policy, Bridge allow-list IAM, `ExternalId`
    handling, exact metadata/SSM sync, and aligned role naming/trust rules.

## Why `cross-account allow-list` Is Rejected For Now

The repository is not ready to claim cross-account support yet:

- the Bridge IAM policy is broader than intended, but broad IAM by itself is not
  a valid approval boundary
- the Terraform cross-account role trust requires `sts:ExternalId`, while the
  Bridge wrapper never supplies one
- the Terraform cross-account role name does not line up with the Bridge IAM
  resource pattern
- tenant metadata accepts arbitrary `accountId` values without a documented
  approved-account contract
- the authoriser and Bridge both rely on exact `executionRoleArn` identity, but
  the repo does not yet provide an end-to-end path that syncs Terraform
  vended-account outputs into that authoritative binding

Choosing `cross-account allow-list` in this issue would therefore document an
intended future state, not a current safe boundary.

## Boundary Rules

### Required current invariants

- `tenant.accountId == home account`
- `executionRoleArn.account == tenant.accountId`
- the SSM execution-role ARN value and tenant metadata value must agree on the
  same-account target
- SigV4 machine binding continues to resolve by exact `executionRoleArn`, not by
  caller-selected tenant header or account ID alone

### Explicitly disallowed

- assuming an arbitrary role in another account solely because the role name
  matches `platform-tenant-*-execution-role`
- treating the current wildcard Bridge IAM resource as evidence that
  cross-account execution is approved
- accepting non-home `accountId` values as a normal tenant execution path
  without a successor cross-account design

## Consequences

### Positive

- matches the only end-to-end execution-role path that is coherent today
- gives ENG-03 a clear remediation target
- preserves the exact-role-ARN binding used by the authoriser and Bridge

### Negative

- blocks use of the partial account-vending execution-role lane for now
- requires follow-up work to stop tenant creation/provisioning from implying
  unsupported cross-account operation

## Required Follow-Up Issues

This issue is decision-only. The following follow-up issues are required:

1. **Constrain Bridge `sts:AssumeRole` permissions to the home account only.**
   - Replace `arn:aws:iam::*:role/platform-tenant-*-execution-role` with the
     home-account resource shape.
   - Tests must prove matching roles in unrelated accounts are denied.

2. **Reject non-home `accountId` on the supported tenant create/provision path.**
   - Tenant create/provision currently accepts arbitrary `accountId`.
   - The supported same-account path must enforce the home-account invariant.

3. **Document the Terraform vended-account execution-role lane as future/incomplete, not current Bridge behavior.**
   - Architecture and operator docs must not imply that the current Bridge path
     already supports that Terraform trust model.

4. **If cross-account support is still desired later, open a successor ADR + implementation issue set for `cross-account allow-list`.**
   - That future work must cover allow-listed accounts, Bridge IAM,
     `sts:ExternalId`, role naming alignment, metadata/SSM sync, and
     validation/threat-model updates.

## Rejected Alternatives

- **`cross-account allow-list` now**: rejected because the repository does not
  yet implement the trust, metadata, IAM, and sync requirements needed to make
  it real.
- **Broad cross-account wildcard by role-name convention**: rejected because role
  name alone is not a safe approval boundary and recreates ENG-03.
- **Account-only tenant binding**: rejected because the authoriser and Bridge
  both depend on exact `executionRoleArn` identity, not just account membership.

## Implementation Notes / Evidence

The decision is based on the current repository behavior:

- `src/bridge/runtime_calls.py` validates `executionRoleArn.account == tenant.accountId`
- `src/authoriser/sigv4_service.py` resolves SigV4 callers via exact
  `executionRoleArn` on `gsi-execution-role-arn`
- `infra/cdk/lib/tenant-stack.ts` creates the same-account tenant execution role
  and writes its ARN to SSM
- `src/tenant_provisioner/handler.py` propagates `accountId` without enforcing a
  home-account invariant
- `infra/terraform/modules/vended-account/main.tf` defines a cross-account role
  that currently requires trust behavior the Bridge path does not supply
