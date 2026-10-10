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
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

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
        # DashScope-style usage-only trailing chunk with an empty choices list.
        chunks.append("data: " + json.dumps({"choices": [], "usage": {"total_tokens": 42}}) + "\n\n")
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


class FakeGenerationServer:
    """Loopback OpenAI Chat Completions 端点, 固定返回一条记忆提取候选。

    内嵌 PowerContext 用 openai-chat: 前缀真连这里, 走完整的真实提取链路。
    """

    EXTRACTION_OUTPUT = json.dumps(
        {"candidates": [{"intent": "add", "kind": "preference", "text": "用户喜欢吃辣", "evidence_ids": ["source:0"]}]},
        ensure_ascii=False,
    )

    def __init__(self) -> None:
        self.base_url = ""
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._listener: socket.socket | None = None

    def start(self) -> str:
        app = FastAPI()

        @app.post("/chat/completions")
        async def completions() -> dict[str, object]:
            return {
                "id": "chatcmpl-fake-extract",
                "object": "chat.completion",
                "created": 0,
                "model": "fake-extract",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": self.EXTRACTION_OUTPUT},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        self._listener = listener
        self.base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False, lifespan="off"))
        self._server = server
        self._thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 30
        while self._thread.is_alive() and not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not server.started:
            self.stop()
            raise RuntimeError("fake generation server failed to start")
        return self.base_url

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=15)
        if self._listener is not None:
            self._listener.close()
        self._server = None
        self._thread = None
        self._listener = None


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


async def _swap_in_fake_llm(app: Any, captured: list[dict[str, Any]]) -> None:
    """替换外部 LLM 客户端前先关掉 startup 建的原客户端, 避免泄漏连接池。"""
    await app.state.http.aclose()
    app.state.http = httpx.AsyncClient(transport=make_llm_transport(captured))


def test_chat_off_has_no_recall_and_plain_system_prompt(tmp_path: Path) -> None:
    captured: list[dict[str, Any]] = []
    cfg = make_config(tmp_path)

    async def body() -> list[dict[str, Any]]:
        app = create_demo_app(cfg)
        await startup(app)
        # 测试替换外部 LLM 客户端, 内嵌 PowerContext 保持真实
        await _swap_in_fake_llm(app, captured)
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
        await _swap_in_fake_llm(app, captured)
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


def test_chat_on_flushes_and_next_turn_recalls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    cfg = make_config(tmp_path)
    generation = FakeGenerationServer()
    generation.start()
    try:
        # httpx 的 NO_PROXY 只认精确 IP (127.0.0.1), 不认 CIDR (127.0.0.0/8);
        # 推理客户端默认 trust_env=True, 不补精确条目时 loopback 调用会被
        # HTTP_PROXY 劫持成 502, 内嵌服务的提取每回合都会失败
        no_proxy = f"{os.environ.get('NO_PROXY', os.environ.get('no_proxy', ''))},127.0.0.1"
        monkeypatch.setenv("NO_PROXY", no_proxy)
        monkeypatch.setenv("no_proxy", no_proxy)
        # 推理配置在 EmbeddedPowerContext.__init__ (startup 内) 读取, 必须先于 startup 设置
        monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL", "openai-chat:fake-extract")
        monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL", generation.base_url)
        monkeypatch.setenv(
            "POWERCONTEXT_SERVER_INFERENCE_GENERATION_HEADERS",
            json.dumps({"Authorization": "Bearer x"}),
        )

        async def body() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            app = create_demo_app(cfg)
            await startup(app)
            await _swap_in_fake_llm(app, captured)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                    await client.post("/api/enter", json={"passphrase": "open-sesame", "nickname": "张三"})
                    first = await _post_chat(
                        client, [{"role": "user", "content": "我最喜欢吃辣，无辣不欢"}], memory_on=True
                    )
                    second = await _post_chat(client, [{"role": "user", "content": "我平时喜欢吃什么"}], memory_on=True)
                    return first, second
            finally:
                await shutdown(app)

        first, second = asyncio.run(body())
    finally:
        generation.stop()
    flushed = [event for event in first if event["type"] == "flushed"]
    assert flushed and flushed[0]["processed_source_count"] >= 1
    recall_events = [event for event in second if event["type"] == "recall"]
    assert recall_events and any("辣" in hit["text"] for hit in recall_events[0]["hits"])


def test_chat_llm_failure_yields_error_event(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async def body() -> list[dict[str, Any]]:
        app = create_demo_app(cfg)
        await startup(app)
        await app.state.http.aclose()
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                await client.post("/api/enter", json={"passphrase": "open-sesame", "nickname": "张三"})
                events: list[dict[str, Any]] = []
                async with client.stream(
                    "POST",
                    "/api/chat",
                    json={"messages": [{"role": "user", "content": "推荐个早餐"}], "memory_on": False},
                ) as resp:
                    assert resp.status_code == 200
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            events.append(json.loads(line.removeprefix("data: ")))
                return events
        finally:
            await shutdown(app)

    events = asyncio.run(body())
    types = [event["type"] for event in events]
    assert "error" in types
    assert "done" not in types


def test_chat_on_falls_back_to_recent_memories_when_search_misses(tmp_path: Path) -> None:
    """Identity-style queries share no tokens with the stored text, so search
    misses and the fallback must inject the recent memories anyway."""
    from powercontext.http import RememberMemoryRequest

    captured: list[dict[str, Any]] = []
    cfg = make_config(tmp_path)

    async def body() -> list[dict[str, Any]]:
        app = create_demo_app(cfg)
        await startup(app)
        await _swap_in_fake_llm(app, captured)
        try:
            scope = await ensure_scope(app.state.client, "张三")
            await app.state.client.remember_memory(
                RememberMemoryRequest(
                    scope_id=scope, kind="fact", text="用户是OceanBase的开发工程师，平时主要写分布式存储代码"
                )
            )
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                await client.post("/api/enter", json={"passphrase": "open-sesame", "nickname": "张三"})
                return await _post_chat(client, [{"role": "user", "content": "你知道我是谁吗"}], memory_on=True)
        finally:
            await shutdown(app)

    events = asyncio.run(body())
    recall_events = [event for event in events if event["type"] == "recall"]
    assert recall_events, "expected fallback recall event"
    assert recall_events[0]["fallback"] is True
    assert any("OceanBase" in hit["text"] for hit in recall_events[0]["hits"])
    assert "OceanBase" in captured[-1]["messages"][0]["content"]
