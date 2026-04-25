import subprocess
import sys
from typing import Any, Optional

import typer

from . import (
    agent_invoke,
    dev_bootstrap,
    dev_invoke,
    evaluate_agent,
    failover_lock,
    ops,
    rollback_agent,
    validate_local,
    wait_for_local_services,
)

app = typer.Typer(
    name="platform-cli",
    help="Unified Platform CLI for AgentCore Agent as a Service.",
    add_completion=False,
)

# Sub-apps for different categories
agent_app = typer.Typer(help="Manage agents (package, deploy, invoke, etc.)")
dev_app = typer.Typer(help="Local development commands")
infra_app = typer.Typer(help="Infrastructure commands (CDK, region management)")
ops_app = typer.Typer(help="Platform operations (tenants, quotas, logs)")
validate_app = typer.Typer(help="Validation and linting commands")

app.add_typer(agent_app, name="agent")
app.add_typer(dev_app, name="dev")
app.add_typer(infra_app, name="infra")
app.add_typer(ops_app, name="ops")
app.add_typer(validate_app, name="validate")


@agent_app.command("invoke")
def agent_invoke_cmd(
    agent: str = typer.Option(..., help="Agent name"),
    tenant: str = typer.Option(..., help="Tenant ID"),
    prompt: str = typer.Option("Hello", help="Input prompt"),
    mode: str = typer.Option("sync", help="Requested invocation mode"),
    env: str = typer.Option("dev", help="Deployment environment"),
    session_id: str | None = typer.Option(None, help="Optional session identifier"),
    webhook_id: str | None = typer.Option(None, help="Optional webhook identifier"),
):
    """Invoke a deployed agent directly via the Bridge Lambda."""
    exit_code = agent_invoke.invoke_remote(
        agent=agent,
        tenant=tenant,
        prompt=prompt,
        env=env,
        mode=mode,
        session_id=session_id,
        webhook_id=webhook_id,
    )
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@agent_app.command("push")
def agent_push_cmd(
    agent: str = typer.Argument(..., help="Agent name"),
    env: str = typer.Option("dev", help="Deployment environment"),
):
    """Package and deploy an agent (rebuilds dependencies only if changed)."""
    from . import build_layer, deploy_agent, hash_layer, package_agent, register_agent

    typer.echo(f"==> Checking dependency hash for {agent}")
    if hash_layer.run(agent, env) != 0:
        typer.echo("==> Dependencies changed (cold path)")
        if build_layer.run(agent, env) != 0:
            typer.echo("ERROR: build_layer failed", err=True)
            raise typer.Exit(code=1)
    else:
        typer.echo("==> Dependencies unchanged (fast path)")

    typer.echo("==> Packaging agent code")
    if not package_agent.package_agent(agent):
        typer.echo("ERROR: package_agent failed", err=True)
        raise typer.Exit(code=1)

    typer.echo("==> Running agent tests")
    test_result = subprocess.run(
        ["uv", "run", "pytest", f"agents/{agent}/tests/", "-v", "--tb=short"],
        env={"PYTHONPATH": "."},
    )
    if test_result.returncode != 0:
        typer.echo("ERROR: agent tests failed", err=True)
        raise typer.Exit(code=test_result.returncode)

    typer.echo("==> Deploying to AgentCore Runtime")
    if not deploy_agent.deploy_agent(agent, env):
        typer.echo("ERROR: deploy_agent failed", err=True)
        raise typer.Exit(code=1)

    typer.echo("==> Registering agent")
    if not register_agent.register_agent(agent, env, None, None):
        typer.echo("ERROR: register_agent failed", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"==> Agent {agent} deployed successfully to {env}")


@agent_app.command("evaluate")
def agent_evaluate_cmd(
    agent: str = typer.Argument(..., help="Agent name"),
    env: str = typer.Option("dev", help="Deployment environment"),
):
    """Run golden test cases against AgentCore Evaluations service."""
    if not evaluate_agent.evaluate_agent(agent, env):
        raise typer.Exit(code=1)


@agent_app.command("rollback")
def agent_rollback_cmd(
    agent: str = typer.Argument(..., help="Agent name"),
    env: str = typer.Option("dev", help="Deployment environment"),
    notes: str | None = typer.Option(None, help="Rollback evidence or operator notes"),
    api_base_url: str | None = typer.Option(None, help="Override Platform API base URL"),
    token: str | None = typer.Option(None, help="Override Platform API access token"),
):
    """Roll back agent to previous version."""
    if not rollback_agent.rollback_agent(agent, env, api_base_url, token, notes):
        raise typer.Exit(code=1)


@dev_app.command("bootstrap")
def dev_bootstrap_cmd():
    """Seed LocalStack with test data and parameters."""
    try:
        dev_bootstrap.run_bootstrap()
    except Exception as e:
        typer.echo(f"ERROR: dev-bootstrap failed: {e}", err=True)
        raise typer.Exit(code=1)


@dev_app.command("invoke")
def dev_invoke_cmd(
    agent: str = typer.Option(..., help="Agent name"),
    tenant: str = typer.Option(..., help="Tenant ID"),
    prompt: str = typer.Option("Hello", help="Input prompt"),
    mode: str = typer.Option("sync", help="Requested invocation mode"),
    env: str = typer.Option("local", help="Profile name"),
    api_base_url: str | None = typer.Option(None, help="Override API base URL"),
    token: str | None = typer.Option(None, help="Override Bearer token"),
):
    """Invoke an agent via the contracted REST route locally."""
    exit_code = dev_invoke.dev_invoke(
        agent=agent,
        tenant=tenant,
        prompt=prompt,
        mode=mode,
        env=env,
        api_base_url=api_base_url,
        token=token,
    )
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@dev_app.command("wait-for-services")
def dev_wait_for_services_cmd(
    timeout_seconds: int = typer.Option(60, help="Maximum time to wait"),
    interval_seconds: float = typer.Option(2.0, help="Polling interval"),
    check_seeded_state: bool = typer.Option(False, help="Validate seeded state"),
):
    """Wait until local development dependencies are healthy."""
    exit_code = wait_for_local_services.run_wait(
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
        check_seeded_state=check_seeded_state,
    )
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@infra_app.command("failover-lock-acquire")
def infra_failover_lock_acquire_cmd(
    env: str = typer.Option("dev", help="Environment label"),
    owner: str | None = typer.Option(None, help="Lock owner identity"),
    ttl_seconds: int = typer.Option(300, help="Lock TTL in seconds"),
):
    """Acquire distributed runtime failover lock."""
    exit_code = failover_lock.run_acquire(env=env, owner=owner, ttl_seconds=ttl_seconds)
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@infra_app.command("failover-lock-release")
def infra_failover_lock_release_cmd(
    env: str = typer.Option("dev", help="Environment label"),
    lock_id: str | None = typer.Option(None, help="Expected lockId to release"),
    force: bool = typer.Option(False, help="Release without validating lockId ownership"),
):
    """Release distributed runtime failover lock."""
    exit_code = failover_lock.run_release(env=env, lock_id=lock_id, force=force)
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@ops_app.command("login")
def ops_login_cmd(
    env: str = typer.Option("dev", help="Deployment environment"),
):
    """Authenticate as operator via Entra."""
    exit_code = ops.handle_login(env=env)
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@ops_app.command("top-tenants")
def ops_top_tenants_cmd(
    env: str = typer.Option("dev", help="Deployment environment"),
    n: int = typer.Option(10, help="Number of tenants to show"),
):
    """List top N tenants by token consumption."""
    exit_code = ops.run_api_command(command="top-tenants", env=env, n=n)
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@ops_app.command("quota-report")
def ops_quota_report_cmd(
    env: str = typer.Option("dev", help="Deployment environment"),
):
    """Show AgentCore quota utilisation."""
    exit_code = ops.run_api_command(command="quota-report", env=env)
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@ops_app.command("suspend-tenant")
def ops_suspend_tenant_cmd(
    tenant: str = typer.Option(..., help="Tenant ID"),
    reason: str = typer.Option(..., help="Suspension reason"),
    env: str = typer.Option("dev", help="Deployment environment"),
):
    """Suspend a tenant immediately."""
    exit_code = ops.run_api_command(command="suspend-tenant", env=env, tenant=tenant, reason=reason)
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@ops_app.command("reinstate-tenant")
def ops_reinstate_tenant_cmd(
    tenant: str = typer.Option(..., help="Tenant ID"),
    env: str = typer.Option("dev", help="Deployment environment"),
):
    """Reinstate a suspended tenant."""
    exit_code = ops.run_api_command(command="reinstate-tenant", env=env, tenant=tenant)
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@ops_app.command(
    "run-api-command",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def ops_run_api_cmd(
    ctx: typer.Context,
    command: str = typer.Argument(..., help="API command to run"),
    env: str = typer.Option("dev", help="Deployment environment"),
):
    """Run an arbitrary API command from ops.py."""
    # This is a bit of a hack to support passing extra arguments to run_api_command
    # Typer doesn't easily support arbitrary kwargs from CLI, so we use ctx.args
    extra_args = {}
    it = iter(ctx.args)
    for arg in it:
        if arg.startswith("--"):
            key = arg[2:].replace("-", "_")
            try:
                val = next(it)
                extra_args[key] = val
            except StopIteration:
                extra_args[key] = True

    exit_code = ops.run_api_command(command=command, env=env, **extra_args)
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@validate_app.command("local")
def validate_local_cmd(
    mode: str = typer.Argument(..., help="Validation mode (fast, full)"),
    benchmark: bool = typer.Option(False, help="Measure sequential baseline"),
):
    """Run local validation checks (fast or full)."""
    exit_code = validate_local.validate_local(mode=mode, benchmark=benchmark)
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@app.callback()
def main():
    """
    Platform CLI - Unified tool for managing the AgentCore AaS platform.
    """
    pass


if __name__ == "__main__":
    app()
