# ADR-022: Independent Platform and Agent Developer CLIs

## Status: Proposed
## Date: 2026-04-26

## Context
The platform currently provides a unified `platform-cli` (implemented via `scripts/platform_cli.py`) that serves multiple personas:
1.  **Platform Engineers/Operators:** Responsible for infrastructure (CDK, Terraform), platform-wide operations, and tenant management.
2.  **Agent Developers:** Responsible for building, testing, and deploying AI agents within their respective tenant contexts.

As identified in the Developer Experience specification (`docs/SPEC-DEV-EXPERIENCE-AND-ADR703.md`), this unified approach has several drawbacks:
-   **Dependency Bloat:** Agent developers are forced to install platform-level dependencies (CDK, Docker, Node.js) that are unnecessary for agent logic iteration.
-   **Persona Conflation:** The CLI help and command structure expose administrative operations to developers and developer tools to operators, creating confusion and increasing the surface area for accidental misconfiguration.
-   **Security Boundaries:** Platform operations require high-privilege credentials (`Platform.Admin`), whereas agent development should operate within more restrictive, tenant-scoped boundaries.
-   **User Experience:** The "inner loop" for an agent developer should be fast and lightweight (Python/uv only), whereas the platform engineer's workflow is naturally more complex.

## Decision
We will split the unified `platform-cli` into two independent, purpose-built CLI tools.

### 1. `platform-cli` (The Operator CLI)
-   **Persona:** Platform Engineers and Operators.
-   **Scope:** Infrastructure management (CDK/Terraform), platform-wide configuration (AppConfig, SSM), global tenant/quota management, and system-wide monitoring.
-   **Prerequisites:** Full platform toolset (Python, Node.js, CDK, Docker, AWS CLI).
-   **Auth:** Requires `Platform.Admin` or `Platform.Operator` roles.

### 2. `agent-cli` (The Developer CLI)
-   **Persona:** Agent Developers.
-   **Scope:** Agent lifecycle management: scaffolding, testing, packaging, pushing to the platform, and invoking agents for verification.
-   **Prerequisites:** Minimalist (Python and `uv` only). No requirement for Node.js, CDK, or Docker for standard development.
-   **Auth:** Operates within tenant-scoped boundaries. Authentication should be streamlined for the developer persona.

### Implementation Strategy
-   **Code Sharing:** Shared logic (e.g., API client, manifest validation, common utilities) will be moved to `src/platform_utils` or a dedicated library if necessary, ensuring both CLIs use consistent underlying logic without duplicating code.
-   **Command Migration:**
    -   `infra`, `ops`, and `validate` sub-apps from the current `platform-cli` will remain in the operator tool.
    -   The `agent` and `dev` sub-apps will form the foundation of the new `agent-cli`.
-   **Project Entrypoints:** Both CLIs will be registered as entry points in `pyproject.toml`:
    ```toml
    [project.scripts]
    platform-cli = "scripts.platform_cli:app"
    agent-cli = "scripts.agent_cli:app"
    ```
-   **Makefile Integration:** The root `Makefile` will provide disambiguated targets (e.g., `make help-platform` vs. `make help-agent`) as specified in the DevEx stream (TASK-801).

### Implementation Backlog
The following tasks are identified to fulfill the CLI split, ordered by dependency:

1.  **DevEx Stream 8xx**: Implement `make bootstrap-agent`, per-agent Makefiles, and doc updates as defined in `docs/SPEC-DEV-EXPERIENCE-AND-ADR703.md` (TASK-801 to 804).
2.  **Issue Tool Modularization (TASK-701)**: Complete the extraction of the 4,500-line `scripts/issue_tool/cli.py` monolith into purpose-built modules:
    -   **Phase 1: specialized integrations**: Extract GitNexus and Pre-provisioning logic.
    -   **Phase 2: Core Worktree Ops**: Extract worktree management and terminal multiplexer (tmux/zellij) logic.
    -   **Phase 3: CLI Slimming**: Reduce `cli.py` to a thin `argparse` wrapper for command dispatch.
3.  **Typer Migration (TASK-056)**: After modularization, migrate `scripts/platform_cli.py` to use Typer for improved documentation and argument parsing (Issue #40).
4.  **CLI Split**: Create the new `agent-cli` entry point and move `agent` and `dev` sub-apps from `platform-cli`.

## Consequences

### Positive
-   **Reduced Friction:** Agent developers can get started with just `uv sync`, significantly lowering the barrier to entry.
-   **Improved Security:** Clearer separation of concerns makes it easier to enforce least-privilege access for different personas.
-   **Maintainability:** Each CLI can evolve its own interface, dependencies, and release cycle independently.
-   **Clarity:** Documentation and help messages will be focused on the specific tasks relevant to the user's role.

### Negative
-   **Refactoring Effort:** Requires moving shared logic out of the current `scripts/platform_cli.py` and into reusable modules.
-   **Installation Path:** Users who fulfill both roles (e.g., core platform team) will need to manage two tools, though this is typical for multi-layered platforms.
