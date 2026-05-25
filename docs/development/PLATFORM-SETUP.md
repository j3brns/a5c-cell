# Local Development Setup

This guide is for platform engineers working on `src/`, `infra/`, `spa/`, and
`gateway/`. If you are building only an agent under `agents/<name>/`, start with
[AGENT-DEVELOPER-GUIDE.md](AGENT-DEVELOPER-GUIDE.md) instead.

## Prerequisites

| Tool         | Version    | Install                                               |
|--------------|------------|-------------------------------------------------------|
| uv           | latest     | curl -Ls https://astral.sh/uv/install.sh | sh         |
| Docker       | 24+        | Optional Compose convenience; native mocks also work    |
| Git          | 2.30+      | system package manager                                |
| Node         | 20 LTS     | Handled by `make bootstrap-platform` (or nvm)         |
| AWS CLI      | v2         | Required locally; verify with `make ensure-tools`      |
| glab         | latest     | Required for issue/MR workflows; verify with `make ensure-tools` |
| cfn-guard    | v3         | Required for guard validation; verify with `make ensure-tools` |

## .env.local Values

Copy `.env.example` to `.env.local` and fill in these values. Read-only local
settings load `.env.example` first, then `.env`, `.env.local`, and `.env.test`;
real shell environment variables still win. AWS-mutating scripts still require
critical execution values such as `AWS_REGION` from the real process environment
so they fail closed in clean shells. Keep new local script settings in
`.env.example` so the typed `platform_config` models remain the source of truth.

| Variable              | Where to Find It                                        |
|-----------------------|----------------------------------------------------------|
| VITE_ENTRA_CLIENT_ID  | Entra portal → App Registrations → platform-{env}       |
| VITE_ENTRA_TENANT_ID  | Entra portal → Overview → Directory (tenant) ID         |
| VITE_ENTRA_SCOPES     | Entra app expose-an-API scopes (space/comma separated)  |
| VITE_API_BASE_URL     | CDK outputs after infra-deploy, or team-platform Slack   |
| GITLAB_PROJECT_ID     | GitLab project settings → General → Project ID           |
| AWS_REGION            | Local/home region; defaults to `eu-west-2`               |

For local development only (no real AWS needed):
```bash
VITE_API_BASE_URL=http://localhost:8080
MOCK_RUNTIME=true
```

## Starting the Local Environment

If you do not have a container runtime, run:

```bash
make bootstrap-runtime
```

This audits Docker Engine, Podman, and Rancher Desktop/nerdctl and prints the
lowest-friction bootstrap path for the current machine. See
[Container Runtime Choices](CONTAINER-RUNTIME-CHOICES.md) for the tradeoffs.
For minimal WSL, install Podman in the Ubuntu distro, run the local AWS emulator
with Podman, then use `make dev-native`.

`make dev` uses Docker Compose only when Docker is reachable from the current
shell. If Docker is unavailable, it starts the mock Runtime, mock JWKS, and local
bridge API as native Python processes. In both modes, the local AWS emulator is
accessed through `AWS_ENDPOINT_URL` and must provide the required AWS APIs.

```bash
make dev
```

This starts:
- **Local AWS emulator** on :4566 — Floci by default in Compose, serving S3, DynamoDB, SSM, Secrets Manager, SQS, and EventBridge
- **Mock AgentCore Runtime** on :8765 — returns canned streaming responses from `/invocations`
- **Mock JWKS endpoint** on :8766 — issues test JWTs
- **Local bridge API** on :8080 — forwards the contracted REST route to the bridge handler

Then seeds the local AWS emulator with two test tenants and all SSM parameters.
Startup is not considered ready until the post-bootstrap seeded state exists:
required DynamoDB tables, required SSM parameters, and a populated `.env.test`.

To force the native path, run a compatible local AWS emulator on `:4566` and use:

```bash
AWS_ENDPOINT_URL=http://localhost:4566 make dev-native
```

## Replacing the Local AWS Emulator

The repository contract is provider-neutral:

- AWS API compatibility for DynamoDB, SSM, S3, SQS, Secrets Manager, EventBridge,
  and STS on `AWS_ENDPOINT_URL` (default `http://localhost:4566`).
- A health endpoint exposed through `LOCAL_AWS_HEALTH_URL` for readiness checks.
- No real AWS account or AWS network endpoint for `make dev`, `make dev-invoke`,
  or `make test-int`.

Floci is the default Compose image, not a code dependency. To try another
emulator image without editing repository files:

```bash
LOCAL_AWS_EMULATOR_IMAGE=example/local-aws:latest \
LOCAL_AWS_HEALTH_URL=http://localhost:4566/health \
make dev
```

If the replacement keeps the LocalStack-compatible health route, only
`LOCAL_AWS_EMULATOR_IMAGE` needs to change.

## Verifying the Setup

```bash
make dev-invoke
```

If this exits cleanly, the local invocation path is wired. `make dev` now also
fails when endpoint health is green but the seeded local state is incomplete.
Use `make test-int` for a stronger end-to-end check once the local stack is running.

**Note**: The **Mock AgentCore Runtime** returns canned responses (defined in `tests/mocks/mock_runtime/main.py`). It does **not** execute your actual agent code. To test your agent's logic locally, use `make agent-test`.

## Identity and Local Mocks

Local identity uses the mock JWKS service on `:8766`. `dev-bootstrap.py` asks
that service for signed RS256 tenant JWTs and writes them to `.env.test`.
`make dev-invoke` sends one of those JWTs to the local bridge API on `:8080`.

The local bridge API is a developer-only adapter. It decodes the local JWT only
to build the same authorizer context API Gateway would pass after the real
authorizer succeeds. It does not replace the production authorizer tests.

Production identity remains:
- API Gateway authorizer validates Entra JWTs or SigV4 machine callers.
- Downstream handlers trust `requestContext.authorizer`, not caller-supplied
  tenant headers.
- AgentCore Gateway REQUEST interceptor replaces the original client JWT with a
  short-lived scoped tool token and injects `x-tenant-id`, `x-app-id`, `x-tier`,
  and `x-acting-sub`.
- Tool Lambdas must validate the scoped token for their own tool audience; they
  must not accept the original client JWT.

Mock Lambda/runtime boundaries:
- Mock JWKS on `:8766` replaces Entra only for local JWT issuance.
- Mock AgentCore Runtime on `:8765` replaces the managed runtime endpoint only
  for bridge invocation tests.
- Gateway request/response interceptors, authorizer logic, CloudFront, and API
  Gateway are validated by unit/integration/CDK tests rather than replaced by
  long-running local Lambda containers.

## Test Tenants (seeded by dev-bootstrap.py)

After `make dev`, two test tenants are available. Their tenant IDs and JWTs are in `.env.test`:

| Variable              | Value format | Purpose  |
|-----------------------|--------------|----------|
| BASIC_TENANT_ID       | `t-test-001` | basic tenant ID |
| BASIC_TENANT_JWT      | JWT string   | basic tenant token |
| PREMIUM_TENANT_ID     | `t-test-002` | premium tenant ID |
| PREMIUM_TENANT_JWT    | JWT string   | premium tenant token |
| ADMIN_JWT             | JWT string   | Platform.Admin token |

Use these with `make dev-invoke` for the local bridge path, or in your tests via `conftest.py`.

## Running Tests

```bash
make test-unit      # Unit tests for Lambda and shared Python code
make test-int       # Integration tests (requires make dev running)
make agent-test AGENT=echo-agent    # Tests for a specific agent
```

## UI Testing Snapshot

Testing fixture screenshot for the Admin console:

![Admin console testing fixture](../images/tf_acore_aas_admin_console_testing.png)

The screenshot uses synthetic test data for documentation and QA walkthroughs.

Additional SPA previews for docs and onboarding:

- ![Tenant dashboard preview](../images/tf_acore_aas_portal_tenant_dashboard.svg)
- ![Admin overview preview](../images/tf_acore_aas_portal_admin_overview.svg)
- ![Members and invites preview](../images/tf_acore_aas_portal_members.svg)
- ![Webhooks preview](../images/tf_acore_aas_portal_webhooks.svg)
- ![Invoke flow preview](../images/tf_acore_aas_portal_invoke.svg)

These are stable fixture-based renders derived from the current SPA page structure, not browser screenshots from a live environment.

## Common Issues

**Local AWS emulator not starting through Compose**: use `make dev-native` with a
native local AWS emulator already listening on `AWS_ENDPOINT_URL`, or ensure
Docker is running (`docker ps` should work). On WSL2, confirm Docker Desktop WSL
integration is enabled for this distro, or that the in-WSL Docker service is
running.

**make dev-invoke fails with 401**: local startup did not complete cleanly.
Check `make dev-logs` and rerun `make dev`.

**uv: command not found**: run `source ~/.bashrc` or open a new terminal after install.

**CDK synth fails**: run `cd infra/cdk && npm install` first.
