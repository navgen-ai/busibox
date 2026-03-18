"""
MCP Client for agent-api.

Connects to external MCP servers and proxies their tools into the
agent ToolRegistry so LLM-driven agents can call them transparently.

Architecture:
    AgentDefinition.mcp_servers  ->  MCPServerRegistry  ->  MCPClient
            |                              |                     |
    [{name, url, transport}]      in-memory cache         stdio/SSE conn
                                   of tool schemas        to MCP server

Transport modes:
    - stdio:  Launch a subprocess (e.g. `npx some-mcp-server`)
    - sse:    Connect to a remote HTTP SSE endpoint

Each MCP tool is wrapped as an async function matching the Busibox
ToolRegistry interface (name, args -> result dict).
"""

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# MCP SDK is optional -- tools degrade gracefully if not installed
try:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.client.sse import sse_client
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    logger.info("MCP SDK not installed -- MCP tool proxy disabled")


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server connection."""
    name: str
    transport: str  # "stdio" or "sse"
    url: Optional[str] = None  # SSE endpoint URL
    command: Optional[str] = None  # stdio command (e.g. "npx")
    args: Optional[List[str]] = None  # stdio command args
    env: Optional[Dict[str, str]] = None  # extra env vars for stdio
    headers: Optional[Dict[str, str]] = None  # extra headers for SSE
    tool_filter: Optional[List[str]] = None  # whitelist of tool names (None = all)
    timeout_seconds: int = 30

    def __post_init__(self):
        if self.transport == "stdio" and not self.command:
            raise ValueError(f"MCP server '{self.name}': stdio transport requires 'command'")
        if self.transport == "sse" and not self.url:
            raise ValueError(f"MCP server '{self.name}': sse transport requires 'url'")
        if self.transport not in ("stdio", "sse"):
            raise ValueError(f"MCP server '{self.name}': unknown transport '{self.transport}'")


@dataclass
class MCPToolDefinition:
    """A tool discovered from an MCP server, ready for ToolRegistry."""
    server_name: str
    name: str
    description: str
    input_schema: Dict[str, Any]
    qualified_name: str = ""

    def __post_init__(self):
        if not self.qualified_name:
            self.qualified_name = f"mcp_{self.server_name}_{self.name}"


class MCPClient:
    """
    Manages connections to MCP servers and exposes their tools.

    Usage:
        client = MCPClient()
        tools = await client.discover_tools(server_config)
        result = await client.call_tool(server_config, "tool_name", {"arg": "val"})
    """

    def __init__(self):
        self._tool_cache: Dict[str, List[MCPToolDefinition]] = {}
        self._session_cache: Dict[str, Any] = {}

    async def discover_tools(self, config: MCPServerConfig) -> List[MCPToolDefinition]:
        """
        Connect to an MCP server and discover available tools.

        Results are cached per server name for the lifetime of this client.
        """
        if not MCP_AVAILABLE:
            logger.warning("MCP SDK not installed, cannot discover tools from %s", config.name)
            return []

        if config.name in self._tool_cache:
            return self._tool_cache[config.name]

        try:
            tools = await self._list_tools(config)
            self._tool_cache[config.name] = tools
            logger.info(
                "Discovered %d tools from MCP server '%s'",
                len(tools), config.name,
            )
            return tools
        except Exception as e:
            logger.error("Failed to discover tools from MCP server '%s': %s", config.name, e)
            return []

    async def call_tool(
        self,
        config: MCPServerConfig,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Call a tool on an MCP server and return the result.

        Opens a fresh connection per call to avoid stale session issues.
        For high-frequency calls, a connection pool could be added later.
        """
        if not MCP_AVAILABLE:
            return {"error": "MCP SDK not installed"}

        try:
            return await asyncio.wait_for(
                self._execute_tool(config, tool_name, arguments),
                timeout=config.timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.error("MCP tool call timed out: %s/%s (%ds)", config.name, tool_name, config.timeout_seconds)
            return {"error": f"Tool call timed out after {config.timeout_seconds}s"}
        except Exception as e:
            logger.error("MCP tool call failed: %s/%s: %s", config.name, tool_name, e)
            return {"error": str(e)}

    async def _list_tools(self, config: MCPServerConfig) -> List[MCPToolDefinition]:
        """Connect, list tools, disconnect."""
        tools: List[MCPToolDefinition] = []

        if config.transport == "stdio":
            params = StdioServerParameters(
                command=config.command,
                args=config.args or [],
                env=config.env,
            )
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    for t in result.tools:
                        if config.tool_filter and t.name not in config.tool_filter:
                            continue
                        tools.append(MCPToolDefinition(
                            server_name=config.name,
                            name=t.name,
                            description=t.description or "",
                            input_schema=t.inputSchema or {},
                        ))

        elif config.transport == "sse":
            async with sse_client(config.url, headers=config.headers) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    for t in result.tools:
                        if config.tool_filter and t.name not in config.tool_filter:
                            continue
                        tools.append(MCPToolDefinition(
                            server_name=config.name,
                            name=t.name,
                            description=t.description or "",
                            input_schema=t.inputSchema or {},
                        ))

        return tools

    async def _execute_tool(
        self,
        config: MCPServerConfig,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Open a session, call the tool, return the result."""
        if config.transport == "stdio":
            params = StdioServerParameters(
                command=config.command,
                args=config.args or [],
                env=config.env,
            )
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
                    return self._extract_result(result)

        elif config.transport == "sse":
            async with sse_client(config.url, headers=config.headers) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
                    return self._extract_result(result)

        return {"error": f"Unknown transport: {config.transport}"}

    @staticmethod
    def _extract_result(result: Any) -> Dict[str, Any]:
        """Normalize an MCP CallToolResult into a plain dict."""
        if hasattr(result, "content"):
            parts = []
            for item in result.content:
                if hasattr(item, "text"):
                    try:
                        parts.append(json.loads(item.text))
                    except (json.JSONDecodeError, ValueError):
                        parts.append({"text": item.text})
                elif hasattr(item, "data"):
                    parts.append({"data": str(item.data)[:500]})
                else:
                    parts.append({"content": str(item)[:500]})

            if len(parts) == 1:
                return parts[0] if isinstance(parts[0], dict) else {"result": parts[0]}
            return {"results": parts}

        return {"result": str(result)[:2000]}

    def clear_cache(self, server_name: Optional[str] = None):
        if server_name:
            self._tool_cache.pop(server_name, None)
        else:
            self._tool_cache.clear()


# Singleton for the process
_mcp_client: Optional[MCPClient] = None


def get_mcp_client() -> MCPClient:
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClient()
    return _mcp_client


def build_mcp_tool_function(
    config: MCPServerConfig,
    tool_def: MCPToolDefinition,
) -> Callable:
    """
    Build an async function that calls an MCP tool.

    The returned function has a proper inspect.Signature built from the
    MCP tool's JSON Schema, so PydanticAI can generate correct function
    calling schemas for the LLM.
    """
    schema = tool_def.input_schema
    required = set(schema.get("required", []))

    type_map = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    params = []
    annotations: Dict[str, Any] = {"return": Dict[str, Any]}

    for prop_name, prop_schema in schema.get("properties", {}).items():
        json_type = prop_schema.get("type", "string")
        py_type = type_map.get(json_type, Any)
        annotations[prop_name] = py_type

        if prop_name in required:
            default = inspect.Parameter.empty
        elif "default" in prop_schema:
            default = prop_schema["default"]
        else:
            default = None

        params.append(inspect.Parameter(
            name=prop_name,
            kind=inspect.Parameter.KEYWORD_ONLY,
            default=default,
            annotation=py_type,
        ))

    sig = inspect.Signature(parameters=params, return_annotation=Dict[str, Any])

    async def mcp_tool_proxy(**kwargs: Any) -> Dict[str, Any]:
        client = get_mcp_client()
        return await client.call_tool(config, tool_def.name, kwargs)

    mcp_tool_proxy.__name__ = tool_def.qualified_name
    mcp_tool_proxy.__qualname__ = tool_def.qualified_name
    mcp_tool_proxy.__doc__ = tool_def.description
    mcp_tool_proxy.__signature__ = sig
    mcp_tool_proxy.__annotations__ = annotations

    return mcp_tool_proxy


def parse_mcp_server_configs(raw: List[Dict[str, Any]]) -> List[MCPServerConfig]:
    """
    Parse a list of MCP server config dicts (from agent definition JSON)
    into validated MCPServerConfig objects.
    """
    configs = []
    for entry in raw:
        try:
            configs.append(MCPServerConfig(
                name=entry["name"],
                transport=entry.get("transport", "stdio"),
                url=entry.get("url"),
                command=entry.get("command"),
                args=entry.get("args"),
                env=entry.get("env"),
                headers=entry.get("headers"),
                tool_filter=entry.get("tool_filter"),
                timeout_seconds=entry.get("timeout_seconds", 30),
            ))
        except (KeyError, ValueError) as e:
            logger.warning("Skipping invalid MCP server config: %s (%s)", entry, e)
    return configs
