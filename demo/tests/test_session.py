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

"""会话门测试：口令校验、昵称 slug 化与 cookie 会话。"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import httpx

from demo.app import create_demo_app, shutdown, startup
from demo.config import DemoConfig


def make_config(tmp: str) -> DemoConfig:
    return DemoConfig(
        passphrase="open-sesame",  # noqa: S106
        port=8080,
        llm_base_url="http://fake.local/v1",
        llm_api_key="test-key",
        llm_model="fake-chat",
        data_dir=Path(tmp),
    )


def run_with_app(coro_fn: Any) -> None:
    """启动完整 app（含内嵌 PowerContext），跑一个 async 测试体后干净关闭。"""

    async def wrapper() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = create_demo_app(make_config(tmp))
            await startup(app)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                    await coro_fn(client, app)
            finally:
                await shutdown(app)

    asyncio.run(wrapper())


async def _enter(client: httpx.AsyncClient, passphrase: str, nickname: str) -> httpx.Response:
    return await client.post("/api/enter", json={"passphrase": passphrase, "nickname": nickname})


def test_wrong_passphrase_rejected() -> None:
    async def body(client: httpx.AsyncClient, app: Any) -> None:
        resp = await _enter(client, "wrong", "张三")
        assert resp.status_code == 401

    run_with_app(body)


def test_enter_then_session_and_scope() -> None:
    async def body(client: httpx.AsyncClient, app: Any) -> None:
        resp = await _enter(client, "open-sesame", "张三")
        assert resp.status_code == 200
        assert resp.json()["scope_id"] == "visitor-张三"
        session = await client.get("/api/session")
        assert session.status_code == 200
        assert session.json() == {"nickname": "张三", "scope_id": "visitor-张三"}

    run_with_app(body)


def test_session_without_token_is_401() -> None:
    async def body(client: httpx.AsyncClient, app: Any) -> None:
        resp = await client.get("/api/session")
        assert resp.status_code == 401

    run_with_app(body)


def test_blank_nickname_is_422() -> None:
    async def body(client: httpx.AsyncClient, app: Any) -> None:
        resp = await _enter(client, "open-sesame", "   ")
        assert resp.status_code == 422

    run_with_app(body)
