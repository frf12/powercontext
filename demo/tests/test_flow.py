# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""端到端剧本测试: 进门 -> 开记忆召回 -> 关记忆无召回 -> 面板遗忘后不再召回。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
from powercontext.http import RememberMemoryRequest

from demo.app import create_demo_app, ensure_scope, shutdown, startup
from demo.config import DemoConfig
from demo.tests.test_chat import _post_chat, _swap_in_fake_llm


def _cfg(tmp_path: Path) -> DemoConfig:
    return DemoConfig(
        passphrase="open-sesame",  # noqa: S106
        port=8080,
        llm_base_url="http://fake/v1",
        llm_api_key="k",
        llm_model="fake-chat",
        data_dir=tmp_path,
    )


def test_visitor_script_on_off_retire(tmp_path: Path) -> None:
    captured: list[dict[str, Any]] = []

    async def body() -> None:
        app = create_demo_app(_cfg(tmp_path))
        await startup(app)
        await _swap_in_fake_llm(app, captured)
        try:
            # 播种一条访客记忆, 后续三个回合都围绕它观察召回开关与遗忘
            scope = await ensure_scope(app.state.client, "张三")
            await app.state.client.remember_memory(
                RememberMemoryRequest(scope_id=scope, kind="fact", text="用户对花生过敏，推荐早餐时要避开花生制品")
            )
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                await client.post("/api/enter", json={"passphrase": "open-sesame", "nickname": "张三"})

                # memory on: 新会话也能召回种下的记忆
                on_events = await _post_chat(client, [{"role": "user", "content": "推荐个早餐"}], memory_on=True)
                assert any(e["type"] == "recall" for e in on_events)

                # memory off: 无召回, 系统提示只剩人设
                off_events = await _post_chat(client, [{"role": "user", "content": "推荐个早餐"}], memory_on=False)
                assert all(e["type"] != "recall" for e in off_events)
                assert "长期记忆" not in captured[-1]["messages"][0]["content"]

                # 面板遗忘后, 再开记忆也不召回
                entries = (await client.get("/api/memory/entries")).json()["entries"]
                assert entries
                await client.post("/api/memory/retire", json=entries[0])
                after = await _post_chat(client, [{"role": "user", "content": "推荐个早餐"}], memory_on=True)
                assert all(e["type"] != "recall" for e in after)
        finally:
            await shutdown(app)

    asyncio.run(body())
