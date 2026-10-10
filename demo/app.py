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

"""记忆体验 Demo 的 FastAPI 应用：口令门、昵称会话、聊天与记忆面板路由。"""

from __future__ import annotations

import json
import re
import urllib.parse
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from powercontext.client import PowerContextClient, ServerResponseError
from powercontext.http import (
    ArtifactReference,
    CreateScopeRequest,
    ListMemoryEntriesRequest,
    MemoryCitation,
    MemoryEntry,
    RetireMemoryEntryRequest,
    ReviseMemoryEntryRequest,
)
from pydantic import BaseModel, Field

from .agent import run_chat_turn
from .config import DemoConfig
from .server import EmbeddedPowerContext

_NICK_CLEAN = re.compile(r"[^0-9a-zA-Z一-鿿]+")


def scope_for_nickname(nickname: str) -> str:
    """昵称 -> 稳定 scope_id：小写、非法字符折叠为 '-'、保留中文。"""
    cleaned = _NICK_CLEAN.sub("-", nickname.strip().lower()).strip("-")
    if not cleaned:
        raise ValueError("nickname is empty after normalization")
    return f"visitor-{cleaned}"


async def ensure_scope(client: PowerContextClient, nickname: str) -> str:
    """Get-or-create the visitor's server-side scope via an idempotency key."""
    slug = scope_for_nickname(nickname)
    # title/summary 必须只由 slug 决定: 否则 "Amy"/"amy" 这类同 slug 不同原名的
    # 两次进入会因摘要不一致被 Server 判为幂等冲突 (409)
    descriptor = await client.create_scope(
        CreateScopeRequest(
            title=f"记忆体验访客 {slug}",
            summary=f"线下活动 Demo 访客 {slug} 的独立记忆空间",
            idempotency_key=f"demo:{slug}",
        )
    )
    return descriptor.scope_id


class EnterRequest(BaseModel):
    passphrase: str
    nickname: str = Field(min_length=1, max_length=32)


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    memory_on: bool = True


# 前端把 entries 返回的整张卡片回传给 retire/revise, 多余字段被 pydantic 默认忽略
class CitationBody(BaseModel):
    entry_id: str
    entry_version_id: str
    artifact_id: str
    revision: int


class RetireBody(CitationBody):
    pass


class ReviseBody(CitationBody):
    kind: str
    text: str = Field(min_length=1, max_length=8000)


def _entry_payload(entry: MemoryEntry) -> dict[str, Any]:
    """MemoryEntry -> 前端记忆卡片的扁平字段, 这些字段会被原样回传给 retire/revise。"""
    return {
        "entry_id": entry.citation.entry_id,
        "entry_version_id": entry.citation.entry_version_id,
        "artifact_id": entry.citation.memory_ref.artifact_id,
        "revision": entry.citation.memory_ref.revision,
        "kind": entry.kind,
        "text": entry.text,
        "state": entry.state.value,
        "version": entry.version,
    }


def _add_memory_routes(app: FastAPI) -> None:
    def _citation(body: CitationBody) -> MemoryCitation:
        return MemoryCitation(
            memory_ref=ArtifactReference(family="memory", artifact_id=body.artifact_id, revision=body.revision),
            entry_id=body.entry_id,
            entry_version_id=body.entry_version_id,
        )

    @app.get("/api/memory/entries")
    async def memory_entries(request: Request) -> dict[str, Any]:
        scope = await require_session(request)
        resp = await app.state.client.list_memory_entries(ListMemoryEntriesRequest(scope_id=scope))
        return {"entries": [_entry_payload(entry) for entry in resp.entries]}

    @app.post("/api/memory/retire")
    async def memory_retire(body: RetireBody, request: Request) -> dict[str, bool]:
        scope = await require_session(request)
        try:
            await app.state.client.retire_memory_entry(
                RetireMemoryEntryRequest(scope_id=scope, citation=_citation(body), reason="demo 面板遗忘")
            )
        except ServerResponseError as exc:
            # 引用已过期等上游失败原样透传状态码, 前端读 {"detail": ...} 提示用户
            raise HTTPException(status_code=exc.status_code, detail=exc.server_message or "记忆服务错误") from exc
        return {"ok": True}

    @app.post("/api/memory/revise")
    async def memory_revise(body: ReviseBody, request: Request) -> dict[str, bool]:
        scope = await require_session(request)
        try:
            await app.state.client.revise_memory_entry(
                ReviseMemoryEntryRequest(
                    scope_id=scope, citation=_citation(body), kind=body.kind, text=body.text, reason="demo 面板修改"
                )
            )
        except ServerResponseError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.server_message or "记忆服务错误") from exc
        return {"ok": True}


def create_demo_app(cfg: DemoConfig) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await startup(app)
        yield
        await shutdown(app)

    app = FastAPI(title="PowerContext Memory Demo", lifespan=lifespan)
    app.state.cfg = cfg

    @app.post("/api/enter")
    async def enter(body: EnterRequest, request: Request) -> JSONResponse:
        if body.passphrase != app.state.cfg.passphrase:
            raise HTTPException(status_code=401, detail="口令不对")
        try:
            scope_for_nickname(body.nickname)
        except ValueError:
            raise HTTPException(status_code=422, detail="昵称无效：请使用包含中文或字母的昵称") from None
        # scope_id 由 Server 分配, 幂等键保证同一昵称跨重启拿到同一个 scope
        scope = await ensure_scope(app.state.client, body.nickname)
        token = uuid.uuid4().hex
        app.state.tokens.add(token)
        response = JSONResponse({"ok": True, "nickname": body.nickname, "scope_id": scope})
        response.set_cookie("demo_token", token, httponly=True, samesite="lax")
        # Set-Cookie 头按 latin-1 编码, 昵称需百分号编码后才能放进去
        response.set_cookie("demo_nick", urllib.parse.quote(body.nickname), httponly=False, samesite="lax")
        return response

    @app.get("/api/session")
    async def session(request: Request) -> dict[str, str]:
        scope = await require_session(request)
        nickname = urllib.parse.unquote(request.cookies.get("demo_nick", ""))
        return {"nickname": nickname, "scope_id": scope}

    @app.post("/api/chat")
    async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
        # 鉴权在开流之前完成, 会话无效时直接 401 而不是半路断流
        scope = await require_session(request)
        messages = [{"role": m.role, "content": m.content} for m in body.messages]

        async def event_stream():
            async for event in run_chat_turn(
                app.state.client, app.state.http, app.state.cfg, scope, messages, body.memory_on
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    _add_memory_routes(app)

    return app


async def require_session(request: Request) -> str:
    token = request.cookies.get("demo_token", "")
    if not token or token not in request.app.state.tokens:
        raise HTTPException(status_code=401, detail="请先输入口令进入")
    nickname = urllib.parse.unquote(request.cookies.get("demo_nick", ""))
    try:
        return await ensure_scope(request.app.state.client, nickname)
    except ValueError:
        raise HTTPException(status_code=401, detail="会话无效，请重新进入") from None


# --- 生命周期: 进程内启动真实 PowerContext ---


async def startup(app: FastAPI) -> None:
    if getattr(app.state, "pc", None) is not None:
        return
    app.state.tokens = set()
    pc = EmbeddedPowerContext(app.state.cfg.data_dir)
    # 先启动内嵌服务, 成功后再创建其余客户端, 失败时不泄漏资源
    pc.start()
    app.state.pc = pc
    print(
        f"[demo] powercontext 就绪 {pc.base_url} "
        f"generation={pc.inference.generation_model} embedding={pc.inference.embedding_model}"
    )
    generation = pc.inference.generation_model
    base = pc.inference.generation_base_url
    # openai: 前缀映射到 Responses API (/responses), DeepSeek/vLLM 等 Chat Completions
    # 兼容端点并不提供该接口, 提取每回合都会失败; openai-chat: 才是 chat/completions
    if (
        generation is not None
        and generation.startswith("openai:")
        and base is not None
        and urllib.parse.urlparse(str(base)).hostname != "api.openai.com"
    ):
        print(
            f"[demo] 警告: generation 模型 {generation} 搭配 {base} 会调用 Responses API, "
            "多数 OpenAI 兼容端点不支持; 记忆提取请改用 openai-chat: 前缀"
        )
    # app.state.http 是外部 LLM 客户端, 保留 trust_env 默认值
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
    # loopback 客户端显式 trust_env=False, 避免本机代理劫持 127.0.0.1 请求;
    # 注入的 httpx 客户端不归 PowerContextClient 所有, 单独存引用以便关闭
    app.state.pc_http = httpx.AsyncClient(trust_env=False, timeout=180)
    app.state.client = PowerContextClient(pc.base_url, timeout=180, http_client=app.state.pc_http)


async def shutdown(app: FastAPI) -> None:
    if getattr(app.state, "pc", None) is None:
        return
    await app.state.client.aclose()
    await app.state.pc_http.aclose()
    await app.state.http.aclose()
    app.state.pc.stop()
    app.state.pc = None
