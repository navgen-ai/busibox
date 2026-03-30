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

class ChatMessage(BaseModel):
    role: str
    content: str


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
    chat_template_kwargs: Optional[Dict[str, Any]] = None,
) -> str:
    """Apply the model's chat template to produce a single prompt string."""
    msg_dicts = [{"role": m.role, "content": m.content} for m in messages]
    extra_kwargs = chat_template_kwargs or {}
    try:
        return tokenizer.apply_chat_template(
            msg_dicts, tokenize=False, add_generation_prompt=True, **extra_kwargs
        )
    except Exception:
        parts = []
        for m in messages:
            if m.role == "system":
                parts.append(f"<|system|>\n{m.content}")
            elif m.role == "user":
                parts.append(f"<|user|>\n{m.content}")
            elif m.role == "assistant":
                parts.append(f"<|assistant|>\n{m.content}")
        parts.append("<|assistant|>\n")
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
    """Determine thinking budget: explicit param > extra_body > server default."""
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


async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
    body = await request.json()
    req = ChatCompletionRequest(**body)
    holder = get_holder()

    chat_kwargs = _resolve_chat_template_kwargs(req)
    prompt = _render_chat_prompt(holder.tokenizer, req.messages, chat_template_kwargs=chat_kwargs)

    rf = req.response_format
    use_json_schema = (
        rf is not None
        and rf.type == "json_schema"
        and rf.json_schema is not None
    )
    use_json_object = rf is not None and rf.type == "json_object"

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

    elapsed_ms = round((time.monotonic() - t0) * 1000)
    logger.info(f"Generated {len(content)} chars in {elapsed_ms}ms")

    return JSONResponse(_build_completion_response(content, req.model))


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
