# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Typed contracts for verified, parameterized SQL Tool artifacts."""

# Validation errors intentionally describe the invalid public contract.
# ruff: noqa: TRY003

from __future__ import annotations

from typing import ClassVar, Literal

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from sqlglot import exp

from powercontext.artifacts import Artifact, ArtifactDraft
from powercontext.builtin.artifacts.tool.sql import parse_readonly_sql

_SCALAR_TYPES = frozenset({"string", "integer", "number", "boolean", "null"})


class SqlToolImplementation(BaseModel):
    """One read-only SQL statement with positional, explicitly ordered values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["parameterized_sql"] = "parameterized_sql"
    sql: str = Field(min_length=1, max_length=128 * 1024)
    parameter_order: tuple[str, ...] = Field(max_length=128)
    dialect: str = Field(default="sqlite", min_length=1, max_length=64)
    database_name: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_statement(self) -> SqlToolImplementation:
        statement = parse_readonly_sql(self.sql, self.dialect)
        if len(tuple(statement.find_all(exp.Placeholder))) != len(self.parameter_order):
            raise ValueError("SQL placeholder count must match parameter_order")
        if any(not name or name != name.strip() for name in self.parameter_order):
            raise ValueError("SQL parameter names must be nonempty and trimmed")
        return self


class ToolContent(BaseModel):
    """The complete callable contract and program of one Tool revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    description: str = Field(min_length=1, max_length=2000)
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]
    implementation: SqlToolImplementation

    @field_validator("input_schema", "output_schema")
    @classmethod
    def validate_json_schema(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            Draft202012Validator.check_schema(value)
        except SchemaError as error:
            raise ValueError("Tool contract must contain a valid JSON Schema") from error
        if _has_schema_reference(value):
            raise ValueError("Tool schemas must be self-contained without schema references")
        return value

    @field_validator("output_schema")
    @classmethod
    def validate_output_envelope(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        envelope = {"columns": "array", "rows": "array", "truncated": "boolean"}
        if value.get("type") != "object":
            raise ValueError("Tool output_schema must describe the Datus result object")
        required = value.get("required", [])
        if not isinstance(required, list) or not set(required) <= set(envelope):
            raise ValueError("Tool output_schema requires fields outside the Datus result envelope")
        properties = value.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError("Tool output_schema properties must describe an object")  # noqa: TRY004 - Pydantic validation
        if value.get("additionalProperties") is False and not set(envelope) <= set(properties):
            raise ValueError("Tool output_schema must allow every Datus result envelope field")
        for name, expected in envelope.items():
            definition = properties.get(name, value.get("additionalProperties", True))
            if definition is False:
                raise ValueError("Tool output_schema must allow every Datus result envelope field")
            if isinstance(definition, dict) and (declared := definition.get("type")) is not None:
                allowed = [declared] if isinstance(declared, str) else declared
                if not isinstance(allowed, list) or expected not in allowed:
                    raise ValueError("Tool output_schema field type contradicts the Datus result envelope")
        return value

    @model_validator(mode="after")
    def validate_parameter_contract(self) -> ToolContent:
        schema = self.input_schema
        properties = schema.get("properties")
        if schema.get("type") != "object" or not isinstance(properties, dict):
            raise ValueError("Tool input_schema must declare an object with properties")
        for definition in properties.values():
            if (
                not isinstance(definition, dict)
                or not isinstance(definition.get("type"), str)
                or definition["type"] not in _SCALAR_TYPES
            ):
                raise ValueError("SQL Tool parameters must have scalar JSON Schema types")
        required = schema.get("required", [])
        if not isinstance(required, list) or set(required) != set(properties):
            raise ValueError("Every SQL Tool parameter must be required")
        if set(self.implementation.parameter_order) != set(properties):
            raise ValueError("parameter_order must bind exactly the declared input properties")
        if not self.description.strip():
            raise ValueError("Tool description must not be blank")
        return self


def _has_schema_reference(value: JsonValue) -> bool:
    if isinstance(value, dict):
        return any(key in {"$ref", "$dynamicRef"} or _has_schema_reference(child) for key, child in value.items())
    if isinstance(value, list):
        return any(_has_schema_reference(child) for child in value)
    return False


class Tool(Artifact[ToolContent]):
    """One immutable, verified callable Tool revision."""

    family: ClassVar[str] = "tool"


class ToolDraft(ArtifactDraft[ToolContent]):
    """A complete Tool contract with its learning and validation evidence."""

    family: ClassVar[str] = "tool"


__all__ = ["SqlToolImplementation", "Tool", "ToolContent", "ToolDraft"]
