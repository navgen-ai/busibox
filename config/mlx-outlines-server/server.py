"""
Outlines-backed MLX server with OpenAI-compatible API.

Wraps Outlines + MLX to provide grammar-constrained structured output in
development, matching the vLLM + Outlines behaviour in production.

Exposes the same OpenAI-compatible ``/v1/chat/completions`` and ``/v1/models``
endpoints that ``mlx_lm.server`` provides, but adds ``response_format``
support via Outlines' guided-decoding engine.

Usage:
    python server.py --model mlx-community/Qwen3-4B-Instruct-2507-4bit \
                     --host 0.0.0.0 --port 8080

Environment:
    Requires ``outlines[mlxlm]`` and ``mlx-lm`` in the active venv.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import mlx.core as mx
import mlx_lm
import outlines
from pydantic import BaseModel, Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

logger = logging.getLogger("mlx-outlines-server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# ---------------------------------------------------------------------------
# Request / response schemas (subset of the OpenAI chat-completions API)
# ---------------------------------------------------------------------------

class ToolFunction(BaseModel):
    name: str
    arguments: Optional[str] = None


class ToolCall(BaseModel):
    id: Optional[str] = None
    type: str = "function"
    function: ToolFunction


class ChatMessage(BaseModel):
    role: str
    content: Optional[Any] = None
    name: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None


class RequestToolDefinition(BaseModel):
    type: str = "function"
    function: Dict[str, Any]


class ResponseFormatJsonSchema(BaseModel):
    name: str = "output"
    strict: bool = False
    schema_: Dict[str, Any] = Field(default_factory=dict, alias="schema")

    model_config = {"populate_by_name": True}


class ResponseFormat(BaseModel):
    type: str = "text"
    json_schema: Optional[ResponseFormatJsonSchema] = None


class ChatCompletionRequest(BaseModel):
    model: str = "default"
    messages: List[ChatMessage]
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int = 4096
    stream: bool = False
    response_format: Optional[ResponseFormat] = None
    stop: Optional[List[str]] = None
    chat_template_kwargs: Optional[Dict[str, Any]] = None
    extra_body: Optional[Dict[str, Any]] = None
    max_thinking_tokens: Optional[int] = None
    tools: Optional[List[RequestToolDefinition]] = None
    tool_choice: Optional[Any] = None
    parallel_tool_calls: Optional[bool] = None


# ---------------------------------------------------------------------------
# Model holder (loaded once at startup)
# ---------------------------------------------------------------------------

class ModelHolder:
    def __init__(self, model_path: str, trust_remote_code: bool = True):
        self.model_path = model_path
        self._raw_model = None
        self._raw_tokenizer = None
        self._outlines_model = None

    def load(self):
        logger.info(f"Loading model: {self.model_path}")
        t0 = time.monotonic()
        self._raw_model, self._raw_tokenizer = mlx_lm.load(self.model_path)
        self._outlines_model = outlines.from_mlxlm(self._raw_model, self._raw_tokenizer)
        elapsed = round(time.monotonic() - t0, 1)
        logger.info(f"Model loaded in {elapsed}s")

    @property
    def model(self):
        return self._outlines_model

    @property
    def tokenizer(self):
        return self._raw_tokenizer


_holder: Optional[ModelHolder] = None


def get_holder() -> ModelHolder:
    assert _holder is not None, "Model not loaded"
    return _holder


# ---------------------------------------------------------------------------
# Chat template rendering
# ---------------------------------------------------------------------------

def _render_chat_prompt(
    tokenizer,
    messages: List[ChatMessage],
    tools: Optional[List[Dict[str, Any]]] = None,
    chat_template_kwargs: Optional[Dict[str, Any]] = None,
) -> str:
    """Apply the model's chat template to produce a single prompt string."""
    msg_dicts = []
    for m in messages:
        item: Dict[str, Any] = {"role": m.role, "content": m.content}
        if m.name:
            item["name"] = m.name
        if m.tool_call_id:
            item["tool_call_id"] = m.tool_call_id
        if m.tool_calls:
            tool_call_dicts = []
            for tc in m.tool_calls:
                args_raw = tc.function.arguments or "{}"
                if isinstance(args_raw, str):
                    try:
                        args_parsed = json.loads(args_raw)
                    except (json.JSONDecodeError, TypeError):
                        args_parsed = {}
                else:
                    args_parsed = args_raw
                tool_call_dicts.append({
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": args_parsed,
                    },
                })
            item["tool_calls"] = tool_call_dicts
        msg_dicts.append(item)
    extra_kwargs = chat_template_kwargs or {}
    if tools:
        extra_kwargs = {**extra_kwargs, "tools": tools}
    try:
        return tokenizer.apply_chat_template(
            msg_dicts, tokenize=False, add_generation_prompt=True, **extra_kwargs
        )
    except Exception:
        parts = []
        pending_tool_results: list[str] = []
        for i, m in enumerate(messages):
            if m.role == "system":
                parts.append(f"<|im_start|>system\n{m.content or ''}<|im_end|>")
            elif m.role == "user":
                parts.append(f"<|im_start|>user\n{m.content or ''}<|im_end|>")
            elif m.role == "assistant":
                content = m.content or ""
                if m.tool_calls:
                    tc_parts = []
                    for tc in m.tool_calls:
                        fn_name = tc.function.name
                        args_raw = tc.function.arguments or "{}"
                        try:
                            args_dict = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                        except (json.JSONDecodeError, TypeError):
                            args_dict = {}
                        param_lines = []
                        if isinstance(args_dict, dict):
                            for k, v in args_dict.items():
                                v_str = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                                param_lines.append(f"<{k}>\n{v_str}\n</{k}>")
                        params_block = "\n".join(param_lines)
                        tc_parts.append(f"<tool_call>\n<{fn_name}>\n{params_block}\n</{fn_name}>\n</tool_call>")
                    tc_text = "\n\n".join(tc_parts)
                    if content.strip():
                        parts.append(f"<|im_start|>assistant\n{content}\n\n{tc_text}<|im_end|>")
                    else:
                        parts.append(f"<|im_start|>assistant\n{tc_text}<|im_end|>")
                else:
                    parts.append(f"<|im_start|>assistant\n{content}<|im_end|>")
            elif m.role == "tool":
                tool_content = m.content if isinstance(m.content, str) else json.dumps(m.content or {})
                pending_tool_results.append(f"<tool_response>\n{tool_content}\n</tool_response>")
                next_is_tool = (i + 1 < len(messages) and messages[i + 1].role == "tool")
                if not next_is_tool:
                    parts.append(f"<|im_start|>user\n" + "\n".join(pending_tool_results) + "<|im_end|>")
                    pending_tool_results.clear()
        parts.append("<|im_start|>assistant\n")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------

def _resolve_chat_template_kwargs(req: ChatCompletionRequest) -> Optional[Dict[str, Any]]:
    """Extract chat_template_kwargs from the request (top-level or nested in extra_body)."""
    if req.chat_template_kwargs:
        return req.chat_template_kwargs
    if req.extra_body and "chat_template_kwargs" in req.extra_body:
        return req.extra_body["chat_template_kwargs"]
    return None


# ---------------------------------------------------------------------------
# Thinking budget logits processor (MLX-compatible)
# ---------------------------------------------------------------------------

class ThinkingBudgetProcessor:
    """Limit thinking tokens for Qwen3.5-style <think> blocks.

    After ``max_thinking_tokens`` tokens, forces the model to emit
    ``\\n</think>`` so it transitions to the answer.  Once ``</think>``
    has been emitted, subsequent calls suppress that token to prevent
    the model from repeating it indefinitely.

    MLX logits processors receive ``(tokens, logits)`` where logits has
    shape ``(1, vocab_size)`` (2-D).
    """

    def __init__(self, tokenizer, max_thinking_tokens: int):
        self.max_thinking_tokens = max_thinking_tokens
        self.tokens_generated = 0
        self.stopped_thinking = False
        self._think_end_id = tokenizer.encode("</think>")[0]
        self._nl_id = tokenizer.encode("\n")[0]
        self._think_start_id = tokenizer.encode("<think>")[0]

    def __call__(self, tokens: mx.array, logits: mx.array) -> mx.array:
        self.tokens_generated += 1
        if self.max_thinking_tokens is None:
            return logits

        if self.stopped_thinking:
            logits[..., self._think_end_id] = mx.array(float("-inf"))
            logits[..., self._think_start_id] = mx.array(float("-inf"))
            return logits

        ratio = self.tokens_generated / self.max_thinking_tokens

        if ratio > 0.95 and self.tokens_generated < self.max_thinking_tokens - 1:
            boost = mx.array(1.0 + ratio)
            logits[..., self._nl_id] = logits[..., self._nl_id] * boost
            logits[..., self._think_end_id] = logits[..., self._think_end_id] * boost

        if self.tokens_generated >= self.max_thinking_tokens - 1:
            mask = mx.full(logits.shape, float("-inf"))
            if self.tokens_generated == self.max_thinking_tokens - 1:
                mask[..., self._nl_id] = mx.array(0.0)
            else:
                mask[..., self._think_end_id] = mx.array(0.0)
                self.stopped_thinking = True
            logits = mask

        return logits


DEFAULT_THINKING_BUDGET = 512


def _resolve_thinking_budget(req: ChatCompletionRequest) -> Optional[int]:
    """Determine thinking budget: explicit param > extra_body > server default.

    Auto-disables thinking when tools are active to prevent thinking content
    from bleeding into tool call output (Qwen3.5 best practice per QwenLM/Qwen3#1831).
    """
    if req.tools:
        return None

    if req.max_thinking_tokens is not None:
        return req.max_thinking_tokens if req.max_thinking_tokens > 0 else None

    if req.extra_body:
        budget = req.extra_body.get("max_thinking_tokens")
        if budget is not None:
            return int(budget) if int(budget) > 0 else None

    chat_kwargs = _resolve_chat_template_kwargs(req)
    if chat_kwargs and chat_kwargs.get("enable_thinking") is False:
        return None

    return DEFAULT_THINKING_BUDGET


# ---------------------------------------------------------------------------
# Generation helpers
# ---------------------------------------------------------------------------

def _generate_plain(prompt: str, req: ChatCompletionRequest) -> str:
    holder = get_holder()
    return holder.model(prompt, max_tokens=req.max_tokens)


def _generate_json_schema(prompt: str, req: ChatCompletionRequest, schema: Dict[str, Any]) -> str:
    """Generate with Outlines JSON Schema grammar enforcement."""
    holder = get_holder()
    from pydantic import create_model as _create_model

    pydantic_model = _json_schema_to_pydantic(schema)
    return holder.model(prompt, output_type=pydantic_model, max_tokens=req.max_tokens)


def _generate_json_object(prompt: str, req: ChatCompletionRequest) -> str:
    """Generate with generic JSON grammar (valid JSON, no schema)."""
    holder = get_holder()
    return holder.model(prompt, output_type=dict, max_tokens=req.max_tokens)


# ---------------------------------------------------------------------------
# JSON Schema -> Pydantic model converter (lightweight, for Outlines)
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


def _json_schema_to_pydantic(
    schema: Dict[str, Any],
    model_name: str = "DynamicModel",
) -> type:
    """
    Convert a JSON Schema dict to a dynamic Pydantic BaseModel.

    Handles flat and nested objects, arrays, enums.  Designed for the subset
    of JSON Schema that our extraction and schema-builder schemas use.
    """
    from typing import List as TList, Optional as TOptional
    from pydantic import create_model as _create_model

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    field_definitions: Dict[str, Any] = {}
    for prop_name, prop_def in properties.items():
        python_type = _resolve_type(prop_def)
        if prop_name in required:
            field_definitions[prop_name] = (python_type, ...)
        else:
            field_definitions[prop_name] = (Optional[python_type], None)

    return _create_model(model_name, **field_definitions)


def _resolve_type(prop_def: Dict[str, Any]):
    """Resolve a JSON Schema property definition to a Python type."""
    from typing import List as TList

    prop_type = prop_def.get("type", "string")

    if "enum" in prop_def:
        from enum import Enum as _Enum
        values = prop_def["enum"]
        return _Enum("_AutoEnum", {str(v): v for v in values})

    if prop_type == "array":
        items = prop_def.get("items", {"type": "string"})
        item_type = _resolve_type(items)
        return TList[item_type]

    if prop_type == "object":
        nested_props = prop_def.get("properties")
        if nested_props:
            return _json_schema_to_pydantic(prop_def, model_name="_Nested")
        return Dict[str, Any]

    return _TYPE_MAP.get(prop_type, str)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

def _build_completion_response(content: str, model: str) -> Dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _build_completion_tool_response(
    tool_calls: List[Dict[str, Any]],
    model: str,
) -> Dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": None, "tool_calls": tool_calls},
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _build_stream_chunk(
    chunk_id: str, model: str, delta_content: Optional[str] = None, finish_reason: Optional[str] = None,
) -> str:
    delta: Dict[str, Any] = {}
    if delta_content is not None:
        delta["content"] = delta_content
        delta["role"] = "assistant"
    chunk = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(chunk)}\n\n"


def _build_stream_tool_chunk(
    chunk_id: str,
    model: str,
    tool_calls: List[Dict[str, Any]],
    finish_reason: Optional[str] = None,
) -> str:
    chunk = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "tool_calls": tool_calls},
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\n"


def _strip_think_blocks(text: str) -> str:
    """Remove qwen-style think blocks from output text."""
    original = text
    cleaned = text
    while True:
        start = cleaned.find("<think>")
        if start == -1:
            break
        end = cleaned.find("</think>", start)
        if end == -1:
            # Unterminated think block: keep text before it, but don't
            # discard the whole response if the model omitted the end tag.
            cleaned = cleaned[:start]
            break
        cleaned = cleaned[:start] + cleaned[end + len("</think>") :]
    cleaned = cleaned.strip()
    if cleaned:
        return cleaned
    # Fallback: preserve content if stripping removed everything.
    return original.replace("<think>", "").replace("</think>", "").strip()


def _extract_tool_calls(raw_output: str) -> Optional[List[Dict[str, Any]]]:
    """Parse model output into OpenAI-style tool_calls if present.

    Handles three formats:
    1. Qwen3.5 native XML: <tool_call><fn_name><arg>val</arg></fn_name></tool_call>
    2. Qwen JSON-in-tags: <tool_call>{"name":"fn","arguments":{...}}</tool_call>
    3. Raw JSON / fenced JSON fallback
    """
    import re

    cleaned = _strip_think_blocks(raw_output)
    if not cleaned:
        return None

    candidates: List[Dict[str, Any]] = []

    # 1a) Qwen3.5 native XML tool calls: <tool_call><fn_name><arg>val</arg></fn_name></tool_call>
    for tc_match in re.finditer(r"<tool_call>\s*([\s\S]*?)\s*</tool_call>", cleaned):
        tc_body = tc_match.group(1).strip()

        # Try JSON parse first (format 2)
        if tc_body.startswith("{"):
            try:
                obj = json.loads(tc_body)
                if isinstance(obj, dict):
                    candidates.append(obj)
                    continue
            except Exception:
                pass

        # Try XML format: <function_name><param>val</param>...</function_name>
        fn_match = re.match(r"<([a-zA-Z_][a-zA-Z0-9_]*)>\s*([\s\S]*?)\s*</\1>", tc_body)
        if fn_match:
            fn_name = fn_match.group(1)
            params_body = fn_match.group(2)
            arguments: Dict[str, Any] = {}
            for param_match in re.finditer(
                r"<([a-zA-Z_][a-zA-Z0-9_]*)>\s*([\s\S]*?)\s*</\1>", params_body
            ):
                param_name = param_match.group(1)
                param_value_str = param_match.group(2).strip()
                try:
                    arguments[param_name] = json.loads(param_value_str)
                except (json.JSONDecodeError, ValueError):
                    arguments[param_name] = param_value_str
            candidates.append({"name": fn_name, "arguments": arguments})
            continue

    # 1b) Fallback brace-counting for malformed <tool_call>{...}> variants
    if not candidates:
        start_idx = 0
        while True:
            tag_idx = cleaned.find("<tool_call>", start_idx)
            if tag_idx == -1:
                break
            brace_idx = cleaned.find("{", tag_idx)
            if brace_idx == -1:
                break
            depth = 0
            in_string = False
            escape = False
            end_idx = -1
            for i in range(brace_idx, len(cleaned)):
                ch = cleaned[i]
                if in_string:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == "\"":
                        in_string = False
                    continue
                if ch == "\"":
                    in_string = True
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end_idx = i
                        break
            if end_idx == -1:
                break
            payload = cleaned[brace_idx : end_idx + 1].strip()
            try:
                obj = json.loads(payload)
                if isinstance(obj, dict):
                    candidates.append(obj)
            except Exception:
                pass
            start_idx = end_idx + 1

    # 2) Raw JSON payload fallback
    if not candidates:
        json_like = cleaned
        if "```" in cleaned:
            fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, flags=re.IGNORECASE)
            if fenced:
                json_like = fenced[0].strip()
        try:
            parsed = json.loads(json_like)
            if isinstance(parsed, dict):
                if isinstance(parsed.get("tool_calls"), list):
                    for call in parsed["tool_calls"]:
                        if isinstance(call, dict):
                            candidates.append(call)
                else:
                    candidates.append(parsed)
            elif isinstance(parsed, list):
                for call in parsed:
                    if isinstance(call, dict):
                        candidates.append(call)
        except Exception:
            pass

    # 3) Function-like call fallback: tool_name({...})
    if not candidates:
        for match in re.findall(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\s*(\{[\s\S]*\})\s*\)", cleaned):
            name, args_payload = match
            try:
                args_obj = json.loads(args_payload)
            except Exception:
                continue
            candidates.append({"name": name, "arguments": args_obj})

    if not candidates:
        return None

    tool_calls: List[Dict[str, Any]] = []
    for i, call in enumerate(candidates):
        # Accept both direct and nested OpenAI formats
        function = call.get("function") if isinstance(call.get("function"), dict) else None
        name = (
            (function or {}).get("name")
            or call.get("name")
            or call.get("tool")
            or call.get("tool_name")
        )
        arguments = (
            (function or {}).get("arguments")
            or call.get("arguments")
            or call.get("args")
            or {}
        )
        if not name or not isinstance(name, str):
            continue
        if isinstance(arguments, str):
            args_str = arguments
        else:
            args_str = json.dumps(arguments)

        tool_calls.append(
            {
                "id": call.get("id") or f"call_{uuid.uuid4().hex[:10]}_{i}",
                "type": "function",
                "function": {"name": name, "arguments": args_str},
            }
        )

    return tool_calls or None


async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
    body = await request.json()
    req = ChatCompletionRequest(**body)
    holder = get_holder()

    tools_payload = [t.model_dump() for t in req.tools] if req.tools else None
    chat_kwargs = _resolve_chat_template_kwargs(req) or {}
    if tools_payload:
        chat_kwargs["enable_thinking"] = False
    prompt = _render_chat_prompt(
        holder.tokenizer,
        req.messages,
        tools=tools_payload,
        chat_template_kwargs=chat_kwargs or None,
    )

    rf = req.response_format
    use_json_schema = (
        rf is not None
        and rf.type == "json_schema"
        and rf.json_schema is not None
    )
    use_json_object = rf is not None and rf.type == "json_object"

    # For tool-calling requests, generate full output then emit OpenAI-style
    # tool_calls chunks. Token-by-token stream is kept for plain text only.
    if req.stream and req.tools and not use_json_schema and not use_json_object:
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        t0 = time.monotonic()
        try:
            content = _generate_plain(prompt, req)
            tool_calls = _extract_tool_calls(content)
            if tool_calls:
                async def _stream_tool_result():
                    yield _build_stream_tool_chunk(chunk_id, req.model, tool_calls, finish_reason=None)
                    yield _build_stream_chunk(chunk_id, req.model, finish_reason="tool_calls")
                    yield "data: [DONE]\n\n"
                elapsed_ms = round((time.monotonic() - t0) * 1000)
                logger.info(f"Streamed tool call result in {elapsed_ms}ms")
                return StreamingResponse(_stream_tool_result(), media_type="text/event-stream")
        except Exception as e:
            logger.error(f"Tool-call streaming generation failed: {e}", exc_info=True)

    # Streaming is only supported for plain text generation (no grammar constraints)
    if req.stream and not use_json_schema and not use_json_object:
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        thinking_budget = _resolve_thinking_budget(req)
        extra_kwargs: Dict[str, Any] = {}
        if thinking_budget is not None:
            processor = ThinkingBudgetProcessor(holder.tokenizer, thinking_budget)
            extra_kwargs["logits_processors"] = [processor]
            logger.info(f"Thinking budget: {thinking_budget} tokens")

        async def _stream_tokens():
            t0 = time.monotonic()
            token_count = 0
            try:
                for resp in mlx_lm.stream_generate(
                    holder.model.model,
                    holder.model.mlx_tokenizer,
                    prompt,
                    max_tokens=req.max_tokens,
                    **extra_kwargs,
                ):
                    token_count += 1
                    yield _build_stream_chunk(chunk_id, req.model, delta_content=resp.text)
            except Exception as e:
                logger.error(f"Streaming generation failed: {e}", exc_info=True)
            yield _build_stream_chunk(chunk_id, req.model, finish_reason="stop")
            yield "data: [DONE]\n\n"
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            logger.info(f"Streamed {token_count} tokens in {elapsed_ms}ms")

        return StreamingResponse(_stream_tokens(), media_type="text/event-stream")

    t0 = time.monotonic()
    try:
        if use_json_schema:
            schema = rf.json_schema.schema_
            logger.info(f"Generating with JSON Schema enforcement: {rf.json_schema.name}")
            content = _generate_json_schema(prompt, req, schema)
        elif use_json_object:
            logger.info("Generating with generic JSON enforcement")
            content = _generate_json_object(prompt, req)
        else:
            content = _generate_plain(prompt, req)
    except Exception as e:
        logger.error(f"Generation failed: {e}", exc_info=True)
        return JSONResponse(
            {"error": {"message": str(e), "type": "generation_error"}},
            status_code=500,
        )

    if req.tools and not use_json_schema and not use_json_object:
        tool_calls = _extract_tool_calls(content)
        if tool_calls:
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            logger.info(f"Generated tool call response in {elapsed_ms}ms")
            return JSONResponse(_build_completion_tool_response(tool_calls, req.model))

    cleaned_content = _strip_think_blocks(content)
    elapsed_ms = round((time.monotonic() - t0) * 1000)
    logger.info(f"Generated {len(cleaned_content)} chars in {elapsed_ms}ms")

    return JSONResponse(_build_completion_response(cleaned_content, req.model))


async def list_models(request: Request) -> JSONResponse:
    holder = get_holder()
    return JSONResponse({
        "object": "list",
        "data": [
            {
                "id": holder.model_path,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "local",
            }
        ],
    })


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> Starlette:
    routes = [
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        Route("/v1/models", list_models, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
    ]
    return Starlette(routes=routes)


def main():
    parser = argparse.ArgumentParser(description="Outlines MLX Server")
    parser.add_argument("--model", required=True, help="HuggingFace model path")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--trust-remote-code", action="store_true", default=False)
    args = parser.parse_args()

    global _holder
    _holder = ModelHolder(args.model, trust_remote_code=args.trust_remote_code)
    _holder.load()

    import uvicorn
    app = create_app()
    logger.info(f"Starting Outlines MLX server on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
