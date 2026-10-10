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

"""聊天编排：召回 -> 拼 prompt -> 流式调 LLM -> 捕获与提取。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from powercontext.client import PowerContextClient
from powercontext.http import CreateSourceRequest, FlushMemoryRequest, SearchMemoryHit, SearchMemoryRequest

from .config import DemoConfig

PERSONA = (
    "你是「小忆」，一个温暖健谈的中文 AI 助手，正在一个线下的记忆能力体验活动中陪用户聊天。"
    "保持自然口语化的中文，每次回答不超过三句话，并对用户透露的偏好、习惯、事实表现出兴趣。"
)


def build_system_prompt(hits: list[SearchMemoryHit]) -> str:
    if not hits:
        return PERSONA
    lines = "\n".join(f"- {hit.text}" for hit in hits)
    return (
        f"{PERSONA}\n\n"
        "以下是你记住的关于这位用户的长期记忆（由 PowerContext 召回）：\n"
        f"{lines}\n\n"
        "回答时自然地运用这些记忆；除非用户问起，不要逐条复述记忆列表。"
    )


async def stream_llm_reply(
    http: httpx.AsyncClient,
    cfg: DemoConfig,
    system_prompt: str,
    messages: list[dict[str, str]],
) -> AsyncIterator[str]:
    payload = {
        "model": cfg.llm_model,
        "stream": True,
        "temperature": 0.7,
        "messages": [{"role": "system", "content": system_prompt}, *messages],
    }
    async with http.stream(
        "POST",
        f"{cfg.llm_base_url}/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {cfg.llm_api_key}"},
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            data = line.removeprefix("data: ").strip()
            if data == "[DONE]":
                break
            delta = json.loads(data)["choices"][0]["delta"].get("content")
            if delta:
                yield delta


async def run_chat_turn(
    client: PowerContextClient,
    http: httpx.AsyncClient,
    cfg: DemoConfig,
    scope_id: str,
    messages: list[dict[str, str]],
    memory_on: bool,
) -> AsyncIterator[dict[str, Any]]:
    """One chat turn, yielding SSE event dicts."""
    last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")

    hits: list[SearchMemoryHit] = []
    if memory_on and last_user:
        try:
            resp = await client.search_memory(SearchMemoryRequest(scope_id=scope_id, query=last_user, limit=5))
            hits = resp.hits
        except Exception as exc:  # recall failure must not block the chat
            yield {"type": "recall_error", "message": str(exc)}
        if hits:
            yield {"type": "recall", "hits": [{"text": hit.text, "score": hit.score} for hit in hits]}

    reply: list[str] = []
    try:
        async for delta in stream_llm_reply(http, cfg, build_system_prompt(hits), messages):
            reply.append(delta)
            yield {"type": "delta", "text": delta}
    except Exception as exc:
        yield {"type": "error", "message": f"模型调用失败：{exc}"}
        return
    yield {"type": "done", "reply": "".join(reply)}

    # After the turn: with memory on, capture the exchange and trigger extraction.
    if memory_on and last_user and reply:
        try:
            await client.create_source(
                scope_id, CreateSourceRequest(content=f"用户：{last_user}\n助手：{''.join(reply)}")
            )
            flush = await client.flush_memory(FlushMemoryRequest(scope_id=scope_id))
            yield {"type": "flushed", "processed_source_count": flush.processed_source_count}
        except Exception as exc:
            yield {"type": "write_error", "message": str(exc)}
