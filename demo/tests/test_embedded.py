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

"""内嵌服务测试: 进程内 loopback PowerContext 的 remember/search 闭环。"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import httpx
from powercontext.client import PowerContextClient
from powercontext.http import CreateScopeRequest, RememberMemoryRequest, SearchMemoryRequest

from demo.server import EmbeddedPowerContext


def test_embedded_server_remember_then_search() -> None:
    async def body() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pc = EmbeddedPowerContext(Path(tmp), inference=None)
            pc.start()
            try:
                # 注入 trust_env=False 的 httpx 客户端, 避免本机代理劫持 loopback 请求;
                # PowerContextClient.aclose() 只关闭自建客户端, 注入的由 with 负责
                async with (
                    httpx.AsyncClient(trust_env=False, timeout=60) as http,
                    PowerContextClient(pc.base_url, timeout=60, http_client=http) as client,
                ):
                    # scope_id 由 Server 分配, 幂等键保证同一访客拿到同一个 scope
                    scope = await client.create_scope(
                        CreateScopeRequest(
                            title="visitor-test",
                            summary="记忆体验 Demo 的测试访客",
                            idempotency_key="demo:visitor-test",
                        )
                    )
                    await client.remember_memory(
                        RememberMemoryRequest(
                            scope_id=scope.scope_id, kind="fact", text="用户对花生过敏，早餐要避开花生制品"
                        )
                    )
                    resp = await client.search_memory(
                        SearchMemoryRequest(scope_id=scope.scope_id, query="花生 过敏", limit=5)
                    )
                    assert any("花生" in hit.text for hit in resp.hits)
            finally:
                pc.stop()

    asyncio.run(body())
