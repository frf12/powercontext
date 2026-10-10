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

import re
import urllib.parse
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from powercontext.client import PowerContextClient
from pydantic import BaseModel, Field

from .config import DemoConfig
from .server import EmbeddedPowerContext

_NICK_CLEAN = re.compile(r"[^0-9a-zA-Z一-鿿]+")


def scope_for_nickname(nickname: str) -> str:
    """昵称 -> 稳定 scope_id：小写、非法字符折叠为 '-'、保留中文。"""
    cleaned = _NICK_CLEAN.sub("-", nickname.strip().lower()).strip("-")
    if not cleaned:
        raise ValueError("nickname is empty after normalization")
    return f"visitor-{cleaned}"


class EnterRequest(BaseModel):
    passphrase: str
    nickname: str = Field(min_length=1, max_length=32)


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
            scope = scope_for_nickname(body.nickname)
        except ValueError:
            raise HTTPException(status_code=422, detail="昵称无效：请使用包含中文或字母的昵称") from None
        token = uuid.uuid4().hex
        app.state.tokens.add(token)
        response = JSONResponse({"ok": True, "nickname": body.nickname, "scope_id": scope})
        response.set_cookie("demo_token", token, httponly=True, samesite="lax")
        # Set-Cookie 头按 latin-1 编码, 昵称需百分号编码后才能放进去
        response.set_cookie("demo_nick", urllib.parse.quote(body.nickname), httponly=False, samesite="lax")
        return response

    @app.get("/api/session")
    async def session(request: Request) -> dict[str, str]:
        scope = require_session(request)
        nickname = urllib.parse.unquote(request.cookies.get("demo_nick", ""))
        return {"nickname": nickname, "scope_id": scope}

    return app


def require_session(request: Request) -> str:
    token = request.cookies.get("demo_token", "")
    if not token or token not in request.app.state.tokens:
        raise HTTPException(status_code=401, detail="请先输入口令进入")
    nickname = urllib.parse.unquote(request.cookies.get("demo_nick", ""))
    try:
        return scope_for_nickname(nickname)
    except ValueError:
        raise HTTPException(status_code=401, detail="会话无效，请重新进入") from None


# --- 生命周期: 进程内启动真实 PowerContext ---


async def startup(app: FastAPI) -> None:
    if getattr(app.state, "pc", None) is not None:
        return
    app.state.tokens = set()
    # app.state.http 是外部 LLM 客户端, 保留 trust_env 默认值
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
    pc = EmbeddedPowerContext(app.state.cfg.data_dir)
    pc.start()
    app.state.pc = pc
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
