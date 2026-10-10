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

"""进程内 loopback PowerContext 服务（模式来自 examples/jupyter/_tutorial.py）。"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from pathlib import Path

import uvicorn
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import ExternalSkillsConfig, InferenceConfig, RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    AccessControlConfig,
    BearerAuthConfig,
    HttpConfig,
    McpConfig,
    MetricsConfig,
    ServerSettings,
    TracingConfig,
)
from pydantic import ValidationError

_ENV_PREFIX = "POWERCONTEXT_SERVER_INFERENCE_"
_LOGGER = logging.getLogger(__name__)


def inference_from_env() -> InferenceConfig:
    """只取 generation_/embedding_ 前缀的键，headers 与 model_settings 按 JSON 解析。"""
    values: dict[str, object] = {}
    for key, value in os.environ.items():
        if not key.startswith(_ENV_PREFIX) or not value:
            continue
        field = key.removeprefix(_ENV_PREFIX).lower()
        if not field.startswith(("generation_", "embedding_")):
            continue
        values[field] = json.loads(value) if field.endswith(("_headers", "_model_settings")) else value
    return InferenceConfig.model_validate(values)


class EmbeddedPowerContext:
    """在随机 loopback 端口运行真实 Server；数据落在 data_dir，重启不丢。"""

    def __init__(self, data_dir: Path, inference: InferenceConfig | None = None) -> None:
        self.data_dir = data_dir
        if inference is not None:
            self.inference = inference
        else:
            try:
                self.inference = inference_from_env()
            except (json.JSONDecodeError, ValidationError) as error:
                raise RuntimeError(
                    "解析 POWERCONTEXT_SERVER_INFERENCE_* 环境变量失败, "
                    f"请检查 demo/.env 的 POWERCONTEXT_SERVER_INFERENCE_* 配置: {error}"
                ) from error
        self.base_url = ""
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._listener: socket.socket | None = None

    def start(self) -> str:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        settings = ServerSettings(
            workspace=self.data_dir,
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{self.data_dir / 'demo.db'}"),
            http=HttpConfig(host="127.0.0.1"),
            auth=BearerAuthConfig(enabled=False),
            access=AccessControlConfig(mode="disabled"),
            inference=self.inference,
            runtime=RuntimeConfig(schedule_seconds=None, experience_schedule_seconds=None),
            external_skills=ExternalSkillsConfig(),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
            tracing=TracingConfig(enabled=False),
        )
        app = create_server_app(settings=settings, scheduler_path=self.data_dir / "scheduler.db")
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        self._listener = listener
        self.base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False, lifespan="on"))
        self._server = server
        self._thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 45
        while self._thread.is_alive() and not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not server.started:
            self.stop()
            raise RuntimeError("内嵌 PowerContext 服务启动失败，请检查上方日志。")
        return self.base_url

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=15)
            if self._thread.is_alive():
                _LOGGER.warning("内嵌 PowerContext 服务线程 15 秒内未退出, 可能仍有请求在处理")
        if self._listener is not None:
            self._listener.close()
        self._server = None
        self._thread = None
        self._listener = None
