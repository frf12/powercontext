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

"""python -m demo 启动记忆体验 Demo。"""

from __future__ import annotations

import uvicorn

from demo.app import create_demo_app
from demo.config import load_config


def main() -> None:
    cfg = load_config()
    app = create_demo_app(cfg)
    # SSE 长连接会拖住默认的无限优雅退出, 限时 5 秒保证 Ctrl-C 能确定性走完 lifespan 收尾
    uvicorn.run(
        app,
        host="0.0.0.0",  # noqa: S104
        port=cfg.port,
        log_level="info",
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    main()
