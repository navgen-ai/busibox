"""
Convert a JSON Schema dict to a dynamic Pydantic BaseModel at runtime.

Used to bridge runtime ``response_schema`` dicts (from extraction and
schema-builder APIs) into PydanticAI's ``output_type`` / ``NativeOutput``
mechanism, giving us both ``response_format`` enforcement *and* automatic
response validation with retry.

Handles the JSON Schema subset used by our extraction and schema-builder
schemas: basic types, enums, arrays, nested objects, and
``additionalProperties`` (both ``true`` and ``{schema}`` forms).
"""

from __future__ import annotations

import logging
from enum import Enum as _Enum
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, ConfigDict, Field, create_model

logger = logging.getLogger(__name__)

_SCALAR_MAP: Dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}

RESERVED_FIELD_NAMES = frozenset({
    "type", "schema", "validate", "model_fields", "model_config",
    "model_computed_fields", "model_extra", "model_fields_set",
})

_counter = 0


def _unique_name(base: str) -> str:
    global _counter
    _counter += 1
    return f"{base}_{_counter}"


def json_schema_to_pydantic(
    response_schema: Dict[str, Any],
) -> Type[BaseModel]:
    """
    Convert a ``response_schema`` envelope (with ``name``, ``strict``,
    ``schema`` keys) into a Pydantic model.

    If the dict already *is* a raw JSON Schema (has ``type`` / ``properties``
    at the top level), it is used directly.
    """
    schema_name = response_schema.get("name", "DynamicOutput")
    inner = response_schema.get("schema", response_schema)
    return _build_model(inner, _sanitise_class_name(schema_name))


def _sanitise_class_name(raw: str) -> str:
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw).strip("_") or "Model"


def _build_model(
    schema: Dict[str, Any],
    name: str,
) -> Type[BaseModel]:
    """Recursively build a Pydantic model from a JSON Schema ``object`` node."""
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    additional = schema.get("additionalProperties", True)

    field_defs: Dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        python_type = _resolve_type(prop_schema, f"{name}_{prop_name}")
        field_alias = None

        safe_name = prop_name
        if prop_name in RESERVED_FIELD_NAMES:
            safe_name = f"field_{prop_name}"
            field_alias = prop_name

        if prop_name in required:
            if field_alias:
                field_defs[safe_name] = (python_type, Field(..., alias=field_alias))
            else:
                field_defs[safe_name] = (python_type, ...)
        else:
            if field_alias:
                field_defs[safe_name] = (Optional[python_type], Field(None, alias=field_alias))
            else:
                field_defs[safe_name] = (Optional[python_type], None)

    extra_mode = "ignore"
    if additional is False:
        extra_mode = "forbid"
    elif additional is True:
        extra_mode = "allow"
    elif isinstance(additional, dict):
        extra_mode = "allow"

    has_aliases = any(pn in RESERVED_FIELD_NAMES for pn in properties)

    config = ConfigDict(extra=extra_mode)  # type: ignore[typeddict-item]
    if has_aliases:
        config = ConfigDict(extra=extra_mode, populate_by_name=True)  # type: ignore[typeddict-item]

    base: Type[BaseModel] = type(
        _unique_name(f"{name}_Base"),
        (BaseModel,),
        {"model_config": config},
    )

    model = create_model(
        _unique_name(name),
        __base__=base,
        **field_defs,
    )
    return model


def _resolve_type(prop_schema: Dict[str, Any], context_name: str) -> type:
    """Map a JSON Schema property definition to a Python type annotation."""
    prop_type = prop_schema.get("type", "string")

    if "enum" in prop_schema:
        values = prop_schema["enum"]
        if values and all(isinstance(v, str) for v in values):
            safe_members = {}
            for v in values:
                member_name = v if v.isidentifier() and v not in {"type", "class"} else f"v_{v}"
                safe_members[member_name] = v
            enum_cls = _Enum(_unique_name(f"{context_name}_Enum"), safe_members)
            return enum_cls
        return str

    if prop_type == "array":
        items_schema = prop_schema.get("items", {"type": "string"})
        item_type = _resolve_type(items_schema, f"{context_name}_item")
        return List[item_type]  # type: ignore[valid-type]

    if prop_type == "object":
        return _resolve_object_type(prop_schema, context_name)

    return _SCALAR_MAP.get(str(prop_type), str)


def _resolve_object_type(prop_schema: Dict[str, Any], context_name: str) -> type:
    """
    Resolve an ``object``-typed schema node.

    Three patterns:

    1. Has ``properties`` -> build a nested Pydantic model.
    2. Has ``additionalProperties`` with a sub-schema (but no ``properties``)
       -> ``Dict[str, ValueType]``.
    3. Neither -> ``Dict[str, Any]``.
    """
    properties = prop_schema.get("properties")
    additional = prop_schema.get("additionalProperties")

    if properties:
        return _build_model(prop_schema, context_name)

    if isinstance(additional, dict) and additional:
        if additional.get("properties"):
            value_model = _build_model(additional, f"{context_name}_val")
            return Dict[str, value_model]  # type: ignore[valid-type]
        value_type = _resolve_type(additional, f"{context_name}_val")
        return Dict[str, value_type]  # type: ignore[valid-type]

    return Dict[str, Any]
