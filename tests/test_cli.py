"""Tests for CLI commands."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from src.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_env(tmp_dir):
    """Isolate CLI from real user data."""
    data_dir = tmp_dir / "data"
    data_dir.mkdir(parents=True)
    with patch("src.cli.main.DATA_DIR", data_dir):
        yield data_dir


class TestCLIBasic:
    def test_help(self, runner):
        """CLI should show help text."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "UniMail" in result.output

    def test_list_empty(self, runner, isolated_env):
        """List accounts should work with empty DB."""
        result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        assert "No accounts" in result.output or "Connected" in result.output

    def test_schema_openai(self, runner):
        """Schema export should output valid JSON."""
        result = runner.invoke(cli, ["schema", "openai"])
        assert result.exit_code == 0
        import json
        tools = json.loads(result.output)
        assert isinstance(tools, list)
        assert len(tools) > 0
        # Each tool should have function definition
        for tool in tools:
            assert "function" in tool
            assert "name" in tool["function"]

    def test_schema_mcp(self, runner):
        """MCP schema export should output valid JSON."""
        result = runner.invoke(cli, ["schema", "mcp"])
        assert result.exit_code == 0
        import json
        tools = json.loads(result.output)
        assert isinstance(tools, list)
        for tool in tools:
            assert "name" in tool
            assert "inputSchema" in tool

    def test_add_group_help(self, runner):
        """Add command group should show sub-commands."""
        result = runner.invoke(cli, ["add", "--help"])
        assert result.exit_code == 0
        assert "gmail" in result.output
        assert "outlook" in result.output
        assert "imap" in result.output

    def test_serve_help(self, runner):
        """Serve command should show options."""
        result = runner.invoke(cli, ["serve", "--help"])
        assert result.exit_code == 0
        assert "mode" in result.output
        assert "port" in result.output
