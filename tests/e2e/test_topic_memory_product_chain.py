# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from tests.e2e.topic_memory_product import harness
from tests.e2e.topic_memory_product.common import FakeInference, run_e0, start_loopback_server


def test_r8_e0_runs_the_complete_hermetic_topic_product_chain(tmp_path: Path) -> None:
    report = run_e0(tmp_path / "r8-e0")

    assert report["status"] == "PASS"
    chain = cast(dict[str, Any], report["chain"])
    assert isinstance(chain, dict)
    assert chain["flush"]["returned_before_generation_completed"] is True
    assert chain["search"]["mode"] == "hybrid"
    assert chain["prepared_context"]["full_detail_absent"] is True
    assert chain["mcp"]["tools"] == ["search_topic_memory", "get_topic_memory"]
    assert chain["web"]["routes"] == [
        "/topics",
        "/dashboard/topic-memories/list",
        "/dashboard/topic-memories/get",
    ]
    cleanup = cast(dict[str, Any], report["cleanup"])
    assert cleanup == {
        "powercontext_port_closed": True,
        "fake_provider_port_closed": True,
        "temporary_runtime_removed": True,
    }
    assert not any(path.name.startswith(".runtime-") for path in (tmp_path / "r8-e0").iterdir())


def test_environment_layers_dispatch_every_fully_configured_real_layer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Path, object]] = []

    def e2(directory: Path, **kwargs: object) -> dict[str, object]:
        calls.append(("E2", directory, kwargs["config"]))
        assert kwargs["e1_status"] == "PASS"
        return {"status": "PASS"}

    def e3(directory: Path, **kwargs: object) -> dict[str, object]:
        calls.append(("E3", directory, kwargs["config"]))
        return {"status": "PASS"}

    def e4(directory: Path, **kwargs: object) -> dict[str, object]:
        calls.append(("E4", directory, kwargs["generation_timeout"]))
        return {"status": "PASS"}

    monkeypatch.setattr(harness, "run_e2", e2)
    monkeypatch.setattr(harness, "run_e3", e3)
    monkeypatch.setattr(harness, "run_e4", e4)
    monkeypatch.setattr(harness.importlib.util, "find_spec", lambda _name: object())
    environment = {
        "POWERCONTEXT_R8_EMBEDDING_MODEL": "openai:test-embedding",
        "POWERCONTEXT_R8_EMBEDDING_PROFILE_ID": "r8-real-embedding-8-unit",
        "POWERCONTEXT_R8_EMBEDDING_DIMENSION": "8",
        "POWERCONTEXT_R8_EMBEDDING_BASE_URL": "https://embedding.example.invalid/v1",
        "POWERCONTEXT_R8_EMBEDDING_HEADERS_JSON": '{"Authorization":"secret-value"}',
        "POWERCONTEXT_R8_OCEANBASE_URL": (
            "mysql+aoceanbase://r8:secret@db.example.invalid:2881/r8_disposable?charset=utf8mb4"
        ),
        "POWERCONTEXT_R8_SEEKDB_ENABLED": "1",
    }

    layers = harness._execute_environment_layers(
        requested={"e0", "e1", "e2", "e3", "e4"},
        directory=tmp_path,
        e1_status="PASS",
        generation_timeout=42,
        environment=environment,
    )

    assert {name: layer["status"] for name, layer in layers.items()} == {
        "E2": "PASS",
        "E3": "PASS",
        "E4": "PASS",
    }
    assert [name for name, _directory, _config in calls] == ["E2", "E3", "E4"]
    embedding = cast(Any, calls[0][2])
    assert (embedding.model, embedding.profile_id, embedding.dimension) == (
        "openai:test-embedding",
        "r8-real-embedding-8-unit",
        8,
    )
    assert embedding.headers["Authorization"].get_secret_value() == "secret-value"
    oceanbase = cast(Any, calls[1][2])
    assert oceanbase.schema_fingerprint == harness.digest_text("r8_disposable")


def test_missing_or_unrequested_layers_never_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError

    monkeypatch.setattr(harness, "run_e2", unexpected)
    monkeypatch.setattr(harness, "run_e3", unexpected)
    monkeypatch.setattr(harness, "run_e4", unexpected)

    missing = harness._execute_environment_layers(
        requested={"e0", "e2", "e3", "e4"},
        directory=tmp_path,
        e1_status="PASS",
        generation_timeout=42,
        environment={"POWERCONTEXT_R8_EMBEDDING_MODEL": "openai:test"},
    )
    assert missing["E2"]["status"] == "UNAVAILABLE"
    assert "POWERCONTEXT_R8_EMBEDDING_PROFILE_ID" in str(missing["E2"]["gap"])
    assert "POWERCONTEXT_R8_EMBEDDING_DIMENSION" in str(missing["E2"]["gap"])
    assert missing["E3"]["gap"] == ("missing dedicated disposable OceanBase URL/schema: POWERCONTEXT_R8_OCEANBASE_URL")
    assert missing["E4"]["gap"] == "missing explicit opt-in: POWERCONTEXT_R8_SEEKDB_ENABLED=1"

    unrequested = harness._execute_environment_layers(
        requested={"e0"},
        directory=tmp_path,
        e1_status="UNAVAILABLE",
        generation_timeout=42,
        environment={
            "POWERCONTEXT_R8_EMBEDDING_MODEL": "configured-but-unrequested",
            "POWERCONTEXT_R8_OCEANBASE_URL": "configured-but-unrequested",
            "POWERCONTEXT_R8_SEEKDB_ENABLED": "1",
        },
    )
    assert {name: layer["gap"] for name, layer in unrequested.items()} == {
        "E2": "not requested in this invocation",
        "E3": "not requested in this invocation",
        "E4": "not requested in this invocation",
    }


def test_e2_automatically_requests_its_e1_prerequisite() -> None:
    assert harness._requested_layers("e2") == {"e0", "e1", "e2"}


def test_e2_configured_provider_runs_hybrid_and_controlled_fallback(
    tmp_path: Path,
) -> None:
    provider = start_loopback_server(FakeInference().app())
    try:
        config, unavailable = harness._real_embedding_config({
            "POWERCONTEXT_R8_EMBEDDING_MODEL": "openai:r8-configured-embedding",
            "POWERCONTEXT_R8_EMBEDDING_PROFILE_ID": "r8-configured-embedding-4-unit",
            "POWERCONTEXT_R8_EMBEDDING_DIMENSION": "4",
            "POWERCONTEXT_R8_EMBEDDING_BASE_URL": f"{provider.base_url}/v1",
            "POWERCONTEXT_R8_EMBEDDING_HEADERS_JSON": '{"Authorization":"Bearer r8-test"}',
        })
        assert unavailable is None
        assert config is not None

        report = harness.run_e2(
            tmp_path / "r8-e2",
            config=config,
            e1_status="PASS",
            generation_timeout=30,
        )
    finally:
        provider.stop()

    assert report["status"] == "PASS"
    hybrid = cast(dict[str, Any], report["hybrid_chain"])
    assert hybrid["search"]["mode"] == "hybrid"
    fallback = cast(dict[str, Any], report["controlled_fallback"])
    assert fallback["search_mode"] == "fts"
    assert fallback["exact_ref"] == hybrid["search"]["exact_ref"]
    assert fallback["signal"] == {
        "event": "topic_memory.search.embedding_fallback",
        "mode": "fts",
        "error_code": "inference_unavailable",
    }


def test_main_can_report_all_requested_layers_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = argparse.Namespace(
        layers="e0,e1,e2,e3,e4",
        output=tmp_path / "all-pass",
        codex_timeout=10,
        generation_timeout=10,
        codex_model="test-codex",
        generation_model="test-generation",
    )
    monkeypatch.setattr(harness, "_parse_args", lambda: args)
    monkeypatch.setattr(harness, "_version", lambda *_args, **_kwargs: "test-head")
    monkeypatch.setattr(harness, "run_e0", lambda _directory: {"status": "PASS"})
    monkeypatch.setattr(harness, "run_e1", lambda _directory, **_kwargs: {"status": "PASS"})
    monkeypatch.setattr(
        harness,
        "_execute_environment_layers",
        lambda **_kwargs: {
            "E2": {"status": "PASS"},
            "E3": {"status": "PASS"},
            "E4": {"status": "PASS"},
        },
    )

    assert harness.main() == 0
    report = json.loads((args.output / "r8-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "PASS"


def test_e3_background_environment_loads_the_production_split_role() -> None:
    config, unavailable = harness._oceanbase_layer_config({
        "POWERCONTEXT_R8_OCEANBASE_URL": (
            "mysql+aoceanbase://r8:secret@db.example.invalid:2881/r8_disposable?charset=utf8mb4"
        )
    })
    assert unavailable is None
    assert config is not None
    environment = harness._background_environment(config, inference_base_url="http://127.0.0.1:12345")

    with patch.dict(os.environ, environment, clear=True):
        settings = harness.ServerSettings()

    assert settings.runtime.artifact_processing_role == "background"
    assert settings.database.kind == "oceanbase"
    assert settings.inference.generation_model == "openai-chat:r8-fake-generation"
    assert str(settings.inference.generation_base_url) == "http://127.0.0.1:12345/v1"
    assert settings.handoff_report.enabled is False
