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

"""demo.config 的配置加载测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from demo.config import load_config

ENV_KEYS = ("DEMO_PASSPHRASE", "DEMO_LLM_BASE_URL", "DEMO_LLM_API_KEY", "DEMO_LLM_MODEL", "DEMO_PORT")


def test_load_config_reads_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEMO_PASSPHRASE=open-sesame\n"
        "DEMO_LLM_BASE_URL=http://x.example/v1/\n"
        "DEMO_LLM_API_KEY=test-key\n"
        "DEMO_LLM_MODEL=fake-chat\n"
        "DEMO_PORT=9001\n",
        encoding="utf-8",
    )
    cfg = load_config(env_file)
    assert cfg.passphrase == "open-sesame"
    assert cfg.llm_base_url == "http://x.example/v1"  # 尾部斜杠被去掉
    assert cfg.llm_api_key == "test-key"
    assert cfg.llm_model == "fake-chat"
    assert cfg.port == 9001
    assert cfg.data_dir == Path(__file__).resolve().parent.parent / "data"
    # 未配置 DEMO_PORT 时默认 8080 (先清掉上一次 load_dotenv 注入的值)
    monkeypatch.delenv("DEMO_PORT", raising=False)
    env_file.write_text(
        "DEMO_PASSPHRASE=open-sesame\n"
        "DEMO_LLM_BASE_URL=http://x.example/v1/\n"
        "DEMO_LLM_API_KEY=test-key\n"
        "DEMO_LLM_MODEL=fake-chat\n",
        encoding="utf-8",
    )
    cfg = load_config(env_file)
    assert cfg.port == 8080


def test_load_config_missing_required_key_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("DEMO_PASSPHRASE=open-sesame\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="DEMO_LLM_BASE_URL"):
        load_config(env_file)


def test_load_config_rejects_bad_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEMO_PASSPHRASE=open-sesame\n"
        "DEMO_LLM_BASE_URL=http://x.example/v1/\n"
        "DEMO_LLM_API_KEY=test-key\n"
        "DEMO_LLM_MODEL=fake-chat\n"
        "DEMO_PORT=8080x\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="DEMO_PORT"):
        load_config(env_file)
