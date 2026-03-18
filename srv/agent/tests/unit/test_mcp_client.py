"""
Tests for the MCP client module.

Tests config validation, tool proxy generation, result extraction,
and config parsing -- all pure logic, no live MCP connections.
"""

import asyncio
import inspect
import json
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.mcp_client import (
    MCPClient,
    MCPServerConfig,
    MCPToolDefinition,
    build_mcp_tool_function,
    parse_mcp_server_configs,
)


# ── MCPServerConfig validation ─────────────────────────────────────

class TestMCPServerConfig:
    def test_valid_stdio(self):
        config = MCPServerConfig(
            name="test-server",
            transport="stdio",
            command="npx",
            args=["-y", "some-mcp-server"],
        )
        assert config.name == "test-server"
        assert config.transport == "stdio"
        assert config.command == "npx"

    def test_valid_sse(self):
        config = MCPServerConfig(
            name="remote",
            transport="sse",
            url="http://localhost:3001/sse",
        )
        assert config.url == "http://localhost:3001/sse"

    def test_stdio_requires_command(self):
        with pytest.raises(ValueError, match="stdio transport requires 'command'"):
            MCPServerConfig(name="bad", transport="stdio")

    def test_sse_requires_url(self):
        with pytest.raises(ValueError, match="sse transport requires 'url'"):
            MCPServerConfig(name="bad", transport="sse")

    def test_unknown_transport(self):
        with pytest.raises(ValueError, match="unknown transport"):
            MCPServerConfig(name="bad", transport="grpc", command="x")

    def test_default_timeout(self):
        config = MCPServerConfig(name="x", transport="stdio", command="echo")
        assert config.timeout_seconds == 30

    def test_custom_timeout(self):
        config = MCPServerConfig(
            name="x", transport="stdio", command="echo",
            timeout_seconds=60,
        )
        assert config.timeout_seconds == 60

    def test_optional_fields_default_none(self):
        config = MCPServerConfig(name="x", transport="stdio", command="echo")
        assert config.args is None
        assert config.env is None
        assert config.headers is None
        assert config.tool_filter is None


# ── MCPToolDefinition ──────────────────────────────────────────────

class TestMCPToolDefinition:
    def test_qualified_name_auto_generated(self):
        tool = MCPToolDefinition(
            server_name="myserver",
            name="search",
            description="Search things",
            input_schema={"type": "object"},
        )
        assert tool.qualified_name == "mcp_myserver_search"

    def test_explicit_qualified_name(self):
        tool = MCPToolDefinition(
            server_name="s",
            name="t",
            description="d",
            input_schema={},
            qualified_name="custom_name",
        )
        assert tool.qualified_name == "custom_name"


# ── MCPClient._extract_result ─────────────────────────────────────

class TestExtractResult:
    def test_text_content_as_json(self):
        result = MagicMock()
        item = MagicMock()
        item.text = '{"key": "value"}'
        del item.data
        result.content = [item]

        extracted = MCPClient._extract_result(result)
        assert extracted == {"key": "value"}

    def test_text_content_plain(self):
        result = MagicMock()
        item = MagicMock()
        item.text = "plain text response"
        del item.data
        result.content = [item]

        extracted = MCPClient._extract_result(result)
        assert extracted == {"text": "plain text response"}

    def test_multiple_content_items(self):
        result = MagicMock()
        item1 = MagicMock()
        item1.text = "first"
        del item1.data
        item2 = MagicMock()
        item2.text = "second"
        del item2.data
        result.content = [item1, item2]

        extracted = MCPClient._extract_result(result)
        assert "results" in extracted
        assert len(extracted["results"]) == 2

    def test_data_content(self):
        result = MagicMock()
        item = MagicMock()
        del item.text
        item.data = b"binary data"
        result.content = [item]

        extracted = MCPClient._extract_result(result)
        assert "data" in extracted

    def test_no_content_attribute(self):
        result = "raw string result"
        extracted = MCPClient._extract_result(result)
        assert extracted == {"result": "raw string result"}


# ── MCPClient cache ────────────────────────────────────────────────

class TestMCPClientCache:
    def test_clear_all(self):
        client = MCPClient()
        client._tool_cache["server1"] = [MCPToolDefinition("s", "t", "d", {})]
        client._tool_cache["server2"] = [MCPToolDefinition("s", "t", "d", {})]
        client.clear_cache()
        assert len(client._tool_cache) == 0

    def test_clear_specific(self):
        client = MCPClient()
        client._tool_cache["server1"] = [MCPToolDefinition("s", "t", "d", {})]
        client._tool_cache["server2"] = [MCPToolDefinition("s", "t", "d", {})]
        client.clear_cache("server1")
        assert "server1" not in client._tool_cache
        assert "server2" in client._tool_cache


# ── build_mcp_tool_function ────────────────────────────────────────

class TestBuildMCPToolFunction:
    def _make_config(self):
        return MCPServerConfig(name="test", transport="stdio", command="echo")

    def _make_tool_def(self, schema=None):
        return MCPToolDefinition(
            server_name="test",
            name="search",
            description="Search for things",
            input_schema=schema or {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        )

    def test_function_name(self):
        fn = build_mcp_tool_function(self._make_config(), self._make_tool_def())
        assert fn.__name__ == "mcp_test_search"

    def test_function_doc(self):
        fn = build_mcp_tool_function(self._make_config(), self._make_tool_def())
        assert fn.__doc__ == "Search for things"

    def test_signature_has_parameters(self):
        fn = build_mcp_tool_function(self._make_config(), self._make_tool_def())
        sig = inspect.signature(fn)
        param_names = list(sig.parameters.keys())
        assert "query" in param_names
        assert "limit" in param_names

    def test_required_param_has_no_default(self):
        fn = build_mcp_tool_function(self._make_config(), self._make_tool_def())
        sig = inspect.signature(fn)
        assert sig.parameters["query"].default is inspect.Parameter.empty

    def test_optional_param_has_default(self):
        fn = build_mcp_tool_function(self._make_config(), self._make_tool_def())
        sig = inspect.signature(fn)
        assert sig.parameters["limit"].default == 5

    def test_optional_param_without_default_gets_none(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "tags": {"type": "array"},
            },
            "required": ["name"],
        }
        fn = build_mcp_tool_function(self._make_config(), self._make_tool_def(schema))
        sig = inspect.signature(fn)
        assert sig.parameters["tags"].default is None

    def test_annotations_set(self):
        fn = build_mcp_tool_function(self._make_config(), self._make_tool_def())
        assert fn.__annotations__["query"] is str
        assert fn.__annotations__["limit"] is int
        assert fn.__annotations__["return"] is Dict[str, Any]

    def test_return_annotation(self):
        fn = build_mcp_tool_function(self._make_config(), self._make_tool_def())
        sig = inspect.signature(fn)
        assert sig.return_annotation is Dict[str, Any]

    def test_empty_schema(self):
        tool_def = MCPToolDefinition(
            server_name="s", name="t", description="d",
            input_schema={"type": "object"},
        )
        fn = build_mcp_tool_function(self._make_config(), tool_def)
        sig = inspect.signature(fn)
        assert len(sig.parameters) == 0

    @pytest.mark.asyncio
    async def test_calls_mcp_client(self):
        """The generated proxy function calls MCPClient.call_tool."""
        config = self._make_config()
        tool_def = self._make_tool_def()
        fn = build_mcp_tool_function(config, tool_def)

        mock_client = AsyncMock()
        mock_client.call_tool.return_value = {"result": "ok"}

        with patch("app.services.mcp_client.get_mcp_client", return_value=mock_client):
            result = await fn(query="test", limit=3)

        mock_client.call_tool.assert_called_once_with(
            config, "search", {"query": "test", "limit": 3}
        )
        assert result == {"result": "ok"}


# ── parse_mcp_server_configs ───────────────────────────────────────

class TestParseMCPServerConfigs:
    def test_valid_stdio(self):
        configs = parse_mcp_server_configs([{
            "name": "local-mcp",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "some-mcp"],
        }])
        assert len(configs) == 1
        assert configs[0].name == "local-mcp"
        assert configs[0].command == "npx"

    def test_valid_sse(self):
        configs = parse_mcp_server_configs([{
            "name": "remote-mcp",
            "transport": "sse",
            "url": "http://host:3000/sse",
            "headers": {"Authorization": "Bearer xxx"},
        }])
        assert len(configs) == 1
        assert configs[0].url == "http://host:3000/sse"

    def test_default_transport_is_stdio(self):
        configs = parse_mcp_server_configs([{
            "name": "default",
            "command": "echo",
        }])
        assert len(configs) == 1
        assert configs[0].transport == "stdio"

    def test_invalid_config_skipped(self):
        configs = parse_mcp_server_configs([
            {"name": "good", "transport": "stdio", "command": "echo"},
            {"name": "bad", "transport": "stdio"},  # missing command
            {"name": "also-good", "transport": "sse", "url": "http://x"},
        ])
        assert len(configs) == 2

    def test_missing_name_skipped(self):
        configs = parse_mcp_server_configs([
            {"transport": "stdio", "command": "echo"},
        ])
        assert len(configs) == 0

    def test_empty_list(self):
        assert parse_mcp_server_configs([]) == []

    def test_tool_filter_passed(self):
        configs = parse_mcp_server_configs([{
            "name": "filtered",
            "command": "x",
            "tool_filter": ["tool_a", "tool_b"],
        }])
        assert configs[0].tool_filter == ["tool_a", "tool_b"]

    def test_custom_timeout(self):
        configs = parse_mcp_server_configs([{
            "name": "slow",
            "command": "x",
            "timeout_seconds": 120,
        }])
        assert configs[0].timeout_seconds == 120


# ── MCPClient.discover_tools (without MCP SDK) ────────────────────

class TestMCPClientWithoutSDK:
    @pytest.mark.asyncio
    async def test_discover_tools_returns_empty_when_unavailable(self):
        client = MCPClient()
        config = MCPServerConfig(name="test", transport="stdio", command="echo")
        with patch("app.services.mcp_client.MCP_AVAILABLE", False):
            tools = await client.discover_tools(config)
        assert tools == []

    @pytest.mark.asyncio
    async def test_call_tool_returns_error_when_unavailable(self):
        client = MCPClient()
        config = MCPServerConfig(name="test", transport="stdio", command="echo")
        with patch("app.services.mcp_client.MCP_AVAILABLE", False):
            result = await client.call_tool(config, "some_tool", {})
        assert "error" in result
        assert "not installed" in result["error"]

    @pytest.mark.asyncio
    async def test_discover_tools_uses_cache(self):
        client = MCPClient()
        cached_tools = [MCPToolDefinition("s", "cached_tool", "desc", {})]
        client._tool_cache["test"] = cached_tools

        config = MCPServerConfig(name="test", transport="stdio", command="echo")
        tools = await client.discover_tools(config)
        assert tools is cached_tools
