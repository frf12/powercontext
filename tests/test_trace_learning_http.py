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

"""Trace learning is authenticated and opt-in without changing legacy context payloads."""

from contextlib import asynccontextmanager
from pathlib import Path

import yaml


def test_trace_learning_has_import_and_read_contract_without_generic_tool_writes():
    contract = yaml.safe_load((Path(__file__).parents[1] / "openapi/powercontext.yaml").read_text())
    assert contract["paths"]["/v1/scopes/{scope_id}/trace-learning"]["post"]["operationId"] == "import_trace_learning"
    assert (
        contract["paths"]["/v1/scopes/{scope_id}/trace-learning/{run_id}"]["get"]["operationId"] == "get_learning_run"
    )
    schemas = contract["components"]["schemas"]
    assert "tool" in schemas["ArtifactReadFamily"]["enum"]
    assert "tool" not in schemas["BaseArtifactFamily"]["enum"]


def test_learned_context_contract_is_explicitly_opt_in():
    contract = yaml.safe_load((Path(__file__).parents[1] / "openapi/powercontext.yaml").read_text())
    schemas = contract["components"]["schemas"]
    assert schemas["PrepareContextRequest"]["properties"]["learned_tools"]["default"] is False
    assert "host_profile" in schemas["PrepareContextRequest"]["properties"]
    assert "learned_context" in schemas["PreparedContext"]["properties"]
    assert "learned_context" not in schemas["PreparedContext"]["required"]


def test_skill_http_mapping_preserves_exact_tool_dependencies():
    from powercontext.artifacts import ArtifactRef
    from powercontext.builtin.artifacts.skill import SkillContent
    from powercontext.server.mapping import skill_content, skill_proposal

    content = SkillContent(
        name="count-stations",
        description="Count stations",
        instructions="Call the exact count tool.",
        validation=("Use the current result.",),
        tool_dependencies=(ArtifactRef(family="tool", artifact_id="count-tool", revision=2),),
    )
    assert skill_content(skill_proposal(content)) == content


def test_authenticated_http_import_recall_and_legacy_compatibility(tmp_path):
    import asyncio

    from powercontext.client import PowerContextClient
    from powercontext.http import ImportTraceLearningRequest, PrepareContextRequest
    from powercontext.server.authz import ResourceRef
    from tests.e2e.test_trace_learning import Generator, request_data

    async def scenario():
        async with learning_http(tmp_path, Generator()) as env:
            api = PowerContextClient("http://127.0.0.1", token="author-token", http_client=env.client)  # noqa: S106
            request = ImportTraceLearningRequest.model_validate(request_data())
            accepted = await api.import_trace_learning("learning", request)
            assert accepted.status == "queued"
            assert (await api.import_trace_learning("learning", request)).run_id == accepted.run_id
            await env.controller.process()
            run = await api.get_learning_run("learning", accepted.run_id)
            assert run.status == "succeeded", run.error
            assert {ref.family for ref in run.artifacts} == {"experience", "skill", "tool"}
            for ref in run.artifacts:
                owner = await env.access.artifact_owner(
                    ResourceRef.artifact(
                        "learning",
                        family=ref.family,
                        artifact_id=ref.artifact_id,
                    )
                )
                assert owner is not None and owner.owner == env.author
            replay = await env.client.post("/v1/scopes/learning/trace-learning", json=request_data())
            assert replay.status_code == 200
            legacy = await env.client.post(
                "/v1/context/prepare",
                json={
                    "scope_id": "learning",
                    "query": "count stations by country",
                    "max_bytes": 8000,
                },
            )
            assert legacy.status_code == 200, legacy.text
            assert set(legacy.json()) == {"schema", "status", "content", "content_bytes"}
            prepared = await api.prepare_context(
                PrepareContextRequest.model_validate({
                    "scope_id": "learning",
                    "query": "count stations by country",
                    "max_bytes": 8000,
                    "learned_tools": True,
                    "host_profile": request_data()["host_profile"],
                })
            )
            assert prepared.learned_context is not None
            learned = prepared.learned_context
            assert learned.tools[0].implementation.sql.endswith("country = ?")
            assert learned.skills[0].tool_dependencies == [learned.tools[0].ref]
            tool = learned.tools[0].ref
            read = await env.client.get(f"/v1/scopes/learning/artifacts/tool/{tool.artifact_id}")
            assert read.status_code == 200, read.text
            resources = await env.client.post(
                "/v1/access/resources/list",
                json={
                    "action": "artifact.read",
                    "resource_type": "artifact",
                    "family": "tool",
                },
            )
            assert resources.status_code == 200, resources.text
            assert [item["identity"]["artifact_id"] for item in resources.json()["items"]] == [tool.artifact_id]
            grant = await env.client.post(
                "/v1/access/bindings/create",
                json={
                    "subject": {"type": "user", "id": "tool-reader"},
                    "resource": {
                        "type": "artifact",
                        "scope_id": "learning",
                        "identity": {"family": "tool", "artifact_id": tool.artifact_id},
                    },
                    "role": "artifact.viewer",
                    "idempotency_key": "share-learned-tool",
                },
            )
            assert grant.status_code == 201, grant.text
            invalid = await env.client.post(
                "/v1/context/prepare",
                json={
                    "scope_id": "learning",
                    "query": "stations",
                    "learned_tools": True,
                },
            )
            assert invalid.status_code == 422, invalid.text
            missing = await env.client.get("/v1/scopes/learning/trace-learning/missing")
            assert missing.status_code == 404, missing.text

    asyncio.run(scenario())


def test_revoked_importer_cannot_publish_generated_artifacts(tmp_path):
    import asyncio

    from powercontext.builtin.trace_learning.models import GetLearningRunRequest
    from powercontext.server.dream_access import principal_identity
    from tests.e2e.test_trace_learning import Generator, request_data

    class BlockedGenerator(Generator):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def generate(self, value):
            self.started.set()
            await self.release.wait()
            return await super().generate(value)

    async def scenario():
        generator = BlockedGenerator()
        async with learning_http(tmp_path, generator) as env:
            accepted = await env.client.post("/v1/scopes/learning/trace-learning", json=request_data())
            assert accepted.status_code == 202, accepted.text
            work = asyncio.create_task(env.controller.process())
            await asyncio.wait_for(generator.started.wait(), 10)
            await env.access.revoke_binding(
                env.admin,
                env.binding.binding_id,
                expected_version=env.binding.version,
                idempotency_key="revoke-importer",
                context=env.audit,
            )
            generator.release.set()
            await work
            run = await env.runtime.trace_learning.for_scope(
                "learning",
                principal_id=principal_identity(env.admin),
            ).get(GetLearningRunRequest(run_id=accepted.json()["run_id"]))
            assert (run.status, run.error, run.artifacts) == ("failed", "access_revoked", ())
            denied = await env.client.get(f"/v1/scopes/learning/trace-learning/{run.run_id}")
            assert denied.status_code == 403, denied.text
            denied = await env.client.post("/v1/scopes/learning/trace-learning", json=request_data("denied"))
            assert denied.status_code == 403, denied.text

    asyncio.run(scenario())


@asynccontextmanager
async def learning_http(tmp_path, generator):
    from types import SimpleNamespace
    from typing import cast

    import httpx
    from starlette.middleware import Middleware

    from powercontext.builtin.persistence.sqlite import SQLiteConfig
    from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig
    from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingBinding
    from powercontext.builtin.runtime.composition import open_builtin_runtime
    from powercontext.builtin.scope import ScopeDraft
    from powercontext.builtin.scope.application import ScopeApplication
    from powercontext.builtin.trace_learning.models import TRACE_LEARNING_BINDING
    from powercontext.server.app import ServerApplication, create_app
    from powercontext.server.authentication import StaticBearerAuthenticationProvider
    from powercontext.server.authz import AccessRole, PrincipalRef, ResourceRef
    from powercontext.server.authz.composition import open_builtin_access_control
    from powercontext.server.authz.service import AccessAuditContext, CreateBinding
    from powercontext.server.middleware import AuthenticationMiddleware

    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'learning-http.db'}")
    admin = PrincipalRef(type="service", id="learning-admin")
    author = PrincipalRef(type="user", id="learning-author")
    audit = AccessAuditContext(transport="http", operation="test_trace_learning")
    controller = _LearningController()
    binding_config = ArtifactProcessingBinding(
        binding_name=TRACE_LEARNING_BINDING,
        artifact_family="tool",
        launcher=controller,
        max_workers=1,
        worker_timeout_seconds=30,
    )
    async with (
        open_builtin_access_control(database, bootstrap_administrators=(admin,)) as access,
        open_builtin_runtime(
            BuiltinConfig(
                database=database,
                runtime=RuntimeConfig(
                    trace_learning_enabled=True,
                    artifact_processing_families=("tool",),
                ),
            ),
            trace_learning_generator=generator,
            artifact_processing_bindings=(binding_config,),
        ) as runtime,
    ):
        controller.runtime = runtime
        service = runtime._trace_learning_service
        assert service is not None
        await ScopeApplication(service.database, id_factory=lambda: "learning").create(
            ScopeDraft(title="Learning", summary="Trace learning HTTP tests", idempotency_key="learning-scope")
        )
        await access.create_binding(
            admin,
            CreateBinding(
                subject=admin,
                resource=ResourceRef.scope("learning"),
                role=AccessRole.SCOPE_REVIEWER,
                idempotency_key="learning-reviewer",
            ),
            context=audit,
        )
        binding = await access.create_binding(
            admin,
            CreateBinding(
                subject=author,
                resource=ResourceRef.scope("learning"),
                role=AccessRole.SCOPE_CONTRIBUTOR,
                idempotency_key="learning-author",
            ),
            context=audit,
        )
        provider = StaticBearerAuthenticationProvider("author-token", author)
        app = create_app(
            application=cast(ServerApplication, runtime),
            access_control=access,
            access_mode="enforced",
            authentication_provider=provider,
            middleware=[Middleware(AuthenticationMiddleware, provider=provider)],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
            headers={"Authorization": "Bearer author-token"},
        ) as client:
            yield SimpleNamespace(
                client=client,
                access=access,
                runtime=runtime,
                author=author,
                admin=admin,
                audit=audit,
                binding=binding,
                controller=controller,
            )


class _LearningHandle:
    def __init__(self, controller, assignment):
        import asyncio

        self.controller = controller
        self.assignment = assignment
        self.ready = asyncio.Event()
        self.task = asyncio.create_task(self.execute())

    async def execute(self):
        from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingWorkerCompletion
        from powercontext.builtin.runtime.processing_execution import ScopeInvocation

        await self.ready.wait()
        await self.controller.runtime._trace_learning_service.execute(ScopeInvocation(self.assignment))
        return ArtifactProcessingWorkerCompletion()

    async def wait(self):
        import asyncio

        return await asyncio.shield(self.task)

    async def terminate(self):
        import asyncio
        from contextlib import suppress

        self.task.cancel()
        with suppress(asyncio.CancelledError):
            await self.task


class _LearningController:
    def __init__(self):
        import asyncio

        self.runtime = None
        self.handles = []
        self.started = asyncio.Event()

    async def start(self, assignment):
        handle = _LearningHandle(self, assignment)
        self.handles.append(handle)
        self.started.set()
        return handle

    async def process(self):
        import asyncio

        async with asyncio.timeout(15):
            while not (pending := [handle for handle in self.handles if not handle.task.done()]):
                self.started.clear()
                await self.started.wait()
            for handle in pending:
                handle.ready.set()
            await asyncio.gather(*(handle.task for handle in pending))


def test_scope_contributor_cannot_revise_another_importers_tools(tmp_path):
    import asyncio
    from typing import cast

    import httpx
    from starlette.middleware import Middleware

    from powercontext.server.app import ServerApplication, create_app
    from powercontext.server.authentication import StaticBearerAuthenticationProvider
    from powercontext.server.authz import AccessRole, PrincipalRef, ResourceRef
    from powercontext.server.authz.service import CreateBinding
    from powercontext.server.middleware import AuthenticationMiddleware
    from tests.e2e.test_trace_learning import Generator, request_data

    async def scenario():
        async with learning_http(tmp_path, Generator()) as env:
            first = await env.client.post("/v1/scopes/learning/trace-learning", json=request_data())
            assert first.status_code == 202, first.text
            await env.controller.process()
            original = await env.client.get(f"/v1/scopes/learning/trace-learning/{first.json()['run_id']}")
            assert original.json()["status"] == "succeeded", original.text
            other = PrincipalRef(type="user", id="other-contributor")
            await env.access.create_binding(
                env.admin,
                CreateBinding(
                    subject=other,
                    resource=ResourceRef.scope("learning"),
                    role=AccessRole.SCOPE_CONTRIBUTOR,
                    idempotency_key="other-contributor",
                ),
                context=env.audit,
            )
            provider = StaticBearerAuthenticationProvider("other-token", other)
            app = create_app(
                application=cast(ServerApplication, env.runtime),
                access_control=env.access,
                access_mode="enforced",
                authentication_provider=provider,
                middleware=[Middleware(AuthenticationMiddleware, provider=provider)],
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1",
                headers={"Authorization": "Bearer other-token"},
            ) as client:
                second = await client.post(
                    "/v1/scopes/learning/trace-learning", json=request_data("second", "trace-2", "SVK")
                )
                assert second.status_code == 202, second.text
                await env.controller.process()
                run = await client.get(f"/v1/scopes/learning/trace-learning/{second.json()['run_id']}")
                assert run.status_code == 200, run.text
                assert (run.json()["status"], run.json()["error"], run.json()["artifacts"]) == (
                    "failed",
                    "access_revoked",
                    [],
                )
            for ref in original.json()["artifacts"]:
                current = await env.client.get(f"/v1/scopes/learning/artifacts/{ref['family']}/{ref['artifact_id']}")
                assert current.status_code == 200, current.text
                assert current.json()["revision"] == 1

    asyncio.run(scenario())


def test_learned_tool_execution_trace_can_be_imported_again_over_http(tmp_path):
    import asyncio
    import json

    from tests.e2e.test_trace_learning import Generator, request_data

    async def scenario():
        async with learning_http(tmp_path, Generator()) as env:
            first = await env.client.post("/v1/scopes/learning/trace-learning", json=request_data())
            assert first.status_code == 202, first.text
            await env.controller.process()
            prepared = await env.client.post(
                "/v1/context/prepare",
                json={
                    "scope_id": "learning",
                    "query": "count stations by country",
                    "learned_tools": True,
                    "host_profile": request_data()["host_profile"],
                    "max_bytes": 8000,
                },
            )
            assert prepared.status_code == 200, prepared.text
            tool = prepared.json()["learned_context"]["tools"][0]
            second_request = request_data("learned-trace", "trace-2", "SVK")
            result = {"columns": [{"name": "total"}], "rows": [{"total": 8}], "truncated": False}
            second_request["traces"][0]["tool_calls"] = [
                {
                    "call_id": "call-1",
                    "name": tool["name"],
                    "arguments": {"country": "SVK"},
                    "result": {
                        "tool_call_id": "call-1",
                        "content": json.dumps(result),
                        "status": "success",
                        "artifact": {
                            "powercontext_tool": {
                                "ref": tool["ref"],
                                "name": tool["name"],
                                "sql": tool["implementation"]["sql"],
                                "database_name": "cards",
                                "arguments": {"country": "SVK"},
                                "complete": True,
                                "result": result,
                            }
                        },
                    },
                }
            ]
            second_request["traces"][0]["final_answer"] = "8 stations."
            second = await env.client.post("/v1/scopes/learning/trace-learning", json=second_request)
            assert second.status_code == 202, second.text
            await env.controller.process()
            run = await env.client.get(f"/v1/scopes/learning/trace-learning/{second.json()['run_id']}")
            assert run.status_code == 200, run.text
            assert run.json()["status"] == "succeeded", run.text
            assert all(ref["revision"] == 2 for ref in run.json()["artifacts"])
            assert run.json()["validation"]["checked_examples"] >= 2

    asyncio.run(scenario())
