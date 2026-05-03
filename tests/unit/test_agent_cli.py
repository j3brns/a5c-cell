from typer.testing import CliRunner

from scripts.agent_cli import app

runner = CliRunner()


def test_cli_help():
    """Verify the main help command works."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Developer CLI" in result.output
    assert "agent" in result.output
    assert "dev" in result.output


def test_agent_help():
    """Verify agent sub-app help."""
    result = runner.invoke(app, ["agent", "--help"])
    assert result.exit_code == 0
    assert "Manage agents" in result.output
    assert "push" in result.output
    assert "invoke" in result.output


def test_dev_help():
    """Verify dev sub-app help."""
    result = runner.invoke(app, ["dev", "--help"])
    assert result.exit_code == 0
    assert "Local development commands" in result.output
    assert "bootstrap" in result.output
    assert "wait-for-services" in result.output


def test_agent_invoke_missing_args():
    """Verify agent invoke fails when required args are missing."""
    result = runner.invoke(app, ["agent", "invoke"])
    assert result.exit_code != 0
    assert "Missing option" in result.output or "Error" in result.output
