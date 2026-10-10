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

"""测试环境清理: 防止开发 shell 的推理配置泄入 demo 测试。"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _scrub_inference_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("POWERCONTEXT_SERVER_INFERENCE_"):
            monkeypatch.delenv(key, raising=False)
