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

"""Demo 配置：从 demo/.env 读取口令、端口与聊天模型。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEMO_DIR = Path(__file__).resolve().parent

_REQUIRED = ("DEMO_PASSPHRASE", "DEMO_LLM_BASE_URL", "DEMO_LLM_API_KEY", "DEMO_LLM_MODEL")


@dataclass(frozen=True)
class DemoConfig:
    passphrase: str
    port: int
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    data_dir: Path


def load_config(env_file: Path | None = None) -> DemoConfig:
    path = env_file if env_file is not None else DEMO_DIR / ".env"
    if path.is_file():
        load_dotenv(path, override=False)
    missing = [key for key in _REQUIRED if not os.environ.get(key)]
    if missing:
        raise SystemExit(f"缺少环境变量: {', '.join(missing)}；请参考 demo/.env.example 创建 demo/.env")
    try:
        port = int(os.environ.get("DEMO_PORT", "8080"))
    except ValueError:
        raise SystemExit(f"DEMO_PORT 必须是整数，当前值: {os.environ.get('DEMO_PORT')!r}；请修改 demo/.env") from None
    return DemoConfig(
        passphrase=os.environ["DEMO_PASSPHRASE"],
        port=port,
        llm_base_url=os.environ["DEMO_LLM_BASE_URL"].rstrip("/"),
        llm_api_key=os.environ["DEMO_LLM_API_KEY"],
        llm_model=os.environ["DEMO_LLM_MODEL"],
        data_dir=Path(os.environ.get("DEMO_DATA_DIR", DEMO_DIR / "data")),
    )
