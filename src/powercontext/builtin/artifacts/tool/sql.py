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

"""Validate SQL programs and bind their declared scalar arguments."""

# ruff: noqa: TRY003

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

import sqlglot
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JsonSchemaValidationError
from pydantic import JsonValue
from sqlglot import Dialect, exp
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.tool.models import ToolContent

_FORBIDDEN = (exp.DML, exp.DDL, exp.Command, exp.Into, exp.Lock, exp.Transaction, exp.Set, exp.Pragma)


def parse_readonly_sql(sql: str, dialect: str) -> exp.Expression:
    """Accept one SELECT/UNION statement, including read-only common table expressions.

    Database read-only authorization remains necessary: a SELECT may call a user-defined function.
    """

    try:
        statements = sqlglot.parse(sql, read=dialect)
    except (SqlglotError, ValueError) as error:
        raise ValueError("Tool SQL must parse in its declared dialect") from error
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union, exp.Subquery)):
        raise ValueError("Tool SQL must be one read-only SELECT or UNION statement")
    statement = statements[0]
    for node in statement.walk():
        if isinstance(node, _FORBIDDEN):
            raise ValueError("Tool SQL must not contain writes, INTO, or locking statements")  # noqa: TRY004
        if isinstance(node, exp.Parameter) or (isinstance(node, exp.Placeholder) and node.this):
            raise ValueError("Tool SQL supports only positional question-mark placeholders")
    return statement


def validate_tool_arguments(content: ToolContent, arguments: Mapping[str, JsonValue]) -> tuple[JsonValue, ...]:
    """Return validated values in positional binding order without interpolating SQL."""

    properties = content.input_schema["properties"]
    if not isinstance(properties, dict) or set(arguments) != set(properties):
        raise ValueError("Tool arguments must match the declared parameter names exactly")
    for value in arguments.values():
        if not (value is None or type(value) in {str, int, float, bool}):
            raise ValueError("Tool arguments must be JSON scalar values")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Tool numeric arguments must be finite")
    try:
        Draft202012Validator(content.input_schema).validate(dict(arguments))
    except JsonSchemaValidationError as error:
        raise ValueError("Tool arguments do not satisfy the declared JSON Schema") from error
    return tuple(arguments[name] for name in content.implementation.parameter_order)


def render_tool_sql(content: ToolContent, arguments: Mapping[str, JsonValue]) -> str:
    """Render literal SQL only for recorded-query equivalence checks, never for execution.

    Execution must send ``implementation.sql`` and ``validate_tool_arguments`` values separately.
    """

    values = validate_tool_arguments(content, arguments)
    implementation = content.implementation
    parse_readonly_sql(implementation.sql, implementation.dialect)
    tokens = Dialect.get_or_raise(implementation.dialect).tokenize(implementation.sql)
    placeholders = [token for token in tokens if token.token_type == TokenType.PLACEHOLDER]
    if len(placeholders) != len(values):
        raise ValueError("Tool SQL placeholder count changed")
    # AST traversal visits CTEs after the main SELECT; label placeholders in lexical SQL order first.
    labeled = implementation.sql
    for index in range(len(placeholders) - 1, -1, -1):
        token = placeholders[index]
        labeled = labeled[: token.start] + f":__pc_tool_{index}" + labeled[token.end + 1 :]
    statement = sqlglot.parse_one(labeled, read=implementation.dialect)
    replacements = {f"__pc_tool_{index}": exp.convert(value) for index, value in enumerate(values)}
    statement = statement.transform(
        lambda node: replacements[node.this].copy() if isinstance(node, exp.Placeholder) else node
    )
    return statement.sql(dialect=implementation.dialect)


__all__ = ["render_tool_sql", "validate_tool_arguments"]
