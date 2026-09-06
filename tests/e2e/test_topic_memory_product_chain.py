# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from tests.e2e.topic_memory_product.common import run_e0


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
