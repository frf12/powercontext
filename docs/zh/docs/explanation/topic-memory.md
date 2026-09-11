---
title: 理解 Topic Memory
description: 了解 Topic Memory 如何从 Source 增量维护长期主题，并在检索时渐进式披露。
---

# 理解 Topic Memory

Topic Memory 是 PowerContext 中用于维护“长期主题”的 Artifact Family。它把跨会话、跨任务的相关 Source
整理成一个持续演进的主题，而不是把每条证据拆成互不关联的 Memory entry。一个 Topic 包含标题、概要和详细正文；
同一主题更新时保留稳定的 `artifact_id`，并发布新的不可变 Revision。

## 它解决什么问题

普通 Memory 适合保存可独立检索的事实、偏好、决定、约束和工作笔记。长期主题则需要把多条证据组织成一个可以
持续修订的整体，例如一个架构主题同时包含背景、关键决定、当前实现和来源。

Topic Memory、Memory 和 Handoff 的边界如下：

| 需要保存的内容 | 使用 |
| --- | --- |
| 后续工作可以独立使用的事实、决定或约束 | Memory |
| 跨 Source 组织、会持续演进的主题 | Topic Memory |
| 把当前任务目标、进度、阻塞和下一步交给接手者 | Handoff |

Topic Memory 不替代 Memory，也不改变 Handoff 必须由用户主动选择和继续的行为。三者可以在同一个 Scope 中共存。

## 从 Source 到检索的生命周期

Topic Memory 的主链路是：

```text
Source → Pending → 后台 Worker → Topic Revision → 检索投影
```

1. **Source**：消息、对话或文档先写入指定 Scope 的不可变 Source Journal，并获得稳定的 SourceRef。
2. **Pending**：Source 写入只标记 Topic Memory 可能落后，不在写入请求中同步调用模型。
3. **后台 Worker**：Worker 按 Topic Memory 自己的 Cursor 选择有界、连续的 Source Window，结合历史 Topic 候选，
   决定新建、更新或不处理。处理、切片、检索和向量生成都在后台完成。
4. **Topic Revision**：发布结果由服务端控制 identity、Revision、操作类型和证据关联。新 Revision 不可变；只有
   当前部署启用的全部检索投影准备好后，才会原子地替换旧的可检索 Revision。
5. **检索投影**：当前 Revision 进入该 Scope 的 Topic 检索。处理失败会保留 Cursor 并重试，不会用不完整结果替换
   当前 Revision。

Source Window 是本轮处理的输入边界，不是一个需要保存的 Artifact，也不等于某个 Topic 的全部证据。服务端只接受
模型返回的、确实位于 Window 内的 evidence ID，并将它们映射为精确 SourceRef。

## 渐进式披露

Topic 检索参与相关性判断的字段包括标题、概要和详细正文 chunks，但默认返回保持紧凑：

1. `search_topic_memory` 或 HTTP 搜索返回精确 `ArtifactRef`、标题、概要和可选正文片段。
2. Agent 根据这些信息判断主题是否值得展开。
3. 需要完整内容时，使用搜索结果中的同一个 `ArtifactRef` 调用 `get_topic_memory`，读取精确 Revision 和 Source lineage。

不要只拿 `artifact_id` 再读取最新 Head。主题可能在搜索和读取之间产生新 Revision；继续使用精确引用，才能保证检索到
的内容和展开的内容一致。`POST /v1/context/prepare` 也只自动加入紧凑命中，不会把完整 detail 注入每次上下文。

## 自动处理与显式 flush

Topic Memory 自动波次默认关闭。需要定期处理时，设置正数的
`POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS`；需要立即请求后台处理时，调用：

```http
POST /v1/topic-memory/flush
Content-Type: application/json

{"scope_id":"project:quickstart"}
```

该请求只持久化处理意图，不等待模型和索引完成：

- `{"status":"accepted"}`：请求已接受，后台处理尚未必完成；
- `{"status":"idle"}`：调用时 Cursor 已覆盖当前 Source Head，可能是 Scheduler 或另一次 flush 已经处理。

多个并发 flush 会合并，不会为每次调用创建可查询的一次性 Job。完整的运行配置见[配置参考](../reference/configuration.md)。

## 如何检索和展开

搜索当前 Scope 的 Topic：

```http
POST /v1/topic-memory/search
Content-Type: application/json

{"scope_id":"project:quickstart","query":"后台制品处理","limit":10}
```

将搜索结果中的完整 `artifact` 引用原样传给精确读取：

```http
POST /v1/topic-memory/get
Content-Type: application/json

{
  "scope_id":"project:quickstart",
  "artifact":{
    "family":"topic-memory",
    "artifact_id":"<from-search-result>",
    "revision":<from-search-result>
  }
}
```

HTTP 请求、响应和错误契约见 [HTTP API](../reference/http-api.md)。面向 Agent 的 MCP 只提供只读的
`search_topic_memory` 和 `get_topic_memory`；`flush_topic_memory` 保持为 HTTP-only 操作。

## Scope、检索形态与 Dashboard

- 首版的生成、检索和精确读取都限定在一个 `scope_id`，不执行跨 Scope 检索。
- 未配置 Embedding 时，部署使用 FTS-only；要启用 vector 或 hybrid，需要在启动时提供完整且匹配的 Embedding
  profile、维度和向量基础设施。已有 FTS Topic 不会因为后来增加配置就自动回填向量。
- 检索形态由部署配置拥有，调用方不能在 Topic 搜索请求中任意切换检索模式。
- Dashboard 的 `/topics` 页面是只读管理视图：可以按最近发布浏览或搜索，再读取精确 Revision；页面不提供创建、编辑、
  审核、flush、发布、退役或删除操作。

Dashboard 的 scope 列表只是 UI 的可发现范围，不是通用授权边界。页面和 private Topic support route 的行为见
[Server Web UI：浏览 Topic Memory](../../development/server-web-ui.md)。

## 验证路径与常见问题

建议先按[完整功能 Quick Start](../how-to/full-capability-runtime.md)完成 Generation、Scope、数据库和 Dashboard 配置，
再按以下顺序验证：

1. 确认 Source 写入的 `scope_id` 与检索、Dashboard 使用的 Scope 完全一致。
2. 写入一条明确描述长期主题的 Source，调用 Topic Memory flush，或等待已配置的自动波次。
3. `accepted` 后等待后台 Worker 完成；它不是已发布 Revision 的确认。
4. 搜索 Topic，记录命中的精确 `artifact`，再用该引用读取完整 detail 和 SourceRefs。
5. 检查搜索响应中的实际 `mode`，不要把“有命中”误认为 vector 或 hybrid 已启用。

如果一直没有 Topic，先检查 Generation 配置、Worker 日志、Cursor 和 Pending；不要删除数据库或手动推进 Cursor。
如果只有 FTS，检查 Embedding model、profile ID、维度和已有投影是否完整。完整设计和故障恢复边界见
[Topic Memory RFC](../../rfcs/0000_topic_memory.md)；不要把 RFC 的设计意图误认为每个部署都已经完成配置。
