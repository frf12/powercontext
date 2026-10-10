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

"""聊天编排测试: memory_on/off 两种回合的召回、系统提示与 SSE 事件流。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from demo.app import create_demo_app, ensure_scope, shutdown, startup
from demo.config import DemoConfig

# done 之后只允许出现的收尾事件: 记忆写入成功或失败都不影响当回合话
_TAIL_EVENT_TYPES = {"flushed", "write_error"}


def make_llm_transport(captured: list[dict[str, Any]]) -> httpx.MockTransport:
    """Fake LLM: record requests, return fixed SSE chunks."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        chunks = []
        for text in ("你好呀", "，我记得你！"):
            chunks.append(
                "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False) + "\n\n"
            )
        chunks.append("data: [DONE]\n\n")
        return httpx.Response(200, content="".join(chunks).encode("utf-8"))

    return httpx.MockTransport(handler)


def make_config(tmp: Path) -> DemoConfig:
    return DemoConfig(
        passphrase="open-sesame",  # noqa: S106
        port=8080,
        llm_base_url="http://fake.local/v1",
        llm_api_key="test-key",
        llm_model="fake-chat",
        data_dir=tmp,
    )


async def _post_chat(
    client: httpx.AsyncClient, messages: list[dict[str, str]], memory_on: bool
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async with client.stream("POST", "/api/chat", json={"messages": messages, "memory_on": memory_on}) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
    types = [event["type"] for event in events]
    # 回合必须到达 done; 之后只允许记忆写入的收尾事件
    assert "done" in types
    assert set(types[types.index("done") + 1 :]) <= _TAIL_EVENT_TYPES
    return events


def test_chat_off_has_no_recall_and_plain_system_prompt(tmp_path: Path) -> None:
    captured: list[dict[str, Any]] = []
    cfg = make_config(tmp_path)

    async def body() -> list[dict[str, Any]]:
        app = create_demo_app(cfg)
        await startup(app)
        # 测试替换外部 LLM 客户端, 内嵌 PowerContext 保持真实
        app.state.http = httpx.AsyncClient(transport=make_llm_transport(captured))
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                await client.post("/api/enter", json={"passphrase": "open-sesame", "nickname": "张三"})
                return await _post_chat(client, [{"role": "user", "content": "推荐个早餐"}], memory_on=False)
        finally:
            await shutdown(app)

    events = asyncio.run(body())
    types = [event["type"] for event in events]
    assert "recall" not in types
    assert "delta" in types
    system_message = captured[-1]["messages"][0]
    assert system_message["role"] == "system"
    assert "长期记忆" not in system_message["content"]


def test_chat_on_recalls_seeded_memory_into_system_prompt(tmp_path: Path) -> None:
    from powercontext.http import RememberMemoryRequest

    captured: list[dict[str, Any]] = []
    cfg = make_config(tmp_path)

    async def body() -> list[dict[str, Any]]:
        app = create_demo_app(cfg)
        await startup(app)
        app.state.http = httpx.AsyncClient(transport=make_llm_transport(captured))
        try:
            # 先用 Server 分配的 scope_id 播种一条记忆, 再带 memory_on 聊天
            scope = await ensure_scope(app.state.client, "张三")
            await app.state.client.remember_memory(
                RememberMemoryRequest(scope_id=scope, kind="fact", text="用户对花生过敏，推荐早餐时要避开花生制品")
            )
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                await client.post("/api/enter", json={"passphrase": "open-sesame", "nickname": "张三"})
                return await _post_chat(client, [{"role": "user", "content": "推荐个早餐"}], memory_on=True)
        finally:
            await shutdown(app)

    events = asyncio.run(body())
    recall_events = [event for event in events if event["type"] == "recall"]
    assert recall_events and any("花生" in hit["text"] for hit in recall_events[0]["hits"])
    system_content = captured[-1]["messages"][0]["content"]
    assert "花生" in system_content
