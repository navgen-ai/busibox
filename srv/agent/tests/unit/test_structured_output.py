"""
Unit tests for structured output utilities in BaseStreamingAgent.

Tests cover:
- _extract_json_from_response: stripping <think> tags, code fences, preamble
- _call_structured_output: prompt construction, /no_think injection, max_tokens default
- _run_native_structured_output fallback behavior
"""

import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.base_agent import (
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    PipelineStep,
    ToolStrategy,
)
from app.schemas.auth import Principal


# -- Helpers ------------------------------------------------------------------

SIMPLE_SCHEMA = {
    "name": "test_schema",
    "strict": True,
    "schema": {
        "type": "object",
        "required": ["name", "age"],
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
        },
    },
}

EXTRACTION_SCHEMA = {
    "name": "extraction_schema",
    "strict": True,
    "schema": {
        "type": "object",
        "required": ["schemaName", "displayName", "itemLabel", "fields"],
        "additionalProperties": False,
        "properties": {
            "schemaName": {"type": "string"},
            "displayName": {"type": "string"},
            "itemLabel": {"type": "string"},
            "fields": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "required": ["type", "description"],
                    "additionalProperties": False,
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["string", "integer", "number", "boolean", "array", "enum", "datetime"],
                        },
                        "required": {"type": "boolean"},
                        "description": {"type": "string"},
                        "display_order": {"type": "integer"},
                        "search": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["keyword", "embed", "graph"]},
                        },
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {"type": {"type": "string"}},
                            "required": ["type"],
                        },
                    },
                },
            },
        },
    },
}


class _StubAgent(BaseStreamingAgent):
    """Minimal concrete subclass for testing."""

    def __init__(self, **overrides):
        config = AgentConfig(
            name="test-structured",
            display_name="Test Structured",
            instructions="You are a test agent.",
            tools=[],
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.LLM_DRIVEN,
            **overrides,
        )
        super().__init__(config)

    def pipeline_steps(self, query, context):
        return []


def _make_openai_response(content: str):
    """Build a mock OpenAI ChatCompletion response."""
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# =============================================================================
# _extract_json_from_response
# =============================================================================


class TestExtractJsonFromResponse:
    """Unit tests for the static JSON extraction helper."""

    def test_clean_json(self):
        raw = '{"name": "Alice", "age": 30}'
        assert BaseStreamingAgent._extract_json_from_response(raw) == raw

    def test_strip_think_tags(self):
        raw = '<think>I should output JSON</think>{"name": "Bob", "age": 25}'
        result = BaseStreamingAgent._extract_json_from_response(raw)
        parsed = json.loads(result)
        assert parsed == {"name": "Bob", "age": 25}

    def test_strip_multiline_think_tags(self):
        raw = (
            "<think>\nLet me reason about this.\n"
            "The user wants a schema.\n"
            "I'll produce JSON.\n</think>\n"
            '{"schemaName": "Resume"}'
        )
        result = BaseStreamingAgent._extract_json_from_response(raw)
        parsed = json.loads(result)
        assert parsed["schemaName"] == "Resume"

    def test_extract_from_code_fence(self):
        raw = 'Here is the schema:\n```json\n{"name": "test"}\n```\n'
        result = BaseStreamingAgent._extract_json_from_response(raw)
        assert json.loads(result) == {"name": "test"}

    def test_extract_from_code_fence_no_lang(self):
        raw = 'Result:\n```\n{"x": 1}\n```'
        result = BaseStreamingAgent._extract_json_from_response(raw)
        assert json.loads(result) == {"x": 1}

    def test_extract_json_blob_from_preamble(self):
        raw = 'Sure! Here is the output:\n\n{"key": "value"}'
        result = BaseStreamingAgent._extract_json_from_response(raw)
        assert json.loads(result) == {"key": "value"}

    def test_extract_array(self):
        raw = 'The results are:\n[1, 2, 3]'
        result = BaseStreamingAgent._extract_json_from_response(raw)
        assert json.loads(result) == [1, 2, 3]

    def test_think_then_code_fence(self):
        raw = (
            "<think>reasoning</think>\n"
            "```json\n"
            '{"a": 1}\n'
            "```"
        )
        result = BaseStreamingAgent._extract_json_from_response(raw)
        assert json.loads(result) == {"a": 1}

    def test_empty_string(self):
        result = BaseStreamingAgent._extract_json_from_response("")
        assert result == ""

    def test_pure_text_no_json(self):
        raw = "I couldn't find any relevant information."
        result = BaseStreamingAgent._extract_json_from_response(raw)
        assert result == raw

    def test_nested_json(self):
        nested = {
            "schemaName": "Resume Schema",
            "displayName": "Parsed Resumes",
            "itemLabel": "Resume",
            "fields": {
                "name": {
                    "type": "string",
                    "description": "Full name",
                    "search": ["keyword", "graph"],
                },
            },
        }
        raw = f"<think>analyzing</think>{json.dumps(nested)}"
        result = BaseStreamingAgent._extract_json_from_response(raw)
        assert json.loads(result) == nested


# =============================================================================
# _call_structured_output
# =============================================================================


class TestCallStructuredOutput:
    """Tests for the direct OpenAI structured output call."""

    @pytest.fixture
    def agent(self):
        return _StubAgent()

    @pytest.mark.asyncio
    async def test_no_think_prepended_to_prompt(self, agent):
        """Verify /no_think is prepended to the user prompt."""
        valid_json = '{"name": "Alice", "age": 30}'
        mock_response = _make_openai_response(valid_json)

        with patch("app.agents.base_agent.get_settings") as mock_settings:
            settings = MagicMock()
            settings.litellm_base_url = "http://localhost:4000"
            settings.litellm_api_key = "test"
            settings.default_model = "default"
            settings.llm_backend = "mlx"
            mock_settings.return_value = settings

            with patch("openai.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
                MockClient.return_value = mock_client

                await agent._call_structured_output(
                    prompt="Generate a person",
                    system_prompt="You are a test agent.",
                    response_schema=SIMPLE_SCHEMA,
                )

                call_kwargs = mock_client.chat.completions.create.call_args[1]
                user_msg = call_kwargs["messages"][1]["content"]
                assert user_msg.startswith("/no_think\n")

    @pytest.mark.asyncio
    async def test_default_max_tokens(self, agent):
        """Verify max_tokens defaults to 32768 when not specified."""
        valid_json = '{"name": "Alice", "age": 30}'
        mock_response = _make_openai_response(valid_json)

        with patch("app.agents.base_agent.get_settings") as mock_settings:
            settings = MagicMock()
            settings.litellm_base_url = "http://localhost:4000"
            settings.litellm_api_key = "test"
            settings.default_model = "default"
            settings.llm_backend = "mlx"
            mock_settings.return_value = settings

            with patch("openai.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
                MockClient.return_value = mock_client

                await agent._call_structured_output(
                    prompt="Generate a person",
                    system_prompt="You are a test agent.",
                    response_schema=SIMPLE_SCHEMA,
                )

                call_kwargs = mock_client.chat.completions.create.call_args[1]
                assert call_kwargs["max_tokens"] == 32768

    @pytest.mark.asyncio
    async def test_custom_max_tokens_honored(self, agent):
        """Verify explicit max_tokens overrides default."""
        valid_json = '{"name": "Alice", "age": 30}'
        mock_response = _make_openai_response(valid_json)

        with patch("app.agents.base_agent.get_settings") as mock_settings:
            settings = MagicMock()
            settings.litellm_base_url = "http://localhost:4000"
            settings.litellm_api_key = "test"
            settings.default_model = "default"
            settings.llm_backend = "mlx"
            mock_settings.return_value = settings

            with patch("openai.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
                MockClient.return_value = mock_client

                await agent._call_structured_output(
                    prompt="Generate a person",
                    system_prompt="You are a test agent.",
                    response_schema=SIMPLE_SCHEMA,
                    max_tokens=8192,
                )

                call_kwargs = mock_client.chat.completions.create.call_args[1]
                assert call_kwargs["max_tokens"] == 8192

    @pytest.mark.asyncio
    async def test_response_format_always_sent(self, agent):
        """Verify response_format is always included in the request."""
        valid_json = '{"name": "Alice", "age": 30}'
        mock_response = _make_openai_response(valid_json)

        with patch("app.agents.base_agent.get_settings") as mock_settings:
            settings = MagicMock()
            settings.litellm_base_url = "http://localhost:4000"
            settings.litellm_api_key = "test"
            settings.default_model = "default"
            settings.llm_backend = "mlx"
            mock_settings.return_value = settings

            with patch("openai.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
                MockClient.return_value = mock_client

                await agent._call_structured_output(
                    prompt="Generate a person",
                    system_prompt="You are a test agent.",
                    response_schema=SIMPLE_SCHEMA,
                )

                call_kwargs = mock_client.chat.completions.create.call_args[1]
                assert "response_format" in call_kwargs
                assert call_kwargs["response_format"]["type"] == "json_schema"

    @pytest.mark.asyncio
    async def test_think_tags_stripped_from_response(self, agent):
        """Verify <think> tags in response are handled gracefully."""
        think_response = '<think>reasoning here</think>{"name": "Bob", "age": 25}'
        mock_response = _make_openai_response(think_response)

        with patch("app.agents.base_agent.get_settings") as mock_settings:
            settings = MagicMock()
            settings.litellm_base_url = "http://localhost:4000"
            settings.litellm_api_key = "test"
            settings.default_model = "default"
            settings.llm_backend = "mlx"
            mock_settings.return_value = settings

            with patch("openai.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
                MockClient.return_value = mock_client

                result = await agent._call_structured_output(
                    prompt="Generate a person",
                    system_prompt="You are a test agent.",
                    response_schema=SIMPLE_SCHEMA,
                )

                parsed = json.loads(result)
                assert parsed == {"name": "Bob", "age": 25}

    @pytest.mark.asyncio
    async def test_retry_on_invalid_json(self, agent):
        """Verify retry happens when first response is invalid JSON."""
        bad_response = _make_openai_response("This is not JSON at all")
        good_response = _make_openai_response('{"name": "Alice", "age": 30}')

        with patch("app.agents.base_agent.get_settings") as mock_settings:
            settings = MagicMock()
            settings.litellm_base_url = "http://localhost:4000"
            settings.litellm_api_key = "test"
            settings.default_model = "default"
            settings.llm_backend = "mlx"
            mock_settings.return_value = settings

            with patch("openai.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(
                    side_effect=[bad_response, good_response]
                )
                MockClient.return_value = mock_client

                result = await agent._call_structured_output(
                    prompt="Generate a person",
                    system_prompt="You are a test agent.",
                    response_schema=SIMPLE_SCHEMA,
                )

                assert json.loads(result) == {"name": "Alice", "age": 30}
                assert mock_client.chat.completions.create.call_count == 2

                # Retry message should also have /no_think
                retry_kwargs = mock_client.chat.completions.create.call_args_list[1][1]
                retry_user_msg = retry_kwargs["messages"][-1]["content"]
                assert "/no_think" in retry_user_msg

    @pytest.mark.asyncio
    async def test_raises_after_max_attempts(self, agent):
        """Verify ValueError raised after all attempts fail."""
        bad_response = _make_openai_response("not json")

        with patch("app.agents.base_agent.get_settings") as mock_settings:
            settings = MagicMock()
            settings.litellm_base_url = "http://localhost:4000"
            settings.litellm_api_key = "test"
            settings.default_model = "default"
            settings.llm_backend = "mlx"
            mock_settings.return_value = settings

            with patch("openai.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(return_value=bad_response)
                MockClient.return_value = mock_client

                with pytest.raises(ValueError, match="Structured output failed after 2 attempts"):
                    await agent._call_structured_output(
                        prompt="Generate a person",
                        system_prompt="You are a test agent.",
                        response_schema=SIMPLE_SCHEMA,
                    )

    @pytest.mark.asyncio
    async def test_schema_validation_retry(self, agent):
        """Verify retry on valid JSON that fails schema validation."""
        wrong_schema = _make_openai_response('{"name": "Alice"}')  # missing required 'age'
        correct = _make_openai_response('{"name": "Alice", "age": 30}')

        with patch("app.agents.base_agent.get_settings") as mock_settings:
            settings = MagicMock()
            settings.litellm_base_url = "http://localhost:4000"
            settings.litellm_api_key = "test"
            settings.default_model = "default"
            settings.llm_backend = "mlx"
            mock_settings.return_value = settings

            with patch("openai.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(
                    side_effect=[wrong_schema, correct]
                )
                MockClient.return_value = mock_client

                result = await agent._call_structured_output(
                    prompt="Generate a person",
                    system_prompt="You are a test agent.",
                    response_schema=SIMPLE_SCHEMA,
                )

                assert json.loads(result) == {"name": "Alice", "age": 30}
                assert mock_client.chat.completions.create.call_count == 2

    @pytest.mark.asyncio
    async def test_extraction_schema_with_think_tags(self, agent):
        """End-to-end: extraction schema response wrapped in <think> tags."""
        schema_output = {
            "schemaName": "Resume Schema",
            "displayName": "Parsed Resumes",
            "itemLabel": "Resume",
            "fields": {
                "name": {
                    "type": "string",
                    "description": "Candidate full name",
                    "search": ["keyword", "graph"],
                },
                "email": {
                    "type": "string",
                    "description": "Email address",
                    "search": ["keyword"],
                },
            },
        }
        raw = f"<think>\nI need to analyze this resume and create a schema.\n</think>\n{json.dumps(schema_output)}"
        mock_response = _make_openai_response(raw)

        with patch("app.agents.base_agent.get_settings") as mock_settings:
            settings = MagicMock()
            settings.litellm_base_url = "http://localhost:4000"
            settings.litellm_api_key = "test"
            settings.default_model = "default"
            settings.llm_backend = "mlx"
            mock_settings.return_value = settings

            with patch("openai.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
                MockClient.return_value = mock_client

                result = await agent._call_structured_output(
                    prompt="Analyze this document and generate an extraction schema.\n\nDocument content:\nJohn Doe\njohn@example.com",
                    system_prompt="You are a Schema Builder assistant.",
                    response_schema=EXTRACTION_SCHEMA,
                )

                parsed = json.loads(result)
                assert parsed["schemaName"] == "Resume Schema"
                assert "name" in parsed["fields"]
                assert parsed["fields"]["name"]["search"] == ["keyword", "graph"]
