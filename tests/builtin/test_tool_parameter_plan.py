"""Parameter plans preserve source SQL and validate a callable contract."""

from __future__ import annotations

import pytest
from pydantic import JsonValue


def plan_source(sql):
    from powercontext.builtin.trace_learning.parameter_plan import ToolPlanSource, literal_inventory

    return ToolPlanSource(
        id="source_0", trace_id="trace-1", call_id="call-1", sql=sql, literals=literal_inventory(sql, "mysql")
    )


def compile_plan(source, chosen, *, schema: dict[str, JsonValue] | None = None, output_schema=None):
    from powercontext.builtin.trace_learning.models import TraceLearningHostProfile
    from powercontext.builtin.trace_learning.parameter_plan import (
        PlanBinding,
        PlanConstant,
        ToolParameterPlan,
        assemble_parameter_plan,
    )

    schema = schema or {"type": "integer", "minimum": 1, "description": "Positive interval in seconds."}
    ids = [item.id for item in source.literals if item.id in chosen]
    constants = [item.id for item in source.literals if item.id not in chosen]
    plan = ToolParameterPlan(
        key="example",
        source_id=source.id,
        name="example",
        description="Measure event gaps.",
        bindings=(
            PlanBinding(
                name="interval_seconds", literal_ids=tuple(ids), property_schema=schema, reason="Caller-selected gap."
            ),
        ),
        constants=(PlanConstant(literal_ids=tuple(constants), reason="Preserve the method."),) if constants else (),
        output_schema=output_schema or {"type": "object"},
    )
    return assemble_parameter_plan(plan, (source,), TraceLearningHostProfile(dialect="mysql"))


def test_plan_preserves_regexp_source_and_all_coupled_interval_occurrences():
    sql = "SELECT DATE_ADD(ts, INTERVAL 60 SECOND) AS next_ts FROM events WHERE label REGEXP '^[0-9]$' AND ts < DATE_ADD(ts, INTERVAL 60 SECOND) LIMIT 60"
    source = plan_source(sql)
    chosen = [item.id for item in source.literals if sql[: item.start].endswith("INTERVAL ")]
    tool = compile_plan(source, chosen)
    assert tool.content.implementation.sql == sql.replace("INTERVAL 60 SECOND", "INTERVAL ? SECOND")
    assert tool.content.implementation.parameter_order == ("interval_seconds", "interval_seconds")
    assert tool.examples[0].arguments == {"interval_seconds": 60}


def test_parameter_order_follows_source_text_not_ast_walk():
    source = plan_source("WITH x AS (SELECT 60 AS n) SELECT 60 AS value FROM x")
    tool = compile_plan(source, [source.literals[0].id])
    assert tool.content.implementation.sql == "WITH x AS (SELECT ? AS n) SELECT 60 AS value FROM x"


@pytest.mark.parametrize("sql", ["SELECT CAST(value AS DECIMAL(38,6)) FROM data", "SELECT value AS '60' FROM data"])
def test_plan_cannot_parameterize_types_or_aliases(sql):
    source = plan_source(sql)
    with pytest.raises(ValueError, match="structural"):
        compile_plan(source, [source.literals[0].id])


def test_plan_rejects_schema_that_excludes_demonstrated_parameter():
    source = plan_source("SELECT 60 AS value")
    with pytest.raises(ValueError, match="schema"):
        compile_plan(
            source, [source.literals[0].id], schema={"type": "integer", "maximum": 30, "description": "Window."}
        )


def test_plan_checks_recorded_output_envelope():
    source = plan_source("SELECT 60 AS value").model_copy(
        update={"result": {"columns": ["value"], "rows": [[60]], "truncated": False}}
    )
    with pytest.raises(ValueError, match="output"):
        compile_plan(
            source,
            [source.literals[0].id],
            output_schema={"type": "object", "properties": {"rows": {"type": "array", "items": {"type": "object"}}}},
        )


def test_planning_offers_only_the_explicit_successful_source_call():
    from powercontext.builtin.trace_learning.models import (
        CandidateSpec,
        ImportTraceLearningRequest,
        TraceLearningGenerationInput,
    )
    from powercontext.builtin.trace_learning.parameter_plan import recorded_tool_sources
    from tests.e2e.test_trace_learning import request_data

    request = ImportTraceLearningRequest.model_validate(request_data())
    context = TraceLearningGenerationInput(traces=request.traces, host_profile=request.host_profile)
    spec = CandidateSpec.model_validate({
        "family": "tool",
        "key": "count",
        "purpose": "Count population",
        "trace_ids": ["trace-1"],
        "source_calls": [{"trace_id": "trace-1", "call_id": "missing", "query_index": 0}],
    })
    assert recorded_tool_sources(context, spec.trace_ids, spec.source_calls) == ()
    spec = spec.model_copy(update={"source_calls": ()})
    assert len(recorded_tool_sources(context, spec.trace_ids, spec.source_calls)) == 1


def test_negative_interval_retains_sign_when_parameterized():
    source = plan_source("SELECT DATE_ADD(ts, INTERVAL -60 SECOND) FROM events")
    tool = compile_plan(
        source, [source.literals[0].id], schema={"type": "integer", "description": "Signed offset in seconds."}
    )
    assert tool.content.implementation.sql == "SELECT DATE_ADD(ts, INTERVAL ? SECOND) FROM events"
    assert tool.examples[0].arguments == {"interval_seconds": -60}


def test_typed_date_literal_is_not_replaced_with_invalid_date_placeholder():
    source = plan_source("SELECT DATE '2024-01-01'")
    with pytest.raises(ValueError, match="structural"):
        compile_plan(source, [source.literals[0].id], schema={"type": "string", "description": "Date."})


def test_boolean_literal_can_be_bound():
    source = plan_source("SELECT enabled FROM events WHERE enabled = TRUE")
    assert source.literals[0].value is True
    tool = compile_plan(
        source, [source.literals[0].id], schema={"type": "boolean", "description": "Whether events are enabled."}
    )
    assert tool.content.implementation.sql == "SELECT enabled FROM events WHERE enabled = ?"
    assert tool.examples[0].arguments == {"interval_seconds": True}
