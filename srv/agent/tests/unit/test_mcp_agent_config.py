"""
Tests for MCP server config wiring into agent definitions.

Tests the schema, domain model, and create_agent_from_definition
for MCP server support.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.definitions import (
    AgentDefinitionCreate,
    AgentDefinitionUpdate,
    MCPServerEntry,
)
from app.services.mcp_client import MCPServerConfig, parse_mcp_server_configs


# ── MCPServerEntry schema ──────────────────────────────────────────

class TestMCPServerEntry:
    def test_minimal_stdio(self):
        entry = MCPServerEntry(name="local", command="npx")
        assert entry.transport == "stdio"
        assert entry.timeout_seconds == 30

    def test_sse_with_url(self):
        entry = MCPServerEntry(
            name="remote",
            transport="sse",
            url="http://localhost:3001/sse",
        )
        assert entry.url == "http://localhost:3001/sse"

    def test_all_fields(self):
        entry = MCPServerEntry(
            name="full",
            transport="stdio",
            command="python",
            args=["-m", "server"],
            env={"KEY": "val"},
            tool_filter=["t1", "t2"],
            timeout_seconds=60,
        )
        assert entry.args == ["-m", "server"]
        assert entry.env == {"KEY": "val"}
        assert entry.tool_filter == ["t1", "t2"]

    def test_serialization(self):
        entry = MCPServerEntry(name="x", command="echo")
        data = entry.model_dump(exclude_none=True)
        assert data["name"] == "x"
        assert data["command"] == "echo"
        assert "url" not in data


# ── AgentDefinitionCreate with mcp_servers ─────────────────────────

class TestAgentDefinitionWithMCPServers:
    def test_no_mcp_servers_by_default(self):
        defn = AgentDefinitionCreate(
            name="test-agent",
            model="agent",
            instructions="Hello",
        )
        assert defn.mcp_servers is None

    def test_with_mcp_servers(self):
        defn = AgentDefinitionCreate(
            name="mcp-agent",
            model="agent",
            instructions="Use MCP tools",
            mcp_servers=[
                MCPServerEntry(
                    name="local-mcp",
                    transport="stdio",
                    command="npx",
                    args=["-y", "some-mcp-server"],
                ),
                MCPServerEntry(
                    name="remote-mcp",
                    transport="sse",
                    url="http://tools.example.com/sse",
                ),
            ],
        )
        assert len(defn.mcp_servers) == 2
        assert defn.mcp_servers[0].name == "local-mcp"
        assert defn.mcp_servers[1].transport == "sse"

    def test_serialization_round_trip(self):
        defn = AgentDefinitionCreate(
            name="test",
            model="agent",
            instructions="x",
            mcp_servers=[MCPServerEntry(name="s", command="echo")],
        )
        data = defn.model_dump()
        restored = AgentDefinitionCreate(**data)
        assert len(restored.mcp_servers) == 1
        assert restored.mcp_servers[0].name == "s"

    def test_json_round_trip(self):
        """MCP configs should survive JSON serialization (DB storage)."""
        defn = AgentDefinitionCreate(
            name="test",
            model="agent",
            instructions="x",
            mcp_servers=[
                MCPServerEntry(
                    name="s",
                    transport="stdio",
                    command="npx",
                    args=["-y", "pkg"],
                    tool_filter=["tool_a"],
                )
            ],
        )
        import json
        serialized = json.dumps(
            [s.model_dump(exclude_none=True) for s in defn.mcp_servers]
        )
        deserialized = json.loads(serialized)
        configs = parse_mcp_server_configs(deserialized)
        assert len(configs) == 1
        assert configs[0].name == "s"
        assert configs[0].tool_filter == ["tool_a"]


# ── AgentDefinitionUpdate with mcp_servers ─────────────────────────

class TestAgentDefinitionUpdateMCPServers:
    def test_mcp_servers_optional(self):
        update = AgentDefinitionUpdate(display_name="New Name")
        assert update.mcp_servers is None

    def test_mcp_servers_set(self):
        update = AgentDefinitionUpdate(
            mcp_servers=[MCPServerEntry(name="new", command="echo")],
        )
        assert len(update.mcp_servers) == 1

    def test_exclude_unset(self):
        update = AgentDefinitionUpdate(display_name="New Name")
        data = update.model_dump(exclude_unset=True)
        assert "mcp_servers" not in data

    def test_set_includes(self):
        update = AgentDefinitionUpdate(
            mcp_servers=[MCPServerEntry(name="s", command="c")],
        )
        data = update.model_dump(exclude_unset=True)
        assert "mcp_servers" in data


# ── create_agent_from_definition MCP wiring ────────────────────────

class TestCreateAgentFromDefinitionMCP:
    def _make_definition(self, mcp_servers=None):
        defn = MagicMock()
        defn.name = "test-agent"
        defn.display_name = "Test Agent"
        defn.description = "A test agent"
        defn.model = "agent"
        defn.instructions = "You are a test agent."
        defn.tools = {"names": ["web_search"]}
        defn.workflows = {"tool_strategy": "llm_driven"}
        defn.scopes = []
        defn.is_active = True
        defn.is_builtin = False
        defn.allow_frontier_fallback = False
        defn.mcp_servers = mcp_servers
        return defn

    @patch("app.agents.base_agent._ensure_openai_env")
    @patch("app.agents.base_agent.get_settings")
    def test_no_mcp_servers(self, mock_settings, mock_env):
        mock_settings.return_value.default_model = "agent"
        mock_settings.return_value.litellm_base_url = "http://localhost:4000/v1"
        mock_settings.return_value.litellm_api_key = "key"

        from app.agents.base_agent import create_agent_from_definition
        agent = create_agent_from_definition(self._make_definition())
        assert len(agent.config.mcp_servers) == 0

    @patch("app.agents.base_agent._ensure_openai_env")
    @patch("app.agents.base_agent.get_settings")
    def test_with_mcp_servers(self, mock_settings, mock_env):
        mock_settings.return_value.default_model = "agent"
        mock_settings.return_value.litellm_base_url = "http://localhost:4000/v1"
        mock_settings.return_value.litellm_api_key = "key"

        mcp_configs = [
            {"name": "local", "transport": "stdio", "command": "npx", "args": ["-y", "pkg"]},
            {"name": "remote", "transport": "sse", "url": "http://tools.example.com/sse"},
        ]

        from app.agents.base_agent import create_agent_from_definition
        agent = create_agent_from_definition(self._make_definition(mcp_configs))
        assert len(agent.config.mcp_servers) == 2
        assert agent.config.mcp_servers[0].name == "local"
        assert agent.config.mcp_servers[1].name == "remote"
        assert agent.config.mcp_servers[0].command == "npx"

    @patch("app.agents.base_agent._ensure_openai_env")
    @patch("app.agents.base_agent.get_settings")
    def test_invalid_mcp_server_skipped(self, mock_settings, mock_env):
        mock_settings.return_value.default_model = "agent"
        mock_settings.return_value.litellm_base_url = "http://localhost:4000/v1"
        mock_settings.return_value.litellm_api_key = "key"

        mcp_configs = [
            {"name": "good", "transport": "stdio", "command": "echo"},
            {"name": "bad", "transport": "stdio"},  # missing command
        ]

        from app.agents.base_agent import create_agent_from_definition
        agent = create_agent_from_definition(self._make_definition(mcp_configs))
        assert len(agent.config.mcp_servers) == 1
        assert agent.config.mcp_servers[0].name == "good"
