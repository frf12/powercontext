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

"""Unified Atomic retrieval through the composed HTTP and authorization boundaries."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.atomic_memory_security import AtomicMemorySecurity
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.client import PowerContextClient
from powercontext.http import CreateAccessBindingRequest, CreateScopeRequest, RememberMemoryRequest
from powercontext.server.authentication import AuthenticationRejectedError, AuthenticationResult, ProviderReadiness
from powercontext.server.authz import AccessAction, GroupRef, PrincipalRef, ResourceRef
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, MetricsConfig, ServerSettings

ADMIN = PrincipalRef(type="service", id="atomic-search-admin")
VIEWER = PrincipalRef(type="user", id="atomic-search-viewer")
SHARED_READER = PrincipalRef(type="user", id="atomic-search-shared-reader")
GROUP = GroupRef(type="group", id="atomic-search-group")
DEPLOYMENT = "unified-atomic-search"


class Authentication:
    async def authenticate(self, request) -> AuthenticationResult:
        authorization = request.headers.get("authorization")
        if authorization == f"Bearer {ADMIN.id}":
            return AuthenticationResult(subject=ADMIN)
        if authorization == f"Bearer {VIEWER.id}":
            return AuthenticationResult(subject=VIEWER, actor=ADMIN, subject_groups=(GROUP,))
        if authorization == f"Bearer {SHARED_READER.id}":
            return AuthenticationResult(subject=SHARED_READER)
        raise AuthenticationRejectedError

    async def readiness(self) -> ProviderReadiness:
        return ProviderReadiness(ready=True)


def test_composed_atomic_search_returns_full_content_scores_and_trusted_local_identity(
    tmp_path: Path, monkeypatch
) -> None:
    contexts = []
    filters = AtomicMemorySecurity.filters

    async def capture(self, scope_id, context, **kwargs):
        contexts.append(context)
        return await filters(self, scope_id, context, **kwargs)

    monkeypatch.setattr(AtomicMemorySecurity, "filters", capture)

    async def scenario() -> None:
        app = create_server_app(
            settings=ServerSettings(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'local.db'}"),
                runtime=RuntimeConfig(artifact_processing_families=()),
                metrics=MetricsConfig(enabled=False),
                mcp=McpConfig(enabled=False),
            ),
            scheduler_path=tmp_path / "scheduler.db",
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
            PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True) as client,
        ):
            scope_id = (await client.get_default_scope()).scope_id
            record = (
                await client.remember_memory(
                    RememberMemoryRequest(
                        scope_id=scope_id, kind="decision", text="Release rollback uses a reviewed plan."
                    )
                )
            ).records[0]
            path = f"/v1/scopes/{scope_id}/artifacts/atomic-memory/search"
            plain = await transport.post(path, json={"query": "rollback"})
            scored = await transport.post(path, json={"query": "rollback", "include_scores": True})
            assert plain.status_code == scored.status_code == 200
            plain_item, scored_item = plain.json()["results"][0], scored.json()["results"][0]
            assert (plain_item["family"], plain_item["artifact_id"], plain_item["revision"]) == (
                record.artifact.family,
                record.artifact.artifact_id,
                record.artifact.revision,
            )
            assert plain_item["content"] == {
                "schema": "powercontext.atomic-memory.v1",
                "kind": "decision",
                "text": "Release rollback uses a reviewed plan.",
                "creation": None,
            }
            assert "scores" not in plain_item
            assert {key: value for key, value in scored_item.items() if key != "scores"} == plain_item
            assert scored_item["scores"]["retrieval"] == 1.0
            raw = scored_item["scores"]["channels"]["text"]
            assert raw["raw"] < 0
            assert raw["metric"] == "sqlite_bm25"
            assert raw["higher_is_better"] is False
            assert contexts
            assert all(context.access is None and context.trusted_local is True for context in contexts)
            assert all(
                context.principal == app.state.application.atomic_memory.default_context.principal
                for context in contexts
            )
            empty_scope = (
                await client.create_scope(
                    CreateScopeRequest(title="Empty search", summary="Validate controls", idempotency_key="empty")
                )
            ).scope_id
            empty_path = f"/v1/scopes/{empty_scope}/artifacts/atomic-memory/search"
            empty = await transport.post(empty_path, json={"query": "rollback"})
            assert empty.status_code == 200
            assert empty.json() == {"results": []}
            for controls in (
                {"mode": "vector"},
                {"mode": "hybrid"},
                {"admission": {"min_semantic_similarity": 0.3}},
                {"fusion": {"method": "rrf", "params": {"weights": {"vector": 0}}}},
                {"filters": {"tag_match": "any"}},
                {"limit": 101},
                {"rerank": True},
            ):
                invalid = await transport.post(empty_path, json={"query": "rollback", **controls})
                assert invalid.status_code == 422
                assert invalid.json()["error"]["code"] in {"invalid_request", "artifact_search_not_supported"}

    asyncio.run(scenario())


def test_unified_atomic_search_preserves_authenticated_subject_actor_groups_and_audit(
    tmp_path: Path, monkeypatch
) -> None:
    contexts = []
    filters = AtomicMemorySecurity.filters

    async def capture(self, scope_id, context, **kwargs):
        contexts.append(context)
        return await filters(self, scope_id, context, **kwargs)

    monkeypatch.setattr(AtomicMemorySecurity, "filters", capture)

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'authenticated.db'}")
        async with open_builtin_access_control(
            database, bootstrap_administrators=(ADMIN,), deployment_id=DEPLOYMENT
        ) as access:
            app = create_server_app(
                settings=ServerSettings(
                    database=database,
                    runtime=RuntimeConfig(artifact_processing_families=()),
                    access=AccessControlConfig(mode="enforced", deployment_id=DEPLOYMENT),
                    metrics=MetricsConfig(enabled=False),
                    mcp=McpConfig(enabled=False),
                ),
                scheduler_path=tmp_path / "scheduler.db",
                access_control=access,
                authentication_provider=Authentication(),
            )
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
                PowerContextClient(
                    "http://testserver", token=ADMIN.id, http_client=transport, trust_transport_security=True
                ) as admin,
            ):
                scope_id = (
                    await admin.create_scope(
                        CreateScopeRequest(title="Atomic search", summary="Trusted identity", idempotency_key="search")
                    )
                ).scope_id
                record = (
                    await admin.remember_memory(
                        RememberMemoryRequest(scope_id=scope_id, kind="fact", text="Rollback requires a reviewed plan.")
                    )
                ).records[0]
                await admin.create_access_binding(
                    CreateAccessBindingRequest.model_validate({
                        "subject": {"type": VIEWER.type, "id": VIEWER.id},
                        "resource": {"type": "scope", "scope_id": scope_id},
                        "role": "scope.viewer",
                        "idempotency_key": "search-viewer",
                    })
                )
                contexts.clear()
                path = f"/v1/scopes/{scope_id}/artifacts/atomic-memory/search"
                response = await transport.post(
                    path, headers={"Authorization": f"Bearer {VIEWER.id}"}, json={"query": "rollback"}
                )
                assert response.status_code == 200
                assert response.json()["results"][0]["artifact_id"] == record.artifact.artifact_id
                assert contexts
                for context in contexts:
                    assert context.principal == VIEWER
                    assert context.trusted_local is False
                    assert context.audit.actor == ADMIN
                    assert context.audit.subject_groups == (GROUP,)
                    assert context.audit.operation == "search_artifacts"
                    assert context.audit.transport == "http"
                    assert context.audit.request_id == response.headers["x-powercontext-request-id"]
                events = await access.audit.list_audit(resource=ResourceRef.scope(scope_id), subject=VIEWER)
                decisions = [
                    event for event in events if event.request_id == response.headers["x-powercontext-request-id"]
                ]
                assert decisions
                assert all(event.principal == VIEWER and event.actor == ADMIN for event in decisions)
                assert all(event.operation == "search_artifacts" and event.transport == "http" for event in decisions)
                assert any(event.action is AccessAction.SCOPE_READ and event.allowed for event in decisions)
                before = tuple(contexts)
                for field in ("principal", "access", "audit", "trusted_local", "execution_context"):
                    rejected = await transport.post(
                        path,
                        headers={"Authorization": f"Bearer {VIEWER.id}"},
                        json={"query": "rollback", field: "spoofed"},
                    )
                    assert rejected.status_code == 422
                assert tuple(contexts) == before
                await admin.create_access_binding(
                    CreateAccessBindingRequest.model_validate({
                        "subject": {"type": SHARED_READER.type, "id": SHARED_READER.id},
                        "resource": {
                            "type": "artifact",
                            "scope_id": scope_id,
                            "identity": {"family": "atomic-memory", "artifact_id": record.artifact.artifact_id},
                        },
                        "role": "artifact.viewer",
                        "idempotency_key": "search-artifact-only-reader",
                    })
                )
                shared_headers = {"Authorization": f"Bearer {SHARED_READER.id}"}
                unified = await transport.post(path, headers=shared_headers, json={"query": "rollback"})
                dedicated = await transport.post(
                    "/v1/atomic-memory/search", headers=shared_headers, json={"scope_id": scope_id, "query": "rollback"}
                )
                assert unified.status_code == 403
                assert dedicated.status_code == 200
                assert [item["memory"]["artifact"]["artifact_id"] for item in dedicated.json()["hits"]] == [
                    record.artifact.artifact_id
                ]

    asyncio.run(scenario())
