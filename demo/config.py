# Copyright (c) 2026 OceanBase.
# SPDX-License-Identifier: Apache-2.0

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
    return DemoConfig(
        passphrase=os.environ["DEMO_PASSPHRASE"],
        port=int(os.environ.get("DEMO_PORT", "8080")),
        llm_base_url=os.environ["DEMO_LLM_BASE_URL"].rstrip("/"),
        llm_api_key=os.environ["DEMO_LLM_API_KEY"],
        llm_model=os.environ["DEMO_LLM_MODEL"],
        data_dir=Path(os.environ.get("DEMO_DATA_DIR", DEMO_DIR / "data")),
    )
