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

"""Bounded syntax-only compatibility for Python-literal structured responses."""

from __future__ import annotations

import ast
import json
import math

from pydantic import JsonValue


def normalize_python_literal(text: str) -> str | None:
    """Return JSON only when the entire response is an unambiguous JSON-compatible literal."""
    if len(text) > 1_048_576:
        return None
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if len(lines) < 3 or lines[0] not in {"```", "```json", "```python"} or lines[-1] != "```":
            return None
        value = "\n".join(lines[1:-1])
    try:
        json.loads(value)
    except (ValueError, RecursionError):
        pass
    else:
        return None
    try:
        tree = ast.parse(value, mode="eval")
        return json.dumps(_literal(tree.body, 0), ensure_ascii=False, allow_nan=False)
    except (SyntaxError, ValueError, TypeError, RecursionError, OverflowError):
        return None


def _literal(node: ast.AST, depth: int) -> JsonValue:  # noqa: C901 - explicit accepted literal grammar
    if depth > 64:
        raise ValueError("literal nesting exceeds the compatibility limit")  # noqa: TRY003
    if isinstance(node, ast.Constant):
        value = node.value
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float) and math.isfinite(value):
            return value
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _literal(node.operand, depth + 1)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return -value if isinstance(node.op, ast.USub) else value
    elif isinstance(node, ast.List):
        return [_literal(item, depth + 1) for item in node.elts]
    elif isinstance(node, ast.Dict):
        result: dict[str, JsonValue] = {}
        for key, child in zip(node.keys, node.values, strict=True):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str) or key.value in result:
                raise ValueError("literal keys must be unique strings")  # noqa: TRY003
            result[key.value] = _literal(child, depth + 1)
        return result
    raise ValueError("not a JSON-compatible Python literal")  # noqa: TRY003
