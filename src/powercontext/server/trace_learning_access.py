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

"""Recheck the importing principal and attest learned Artifact ownership."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.trace_learning.models import TraceLearningError
from powercontext.builtin.trace_learning.service import TraceLearningPermission
from powercontext.server.authz.errors import AccessControlError, AccessDeniedError
from powercontext.server.authz.models import AccessAction, PrincipalRef, ResourceRef
from powercontext.server.authz.service import AccessAuditContext, AccessControlService
from powercontext.server.context import current_request_id
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime


def _principal(identity: str) -> PrincipalRef:
    try:
        value = json.loads(identity)
        if not isinstance(value, dict) or set(value) != {"type", "id"}:
            raise TraceLearningError("access_revoked")
        return PrincipalRef(type=value["type"], id=value["id"])
    except (ValueError, TypeError, AccessControlError) as error:
        raise TraceLearningError("access_revoked") from error


def _audit() -> AccessAuditContext:
    return AccessAuditContext(request_id=current_request_id(), transport="background", operation="trace_learning")


class TraceLearningAccess:
    def __init__(self, access: AccessControlService) -> None:
        self.access = access

    def bind(self, runtime: BuiltinRuntime) -> None:
        service = runtime._trace_learning_service
        if service is not None:
            service.configure_authorization(self.authorize, self.access.defer_decision_audit, self.attest_artifact)

    async def authorize(
        self,
        scope_id: str,
        principal_id: str,
        permission: TraceLearningPermission,
        ref: SourceRef | ArtifactRef | None,
    ) -> None:
        principal = _principal(principal_id)
        resource = (
            ResourceRef.artifact(scope_id, family=ref.family, artifact_id=ref.artifact_id)
            if isinstance(ref, ArtifactRef)
            else ResourceRef.scope(scope_id)
        )
        action = (
            AccessAction.SCOPE_CONTRIBUTE
            if permission == "contribute"
            else AccessAction.ARTIFACT_WRITE
            if permission == "write"
            else AccessAction.ARTIFACT_READ
            if isinstance(ref, ArtifactRef)
            else AccessAction.SCOPE_READ
        )
        try:
            await self.access.require(principal, action, resource, context=_audit())
        except AccessDeniedError as error:
            raise TraceLearningError("access_revoked") from error
        except AccessControlError as error:
            raise TraceLearningError("access_unavailable") from error

    async def attest_artifact(
        self,
        connection: AsyncConnection,
        scope_id: str,
        principal_id: str,
        ref: ArtifactRef,
    ) -> None:
        bound = TraceLearningAccess(self.access.with_connection(connection))
        await bound.authorize(scope_id, principal_id, "contribute", None)
        resource = ResourceRef.artifact(scope_id, family=ref.family, artifact_id=ref.artifact_id)
        try:
            owner = await bound.access.artifact_owner(resource)
            if owner is not None:
                await bound.authorize(scope_id, principal_id, "write", ref)
            elif ref.revision != 1:
                raise TraceLearningError("access_unavailable")
            else:
                identity = json.dumps([scope_id, ref.family, ref.artifact_id], ensure_ascii=False).encode()
                await bound.access.establish_artifact_owner(
                    resource,
                    _principal(principal_id),
                    idempotency_key="trace-learning-owner:" + sha256(identity).hexdigest(),
                    context=_audit(),
                )
        except AccessControlError as error:
            raise TraceLearningError("access_unavailable") from error
