---
title: 从正确轨迹学习经验、Skill 和 Tool
description: 显式导入完整正确的轨迹，通过 Supervisor 独立生成并校验可复用产物。
---

# 从正确轨迹学习经验、Skill 和 Tool

此流程接受调用方显式选择的完整、正确轨迹。PowerContext 通过现有 Artifact Processing Supervisor 生成可复用的
Experience、Skill 和只读 SQL Tool。每个已发布产物保留精确的 Artifact revision、Source lineage 和权限边界。
三类产物共用现有 Artifact 存储，不为学习单独新增制品表。

## 开启独立试验环境

在 Server Runtime 中配置生成模型、对应认证和持久化数据库：

```json
{
  "trace_learning_enabled": true,
  "artifact_processing_families": ["tool"],
  "dream_enabled": false,
  "tool_max_workers": 1,
  "tool_worker_timeout_seconds": 1860
}
```

Trace learning 的 Run 默认截止时间为 1800 秒。示例中的 1860 秒为 Tool Worker 启动和检查点确认预留少量时间。
`tool.trace-learning.v1` 复用现有 Supervisor 的调度、子进程隔离、Lease、额度和 fencing。普通 Source 写入不会触发
trace learning。

## 导入轨迹

调用 `POST /v1/scopes/{scope_id}/trace-learning`，传入 `idempotency_key`、包含实际 SQL 方言和数据库名称的 Datus
`host_profile`，以及 `traces`。每条轨迹保留 `trace_id`、问题、最终答案、上下文和每次工具调用的 ID、名称、参数、结果
及成功标记。完整正确是调用方选择轨迹时应满足的前提；轨迹可以包含失败的中间尝试。

保留 Datus `read_queries` 的原始参数和结果制品，不要伪造拆分后的调用身份。生成 Tool 的示例用 `trace_id`、`call_id` 和
`query_index` 指向原始调用；示例 `arguments` 是生成 Tool 的 input schema 参数值，不能复制源调用中的 `queries` 或
`database_name`。

初次接受返回 `202`。通过 `GET /v1/scopes/{scope_id}/trace-learning/{run_id}` 读取进度。相同幂等键和相同内容会复用 Run；
相同幂等键对应不同内容会被拒绝。成功的工作返回精确的 Experience、Skill 和 Tool 引用。后续显式导入可以沿用稳定 key
更新对应 Artifact，但写入前会校验权限和预期 head revision。

## 理解候选工作流

Supervisor 先让模型生成有界的候选 inventory，然后按 Experience、Tool、Skill 顺序逐候选处理。
`max_candidates_per_family` 分别限制每个 Family 的候选数量。依赖 Tool 的 Skill 会在它引用的 Tool 候选完成校验后生成，
因此同一 Run 中的 `tool_keys` 可以引用已校验候选。

每个候选都有独立保存的模型 `messages`、revision、review finding、validation 结果、修复次数和 outcome。Worker 重启后，
从已保存的 conversation 和候选检查点继续；已经发布的候选会跳过，不会重新生成。

Tool reviewer 完全独立：只接收候选的 `ToolContent`，看不到 trace、question、参考答案、examples 或生成 conversation。
候选生成器在修复时沿保存的原始 `messages` 继续。它必须对每条 finding 恰好返回一次 `accept`、`partial` 或 `reject`，并给出
理由。review 建议只是质量反馈；确定性的校验错误（包括硬 SQL 校验）不能靠 review decision 豁免。

候选失败相互隔离。被拒绝或延期的候选不会回滚已经发布的候选。Run 的 `status=succeeded` 只表示至少有一个候选产出了可发布
产物，并不表示所有计划候选都完成。检查 `candidate_outcomes`，确认候选是否全部为 `published`，以及是否存在 `rejected` 或
`deferred`。预算或截止时间中止时可以保留部分已发布产物，同时在候选 outcome 或 Run 的 error 状态中保留剩余工作被中止的原因。

## 检查校验和预算

校验会绑定每个 Tool 示例的参数，并把生成 SQL 的 AST 与已记录的成功查询比较。这只证明被示例覆盖的路径：不会重放变化中的
生产数据，不证明所有参数值，也不会因此把 `live_execution_verified` 设为 `true`。即使 reviewer 接受 finding，历史 SQL AST 校验仍然
必须通过。实际执行和答案效果需要在宿主环境中独立评测。

一次 Run 的预算由候选 inventory、候选生成、Tool review、结构化输出格式修复、质量修复以及后续模型请求共同消耗：

| 预算字段 | 默认值 | 上限 | 含义 |
| --- | ---: | ---: | --- |
| `max_model_calls` | `128` | `1024` | 整个 Run 的 provider 请求总数 |
| `max_candidates_per_family` | `32` | `128` | 每个 Experience、Tool 或 Skill Family 的 inventory 候选数 |
| `max_candidate_repair_rounds` | `2` | `8` | 单个候选额外质量修复轮数 |
| `max_output_tokens` | `16000` | `64000` | 每次 provider 请求的输出 token 上限 |
| `max_input_chars` | `400000` | `4194304` | 序列化输入和保存 messages 的预算 |
| `timeout_seconds` | `1800` | `7200` | Run 的总截止时间 |
| `previous_artifact_limit` | `30` | `100` | 召回后选择的可复用历史 Artifact head 数量 |
| `max_pending_per_scope` | `32` | `1000` | 单个 Scope 排队和执行中的 trace-learning Run 数 |

Supervisor 在发起模型请求前保守预留下一次额度。Worker 在模型调用中丢失响应或退出时，已预留额度不会退回，重启也不能再次消费。
`max_output_tokens` 按请求计算，而 Run 的 usage 会累计所有请求。

普通结构化 generation 的 `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MAX_REQUESTS=2` 表示 initial 1 次加 repair 1 次；改为 `3`
表示 initial 1 次加额外 2 次 repair。该单次 operation 限制仍受 trace-learning Run 总预算约束。

`usage.reserved_model_calls` 表示计入 `usage.model_calls` 的未确认预留；两者相减得到已确认请求数。已知在请求前发生的配置错误会释放预留；响应未知的请求保留额度，不冒充已确认模型用量。

## 准备一次推理

向 `POST /v1/context/prepare` 显式传入 `learned_tools=true` 和实际 `host_profile`：

```json
{
  "scope_id": "your-scope",
  "query": "CZE 有多少个站点？",
  "max_bytes": 16000,
  "assembly": {"sections": []},
  "learned_tools": true,
  "host_profile": {"kind": "datus", "dialect": "mysql", "database_name": "your-db"}
}
```

返回的 `learned_context` 包含选中的 Experience 文本、Skill 指令和完整 Tool 描述。只请求 learned context 时，返回
`status=ready`、`content=null`、`content_bytes=0`；旧请求不会出现新增字段。准备阶段保证 Skill 与精确 Tool revision 的依赖完整，
再在字节预算内加入 Experience，不会暴露缺少依赖的 Skill。

服务端调用方可以用 `learned_families` 独立选择三类学习制品。这个选择在候选排序和预算分配之前生效，覆盖旧的
`learned_tools` 开关；省略时保留旧调用行为。`[]` 关闭三类学习制品，`["tool"]` 只启用学习工具，
`["experience", "skill"]` 关闭学习工具。关闭 Experience 同时禁用普通 assembly 的 Experience 回退；其他 Memory、Profile 等
仍由 `assembly` 控制。只要选择非空的学习能力，就需要实际的 `host_profile`。

```json
{
  "scope_id": "your-scope",
  "query": "CZE 有多少个站点？",
  "max_bytes": 32768,
  "assembly": {"sections": []},
  "learned_families": ["tool"],
  "tool_limit": 3,
  "host_profile": {"kind": "datus", "dialect": "mysql", "database_name": "your-db"}
}
```

`tool_limit` 默认 3，上限 8，包含 Skill 依赖和独立匹配的 Tool。Skill 不会阻止其他匹配 Tool 入选，无依赖的长 Skill
也不会挤掉本可放入预算的相关 Tool。关闭 Tool 时，依赖学习 Tool 的 Skill 被跳过，独立方法仍可使用。完整依赖、参数契约和
执行程序必须整体放入预算，不会截断为不可调用的工具。

需要主动寻找工具时，调用 `POST /v1/tools/search`，传入 `scope_id`、`query`、`host_profile`，以及可选的 `limit`、
`max_bytes`。返回 `{"tools": [...]}`，每项包含完整使用契约、执行程序和精确 Artifact revision；无匹配正常返回空数组。
Python 客户端对应 `client.search_tools(SearchToolsRequest(...))`。接口沿用 Scope 和 Artifact 读取授权，只返回适合执行环境的工具，
不执行 SQL，也不调用模型。宿主负责将检索结果绑定成可调用工具。

PC 的 `max_bytes` 默认仍为 8,000，上限为 32,768 字节。调用方可显式提高请求预算；增加预算不会触发额外模型请求，也不代表
每次必须填满上下文。

召回会分页读取含已发布候选的 Run 的全部 active head，再按 query 相关性和剩余字节预算选择。当前使用 Scope 内的词法匹配，不使用语义向量索引，
因此不再只看最近 30 个含已发布候选的 Run；生成阶段仍受 Run budget 对历史 head 数量的限制。

对 Datus，传给 LLM 的 Tool description 会追加 `output_schema`，`args_schema` 保持原样。Tool 参数单独绑定，固定只读 SQL 通过当前请求的
Data Gateway 执行；数据源权限、结果限制和当前数据继续由 Gateway 控制。Tool 内没有隐藏的模型调用；评估宿主总模型请求数时，应计入
回退和最终答案请求。

候选一旦独立发布，即使同一 Run 仍在处理其他候选，也可以参与召回；草稿、拒绝及暂缓的候选不会被召回。

## 当前范围

当前宿主为 Datus，执行器为 MySQL/PostgreSQL 兼容的参数化只读 SQL，支持普通顺序的模型工具调用。动态 DAG、通用代码沙箱、跨宿主
MCP 执行和自动收集普通日志不在此流程中。Tool 读取受 Artifact 权限控制，没有通用 Tool 创建/替换接口。
