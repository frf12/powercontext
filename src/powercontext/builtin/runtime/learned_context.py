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

"""Prepare bounded learned methods and complete callable contracts together."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from powercontext.artifacts import Artifact, ArtifactRef
from powercontext.builtin.artifacts.experience import Experience
from powercontext.builtin.artifacts.skill import Skill
from powercontext.builtin.artifacts.tool import Tool, ToolContent


class LearnedExperience(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: ArtifactRef
    text: str


class LearnedSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: ArtifactRef
    instructions: str
    tool_dependencies: tuple[ArtifactRef, ...] = ()


class LearnedTool(ToolContent):
    ref: ArtifactRef


class LearnedContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiences: tuple[LearnedExperience, ...] = ()
    skills: tuple[LearnedSkill, ...] = ()
    tools: tuple[LearnedTool, ...] = ()

    @property
    def refs(self) -> tuple[ArtifactRef, ...]:
        return tuple(item.ref for group in (self.experiences, self.skills, self.tools) for item in group)

    def text(self) -> str:
        sections = [f"Experience (historical method):\n{item.text}" for item in self.experiences]
        sections.extend(f"Skill:\n{item.instructions}" for item in self.skills)
        if self.tools:
            sections.append("Available learned tools: " + ", ".join(tool.name for tool in self.tools))
        return "\n\n".join(sections)


def _ref_key(ref: ArtifactRef) -> tuple[str, str, int]:
    return ref.family, ref.artifact_id, ref.revision


def _terms(text: str) -> set[str]:
    return {
        term.removesuffix("s") if len(term) > 4 else term
        for term in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower())
        if term not in {"the", "a", "an", "in", "of", "for", "by", "is", "to", "how", "many", "what", "and", "with"}
    }


def _score(query: set[str], text: str) -> float:
    terms = _terms(text)
    return len(query & terms) / max(1, len(query))


def _fits(context: LearnedContext, budget: int) -> bool:
    return len(context.model_dump_json().encode("utf-8")) <= budget


def prepare_learned_context(
    artifacts: Sequence[Artifact[Any]],
    *,
    query: str,
    dialect: str,
    database_name: str | None,
    max_bytes: int,
) -> LearnedContext:
    """Select one related method with all dependencies, then related experience.

    This initial deterministic retrieval policy uses only learned descriptions
    and instructions. It does not read held-out answers or request another model.
    """

    terms = _terms(query)
    tools = {
        _ref_key(artifact.as_ref()): artifact
        for artifact in artifacts
        if isinstance(artifact, Tool)
        and artifact.content.implementation.dialect == dialect
        and artifact.content.implementation.database_name == database_name
    }
    context = _select_skill(artifacts, tools, terms, max_bytes)
    if not context.skills and not context.tools:
        context = _select_tool(tools, terms, max_bytes)
    return _add_experiences(context, artifacts, terms, max_bytes)


def _select_skill(
    artifacts: Sequence[Artifact[Any]],
    tools: dict[tuple[str, str, int], Tool],
    terms: set[str],
    max_bytes: int,
) -> LearnedContext:
    skills = sorted(
        (artifact for artifact in artifacts if isinstance(artifact, Skill)),
        key=lambda artifact: _score(terms, artifact.content.description + " " + artifact.content.instructions),
        reverse=True,
    )
    for skill in skills:
        if not _score(terms, skill.content.description + " " + skill.content.instructions):
            continue
        dependencies = skill.content.tool_dependencies
        if any(_ref_key(ref) not in tools for ref in dependencies):
            continue
        selected_tools = tuple(
            LearnedTool(ref=ref, **tools[_ref_key(ref)].content.model_dump()) for ref in dependencies
        )
        if len({tool.name for tool in selected_tools}) != len(selected_tools):
            continue
        candidate = LearnedContext(
            skills=(
                LearnedSkill(
                    ref=skill.as_ref(), instructions=skill.content.instructions, tool_dependencies=dependencies
                ),
            ),
            tools=selected_tools,
        )
        if _fits(candidate, max_bytes):
            return candidate
    return LearnedContext()


def _select_tool(tools: dict[tuple[str, str, int], Tool], terms: set[str], max_bytes: int) -> LearnedContext:
    ranked_tools = sorted(
        tools.values(),
        key=lambda tool: _score(terms, tool.content.name + " " + tool.content.description),
        reverse=True,
    )
    for tool in ranked_tools:
        if not _score(terms, tool.content.name + " " + tool.content.description):
            continue
        candidate = LearnedContext(tools=(LearnedTool(ref=tool.as_ref(), **tool.content.model_dump()),))
        if _fits(candidate, max_bytes):
            return candidate
    return LearnedContext()


def _add_experiences(
    context: LearnedContext,
    artifacts: Sequence[Artifact[Any]],
    terms: set[str],
    max_bytes: int,
) -> LearnedContext:
    experiences = sorted(
        (artifact for artifact in artifacts if isinstance(artifact, Experience)),
        key=lambda artifact: _score(terms, artifact.content.situation + " " + artifact.content.lesson),
        reverse=True,
    )
    for experience in experiences:
        if len(context.experiences) == 2:
            break
        content = experience.content
        if not _score(terms, content.situation + " " + content.lesson):
            continue
        candidate = context.model_copy(
            update={
                "experiences": (
                    *context.experiences,
                    LearnedExperience(
                        ref=experience.as_ref(),
                        text=f"Situation: {content.situation}\nAction: {content.action}\nLesson: {content.lesson}",
                    ),
                ),
            }
        )
        if _fits(candidate, max_bytes):
            context = candidate
    return context
