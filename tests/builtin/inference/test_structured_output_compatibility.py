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

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from powercontext.builtin.inference.structured_output import normalize_python_literal


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "{'enabled': True, 'missing': None, 'delta': -3, 'nested': {'items': [False, None, -1.5]}}",
            {
                "enabled": True,
                "missing": None,
                "delta": -3,
                "nested": {"items": [False, None, -1.5]},
            },
        ),
        (
            "```python\n{'enabled': True, 'missing': None, 'delta': -3}\n```",
            {"enabled": True, "missing": None, "delta": -3},
        ),
        (
            "{'value': \"customer's balance\"}",
            {"value": "customer's balance"},
        ),
    ],
)
def test_normalize_python_literal_accepts_bounded_json_compatible_values(raw: str, expected: object) -> None:
    normalized = normalize_python_literal(raw)

    assert normalized is not None
    assert json.loads(normalized) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "{'value': 1, 'value': 2}",
        "{1: 'non-string key'}",
        "{'value': (1, 2)}",
        "{'value': {1, 2}}",
        "{'value': str(42)}",
        "{'value': nan}",
        "{'value': inf}",
        "{'value': -inf}",
        "{'value': 1e999}",
    ],
)
def test_normalize_python_literal_rejects_ambiguous_or_unsafe_values(raw: str) -> None:
    assert normalize_python_literal(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "{'value': True",
        "{'value': True, \"other\": false}",
    ],
)
def test_normalize_python_literal_rejects_incomplete_and_mixed_syntax(raw: str) -> None:
    assert normalize_python_literal(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "x" * (1_048_576 + 1),
        "[" * 65 + "0" + "]" * 65,
    ],
)
def test_normalize_python_literal_rejects_length_and_depth_overflows(raw: str) -> None:
    assert normalize_python_literal(raw) is None


def test_normalize_python_literal_leaves_valid_json_untouched() -> None:
    raw = '{"enabled": true, "missing": null, "delta": -3}'

    assert normalize_python_literal(raw) is None


@dataclass(frozen=True, slots=True)
class Question:
    value: str


@dataclass(frozen=True, slots=True)
class StructuredAnswer:
    value: str
    reason: str


def test_structured_generator_validates_normalized_literal_and_repairs_once() -> None:
    calls = 0

    async def respond(messages, info) -> ModelResponse:
        del messages, info
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[TextPart("{'value': \"customer's balance\"}")])
        return ModelResponse(parts=[TextPart('{"value":"customer\'s balance","reason":"account lookup"}')])

    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=FunctionModel(respond),
            instructions="Return a structured answer.",
            input_type=Question,
            output_type=StructuredAnswer,
            limits=InferenceLimits(max_requests=2),
        )

        result = await generator.generate(Question("balance"))

        assert result.output == StructuredAnswer("customer's balance", "account lookup")
        assert result.usage.requests == 2

    asyncio.run(scenario())
