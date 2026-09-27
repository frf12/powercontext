# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Compile model-selected literal roles without rewriting the demonstrated SQL."""

# Errors are actionable feedback to the generating conversation.
# ruff: noqa: TRY003
from __future__ import annotations

import math
from typing import Literal, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlglot import Dialect, exp
from sqlglot.tokens import TokenType

from powercontext.builtin.artifacts.tool import SqlToolImplementation, ToolContent
from powercontext.builtin.artifacts.tool.sql import parse_readonly_sql, validate_tool_arguments
from powercontext.builtin.trace_learning.models import (
    GeneratedTool,
    ToolSourceReference,
    ToolTraceExample,
    TraceLearningError,
    TraceLearningGenerationInput,
    TraceLearningHostProfile,
)


class SourceLiteral(BaseModel):
    id: str
    value: str | int | float | bool
    kind: Literal["string", "integer", "number", "boolean"]
    start: int
    end: int
    expression: str
    parameterizable: bool


class ToolPlanSource(BaseModel):
    id: str
    trace_id: str
    call_id: str
    query_index: int = 0
    sql: str
    literals: tuple[SourceLiteral, ...]
    result: dict[str, JsonValue] | None = None


class PlanBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z][a-zA-Z0-9_]*$")
    literal_ids: tuple[str, ...] = Field(min_length=1, max_length=128)
    property_schema: dict[str, JsonValue]
    reason: str = Field(min_length=1, max_length=2000)


class PlanConstant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    literal_ids: tuple[str, ...] = Field(min_length=1, max_length=2048)
    reason: str = Field(min_length=1, max_length=2000)


class ToolParameterPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    source_id: str
    name: str
    description: str
    output_schema: dict[str, JsonValue]
    bindings: tuple[PlanBinding, ...] = Field(default=(), max_length=128)
    constants: tuple[PlanConstant, ...] = Field(default=(), max_length=2048)


def literal_inventory(sql: str, dialect: str) -> tuple[SourceLiteral, ...]:
    """Locate literals lexically, including numeric INTERVALs normalized by SQLGlot."""
    tree = parse_readonly_sql(sql, dialect)
    if any(tree.find_all(exp.Placeholder)):
        raise ValueError("Source SQL must already have its recorded parameter values bound.")
    nodes = {node.meta.get("start"): node for node in tree.find_all(exp.Literal) if "start" in node.meta}
    tokens = Dialect.get_or_raise(dialect).tokenize(sql)
    literals = []
    for index, token in enumerate(tokens):
        if token.token_type not in {TokenType.NUMBER, TokenType.STRING, TokenType.TRUE, TokenType.FALSE}:
            continue
        node = nodes.get(token.start)
        negative_interval = (
            index > 1
            and tokens[index - 1].token_type == TokenType.DASH
            and tokens[index - 2].token_type == TokenType.INTERVAL
        )
        interval = negative_interval or (index > 0 and tokens[index - 1].token_type == TokenType.INTERVAL)
        boolean = token.token_type in {TokenType.TRUE, TokenType.FALSE}
        # Typed SQL literals require a literal token, not a bind placeholder.
        typed = (
            index > 0
            and token.token_type == TokenType.STRING
            and tokens[index - 1].token_type
            in {TokenType.DATE, TokenType.TIME, TokenType.TIMESTAMP, TokenType.TIMESTAMPTZ}
        )
        allowed = not typed and (
            interval or boolean or (node is not None and not isinstance(node.parent, exp.DataTypeParam))
        )
        start = token.start
        value: str | int | float | bool = token.text
        kind: Literal["string", "integer", "number", "boolean"] = "string"
        if boolean:
            value = token.token_type == TokenType.TRUE
            kind = "boolean"
        elif token.token_type == TokenType.NUMBER:
            try:
                value = int(token.text)
                kind = "integer"
            except ValueError:
                value = float(token.text)
                kind = "number"
            if not math.isfinite(value):
                raise ValueError("Non-finite source literal is unsupported.")
            if negative_interval or (
                node is not None and isinstance(node.parent, exp.Neg) and index and tokens[index - 1].text == "-"
            ):
                start = tokens[index - 1].start
                value = -value
        expression = sql[max(0, start - 45) : min(len(sql), token.end + 46)]
        literals.append(
            SourceLiteral(
                id=f"lit_{len(literals)}",
                value=value,
                kind=kind,
                start=start,
                end=token.end + 1,
                expression=expression,
                parameterizable=allowed,
            )
        )
    return tuple(literals)


def recorded_tool_sources(
    context: TraceLearningGenerationInput,
    trace_ids: tuple[str, ...],
    source_calls: tuple[ToolSourceReference, ...] = (),
) -> tuple[ToolPlanSource, ...]:
    """Offer only whole successful statements backed by recorded result evidence."""
    from powercontext.builtin.trace_learning.service import _recorded_call_sql

    sources = []
    selected = {(ref.trace_id, ref.call_id, ref.query_index) for ref in source_calls}
    for trace in context.traces:
        if trace.trace_id not in trace_ids:
            continue
        for call in trace.tool_calls:
            if not call.succeeded:
                continue
            queries = call.arguments.get("queries")
            for index in range(len(queries) if isinstance(queries, list) else 1):
                if selected and (trace.trace_id, call.call_id, index) not in selected:
                    continue
                example = ToolTraceExample(
                    trace_id=trace.trace_id, call_id=call.call_id, query_index=index, arguments={}
                )
                try:
                    sql = _recorded_call_sql(call, example, context.resolved_tool_calls)
                    literals = literal_inventory(sql, context.host_profile.dialect)
                except (TraceLearningError, ValueError):
                    continue
                result = _recorded_envelope(call.result, index)
                sources.append(
                    ToolPlanSource(
                        id=f"source_{len(sources)}",
                        trace_id=trace.trace_id,
                        call_id=call.call_id,
                        query_index=index,
                        sql=sql,
                        literals=literals,
                        result=result,
                    )
                )
    return tuple(sources)


def _recorded_envelope(observed: JsonValue, index: int) -> dict[str, JsonValue] | None:
    if isinstance(observed, dict):
        observed = observed.get("artifact", observed)
        if isinstance(observed, dict) and isinstance(observed.get("queries"), list):
            observed = observed["queries"][index]
    if (
        isinstance(observed, dict)
        and isinstance(observed.get("columns"), list)
        and isinstance(observed.get("rows"), list)
    ):
        return {"columns": observed["columns"], "rows": observed["rows"], "truncated": observed.get("truncated", False)}
    return None


def _binding_schema(binding: PlanBinding, selected: list[SourceLiteral]) -> dict[str, JsonValue]:
    if any(not literal.parameterizable for literal in selected):
        raise ValueError("Do not parameterize structural SQL types, identifiers or aliases.")
    if len({(type(literal.value), literal.value) for literal in selected}) != 1:
        raise ValueError("Coupled literal positions must have the same recorded value and type.")
    schema = dict(binding.property_schema)
    if schema.get("type") not in {
        selected[0].kind,
        "number" if selected[0].kind == "integer" else selected[0].kind,
    }:
        raise ValueError(f"Parameter schema type differs from source literal: {binding.name}.")
    if not isinstance(schema.get("description"), str) or not str(schema["description"]).strip():
        raise ValueError(f"Describe the meaning, representation, domain and effect of {binding.name}.")
    if "default" in schema:
        raise ValueError("Parameters are required; use examples rather than claiming an unapplied default.")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise ValueError(f"Invalid parameter schema for {binding.name}: {error.message}") from error
    if not Draft202012Validator(schema).is_valid(selected[0].value):
        raise ValueError(f"Parameter schema excludes the successful recorded value: {binding.name}.")
    return schema


def assemble_parameter_plan(  # noqa: C901 - validate coverage and contracts before compiling source spans
    plan: ToolParameterPlan, sources: tuple[ToolPlanSource, ...], host: TraceLearningHostProfile
) -> GeneratedTool:
    """Require exact coverage, preserve SQL bytes, and bind the original example."""
    source = next((source for source in sources if source.id == plan.source_id), None)
    if source is None:
        raise ValueError("Select a supplied whole successful source_id; nested or invented SQL is not a source.")
    known = {literal.id: literal for literal in source.literals}
    assigned: dict[str, str | None] = {}
    properties: dict[str, JsonValue] = {}
    arguments: dict[str, JsonValue] = {}

    def claim(ids: tuple[str, ...], name: str | None) -> None:
        for identifier in ids:
            if identifier not in known or identifier in assigned:
                raise ValueError(f"Unknown or repeated literal ID: {identifier}.")
            assigned[identifier] = name

    for binding in plan.bindings:
        if binding.name in properties:
            raise ValueError(f"Duplicate parameter name: {binding.name}.")
        claim(binding.literal_ids, binding.name)
        selected = [known[identifier] for identifier in binding.literal_ids]
        schema = _binding_schema(binding, selected)
        properties[binding.name] = schema
        arguments[binding.name] = selected[0].value
    for constant in plan.constants:
        claim(constant.literal_ids, None)
    if set(assigned) != set(known):
        raise ValueError(f"Classify every source literal once; missing: {sorted(set(known) - set(assigned))}.")

    sql = source.sql
    ordered = [literal for literal in source.literals if assigned[literal.id] is not None]
    for literal in reversed(ordered):
        sql = sql[: literal.start] + "?" + sql[literal.end :]
    content = ToolContent(
        name=plan.name,
        description=plan.description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        output_schema=plan.output_schema,
        implementation=SqlToolImplementation(
            sql=sql,
            parameter_order=tuple(cast(str, assigned[literal.id]) for literal in ordered),
            dialect=host.dialect,
            database_name=host.database_name,
        ),
    )
    validate_tool_arguments(content, arguments)
    if source.result is not None:
        try:
            Draft202012Validator(content.output_schema).validate(source.result)
        except JsonSchemaValidationError as error:
            raise ValueError(f"Tool output schema rejects recorded result: {error.message}") from error
    return GeneratedTool(
        key=plan.key,
        content=content,
        examples=(
            ToolTraceExample(
                trace_id=source.trace_id, call_id=source.call_id, query_index=source.query_index, arguments=arguments
            ),
        ),
    )
