---
title: 启用 Memory 提取与向量搜索
description: 配置模型、启动 Server，并验证完整 Memory 闭环。
---

# 启用 Memory 提取与向量搜索

以下步骤使用 `master` 和 Bash。Windows 支持为 `experimental`，平台要求见[安装与运行](install-and-run.md)。

`powercontext server run` 不配置模型也可以运行，但依赖模型的提取和向量检索不会启用。`config init` 默认打开向导，
先询问存储、使用场景和记忆能力，再收集所选能力需要的模型连接。基础记忆可以由 Agent 显式保存，不需要独立模型 API；
本文介绍自动提取和向量搜索。

| 能力 | 最小 Server | 已配置 Runtime |
| --- | --- | --- |
| Source capture | 启用 | 启用 |
| 自动 Memory extraction | 关闭 | 启用 |
| Search mode | `auto, fts` | `auto, fts, vector, hybrid` |
| Dashboard | 单独启用，要求静态 token | 单独启用，要求静态 token |
| MCP endpoint | `/mcp` | `/mcp` |

Server 首次启动时创建一个使用不透明 ID 的默认 Scope。Integration 可以把 Session 或 workspace 绑定到默认 Scope，
也可以绑定到其他已经存在的 Scope。

## 1. 安装并生成配置

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext config init --output .env
```

选择完整记忆，或者在自定义能力中勾选自动 Memory 提取和语义检索。向导会收集 Generation、Embedding 模型及凭据，
必要的 embedding profile、维度，以及处理调度。命令生成配置和后续操作说明，不部署服务，也不测试远程连接。

首屏提供英语和中文，默认根据系统语言选择，无法识别或不支持的语言回退英语。可以用 `--language en` 或
`--language zh` 显式选择；需要手动编辑无模型基础模板时，加上 `--template`。
语言判断优先级和已有文件处理方式见[配置 Server 环境](configure-server-environment.md)。

如果手动配置，Memory 自动提取和向量检索对应的设置包括以下各项，还需按 provider 要求补充 credential 和 Base URL：

```dotenv
POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:generation-model
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=provider:embedding-model
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=provider-embedding-model-1536-unit-v1
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=1536
POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS=60
```

其中 `generation-model` 负责自动抽取和生成，`embedding-model` 负责向量检索；定时 Source 处理也需要 generation model。
本地 provider 忽略鉴权时，使用该 provider 接受的非秘密占位值。

在不打印 credential 的情况下检查并校验配置：

```bash
powercontext config show --env-file .env
powercontext config validate --env-file .env
```

生成文件包含 Server、数据库和所选 integration 设置。向导会为选中的自动能力配置调度，基础模式不会启用自动提取。
Scope identity 由运行中的 Server 管理，Config Generator 不会凭空生成 Scope ID。
服务启动后仍需完成下文的 Memory 闭环检查；成功保存文件不代表端到端验收通过。

## 2. 启动并检查 Server

```bash
powercontext server run --env-file .env
```

在另一个终端执行：

```bash
set -a
. ./.env
set +a
export POWERCONTEXT_CLIENT_API_TOKEN="${POWERCONTEXT_CLIENT_API_TOKEN:-${POWERCONTEXT_SERVER_AUTH_TOKEN:-}}"
powercontext doctor
powercontext ready
powercontext capabilities
```

Readiness 为 `ready`、Memory extraction 已启用，并且 search mode 包含 `vector` 和 `hybrid` 时，完整 Runtime 可用。
如果只有 `auto, fts`，检查 Embedding model、profile ID、dimension、credential 和 Base URL。

以下请求使用本机 Server 地址；选择了其他主机或端口时，请相应调整。开启 Dashboard 或鉴权访问后，检查需要
`POWERCONTEXT_CLIENT_API_TOKEN`。上面的 export 会在未加载客户端令牌时使用受保护的 Server token。
生成的客户端环境文件可为 Agent 进程提供连接设置，无需向它们暴露模型凭据。

获取默认 Scope 的不透明 ID，供后续 API 检查使用：

```bash
SCOPE_ID="$(curl -fsS http://127.0.0.1:8000/v1/scopes/default \
  -H "Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}" \
  | python -c 'import json, sys; print(json.load(sys.stdin)["scope_id"])')"
export SCOPE_ID
```

## 3. 验证 Memory 闭环

使用唯一 ID 捕获 Source：

```bash
SOURCE_ID="quickstart-$(date +%s)-$$"
curl -fsS -X POST http://127.0.0.1:8000/v1/sources/content \
  -H "Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}" \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${SCOPE_ID}\",\"source_id\":\"${SOURCE_ID}\",\"content\":\"PowerContext quick start check: prefer small, verifiable steps.\"}"
```

保留响应中的 `position`，再 flush 同一 Scope：

```bash
curl -fsS -X POST http://127.0.0.1:8000/v1/memory/flush \
  -H "Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}" \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${SCOPE_ID}\"}"
```

返回的 `current_cursor` 必须不小于 capture `position`。Scheduler 已经处理 Source 时，`status: "idle"` 也是有效结果。

列出 Memory entry：

```bash
curl -fsS -X POST http://127.0.0.1:8000/v1/memory/entries/list \
  -H "Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}" \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${SCOPE_ID}\"}"
```

找到 `source_refs` 包含已捕获 Source 的 entry，记录其 `citation.entry_id`，再验证向量检索：

```bash
curl -fsS -X POST http://127.0.0.1:8000/v1/memory/search \
  -H "Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}" \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${SCOPE_ID}\",\"query\":\"verifiable steps\",\"mode\":\"vector\",\"limit\":50}"
```

响应包含 `mode: "vector"`、已记录的 `entry_id`，且 `matched_by` 包含 `vector` 时，该闭环验证通过。检查模型用量：

```bash
powercontext stats --scope-id "$SCOPE_ID"
```

## 4. 接入 Agent

Server 验证通过后，按[对应 Agent 的文档](../integrations/index.md)配置连接、认证和采集行为。

## 数据与重启

没有覆盖数据库设置时，SQLite 在用户数据目录保存 `powercontext.db`：

- Linux：`$XDG_DATA_HOME/powercontext`，或 `~/.local/share/powercontext`；
- macOS：`~/Library/Application Support/powercontext`；
- Windows（`experimental`）：`%LOCALAPPDATA%\\powercontext`。

按 `Ctrl+C` 停止 Server。使用同一 `.env` 和数据目录重启后，默认 Scope 及其不透明 ID 保持稳定，因为它们保存在数据库中。

| 现象 | 处理方式 |
| --- | --- |
| Readiness 为 `degraded` | 检查模型标识、credential 和 Base URL |
| 没有 `vector` 或 `hybrid` | 同时配置 Embedding model、profile ID 和 dimension |
| Source 一直 pending | 启用 Scheduler，或调用 `/v1/memory/flush` |
| 已有数据消失 | 恢复原数据库 URL 或 `POWERCONTEXT_HOME` |

更多信息见[故障排查](../operate/troubleshoot.md)和[配置](../operate/configuration.md)。

需要分类和检索制品或单条记忆时，参见[自定义标签](../workflows/manage-artifact-tags.md)。
