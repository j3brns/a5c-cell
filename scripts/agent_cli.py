import subprocess
from typing import Optional

import typer

from . import (
    agent_invoke,
    dev_bootstrap,
    dev_invoke,
    evaluate_agent,
    rollback_agent,
    wait_for_local_services,
)

app = typer.Typer(
    name="agent-cli",
    help="Developer CLI for AgentCore Agent as a Service.",
    add_completion=False,
)

agent_app = typer.Typer(help="Manage agents (package, deploy, invoke, etc.)")
dev_app = typer.Typer(help="Local development commands")

app.add_typer(agent_app, name="agent")
app.add_typer(dev_app, name="dev")


@agent_app.command("invoke")
def agent_invoke_cmd(
    agent: str = typer.Option(..., help="Agent name"),
    tenant: str = typer.Option(..., help="Tenant ID"),
    prompt: str = typer.Option("Hello", help="Input prompt"),
    mode: str = typer.Option("sync", help="Requested invocation mode"),
    env: str = typer.Option("dev", help="Deployment environment"),
    session_id: str | None = typer.Option(None, help="Optional session identifier"),
):
    """Invoke a deployed agent directly via the Bridge Lambda."""
    exit_code = agent_invoke.invoke_remote(
        agent=agent,
        tenant=tenant,
        prompt=prompt,
        env=env,
        mode=mode,
        session_id=session_id,
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
    """Seed the local AWS emulator with test data and parameters."""
    try:
        dev_bootstrap.run_bootstrap(aws_endpoint_url=dev_bootstrap.resolve_aws_endpoint_url())
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


@app.callback()
def main():
    """
    Agent CLI - Tool for developing and deploying agents to the AgentCore AaS platform.
    """
    pass


if __name__ == "__main__":
    app()
