---
title: 从正确轨迹学习经验、Skill 和 Tool
description: 用户显式导入完整正确的轨迹，通过 Supervisor 学习并向 Datus 提供可执行 SQL 工具。
---

# 从正确轨迹学习经验、Skill 和 Tool

此实验能力接受用户选定的完整、正确执行轨迹。PowerContext 从实际调用参数和结果中生成
Experience、Skill 和参数化 SQL Tool，保存在共用的 Artifact 表中。Skill 引用 Tool 的精确版本；
Tool 保存执行接口和 SQL 程序，Skill 保存如何使用工具的说明。

## 开启独立试验环境

在 Server 的 Runtime 配置中设置：

```json
{
  "trace_learning_enabled": true,
  "artifact_processing_families": ["tool"],
  "dream_enabled": false,
  "tool_max_workers": 1,
  "tool_worker_timeout_seconds": 600
}
```

同时配置可用的 `inference.generation_model`、对应认证及数据库。首轮试验使用独立数据库；
已有部署的 processing capabilities 和 binding manifest 仍受现有部署迁移规则约束。
API 与后台拆分部署时，两侧必须声明相同的 Tool 能力并共用持久化数据库。

`tool.trace-learning.v1` 复用 ArtifactProcessingSupervisor 的排队、子进程、并发额度和任期校验。
LearningRun 保存学习阶段、模型用量和检查点。普通 Source 写入没有 Tool 扫描器，也不会触发此学习。

## 导入与观察结果

调用 `POST /v1/scopes/{scope_id}/trace-learning`，传入：

- `idempotency_key`：同一导入的稳定标识。
- `host_profile`：`kind=datus`、实际 SQL 方言和数据库名称。
- `traces`：每条包含 `trace_id`、`question`、`final_answer`、`context` 和完整 `tool_calls`。
- 每次调用保留 `call_id`、`name`、`arguments`、`result`、`succeeded`。最终正确的轨迹可以包含失败的中间尝试。

保留 Datus `read_queries` 的原始参数和结果制品；不要把多条查询伪装成新调用。
生成工具的验证示例使用 `query_index` 指向原调用中的查询，`arguments` 是生成工具的参数。
完成标签不用于替代正确性判断：完整正确是调用方选择轨迹时应满足的前提。

初次接受返回 202。通过 `GET /v1/scopes/{scope_id}/trace-learning/{run_id}` 读取状态。
重复提交相同标识和内容复用已有 Run；相同标识对应不同内容会被拒绝。成功返回 E/S/Tool 的精确引用。
同一方法再次导入时，生成器可沿用已有 key 更新版本；写入检查原用户权限与预期 revision。

验证目前将生成工具绑定示例参数后的 SQL AST 与已记录成功查询比较，检查受覆盖路径的一致性。
它不重放生产数据，也不要求当前结果等于历史数值。`live_execution_verified` 不因此变为 true。
工具的实际效果需要在 Datus 的当前数据源上执行并独立评测。失败 Run 不替换已发布的可用产物。

生成 SQL 无法重现记录示例时，同一个 Run 可以将失败候选和精确差异反馈给模型修正。
失败候选保留在 Run 检查点中，不发布为 Artifact。每次尝试都消耗 `max_model_calls` 额度
（默认共三次），共享原有截止时间，Worker 重启也不会重置额度。`max_output_tokens` 限制每次生成请求，
用量统计累计全部尝试。修正结果仍须通过完整校验，包括此前已经验证的示例。
权限、轨迹完整性及其他验证错误不进入这条 SQL 纠错循环。

## 将三类产物用于一次推理

向 `POST /v1/context/prepare` 增加显式选项：

```json
{
  "scope_id": "your-scope",
  "query": "当前用户问题",
  "max_bytes": 16000,
  "assembly": {"sections": []},
  "learned_tools": true,
  "host_profile": {"kind": "datus", "dialect": "mysql", "database_name": "your-db"}
}
```

`learned_context` 同时返回经验文本、Skill 正文及完整 Tool 描述。调用方在模型请求前注入文本，
把 Tool 的名称、说明和参数 schema 注册给模型，把实现交给执行适配器。
只返回结构化上下文时，`status=ready`、`content=null`、`content_bytes=0`；旧请求不出现新增字段。

准备阶段先为完整 Skill 及其精确 Tool 依赖分配字节预算，再装配普通文本；同一 Experience 不重复注入。
不匹配实际方言/数据库或缺少必要 Tool 版本的 Skill 不会暴露。接口不会截断一半后交给模型。

Datus 适配器通过当前请求的 Data Gateway 执行固定只读 SQL，参数单独绑定。
数据库访问权限、结果限制和当前数据源继续由 Gateway 控制。Tool 内没有额外模型请求。
工具出错时 Agent 可以回退普通工具；评测需要把回退及最终答案请求计入总模型请求数。

## 当前范围

当前宿主为 Datus，执行器为 MySQL/PostgreSQL 兼容的参数化只读 SQL。Skill 可以引用多个工具，
普通顺序调用由 Agent 决策。动态 DAG、通用代码沙箱、跨宿主 MCP 执行和自动收集普通日志不在此能力中。
召回采用当前 Scope 中近期成功 Run 的有限目录和词法匹配；它不等价于完整历史工具池的语义索引。
Tool 可通过 Artifact 读取接口读取，但不开放通用创建/替换写接口。
