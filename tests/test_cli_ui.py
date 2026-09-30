"""Tests for CLI UI commands."""

from typer.testing import CliRunner

from agenteverywhereflow.cli import app

runner = CliRunner()


def test_cli_ui_status() -> None:
    """Verify 'aef ui status' outputs WebUI status."""
    result = runner.invoke(app, ["ui", "status"])
    assert result.exit_code == 0
    assert "WebUI Installation Status" in result.stdout


def test_cli_ui_help() -> None:
    """Verify 'aef ui --help' shows studio options."""
    result = runner.invoke(app, ["ui", "--help"])
    assert result.exit_code == 0
    assert "WebUI Studio" in result.stdout
