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
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
import rfc8785
from sqlalchemy import select, update

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.memory.canonical import canonical_json
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import ARTIFACTS_TABLE
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.atomic_memory_rebuild import rebuild_atomic_memory_projection
from powercontext.client import PowerContextClient
from powercontext.http import (
    AtomicMemoryInput,
    AtomicMemoryWriteContent,
    MergeAtomicMemoryRequest,
    RememberMemoryRequest,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


@pytest.mark.parametrize(
    ("text", "normalized"),
    [(" " + "a" * 8_192 + " ", "a" * 8_192), ("e\N{COMBINING ACUTE ACCENT}" * 4_096, "é" * 4_096)],
    ids=["trimmed-limit", "nfc-limit"],
)
def test_sdk_atomic_memory_merge_accepts_normalized_byte_limit(tmp_path: Path, text: str, normalized: str) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'merge.db'}"), mcp=McpConfig(enabled=False)
        )
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scope_id = (await client.get_default_scope()).scope_id
            inputs = []
            for body in ("First preference", "Second preference"):
                remembered = await client.remember_memory(
                    RememberMemoryRequest(scope_id=scope_id, kind="fact", text=body)
                )
                record = remembered.records[0]
                inputs.append(AtomicMemoryInput(artifact=record.artifact, state_version=record.state_version))
            merged = await client.merge_atomic_memories(
                MergeAtomicMemoryRequest(
                    scope_id=scope_id, inputs=inputs, content=AtomicMemoryWriteContent(kind="fact", text=text)
                )
            )
            result = next(record for record in merged.records if record.state == "active")
            assert result.text == normalized
            exact = await client.get_artifact_revision(
                scope_id, result.artifact.family, result.artifact.artifact_id, result.artifact.revision
            )
            assert exact.content["text"] == normalized

    asyncio.run(scenario())


def test_atomic_memory_new_writes_normalize_stored_content_and_search_projection(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'normalization.db'}"))
        ) as contexts:
            await contexts.get("project")
            memory = contexts.atomic_memory.for_scope("project")
            created = []
            for body in ("  Cafe\N{COMBINING ACUTE ACCENT} preference  ", "  Other preference  "):
                created.append(
                    await contexts.records.create_artifact(
                        "project", "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": body})
                    )
                )
            first, second = created
            initial = await memory.get(first.artifact_id)
            assert initial.artifact.content.text == "Café preference"
            assert {hit.text for hit in (await memory.search("preference", mode="text")).hits} == {
                "Café preference",
                "Other preference",
            }

            revised = await contexts.records.replace_artifact(
                "project",
                "atomic-memory",
                first.artifact_id,
                '"revision:1"',
                ArtifactWrite(content={"kind": "fact", "text": "  Revised Cafe\N{COMBINING ACUTE ACCENT} choice  "}),
            )
            assert revised.content["text"] == "Revised Café choice"
            first_record = await memory.get(first.artifact_id)
            second_record = await memory.get(second.artifact_id)
            merged = await memory.merge(
                (first_record.as_read(), second_record.as_read()),
                AtomicMemoryContent(kind="fact", text="  Cafe\N{COMBINING ACUTE ACCENT} merged  "),
            )
            assert merged.primary.artifact.content.text == "Café merged"
            stored = await contexts.records.get_artifact("project", "atomic-memory", merged.primary_artifact_id)
            assert stored.content["text"] == "Café merged"
            hits = (await memory.search("merged", mode="text")).hits
            assert len(hits) == 1
            assert hits[0].text == "Café merged"

    asyncio.run(scenario())


def test_atomic_memory_history_read_rebuild_and_restore_preserve_original_content(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'history.db'}"))
        ) as contexts:
            await contexts.get("project")
            created = await contexts.records.create_artifact(
                "project", "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": "Original preference"})
            )
            historical_text = "  Cafe\N{COMBINING ACUTE ACCENT} preference  "
            historical_content = AtomicMemoryContent(kind="fact", text=historical_text)
            payload = historical_content.model_dump_json(by_alias=True).encode()
            identity = (
                ARTIFACTS_TABLE.c.scope_id == "project",
                ARTIFACTS_TABLE.c.family == "atomic-memory",
                ARTIFACTS_TABLE.c.artifact_id == created.artifact_id,
                ARTIFACTS_TABLE.c.revision == 1,
            )
            # Seed a revision accepted before write normalization was introduced.
            async with contexts.database.transaction() as connection:
                await connection.execute(update(ARTIFACTS_TABLE).where(*identity).values(content=payload))

            memory = contexts.atomic_memory.for_scope("project")
            record = await contexts.records.get_artifact_revision("project", "atomic-memory", created.artifact_id, 1)
            assert record.content["text"] == historical_text
            assert record.content_digest == f"sha256:{sha256(rfc8785.dumps(record.content)).hexdigest()}"
            assert (await memory.get(created.artifact_id)).artifact.content.text == historical_text

            report = await rebuild_atomic_memory_projection(
                contexts.database, contexts.atomic_memory.index, maintenance_confirmed=True
            )
            assert report.ready, report.errors
            assert (await memory.search("preference", mode="text")).hits[0].text == historical_text
            index_table = contexts.atomic_memory.index.table
            async with contexts.database.transaction() as connection:
                current = (
                    await connection.execute(
                        select(index_table.c.text, index_table.c.content_hash).where(
                            index_table.c.scope_id == "project", index_table.c.artifact_id == created.artifact_id
                        )
                    )
                ).one()
                assert current.text == historical_text
                assert current.content_hash == sha256(canonical_json(record.content)).hexdigest()
                assert await connection.scalar(select(ARTIFACTS_TABLE.c.content).where(*identity)) == payload

            await contexts.records.replace_artifact(
                "project",
                "atomic-memory",
                created.artifact_id,
                '"revision:1"',
                ArtifactWrite(content={"kind": "fact", "text": "Updated preference"}),
            )
            restored = await memory.restore(created.artifact_id, revision=1)
            assert restored.primary.artifact.revision == 3
            assert restored.primary.artifact.content.text == historical_text
            assert (await memory.search("preference", mode="text")).hits[0].text == historical_text
            historical = await contexts.records.get_artifact_revision(
                "project", "atomic-memory", created.artifact_id, 1
            )
            assert historical == record

    asyncio.run(scenario())
