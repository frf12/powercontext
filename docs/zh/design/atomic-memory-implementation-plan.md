---
title: Atomic Memory 开发计划
---

# Atomic Memory 开发计划

**目标**：实现 RFC 1809 和 [实现设计](memory-artifact-and-search-projection.md)，将集合内 entry 改成独立制品，提供抽取、检索、生命周期、恢复和停服迁移。

**架构**：共享 Artifact 内容、head 和 lineage；新增 Family 状态表及一张 current 检索投影；所有入口进入同一个领域写入服务。旧接口只做能够实现的 API 适配，不保存兼容集合状态。

**技术栈**：Python、Pydantic、SQLAlchemy async、SQLite、OceanBase/seekDB、现有模型与 supervisor 接口。

**代码基线**：`673d44d6`。RFC 1809 已在 PR 中发布为 `33f5949e`，实现分支包含同一 RFC 补丁。

## 执行和 review 规则

- 开发 Agent 使用 `gpt-6.1-sol`、`ultra`，主 Agent 负责代码 review、接口协调和最终提交。
- 工作区：`/Users/rongfneg.frf/.codex/worktrees/atomic-memory/powercontext`；分支 `codex/atomic-memory`。
- 各开发者只修改分配的文件。共享文件先确认所有权，不覆盖其他人的改动，不自行提交或推送。
- 按 AGENTS.md 和 REVIEW.md 检查真实调用链、权限、数据保存及读取、并发和失败恢复。
- 本轮执行代码检查、类型检查及必要的生成步骤；未获得运行或新增测试的明确要求，不运行或增加测试。执行结果不得标成测试通过。
- OpenAPI 是契约来源，生成文件只能通过生成器更新。
- 领域模型和索引接口先对齐，再接入运行时；主 Agent 按模块和完整调用链 review，发现的问题交回开发 Agent 修复。

## 1. 领域内容、状态与生命周期

负责文件：`builtin/artifacts/atomic_memory/` 中的内容模型、错误、服务与恢复模型；`builtin/persistence/atomic_memory.py`、`atomic_memory_schema.py`；必要的公共 Artifact 写锁入口。

- [x] 定义 atomic-memory 内容、四态、read-set、准备结果、状态版本、合并输入及恢复结果。
- [x] 复用公共不可变内容和 lineage，只新增状态/current 两张 Family 数据表。
- [x] 提供创建、修订、遗忘、合并、预览及整组恢复；所有修改使用调用方事务。
- [x] 合并创建 C，冻结 A/B；首版 creation selector 关联首版精确 lineage。
- [x] 实现 A+B→C、C+D→E 的整组恢复；内容恢复生成新 revision，退休的 ID 不复活。
- [x] preview_token 绑定身份、操作、目标、终点 revision/state_version；直接恢复不要求 token。
- [x] 固定顺序锁 head，检查内容、状态和读写权限；同步公共治理摘要。
- [x] 向检索及接入 Agent 发布模型与服务接口，主 Agent review 状态转换和事务边界。

## 2. 当前检索投影

负责文件：`builtin/persistence/atomic_memory_index.py`、`{sqlite,oceanbase}/atomic_memory_index.py`；与领域 Agent 协调 current 表定义。

- [x] current 同行保存正文、向量、Scope、标签、权限、revision 和 state_version。
- [x] 写入和删除接收同一个 connection；退出在役即移除投影，恢复按最终状态重建。
- [x] 普通 search 与完整阈值枚举分开；后者不设总 k，不使用 ANN 截断证明完整性。
- [x] 全文和向量召回禁止业务 JOIN，资格条件在截断前生效，直接返回正文与精确引用。
- [x] 无向量模式使用全文；清除失效向量，不允许混用 profile。
- [x] 标签/授权投影刷新与权威变化同事务，辅助索引保持一致。
- [x] 主 Agent review SQL、后端能力声明、过滤和资格集合完整性。

## 3. 公共 Family、运行时和 API 接入

负责文件：公共 Family/records、`builtin/runtime/relational.py` 和 `application.py`、server、client、MCP、OpenAPI 及生成文件、现有消费者和配置中的注册点。

- [x] 注册 Family 及 writer；通用 Create/Replace 进入领域服务；禁止跨 Scope copy/publication 绕过。
- [x] 建立正式 Owner，接入状态、列表、搜索、合并、遗忘和恢复路由。
- [x] 实现设计第 9.2 节的旧接口矩阵：历史 citation、旧 target 最新读、标签 ETag、可空集合前提 remember、search/list 响应变化和 flush。
- [x] 在写入前拒绝旧集合 CAS、旧 citation 写入及集合修改；返回清楚的替代入口。
- [x] 新返回值使用真实 ArtifactRef，验证精确回读路径，更新受影响 SDK/MCP/消费者。
- [x] 更新 OpenAPI 并运行生成器，不手改生成代码。
- [x] 主 Agent review 对象授权、入参/出参、版本前提和完整入口链路。

## 4. Source 抽取与 supervisor

负责文件：`builtin/artifacts/atomic_memory/extraction.py`、`reconciliation.py`、`builtin/runtime/atomic_memory_processing.py`；共享运行时文件由接入 Agent 配合。

- [x] Source 窗口先生成候选，再召回同 Scope 内可读写的相关在役记忆。
- [x] 提示词按时间处理冲突，保留适用条件与证据，自动执行 create/revise/merge/noop。
- [x] 候选和阈值结果仅保存在 Worker 内存，分批交给模型，失败整窗口重做。
- [x] 协调同窗口多候选对同一记忆的动作，准备模型和 embedding 后才开启提交事务。
- [x] 一个事务内检查 lease/fence、Source 资格、read-set 和 cursor，提交内容/状态/投影、推进 cursor 并 complete。
- [x] 后台身份与显式请求遵守同一领域规则；不增加候选工作表、审批流或逐条提交。
- [x] 主 Agent review no-op、崩溃、并发以及新旧处理器切换。

## 5. 停服迁移

负责文件：Atomic 领域迁移资源和执行入口；CLI 共享文件协调所有权。

- [x] 核对 #1771 实现依赖；只提供本领域版本化迁移任务，不另建通用迁移框架。
- [x] 用冻结旧结构读取完整历史，按确定性身份和原 entry version 导入；保留旧历史只读。
- [x] 检查断链、分支、当前 head 落后和身份冲突；错误时阻止切换。
- [x] 迁移状态、Owner、标签、授权、引用及处理进度，重建当前投影。
- [x] 提供明确的离线执行及验证入口；普通服务启动不自动搬运旧业务数据。
- [x] 主 Agent review 可重复执行、错误恢复、旧引用读取和主键/索引一致性。

## 6. 整体接入与交付

- [x] 逐项对照实现设计第 12 节检查代码路径，区分静态结论和实际运行证据。
- [x] 核对新旧 HTTP/SDK/MCP 的能力与错误行为，更新必要的使用及升级说明。
- [x] 运行生成一致性、格式和类型检查，解决本次引入的问题；记录环境或外部数据库验证限制。
- [x] 主 Agent 完成 spec review 和 code review，修复全部确定的阻断问题。
- [x] 检查工作区与提交范围，保留用户主目录的原文档和无关文件；报告交付内容、验证结果及未完成事项。

## 验证范围

- Python lint、格式、类型检查，以及 OpenAPI 和 JS operations 生成一致性检查通过。
- OpenClaw、OpenCode、Pi 类型检查通过；Dsh 源码类型检查通过，完整包仍有 30 项既有测试类型错误。
- Dsh、OpenCode、OpenClaw 构建通过；Pi 已完成静态编译。Bub 的独立类型检查受 7 项宿主导入诊断影响。
- 本机 `prek` 启动退出码为 137；使用 `pre-commit 4.2.0` 执行同一份 hook 配置，全部静态 hook 通过。
- 已维护受影响的现有测试和 fixture，未新增或运行测试。SQLite/OceanBase 的功能、并发、迁移和端到端验收尚未执行。
- 普通向量搜索与抽取阈值枚举均使用精确 L2。原生向量索引已建立，查询尚未使用 ANN；计算成本为资格集合大小乘以维度，未做性能测量。
