"""Unit tests for json_schema_to_pydantic converter.

Validates that dynamic Pydantic models are correctly generated from
JSON Schema structures, including the extraction schema format with
additionalProperties, nested enums, and reserved field names.
"""

import pytest
from pydantic import BaseModel, ValidationError

from app.utils.json_schema_to_pydantic import json_schema_to_pydantic


class TestSimpleSchemas:
    def test_basic_object(self):
        schema = {
            "name": "person",
            "schema": {
                "type": "object",
                "required": ["name", "age"],
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                },
            },
        }
        Model = json_schema_to_pydantic(schema)
        assert issubclass(Model, BaseModel)
        instance = Model(name="Alice", age=30)
        assert instance.name == "Alice"
        assert instance.age == 30

    def test_optional_fields(self):
        schema = {
            "name": "test",
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
        }
        Model = json_schema_to_pydantic(schema)
        instance = Model(id="abc")
        assert instance.id == "abc"
        assert instance.description is None

    def test_enum_field(self):
        schema = {
            "name": "status",
            "schema": {
                "type": "object",
                "required": ["status"],
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["active", "inactive"],
                    },
                },
            },
        }
        Model = json_schema_to_pydantic(schema)
        instance = Model(status="active")
        assert instance.status.value == "active"

    def test_array_of_strings(self):
        schema = {
            "name": "tags",
            "schema": {
                "type": "object",
                "required": ["tags"],
                "properties": {
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        }
        Model = json_schema_to_pydantic(schema)
        instance = Model(tags=["a", "b", "c"])
        assert instance.tags == ["a", "b", "c"]


class TestNestedObjects:
    def test_nested_object_with_properties(self):
        schema = {
            "name": "nested",
            "schema": {
                "type": "object",
                "required": ["address"],
                "properties": {
                    "address": {
                        "type": "object",
                        "required": ["city"],
                        "properties": {
                            "city": {"type": "string"},
                            "zip": {"type": "string"},
                        },
                    },
                },
            },
        }
        Model = json_schema_to_pydantic(schema)
        instance = Model(address={"city": "NYC", "zip": "10001"})
        assert instance.address.city == "NYC"

    def test_dict_str_any_fallback(self):
        schema = {
            "name": "flexible",
            "schema": {
                "type": "object",
                "required": ["data"],
                "properties": {
                    "data": {"type": "object"},
                },
            },
        }
        Model = json_schema_to_pydantic(schema)
        instance = Model(data={"anything": 123})
        assert instance.data == {"anything": 123}


class TestAdditionalProperties:
    def test_additional_properties_false_forbids_extra(self):
        schema = {
            "name": "strict",
            "schema": {
                "type": "object",
                "required": ["name"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                },
            },
        }
        Model = json_schema_to_pydantic(schema)
        instance = Model(name="ok")
        assert instance.name == "ok"
        with pytest.raises(ValidationError):
            Model(name="ok", extra_field="bad")

    def test_additional_properties_with_typed_schema(self):
        """Dict[str, ValueModel] when additionalProperties has a sub-schema."""
        schema = {
            "name": "fields_map",
            "schema": {
                "type": "object",
                "required": ["fields"],
                "properties": {
                    "fields": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "object",
                            "required": ["field_type"],
                            "properties": {
                                "field_type": {"type": "string"},
                                "required": {"type": "boolean"},
                            },
                        },
                    },
                },
            },
        }
        Model = json_schema_to_pydantic(schema)
        instance = Model(fields={"name": {"field_type": "string", "required": True}})
        assert instance.fields["name"].field_type == "string"


class TestExtractionSchema:
    """Test with the actual extraction schema structure used by schema-builder."""

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
                                "enum": [
                                    "string", "integer", "number",
                                    "boolean", "array", "enum", "datetime",
                                ],
                            },
                            "required": {"type": "boolean"},
                            "description": {"type": "string"},
                            "display_order": {"type": "integer"},
                            "search": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "enum": ["keyword", "embed", "graph"],
                                },
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

    def test_creates_model_without_error(self):
        Model = json_schema_to_pydantic(self.EXTRACTION_SCHEMA)
        assert issubclass(Model, BaseModel)

    def test_accepts_valid_schema_data(self):
        Model = json_schema_to_pydantic(self.EXTRACTION_SCHEMA)
        data = {
            "schemaName": "Resume Schema",
            "displayName": "Parsed Resumes",
            "itemLabel": "Resume",
            "fields": {
                "name": {
                    "type": "string",
                    "description": "Full name",
                    "search": ["keyword", "graph"],
                    "display_order": 1,
                },
                "skills": {
                    "type": "array",
                    "description": "Skills list",
                    "items": {"type": "string"},
                    "display_order": 2,
                },
            },
        }
        instance = Model(**data)
        assert instance.schemaName == "Resume Schema"
        assert isinstance(instance.fields, dict)

    def test_search_field_is_optional(self):
        """search should not be required on field definitions."""
        Model = json_schema_to_pydantic(self.EXTRACTION_SCHEMA)
        data = {
            "schemaName": "Test",
            "displayName": "Test",
            "itemLabel": "Record",
            "fields": {
                "name": {
                    "type": "string",
                    "description": "A field without search tags",
                },
            },
        }
        instance = Model(**data)
        assert instance.fields["name"].description == "A field without search tags"


class TestReservedFieldNames:
    def test_field_named_type_handled(self):
        """The word 'type' is a Python builtin and should be aliased."""
        schema = {
            "name": "type_field",
            "schema": {
                "type": "object",
                "required": ["type"],
                "additionalProperties": False,
                "properties": {
                    "type": {"type": "string"},
                },
            },
        }
        Model = json_schema_to_pydantic(schema)
        instance = Model(**{"type": "string"})
        assert instance.field_type == "string"

    def test_field_named_schema_handled(self):
        schema = {
            "name": "schema_field",
            "schema": {
                "type": "object",
                "required": ["schema"],
                "properties": {
                    "schema": {"type": "string"},
                },
            },
        }
        Model = json_schema_to_pydantic(schema)
        instance = Model(**{"schema": "test"})
        assert instance.field_schema == "test"
