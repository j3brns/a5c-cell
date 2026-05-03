import sys
from typing import Any, Optional

import typer

from . import (
    ops,
    validate_local,
)

app = typer.Typer(
    name="platform-cli",
    help="Unified Platform CLI for AgentCore Agent as a Service.",
    add_completion=False,
)

infra_app = typer.Typer(help="Infrastructure commands (CDK)")
ops_app = typer.Typer(help="Platform operations (tenants, quotas, logs)")
validate_app = typer.Typer(help="Validation and linting commands")

app.add_typer(infra_app, name="infra")
app.add_typer(ops_app, name="ops")
app.add_typer(validate_app, name="validate")


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
