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

"""Exact Tool dependencies survive standard Skill package publication."""

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.skill import SkillContent, build_instruction_skill_package, capture_skill_archive


def skill(**values):
    return SkillContent(
        name="monthly-revenue",
        description="Query monthly revenue using the verified tool.",
        instructions="Bind the requested month and call the revenue tool.",
        validation=("Check the returned month.",),
        **values,
    )


def test_old_skill_content_has_no_tool_dependencies():
    assert skill().tool_dependencies == ()


def test_skill_dependency_roundtrips_through_canonical_package():
    dependencies = (ArtifactRef(family="tool", artifact_id="revenue", revision=3),)
    package = build_instruction_skill_package(skill(tool_dependencies=dependencies))
    restored = capture_skill_archive(package.archive_bytes)
    assert restored.reference == package.reference
    assert restored.as_skill_content().tool_dependencies == dependencies
    assert restored.as_skill_content().package == package.reference


def test_dependency_revision_changes_package_digest():
    first = build_instruction_skill_package(
        skill(tool_dependencies=(ArtifactRef(family="tool", artifact_id="revenue", revision=1),))
    )
    second = build_instruction_skill_package(
        skill(tool_dependencies=(ArtifactRef(family="tool", artifact_id="revenue", revision=2),))
    )
    assert first.reference.tree_digest != second.reference.tree_digest


@pytest.mark.parametrize(
    "dependencies",
    [
        (ArtifactRef(family="experience", artifact_id="revenue", revision=1),),
        (ArtifactRef(family="tool", artifact_id="revenue", revision=1),) * 2,
    ],
)
def test_skill_rejects_non_tool_and_duplicate_dependencies(dependencies):
    with pytest.raises(ValidationError):
        skill(tool_dependencies=dependencies)
