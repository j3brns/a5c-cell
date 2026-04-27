# Bootstrap Guide

## Overview

This guide covers first-time deployment of the platform to a new AWS environment.
Follow RUNBOOK-000 for the actual execution steps. This guide explains the
prerequisites and manual steps that cannot be automated.

## AWS Account Setup

The platform bootstrap flow assumes one AWS account per environment.

Target-state note: [ADR-023](decisions/ADR-023-v0-2-secure-deployment-contract.md)
defines v0.2 as one `eu-west-2` platform VPC with the serving AgentCore Runtime in
`eu-west-2`, `NetworkMode: VPC` for staging and production, and no `eu-west-1`
runtime fallback. Bootstrap now seeds that topology by default.

- **Single account, London serving path**:
  - eu-west-2 (London): home region for data plane, control plane, platform VPC,
    and AgentCore Runtime compute
  - eu-central-1 (Frankfurt): non-serving evaluation capability when AWS regional
    support requires it

Record the AWS account ID for the target environment — needed for CDK bootstrap
and IAM role verification.

Before running any bootstrap step, export:

```bash
export BOOTSTRAP_ACCOUNT_ID=<target-account-id>
export PLATFORM_HOME_REGION=eu-west-2
export AWS_REGION=$PLATFORM_HOME_REGION
```

The bootstrap flow now fails closed if the active caller account does not match
`BOOTSTRAP_ACCOUNT_ID` or if `AWS_REGION` does not match `PLATFORM_HOME_REGION`.

## Entra App Registration (manual — see entra-setup.md)

This step cannot be automated. An Entra admin must create the app registration
before bootstrap can run. See docs/entra-setup.md for full instructions.

Once complete, record:
- Application (client) ID
- Directory (tenant) ID
- Client secret value (set in Secrets Manager during bootstrap step 2)

## GitLab OIDC (partially manual)

`make bootstrap-gitlab-oidc` creates the OIDC provider and pipeline roles automatically.
However, the role ARNs must be added to GitLab CI/CD variables manually (UI-only operation).

After running the command, the ARNs are printed to the console. Add them to:
GitLab → Project → Settings → CI/CD → Variables:
- PLATFORM_PIPELINE_VALIDATE_ROLE_ARN
- PLATFORM_PIPELINE_DEPLOY_DEV_ROLE_ARN
- PLATFORM_PIPELINE_DEPLOY_STAGING_ROLE_ARN
- PLATFORM_PIPELINE_DEPLOY_PROD_ROLE_ARN
- APPCONFIG_EXTENSION_LAYER_ARN

Legacy variable names are still accepted temporarily for compatibility:
- AWS_ROLE_ARN_VALIDATE
- AWS_ROLE_ARN_DEPLOY_DEV
- AWS_ROLE_ARN_DEPLOY_STAGING
- AWS_ROLE_ARN_DEPLOY_PROD

## What bootstrap.py Does

Ordered steps with validation at each:

1. **Verify prerequisites** — checks all required tools and account IDs
2. **CDK bootstrap** — creates the CDKToolkit stack in eu-west-2
3. **Seed secrets** — writes Entra credentials and platform private key to Secrets Manager
4. **OIDC wiring** — creates GitLab OIDC provider and pipeline roles, prints ARNs
5. **First CDK deploy** — deploys the 5 bootstrap-supported home-region stacks from the
   local machine (not pipeline). Set `APPCONFIG_EXTENSION_LAYER_ARN` to the
   current AWS-managed ARM64 AppConfig Lambda Extension layer ARN for the target
   region before this step.
6. **Post-deploy seeding** — creates first admin, seeds SSM, registers echo-agent
7. **Smoke test** — validates deployed stacks and seeded records, then optionally invokes echo-agent
8. **Delete bootstrap user** — removes temporary IAM user (MANDATORY)

Each step writes to bootstrap-report.json (S3 bucket: platform-bootstrap-reports-{env}).

## Time Estimates

| Step                  | Duration     |
|-----------------------|--------------|
| Entra app registration| 15–30 min (manual) |
| CDK bootstrap         | 5 min        |
| Secrets seeding       | 2 min        |
| GitLab OIDC + manual  | 5 min + 5 min (manual) |
| First CDK deploy      | 15–20 min    |
| Post-deploy seeding   | 2 min        |
| Smoke test            | 5 min        |
| **Total**             | **~55 min**  |

## Re-Running Bootstrap

Each step in bootstrap.py is idempotent — safe to re-run if a step fails.
The script checks what already exists before creating resources.

Tenant metadata seeded by `bootstrap.py` and `scripts/dev-bootstrap.py` uses the
same camelCase DynamoDB attribute names as the tenant API (`tenantId`, `appId`,
`displayName`, `createdAt`, `updatedAt`, `ownerEmail`, `ownerTeam`, `accountId`,
`executionRoleArn`, `monthlyBudgetUsd`). Runtime readers may keep read-only
snake_case aliases only for compatibility with tenant records written before this
canonical schema was enforced. Remove those aliases only after a separate
migration verifies no live tenant metadata depends on them.

To re-run a specific step using the corresponding make target:
```bash
export BOOTSTRAP_IAM_USER=<bootstrap-iam-username>
export BOOTSTRAP_ACCOUNT_ID=<target-account-id>
export PLATFORM_HOME_REGION=eu-west-2
export AWS_REGION=$PLATFORM_HOME_REGION
make bootstrap-secrets ENV=dev          # re-run step: seed-secrets
make bootstrap-gitlab-oidc ENV=dev      # re-run step: gitlab-oidc
make bootstrap-post-deploy ENV=dev      # re-run step: post-deploy
make bootstrap-verify ENV=dev           # re-run step: verify
```

Or call bootstrap.py directly with the step name:
```bash
export BOOTSTRAP_IAM_USER=<bootstrap-iam-username>
export BOOTSTRAP_ACCOUNT_ID=<target-account-id>
export PLATFORM_HOME_REGION=eu-west-2
export AWS_REGION=$PLATFORM_HOME_REGION
uv run python scripts/bootstrap.py --step seed-secrets --env dev

export APPCONFIG_EXTENSION_LAYER_ARN=<aws-managed-arm64-layer-arn-for-region>
uv run python scripts/bootstrap.py --step first-deploy --env dev
```

## Destroying an Environment

```bash
export APPCONFIG_EXTENSION_LAYER_ARN=<aws-managed-arm64-layer-arn-for-region>
make infra-destroy ENV=dev
# Destroys all CDK stacks
# WARNING: destroys all data including DynamoDB tables and S3 buckets
# Run only on dev — never on prod
```

Re-bootstrapping after destroy takes ~35 minutes (CDK re-uses existing ECR/S3 ARNs).

## Bootstrap Deploy Scope

The day-zero `first-deploy` bootstrap step intentionally deploys only:
- `platform-network-{env}`
- `platform-identity-{env}`
- `platform-core-{env}`
- `platform-tenant-stub-{env}`
- `platform-observability-{env}`

It does not deploy `platform-agentcore-{env}`. `AgentCoreStack` requires explicit runtime
artifact and metric-stream parameters, and the bootstrap path does not have a supported
automatic source for those values on a fresh account. The bootstrap flow and runbooks are
therefore scoped to the 5-stack control-plane deployment until that contract changes.
Under ADR-023, that contract changes to a same-region v0.2 runtime deployment with
fail-closed staging/prod CI gates; this guide remains a pre-implementation note until
the bootstrap issue updates the command flow.
