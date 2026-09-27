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

"""Search projection owned by the managed Skill Artifact Family."""

from __future__ import annotations

from pydantic import BaseModel

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.search import analyze_text
from powercontext.builtin.artifacts.skill.models import SkillContent
from powercontext.builtin.artifacts.skill.package import SkillPackageSnapshot


class SkillSearchHit(BaseModel):
    """One relevant approved, active managed Skill head."""

    artifact_ref: ArtifactRef
    content: SkillContent


def skill_search_text(content: SkillContent, _package: SkillPackageSnapshot | None = None, /) -> str:
    """Index discovery metadata; retain workflows and package files only for execution."""

    return "\n".join((content.name, content.description))


def skill_searchable_text(content: SkillContent, package: SkillPackageSnapshot | None = None, /) -> str:
    """Build the normalized lexical projection for one managed Skill Revision."""

    return analyze_text(skill_search_text(content, package))


__all__ = ["SkillSearchHit", "skill_search_text", "skill_searchable_text"]
