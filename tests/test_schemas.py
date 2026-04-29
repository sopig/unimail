"""Tests for schema definitions (OpenAI, MCP)."""

import json

import pytest

from src.schemas.openai_functions import TOOLS


class TestOpenAISchema:
    def test_tools_list_not_empty(self):
        """Should have tools defined."""
        assert len(TOOLS) > 0

    def test_each_tool_has_required_fields(self):
        """Each tool should have type, function with name/description/parameters."""
        for tool in TOOLS:
            assert tool["type"] == "function"
            func = tool["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func

    def test_tool_names_unique(self):
        """Tool names should be unique."""
        names = [t["function"]["name"] for t in TOOLS]
        assert len(names) == len(set(names))

    def test_mail_send_schema(self):
        """mail_send should have required fields."""
        send_tool = None
        for tool in TOOLS:
            if tool["function"]["name"] == "mail_send":
                send_tool = tool
                break

        assert send_tool is not None
        params = send_tool["function"]["parameters"]
        assert "to" in params["properties"]
        assert "subject" in params["properties"]
        # Required fields
        assert "to" in params.get("required", [])
        assert "subject" in params.get("required", [])

    def test_mail_list_schema(self):
        """mail_list should have folder and limit params."""
        list_tool = None
        for tool in TOOLS:
            if tool["function"]["name"] == "mail_list":
                list_tool = tool
                break

        assert list_tool is not None
        params = list_tool["function"]["parameters"]
        assert "folder" in params["properties"]
        assert "limit" in params["properties"]

    def test_serializable_to_json(self):
        """Schema should be JSON serializable."""
        json_str = json.dumps(TOOLS, ensure_ascii=False)
        assert len(json_str) > 100
        # Round-trip
        parsed = json.loads(json_str)
        assert parsed == TOOLS


class TestMCPSchema:
    def test_convert_to_mcp_format(self):
        """Should be convertible to MCP tool format."""
        mcp_tools = [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "inputSchema": t["function"]["parameters"],
            }
            for t in TOOLS
        ]

        assert len(mcp_tools) == len(TOOLS)
        for tool in mcp_tools:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"
