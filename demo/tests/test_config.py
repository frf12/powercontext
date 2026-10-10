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


def test_load_config_missing_required_key_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("DEMO_PASSPHRASE=open-sesame\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="DEMO_LLM_BASE_URL"):
        load_config(env_file)
