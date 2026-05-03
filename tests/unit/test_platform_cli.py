from typer.testing import CliRunner

from scripts.platform_cli import app

runner = CliRunner()


def test_cli_help():
    """Verify the main help command works."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Unified Platform CLI" in result.output
    assert "infra" in result.output
    assert "ops" in result.output
    assert "validate" in result.output
    # agent and dev moved to agent-cli
    assert "agent" not in result.output
    assert "dev" not in result.output


def test_ops_help():
    """Verify ops sub-app help."""
    result = runner.invoke(app, ["ops", "--help"])
    assert result.exit_code == 0
    assert "Platform operations" in result.output
    assert "login" in result.output
    assert "run-api-command" in result.output


def test_validate_help():
    """Verify validate sub-app help."""
    result = runner.invoke(app, ["validate", "--help"])
    assert result.exit_code == 0
    assert "Validation and linting commands" in result.output
    assert "local" in result.output


def test_infra_help():
    """Verify infra sub-app help."""
    result = runner.invoke(app, ["infra", "--help"])
    assert result.exit_code == 0
    assert "Infrastructure commands" in result.output
