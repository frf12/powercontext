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

"""记忆面板 API 测试：entries/revise/retire 回路与会话门。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from powercontext.http import RememberMemoryRequest, SearchMemoryRequest

from demo.app import create_demo_app, ensure_scope, shutdown, startup
from demo.config import DemoConfig


def _cfg(tmp_path: Path) -> DemoConfig:
    return DemoConfig(
        passphrase="open-sesame",  # noqa: S106
        port=8080,
        llm_base_url="http://fake/v1",
        llm_api_key="k",
        llm_model="fake-chat",
        data_dir=tmp_path,
    )


def test_entries_retire_revise_roundtrip(tmp_path: Path) -> None:
    async def body() -> None:
        app = create_demo_app(_cfg(tmp_path))
        await startup(app)
        try:
            # 先用 Server 分配的 scope_id 播种一条记忆, 再走面板 API 全流程
            scope = await ensure_scope(app.state.client, "张三")
            await app.state.client.remember_memory(
                RememberMemoryRequest(scope_id=scope, kind="preference", text="用户喜欢冰美式咖啡")
            )
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                await client.post("/api/enter", json={"passphrase": "open-sesame", "nickname": "张三"})

                listed = await client.get("/api/memory/entries")
                assert listed.status_code == 200
                entries = listed.json()["entries"]
                assert len(entries) == 1
                entry = entries[0]
                assert entry["text"] == "用户喜欢冰美式咖啡"
                assert entry["state"] == "active"

                revised = await client.post(
                    "/api/memory/revise",
                    json={
                        "entry_id": entry["entry_id"],
                        "entry_version_id": entry["entry_version_id"],
                        "artifact_id": entry["artifact_id"],
                        "revision": entry["revision"],
                        "kind": entry["kind"],
                        "text": "用户喜欢热拿铁",
                    },
                )
                assert revised.status_code == 200
                hits = await app.state.client.search_memory(
                    SearchMemoryRequest(scope_id=scope, query="喜欢 咖啡", limit=5)
                )
                assert any("热拿铁" in hit.text for hit in hits.hits)

                # revise changed the citation - re-fetch before retiring
                entry = (await client.get("/api/memory/entries")).json()["entries"][0]
                assert entry["kind"] == "preference"  # revise 沿用卡片携带的原 kind
                assert entry["text"] == "用户喜欢热拿铁"
                # 前端把整张卡片原样回传, 多余字段必须被容忍
                retired = await client.post(
                    "/api/memory/retire",
                    json={
                        "entry_id": entry["entry_id"],
                        "entry_version_id": entry["entry_version_id"],
                        "artifact_id": entry["artifact_id"],
                        "revision": entry["revision"],
                        "kind": entry["kind"],
                        "text": entry["text"],
                        "state": entry["state"],
                        "version": entry["version"],
                    },
                )
                assert retired.status_code == 200
                hits = await app.state.client.search_memory(
                    SearchMemoryRequest(scope_id=scope, query="喜欢 咖啡", limit=5)
                )
                assert not any("热拿铁" in hit.text for hit in hits.hits)
                # 遗忘后面板列表为空 (默认不包含 inactive 条目)
                assert (await client.get("/api/memory/entries")).json()["entries"] == []
        finally:
            await shutdown(app)

    asyncio.run(body())


# POST 路由带最小合法请求体: FastAPI 先做 body 校验, 401 必须来自会话门而不是 422
_MIN_CITATION = {"entry_id": "e", "entry_version_id": "v", "artifact_id": "a", "revision": 1}


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/api/memory/entries", None),
        ("POST", "/api/memory/retire", _MIN_CITATION),
        ("POST", "/api/memory/revise", {**_MIN_CITATION, "kind": "preference", "text": "x"}),
    ],
)
def test_memory_endpoints_require_session(
    tmp_path: Path, method: str, path: str, payload: dict[str, Any] | None
) -> None:
    async def body() -> None:
        app = create_demo_app(_cfg(tmp_path))
        await startup(app)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                assert (await client.request(method, path, json=payload)).status_code == 401
        finally:
            await shutdown(app)

    asyncio.run(body())
