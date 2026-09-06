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

"""Server-owned HTML pages and their supporting endpoints."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from functools import cache
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, PackageLoader, select_autoescape
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator, model_validator

from powercontext._logging import log_safely
from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.skill import AgentKind, AgentSkillTarget, ExternalSkillResolutionStatus
from powercontext.builtin.artifacts.skill.projection import (
    AgentSkillProjectionConflictError,
    AgentSkillProjectionState,
    inspect_skill_projection,
    publish_skill_projection,
)
from powercontext.builtin.artifacts.topic_memory import (
    PublishedTopicMemory,
    TopicMemory,
    TopicMemoryBrowseCursor,
    TopicMemoryCurrentItem,
)
from powercontext.builtin.review import CandidateStatus
from powercontext.builtin.runtime import (
    GetArtifactCandidateRequest,
    GetSkillRequest,
    GetTopicMemoryRequest,
    ListExternalSkillsRequest,
)
from powercontext.http import ErrorDetail, ErrorResponse
from powercontext.limits import MAX_ARTIFACT_ID_LENGTH
from powercontext.sources import SourceRef

logger = logging.getLogger(__name__)

_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; script-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'"
    ),
}
_DASHBOARD_TOPIC_MEMORY_PAGE_LIMIT = 25
_DASHBOARD_TOPIC_MEMORY_CURSOR_PREFIX = "tm1."
_DASHBOARD_TOPIC_MEMORY_CURSOR_MAX_LENGTH = 1024


class DashboardScope(BaseModel):
    """One Server scope exposed by the personal Dashboard."""

    model_config = ConfigDict(extra="forbid")

    scope_id: str
    display_name: str


class DashboardTopicMemoryListRequest(BaseModel):
    """Browse one configured Dashboard scope without creating a public API."""

    model_config = ConfigDict(extra="forbid")

    scope_id: str = Field(min_length=1, max_length=256)
    limit: StrictInt = Field(default=_DASHBOARD_TOPIC_MEMORY_PAGE_LIMIT, ge=1, le=_DASHBOARD_TOPIC_MEMORY_PAGE_LIMIT)
    cursor: str | None = Field(default=None, min_length=1, max_length=_DASHBOARD_TOPIC_MEMORY_CURSOR_MAX_LENGTH)

    @field_validator("cursor")
    @classmethod
    def require_trimmed_cursor(cls, value: str | None) -> str | None:
        if value is not None and value != value.strip():
            raise ValueError("cursor must be trimmed")  # noqa: TRY003
        return value


class DashboardTopicMemoryGetRequest(BaseModel):
    """Read one exact Topic Memory revision for the private Dashboard."""

    model_config = ConfigDict(extra="forbid")

    scope_id: str = Field(min_length=1, max_length=256)
    artifact: ArtifactRef


class DashboardTopicMemoryItem(BaseModel):
    """One compact current Topic head in recent-publication order."""

    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactRef
    title: str
    summary: str
    published_at: datetime
    source_count: StrictInt = Field(ge=0)


class DashboardTopicMemoryPage(BaseModel):
    """One bounded page from the private Topic Memory browser."""

    model_config = ConfigDict(extra="forbid")

    items: tuple[DashboardTopicMemoryItem, ...]
    next_cursor: str | None


class DashboardTopicMemoryDetail(BaseModel):
    """Exact Topic content plus management-only publication state."""

    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactRef
    title: str
    summary: str
    detail: str
    published_at: datetime
    is_current: bool
    current_artifact: ArtifactRef
    source_refs: tuple[SourceRef, ...]


class _DashboardTopicMemoryCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    published_at: datetime
    artifact_id: str = Field(min_length=1, max_length=MAX_ARTIFACT_ID_LENGTH)
    revision: StrictInt = Field(ge=1)


class _DashboardTopicMemoryRoutes:
    def __init__(self, scope_ids: frozenset[str]) -> None:
        self._scope_ids = scope_ids

    async def list(
        self,
        request: DashboardTopicMemoryListRequest,
        http_request: Request,
        response: Response,
    ) -> DashboardTopicMemoryPage | JSONResponse:
        scoped = _dashboard_topic_memory_application(http_request, request.scope_id, self._scope_ids)
        if isinstance(scoped, JSONResponse):
            return scoped
        after = None
        if request.cursor is not None:
            try:
                after = _decode_dashboard_topic_memory_cursor(request.cursor)
            except (ValueError, ValidationError, UnicodeError, binascii.Error):
                return _web_error(422, "invalid_topic_memory_cursor", "The Topic Memory cursor is invalid.")
        rows = await scoped.browse(limit=request.limit + 1, after=after)
        visible = rows[: request.limit]
        next_cursor = (
            _encode_dashboard_topic_memory_cursor(visible[-1]) if len(rows) > request.limit and visible else None
        )
        response.headers["Cache-Control"] = "no-store"
        return DashboardTopicMemoryPage(
            items=tuple(_dashboard_topic_memory_item(item) for item in visible),
            next_cursor=next_cursor,
        )

    async def get(
        self,
        request: DashboardTopicMemoryGetRequest,
        http_request: Request,
        response: Response,
    ) -> DashboardTopicMemoryDetail | JSONResponse:
        scoped = _dashboard_topic_memory_application(http_request, request.scope_id, self._scope_ids)
        if isinstance(scoped, JSONResponse):
            return scoped
        if request.artifact.family != TopicMemory.family:
            return _web_error(422, "invalid_request", "The request must identify a Topic Memory.")
        published = await scoped.get(GetTopicMemoryRequest(artifact=request.artifact))
        response.headers["Cache-Control"] = "no-store"
        return _dashboard_topic_memory_detail(published)


class DashboardSkillProjectionRequest(BaseModel):
    """Select one exact approved managed Skill Revision from the Review UI."""

    model_config = ConfigDict(extra="forbid")

    scope_id: str = Field(min_length=1, max_length=256)
    candidate_id: str = Field(min_length=1, max_length=MAX_ARTIFACT_ID_LENGTH)
    artifact: ArtifactRef

    @model_validator(mode="after")
    def require_skill_artifact(self) -> DashboardSkillProjectionRequest:
        if self.artifact.family != "skill":
            raise ValueError("artifact must identify a managed Skill")  # noqa: TRY003
        return self


class DashboardSkillPublishRequest(DashboardSkillProjectionRequest):
    """Explicitly publish one exact approved managed Skill Revision."""

    target_id: str = Field(min_length=1, max_length=64)


class DashboardSkillProjectionTarget(BaseModel):
    """One configured host-local Agent publication target and its exact state."""

    model_config = ConfigDict(extra="forbid")

    target_id: str
    agent_kind: AgentKind
    installation_scope: Literal["user", "project", "plugin"]
    destination: str
    state: AgentSkillProjectionState
    published_revision: int | None = None
    reason: str | None = None
    discovery: Literal["available", "unavailable", "not_published"]
    external_skill_id: str | None = None


class DashboardSkillProjection(BaseModel):
    """Publication state for one exact approved managed Skill Revision."""

    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactRef
    name: str
    targets: list[DashboardSkillProjectionTarget]


class _DashboardSkillProjectionRoutes:
    def __init__(self, scope_ids: frozenset[str], targets: tuple[AgentSkillTarget, ...]) -> None:
        self._scope_ids = scope_ids
        self._targets = targets

    async def inspect(
        self,
        request: DashboardSkillProjectionRequest,
        http_request: Request,
    ) -> DashboardSkillProjection | JSONResponse:
        resolved = await _dashboard_managed_skill(http_request, request, self._scope_ids)
        if isinstance(resolved, JSONResponse):
            return resolved
        application, skill = resolved
        return await _skill_projection_response(application, request.scope_id, skill, self._targets)

    async def publish(
        self,
        request: DashboardSkillPublishRequest,
        http_request: Request,
    ) -> DashboardSkillProjection | JSONResponse:
        resolved = await _dashboard_managed_skill(http_request, request, self._scope_ids)
        if isinstance(resolved, JSONResponse):
            return resolved
        application, skill = resolved
        target = next((item for item in self._targets if item.target_id == request.target_id), None)
        if target is None:
            return _web_error(
                404, "skill_publish_target_not_found", "The Agent Skill publication target was not found."
            )
        expected = await asyncio.to_thread(inspect_skill_projection, skill.as_ref(), skill.content, target)
        try:
            await asyncio.to_thread(
                publish_skill_projection,
                skill.as_ref(),
                skill.content,
                target,
                expected=expected,
            )
        except AgentSkillProjectionConflictError as error:
            return _web_error(
                409,
                "skill_projection_conflict",
                "The Agent Skill publication target changed or cannot be updated safely.",
                details={"state": error.status.state.value, "reason": error.status.reason},
            )
        except (OSError, UnicodeError, ValueError) as error:
            return _web_error(
                422,
                "skill_projection_failed",
                "The approved managed Skill could not be published to the configured Agent target.",
                details={"reason": str(error)},
            )
        # The publication itself succeeded above; registry bookkeeping failure must not turn the
        # response into a 500 because _skill_projection_response reports on-disk state anyway.
        try:
            await application.external_skills.for_scope(request.scope_id).scan()
        except Exception as error:
            log_safely(
                logger, logging.WARNING, "PowerContext external Skill scan failed after publication", exc_info=error
            )
        return await _skill_projection_response(application, request.scope_id, skill, self._targets)


def mount_web_ui(
    app: FastAPI,
    *,
    scopes: Mapping[str, str],
    dashboard_enabled: bool = False,
    handoff_report_enabled: bool = False,
    authentication_required: bool = False,
    agent_skill_targets: tuple[AgentSkillTarget, ...] = (),
) -> None:
    """Mount Server-owned pages, static assets, and UI support endpoints."""

    dashboard_scopes = tuple(DashboardScope(scope_id=scope_id, display_name=name) for scope_id, name in scopes.items())
    dashboard_scope_ids = frozenset(scopes)
    publish_targets = tuple(target for target in agent_skill_targets if target.allow_managed_publish)
    skill_projection_routes = _DashboardSkillProjectionRoutes(dashboard_scope_ids, publish_targets)
    topic_memory_routes = _DashboardTopicMemoryRoutes(dashboard_scope_ids)
    templates = _templates()
    if dashboard_enabled:
        templates.env.get_template("pages/dashboard.html")
        templates.env.get_template("pages/topics.html")
        templates.env.get_template("pages/review.html")
        templates.env.get_template("pages/skills.html")
    if handoff_report_enabled:
        templates.env.get_template("pages/handoff_report.html")
    static_files = StaticFiles(packages=[("powercontext.server", "static")])

    router = APIRouter(include_in_schema=False)

    dashboard_context = {
        "dashboard_enabled": True,
        "topics_enabled": True,
        "skills_enabled": True,
        "review_enabled": True,
        "handoff_report_enabled": handoff_report_enabled,
        "home_route": "dashboard_home",
        "authentication_required": authentication_required,
    }
    dashboard_page = _web_page_endpoint(
        templates=templates,
        template_name="pages/dashboard.html",
        context={"active_page": "dashboard", **dashboard_context},
    )
    topics_page = _web_page_endpoint(
        templates=templates,
        template_name="pages/topics.html",
        context={"active_page": "topics", **dashboard_context},
    )
    skills_page = _web_page_endpoint(
        templates=templates,
        template_name="pages/skills.html",
        context={"active_page": "skills", **dashboard_context},
    )
    review_page = _web_page_endpoint(
        templates=templates,
        template_name="pages/review.html",
        context={"active_page": "review", **dashboard_context},
    )
    handoff_report_page = _web_page_endpoint(
        templates=templates,
        template_name="pages/handoff_report.html",
        context={
            "active_page": "handoff_report",
            "dashboard_enabled": dashboard_enabled,
            "topics_enabled": dashboard_enabled,
            "skills_enabled": dashboard_enabled,
            "review_enabled": dashboard_enabled,
            "handoff_report_enabled": True,
            "home_route": "dashboard_home" if dashboard_enabled else "handoff_report_dashboard",
            "authentication_required": authentication_required,
        },
    )

    async def list_dashboard_scopes(response: Response) -> tuple[DashboardScope, ...]:
        response.headers["Cache-Control"] = "no-store"
        return dashboard_scopes

    if dashboard_enabled:
        router.add_api_route(
            "/",
            dashboard_page,
            methods=["GET"],
            response_class=HTMLResponse,
            name="dashboard_home",
        )
        router.add_api_route(
            "/dashboard/scopes",
            list_dashboard_scopes,
            methods=["GET"],
            response_model=list[DashboardScope],
            name="dashboard_scopes",
        )
        router.add_api_route(
            "/topics",
            topics_page,
            methods=["GET"],
            response_class=HTMLResponse,
            name="topics_library",
        )
        router.add_api_route(
            "/dashboard/topic-memories/list",
            topic_memory_routes.list,
            methods=["POST"],
            response_model=DashboardTopicMemoryPage,
            name="dashboard_topic_memories_list",
        )
        router.add_api_route(
            "/dashboard/topic-memories/get",
            topic_memory_routes.get,
            methods=["POST"],
            response_model=DashboardTopicMemoryDetail,
            name="dashboard_topic_memories_get",
        )
        router.add_api_route(
            "/skills",
            skills_page,
            methods=["GET"],
            response_class=HTMLResponse,
            name="skills_library",
        )
        router.add_api_route(
            "/reviews",
            review_page,
            methods=["GET"],
            response_class=HTMLResponse,
            name="review_inbox",
        )
        router.add_api_route(
            "/dashboard/skill-projections/status",
            skill_projection_routes.inspect,
            methods=["POST"],
            response_model=DashboardSkillProjection,
            name="dashboard_skill_projection_status",
        )
        router.add_api_route(
            "/dashboard/skill-projections/publish",
            skill_projection_routes.publish,
            methods=["POST"],
            response_model=DashboardSkillProjection,
            name="dashboard_skill_projection_publish",
        )
    if handoff_report_enabled:
        router.add_api_route(
            "/handoff-reports",
            handoff_report_page,
            methods=["GET"],
            response_class=HTMLResponse,
            name="handoff_report_dashboard",
        )

    app.mount(
        "/static",
        static_files,
        name="web_static",
    )
    app.include_router(router)


def _web_page_endpoint(
    *,
    templates: Jinja2Templates,
    template_name: str,
    context: dict[str, object],
) -> Callable[[Request], Awaitable[Response]]:
    async def render(request: Request) -> Response:
        return templates.TemplateResponse(
            request=request,
            name=template_name,
            context=context,
            headers=_PAGE_HEADERS,
        )

    return render


@cache
def _templates() -> Jinja2Templates:
    environment = Environment(
        loader=PackageLoader("powercontext.server"),
        autoescape=select_autoescape(),
    )
    return Jinja2Templates(env=environment)


def _dashboard_topic_memory_application(
    request: Request,
    scope_id: str,
    dashboard_scope_ids: frozenset[str],
) -> Any | JSONResponse:
    if scope_id not in dashboard_scope_ids:
        return _web_error(404, "dashboard_scope_not_found", "The Dashboard scope was not found.")
    application = request.app.state.application
    if application is None:
        return _web_error(503, "runtime_not_ready", "The Runtime is not ready.")
    return application.topic_memory.for_scope(scope_id)


def _dashboard_topic_memory_item(value: TopicMemoryCurrentItem) -> DashboardTopicMemoryItem:
    return DashboardTopicMemoryItem(
        artifact=value.artifact_ref,
        title=value.title,
        summary=value.summary,
        published_at=value.published_at,
        source_count=value.source_count,
    )


def _dashboard_topic_memory_detail(value: PublishedTopicMemory) -> DashboardTopicMemoryDetail:
    return DashboardTopicMemoryDetail(
        artifact=value.topic.as_ref(),
        title=value.topic.content.title,
        summary=value.topic.content.summary,
        detail=value.topic.content.detail,
        published_at=value.published_at,
        is_current=value.is_current,
        current_artifact=value.current_artifact,
        source_refs=value.topic.lineage.sources,
    )


def _encode_dashboard_topic_memory_cursor(value: TopicMemoryCurrentItem) -> str:
    cursor = _DashboardTopicMemoryCursor(
        published_at=value.published_at,
        artifact_id=value.artifact_ref.artifact_id,
        revision=value.artifact_ref.revision,
    )
    payload = base64.urlsafe_b64encode(cursor.model_dump_json().encode()).decode().rstrip("=")
    return f"{_DASHBOARD_TOPIC_MEMORY_CURSOR_PREFIX}{payload}"


def _decode_dashboard_topic_memory_cursor(value: str) -> TopicMemoryBrowseCursor:
    if not value.startswith(_DASHBOARD_TOPIC_MEMORY_CURSOR_PREFIX):
        raise ValueError("unsupported Topic Memory cursor")  # noqa: TRY003
    encoded = value.removeprefix(_DASHBOARD_TOPIC_MEMORY_CURSOR_PREFIX)
    if not encoded or "=" in encoded:
        raise ValueError("invalid Topic Memory cursor encoding")  # noqa: TRY003
    raw = encoded.encode("ascii")
    decoded = base64.b64decode(raw + b"=" * (-len(raw) % 4), altchars=b"-_", validate=True)
    cursor = _DashboardTopicMemoryCursor.model_validate_json(decoded, strict=True)
    boundary = TopicMemoryBrowseCursor(
        published_at=cursor.published_at,
        artifact_id=cursor.artifact_id,
        revision=cursor.revision,
    )
    canonical = _DashboardTopicMemoryCursor(
        published_at=boundary.published_at,
        artifact_id=boundary.artifact_id,
        revision=boundary.revision,
    )
    canonical_payload = base64.urlsafe_b64encode(canonical.model_dump_json().encode()).decode().rstrip("=")
    if value != f"{_DASHBOARD_TOPIC_MEMORY_CURSOR_PREFIX}{canonical_payload}":
        raise ValueError("non-canonical Topic Memory cursor")  # noqa: TRY003
    return boundary


async def _dashboard_managed_skill(
    request: Request,
    selection: DashboardSkillProjectionRequest,
    dashboard_scope_ids: frozenset[str],
):
    if selection.scope_id not in dashboard_scope_ids:
        return _web_error(404, "dashboard_scope_not_found", "The Dashboard scope was not found.")
    application = request.app.state.application
    if application is None:
        return _web_error(503, "runtime_not_ready", "The Runtime is not ready.")
    candidate = await application.review.for_scope(selection.scope_id).get(
        GetArtifactCandidateRequest(candidate_id=selection.candidate_id)
    )
    if (
        candidate.family != "skill"
        or candidate.status is not CandidateStatus.APPROVED
        or candidate.result_artifact != selection.artifact
    ):
        return _web_error(
            409,
            "skill_projection_not_approved",
            "The selected Artifact is not the exact approved result of this Skill Candidate.",
        )
    skill = await application.skill.for_scope(selection.scope_id).get(GetSkillRequest(artifact=selection.artifact))
    return application, skill


async def _skill_projection_response(
    application,
    scope_id: str,
    skill,
    targets_config: tuple[AgentSkillTarget, ...],
) -> DashboardSkillProjection:
    if not targets_config:
        registrations = ()
        # Registry discovery is best-effort bookkeeping; when it cannot be read (for example an
        # unavailable registry database), report on-disk state with stale discovery instead of
        # failing the whole response after the projection was already published.
    else:
        try:
            registrations = await application.external_skills.for_scope(scope_id).list(
                ListExternalSkillsRequest(include_unavailable=True)
            )
        except Exception as error:
            log_safely(logger, logging.WARNING, "PowerContext external Skill registry discovery failed", exc_info=error)
            registrations = ()
    targets = []
    for target in targets_config:
        status = await asyncio.to_thread(inspect_skill_projection, skill.as_ref(), skill.content, target)
        registration = next(
            (
                item
                for item in registrations
                if item.status is ExternalSkillResolutionStatus.AVAILABLE
                and item.registration.agent_kind == target.agent_kind
                and item.registration.locator == str(status.destination)
            ),
            None,
        )
        if status.state is AgentSkillProjectionState.CURRENT:
            discovery = "available" if registration is not None else "unavailable"
        else:
            discovery = "not_published"
        targets.append(
            DashboardSkillProjectionTarget(
                target_id=target.target_id,
                agent_kind=target.agent_kind,
                installation_scope=target.installation_scope,
                destination=str(status.destination),
                state=status.state,
                published_revision=(None if status.published_artifact is None else status.published_artifact.revision),
                reason=status.reason,
                discovery=discovery,
                external_skill_id=(None if registration is None else registration.registration.external_skill_id),
            )
        )
    return DashboardSkillProjection(artifact=skill.as_ref(), name=skill.content.name, targets=targets)


def _web_error(
    response_status: int,
    code: str,
    message: str,
    *,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    error = ErrorResponse(error=ErrorDetail(code=code, message=message, details=details))
    return JSONResponse(
        status_code=response_status,
        content=error.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


__all__ = [
    "DashboardScope",
    "DashboardSkillProjection",
    "DashboardSkillProjectionRequest",
    "DashboardSkillProjectionTarget",
    "DashboardSkillPublishRequest",
    "DashboardTopicMemoryDetail",
    "DashboardTopicMemoryGetRequest",
    "DashboardTopicMemoryItem",
    "DashboardTopicMemoryListRequest",
    "DashboardTopicMemoryPage",
    "mount_web_ui",
]
