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

"""Behavior contracts for learned SQL tools."""

import sqlite3

import pytest
from pydantic import ValidationError

from powercontext.builtin.artifacts.tool import (
    SqlToolImplementation,
    Tool,
    ToolContent,
    ToolDraft,
    render_tool_sql,
    validate_tool_arguments,
)


def make_tool(sql="SELECT ? AS label WHERE ? > 0", order=("label", "count"), properties=None):
    properties = properties or {"label": {"type": "string"}, "count": {"type": "integer", "minimum": 1}}
    return ToolContent(
        name="example-query",
        description="Return the supplied label when count is positive.",
        input_schema={"type": "object", "properties": properties, "required": list(properties)},
        output_schema={"type": "object"},
        implementation=SqlToolImplementation(sql=sql, parameter_order=order),
    )


def test_tool_artifact_preserves_typed_program_and_exact_revision():
    content = make_tool()
    draft = ToolDraft(content=content)
    artifact = Tool(artifact_id="labels", revision=2, content=draft.content)
    assert artifact.as_ref().model_dump() == {"family": "tool", "artifact_id": "labels", "revision": 2}
    assert ToolContent.model_validate_json(content.model_dump_json()) == content


def test_bound_values_follow_sql_order_and_repeat_parameter_names():
    content = make_tool("SELECT ? AS label WHERE ? = ?", ("label", "count", "count"))
    assert validate_tool_arguments(content, {"count": 3, "label": "quoted ' text"}) == ("quoted ' text", 3, 3)


def test_rendering_preserves_cte_placeholder_order_and_quotes_only_literals():
    content = make_tool("WITH x AS (SELECT ? AS label) SELECT ? AS n, label FROM x", ("label", "count"))
    arguments = {"label": "O'Brien'; DROP TABLE x; --", "count": 2}
    bound = validate_tool_arguments(content, arguments)
    with sqlite3.connect(":memory:") as connection:
        expected = connection.execute(content.implementation.sql, bound).fetchall()
        rendered = connection.execute(render_tool_sql(content, arguments)).fetchall()
    assert rendered == expected == [(2, arguments["label"])]


def test_question_marks_in_literals_and_comments_are_not_parameters():
    content = make_tool("SELECT '?' AS question, ? AS label WHERE ? > 0 -- ?")
    assert validate_tool_arguments(content, {"label": "ok", "count": 1}) == ("ok", 1)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM records WHERE id = ?",
        "INSERT INTO records SELECT ?",
        "UPDATE records SET id = ?",
        "DROP TABLE records",
        "SELECT 1; DELETE FROM records",
        "WITH x AS (DELETE FROM records RETURNING *) SELECT * FROM x",
        "SELECT * INTO copy FROM records",
        "SELECT * FROM records FOR UPDATE",
        "PRAGMA journal_mode = WAL",
        "ATTACH DATABASE 'other.db' AS other",
        "SELECT :label",
        "SELECT @label",
        "SELECT ?1",
    ],
)
def test_tool_rejects_writes_and_unsupported_parameter_syntax(sql):
    with pytest.raises(ValidationError):
        make_tool(sql, ("label",), {"label": {"type": "string"}})


def test_union_with_parameters_is_allowed():
    content = make_tool("SELECT ? AS value UNION ALL SELECT ?", ("count", "count"), {"count": {"type": "integer"}})
    with sqlite3.connect(":memory:") as connection:
        assert connection.execute(render_tool_sql(content, {"count": 3})).fetchall() == [(3,), (3,)]


@pytest.mark.parametrize("order", [("label",), ("label", "missing"), ("label", "count", "count")])
def test_tool_rejects_mismatched_parameter_contract(order):
    with pytest.raises(ValidationError):
        make_tool(order=order)


@pytest.mark.parametrize(
    "arguments",
    [
        {"label": "ok"},
        {"label": "ok", "count": 1, "extra": 1},
        {"label": "ok", "count": True},
        {"label": "ok", "count": "1"},
        {"label": "ok", "count": 0},
        {"label": [], "count": 1},
    ],
)
def test_argument_validation_rejects_missing_extra_and_wrong_scalar_values(arguments):
    with pytest.raises(ValueError):
        validate_tool_arguments(make_tool(), arguments)


def test_tool_rejects_non_scalar_input_schema():
    with pytest.raises(ValidationError):
        make_tool("SELECT ?", ("ids",), {"ids": {"type": "array", "items": {"type": "integer"}}})


def test_tool_requires_bound_parameters_to_be_required():
    payload = make_tool().model_dump()
    payload["input_schema"]["required"] = ["label"]
    with pytest.raises(ValidationError):
        ToolContent.model_validate(payload)


def test_tool_rejects_unsupported_union_parameter_schema_as_a_validation_error():
    with pytest.raises(ValidationError):
        make_tool("SELECT ?", ("label",), {"label": {"type": ["string", "null"]}})


def test_rendering_matches_bound_boolean_number_and_null_values():
    content = make_tool(
        "SELECT ? AS enabled, ? AS amount, ? AS absent",
        ("enabled", "amount", "absent"),
        {"enabled": {"type": "boolean"}, "amount": {"type": "number"}, "absent": {"type": "null"}},
    )
    arguments = {"enabled": False, "amount": 1.25, "absent": None}
    with sqlite3.connect(":memory:") as connection:
        assert (
            connection.execute(render_tool_sql(content, arguments)).fetchall()
            == connection.execute(content.implementation.sql, validate_tool_arguments(content, arguments)).fetchall()
        )


def test_argument_validation_applies_enum_constraints():
    content = make_tool("SELECT ?", ("currency",), {"currency": {"type": "string", "enum": ["USD", "CNY"]}})
    with pytest.raises(ValueError):
        validate_tool_arguments(content, {"currency": "EUR"})


@pytest.mark.parametrize("value", [float("inf"), float("nan")])
def test_argument_validation_rejects_non_finite_numbers(value):
    content = make_tool("SELECT ?", ("amount",), {"amount": {"type": "number"}})
    with pytest.raises(ValueError):
        validate_tool_arguments(content, {"amount": value})


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "integer"},
        {"type": "array", "items": {"type": "object"}},
        {"type": "object", "required": ["answer"]},
        {"type": "object", "properties": {"rows": {"type": "integer"}}},
        {"type": "object", "properties": {"truncated": {"type": "string"}}},
        {"type": "object", "properties": {"columns": {"type": "object"}}},
        {"type": "object", "properties": {"rows": {"type": "array"}}, "additionalProperties": False},
    ],
)
def test_tool_rejects_output_schemas_that_contradict_the_datus_result_envelope(schema):
    payload = make_tool().model_dump()
    payload["output_schema"] = schema
    with pytest.raises(ValidationError, match="output_schema"):
        ToolContent.model_validate(payload)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object"},
        {
            "type": "object",
            "properties": {
                "columns": {"type": "array", "items": {"type": "object"}},
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"total": {"type": "integer"}},
                        "required": ["total"],
                    },
                },
                "truncated": {"type": "boolean"},
            },
            "required": ["columns", "rows", "truncated"],
            "additionalProperties": False,
        },
    ],
)
def test_tool_accepts_permissive_or_explicit_datus_output_envelopes(schema):
    payload = make_tool().model_dump()
    payload["output_schema"] = schema
    assert ToolContent.model_validate(payload).output_schema == schema
