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

"""Bounded imported evidence, generated bundles and durable LearningRun contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

import sqlglot
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.skill import SkillContent
from powercontext.builtin.artifacts.skill.package import SKILL_NAME_PATTERN
from powercontext.builtin.artifacts.tool import ToolContent
from powercontext.builtin.evidence.models import content_digest
from powercontext.errors import PowerContextError
from powercontext.sources import SourceRef

TRACE_LEARNING_BINDING = "tool.trace-learning.v1"
TRACE_LEARNING_PROMPT_VERSION = "powercontext.trace-learning.v3"


class TraceLearningError(PowerContextError, ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class TraceLearningHostProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["datus"] = "datus"
    dialect: str = Field(min_length=1, max_length=32)
    database_name: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("dialect")
    @classmethod
    def normalize_dialect(cls, value: str) -> str:
        normalized = {"postgresql": "postgres", "oceanbase": "mysql"}.get(value.lower(), value.lower())
        sqlglot.Dialect.get_or_raise(normalized)
        return normalized


class TraceToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    arguments: dict[str, JsonValue]
    result: JsonValue
    succeeded: bool = True


class CompleteTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: str = Field(min_length=1, max_length=256)
    question: str = Field(min_length=1, max_length=64_000)
    tool_calls: tuple[TraceToolCall, ...] = Field(min_length=1, max_length=256)
    final_answer: JsonValue
    context: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_calls(self):
        if len({call.call_id for call in self.tool_calls}) != len(self.tool_calls):
            raise TraceLearningError("duplicate_trace_call_id")
        return self


class ImportTraceLearningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: str = Field(min_length=1, max_length=128)
    traces: tuple[CompleteTrace, ...] = Field(min_length=1, max_length=32)
    host_profile: TraceLearningHostProfile

    @model_validator(mode="after")
    def bounded_unique_input(self):
        if self.idempotency_key != self.idempotency_key.strip():
            raise TraceLearningError("invalid_idempotency_key")
        if len({trace.trace_id for trace in self.traces}) != len(self.traces):
            raise TraceLearningError("duplicate_trace_id")
        if len(self.model_dump_json().encode()) > 4 * 1024 * 1024:
            raise TraceLearningError("import_payload_too_large")
        return self

    def digest(self) -> str:
        return content_digest(self.model_dump_json(exclude={"idempotency_key"}).encode())


class LearningBudget(BaseModel):
    max_model_calls: int = Field(default=128, ge=1, le=1024)
    max_candidates_per_family: int = Field(default=32, ge=1, le=128)
    max_candidate_repair_rounds: int = Field(default=2, ge=0, le=8)
    # Per generation request; LearningUsage.output_tokens remains cumulative across the Run.
    max_output_tokens: int = Field(default=16_000, ge=1024, le=64_000)
    max_input_chars: int = Field(default=400_000, ge=1024, le=4 * 1024 * 1024)
    timeout_seconds: float = Field(default=1800, gt=0, le=7200)
    previous_artifact_limit: int = Field(default=30, ge=0, le=100)
    max_pending_per_scope: int = Field(default=32, ge=1, le=1000)


class LearningUsage(BaseModel):
    model_calls: int = Field(default=0, ge=0)
    reserved_model_calls: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class ToolTraceExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    call_id: str
    query_index: int = Field(default=0, ge=0)
    arguments: dict[str, JsonValue] = Field(
        description=(
            "Parameter bindings for the GENERATED Tool's input_schema, not the source call's arguments. "
            "Extract literal values from the selected successful SQL; the keys must exactly match the "
            "generated Tool's declared parameter names. Do not copy read_queries queries/database_name."
        )
    )


class GeneratedExperience(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128)
    content: ExperienceContent


class GeneratedTool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128)
    content: ToolContent
    examples: tuple[ToolTraceExample, ...] = Field(min_length=1, max_length=32)


class GeneratedSkillContent(SkillContent):
    """New generated Skills must satisfy the canonical package contract."""

    name: str = Field(
        min_length=1,
        max_length=64,
        pattern=SKILL_NAME_PATTERN,
        description="Lowercase letters, digits, and single hyphens only, e.g. analyze-order-status.",
    )
    description: str = Field(min_length=1, max_length=1024)


class GeneratedSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128)
    content: GeneratedSkillContent
    tool_keys: tuple[str, ...] = Field(default=(), max_length=16)


class GeneratedTraceLearningBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiences: tuple[GeneratedExperience, ...] = Field(default=(), max_length=128)
    tools: tuple[GeneratedTool, ...] = Field(default=(), max_length=128)
    skills: tuple[GeneratedSkill, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def unique_keys(self):
        for group in (self.experiences, self.tools, self.skills):
            if len({item.key for item in group}) != len(group):
                raise TraceLearningError("duplicate_generated_key")
        return self


ArtifactFamily = Literal["experience", "tool", "skill"]
GeneratedCandidate = GeneratedExperience | GeneratedTool | GeneratedSkill
CandidateT = TypeVar("CandidateT")


class CandidateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    family: ArtifactFamily
    key: str = Field(min_length=1, max_length=128)
    purpose: str = Field(min_length=1, max_length=2000)
    trace_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    tool_keys: tuple[str, ...] = Field(default=(), max_length=16)


class CandidateInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: tuple[CandidateSpec, ...] = Field(default=(), max_length=384)

    @model_validator(mode="after")
    def unique_candidates(self):
        if len({(item.family, item.key) for item in self.candidates}) != len(self.candidates):
            raise ValueError("Candidate family/key pairs must be unique")  # noqa: TRY003
        return self


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=128)
    category: Literal["input_contract", "output_contract", "parameterization", "applicability", "implementation"]
    comment: str = Field(min_length=1, max_length=3000)
    suggestion: str = Field(min_length=1, max_length=3000)


class ToolReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: tuple[ReviewFinding, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def unique_findings(self):
        if len({item.id for item in self.findings}) != len(self.findings):
            raise ValueError("Review finding IDs must be unique")  # noqa: TRY003
        return self


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    finding_id: str
    decision: Literal["accept", "partial", "reject"]
    reason: str = Field(min_length=1, max_length=3000)


class CandidateResponse(BaseModel, Generic[CandidateT]):
    model_config = ConfigDict(extra="forbid")
    candidate: CandidateT
    decisions: tuple[ReviewDecision, ...] = Field(default=(), max_length=32)


class CandidateFeedback(BaseModel):
    validation_errors: tuple[str, ...] = ()
    review: ToolReview | None = None


class CandidateOutcome(BaseModel):
    family: ArtifactFamily
    key: str
    status: Literal["planned", "generating", "reviewing", "repairing", "ready", "published", "rejected", "deferred"] = (
        "planned"
    )
    reason: str | None = None
    repair_rounds: int = 0
    review_rounds: int = 0


class LearningCandidate(BaseModel):
    spec: CandidateSpec
    outcome: CandidateOutcome
    messages: tuple[dict[str, JsonValue], ...] = ()
    revisions: tuple[CandidateResponse[GeneratedCandidate], ...] = ()
    reviews: tuple[ToolReview, ...] = ()
    review_messages: tuple[tuple[dict[str, JsonValue], ...], ...] = ()
    reviewed_revision: int = 0
    review_resolved: bool = False
    feedback: CandidateFeedback | None = None
    validation: ValidationReport | None = None


class PreviousLearningArtifact(BaseModel):
    ref: ArtifactRef
    key: str
    content: dict[str, JsonValue]


class SQLMismatchFeedback(BaseModel):
    code: Literal["trace_sql_mismatch"] = "trace_sql_mismatch"
    tool_key: str
    trace_id: str
    call_id: str
    query_index: int
    expected_sql: str
    actual_sql: str


class SQLMismatchError(TraceLearningError):
    def __init__(self, feedback: tuple[SQLMismatchFeedback, ...]) -> None:
        super().__init__("trace_sql_mismatch")
        self.feedback = feedback


class RejectedLearningCandidate(BaseModel):
    candidate: GeneratedTraceLearningBundle
    feedback: tuple[SQLMismatchFeedback, ...] = Field(min_length=1, max_length=32)
    usage: LearningUsage


class ResolvedToolTraceCall(BaseModel):
    """Server-resolved historical Tool program and its recorded parameter binding."""

    trace_id: str
    call_id: str
    tool_ref: ArtifactRef
    content: ToolContent
    executed_sql: str


class TraceLearningGenerationInput(BaseModel):
    traces: tuple[CompleteTrace, ...]
    host_profile: TraceLearningHostProfile
    previous_artifacts: tuple[PreviousLearningArtifact, ...] = ()
    resolved_tool_calls: tuple[ResolvedToolTraceCall, ...] = ()
    validation_feedback: RejectedLearningCandidate | None = None


class ValidationReport(BaseModel):
    covered_path_verified: bool = False
    live_execution_verified: bool = False
    checked_examples: int = 0
    method: Literal["recorded_sql_ast_equivalence"] = "recorded_sql_ast_equivalence"
    detail: str = "Checks parameter binding against recorded successful SQL; does not rerun a live database."


class LearningRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope_id: str
    run_id: str
    status: Literal["queued", "running", "succeeded", "failed"] = "queued"
    stage: Literal["imported", "discovering", "generating", "reviewing", "validating", "saving", "complete"] = (
        "imported"
    )
    sources: tuple[SourceRef, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    host_profile: TraceLearningHostProfile
    input_digest: str
    accepted_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempt_count: int = 0
    prompt_version: str = TRACE_LEARNING_PROMPT_VERSION
    model_config_id: str | None = None
    usage: LearningUsage = Field(default_factory=LearningUsage)
    budget: LearningBudget = Field(default_factory=LearningBudget)
    validation: ValidationReport | None = None
    error: str | None = None
    candidate_outcomes: tuple[CandidateOutcome, ...] = ()

    @property
    def terminal(self) -> bool:
        return self.status in {"succeeded", "failed"}


class GetLearningRunRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)


class LearningRecord(BaseModel):
    run: LearningRun
    request: ImportTraceLearningRequest
    principal_id: str
    generation: int = 0
    request_generation: int = 0
    deadline_at: datetime | None = None
    generation_input: TraceLearningGenerationInput | None = None
    generated: GeneratedTraceLearningBundle | None = None
    generated_output_tokens: int | None = None
    rejected_candidates: tuple[RejectedLearningCandidate, ...] = ()
    artifact_keys: dict[str, ArtifactRef] = Field(default_factory=dict)
    candidate_plan_ready: bool = False
    discovery_messages: tuple[dict[str, JsonValue], ...] = ()
    candidates: tuple[LearningCandidate, ...] = ()


LearningCandidate.model_rebuild()
