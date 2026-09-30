---
title: Automatic Memory 独立记忆制品
---

- 提案名称：`automatic_memory`
- 起始日期：2026-09-30
- RFC PR：尚未提交
- 前置 RFC：[制品检索宽表与单表召回，#1803](https://github.com/oceanbase/powercontext/pull/1803)
- 修订 RFC：[0014](0014_memory_layer_design.md)、[0019](0019_local_source_memory_runtime.md)、
  [1345](1345_scope_organization_and_agent_integration.md)、[1652](1652_memory_quality_and_lifecycle.md)、
  [1718](1718_memory_capacity_contract.md)
- 相关 RFC：[1417](1417_topic_memory.md)、[1549](1549_artifact_family_unification.md)
- 实现参考：[Automatic Memory 实现方案](../design/memory-artifact-and-search-projection.md)

# 摘要

本 RFC 引入 `automatic-memory` family，将一条可独立维护的记忆表示为一个 Artifact。Scope 下的记忆分别拥有身份、
revision、证据和生命周期；抽取通过有界检索协调新增、修订、归并和冲突。它遵守前置 RFC #1803 的公共检索契约，
每条当前可检索记忆在自己的 family 检索宽表中占一行。旧 `memory` 集合及精确历史引用保留，通过独立、版本化的离线
迁移工具转换全部历史；升级需要维护窗口，新 Runtime 不承担在线搬运、双写或新旧写入权交接。

# 动机

## 集合模型增加了一层身份和版本

[RFC 0014](0014_memory_layer_design.md) 的 Memory Artifact 保存完整 manifest，每个成员引用一个不可变 entry
version；修改 entry 后再提交集合 revision。[RFC 1345](1345_scope_organization_and_agent_integration.md) 引入了
Scope，但保留一条 active Memory 推进线。于是，一条事实同时具有集合 ArtifactRef、entry ID、entry version 和集合内状态。

Scope 已经承担归属与组织，Artifact 已经提供独立身份、历史、lineage、授权和标签。长期需要维护的是一条事实，将该事实
直接表达为 Artifact 可以减少 Memory 专有的身份、版本和引用规则。

## 单条修改仍保存全量目录

当前实现已经具备 entry 修订、精确去重、历史读取、容量限制、可选 compact，以及按变化 entry 增量更新的检索投影。
问题不是每次复制全部正文或重写全部向量，而是每次有效变更仍构造、排序、序列化并保存全量 manifest。平均目录大小为 N、
有效写入次数为 R 时，目录历史约为 O(R × N)。修改不同 entry 的两个任务也共享一个集合 head 的并发前提。

[RFC 1718](1718_memory_capacity_contract.md) 限制单次目录规模，但没有改变版本化单位。独立记忆可以让一次修改只增加
受影响事实的历史，而不为未变化的其他事实重复保存目录项。

## 抽取需要有界的跨窗口协调

当前内置抽取把所选 Memory head 的全部 active entry 交给模型，支持 add/revise，也能修订过去 Source window 产生的
记忆。它不是只能处理当前 window。缺口是：旧记忆上下文随活跃集合增长；缺少候选生成后的独立相关检索；多条归并和未决
冲突没有完整的动作契约。增加一个 entry replace 接口不能独自解决这些问题。

## 检索已有公共前置契约

[RFC #1803](https://github.com/oceanbase/powercontext/pull/1803) 处理各制品的搜索字段同表、当前投影一致性、索引和
后端验收。Automatic Memory 依赖并落实这份契约。本 RFC 只定义记忆领域模型、抽取和升级，不重新定义另一套搜索规则，
也不把 Experience、Skill、Topic Memory 的检索改造作为本 RFC 的领域职责。

# 使用说明

## 一条记忆就是一个 Artifact

Automatic Memory 可以由 Source 自动抽取，也可以由有权限的调用方手工写入。`automatic` 不限制生产入口。

```text
Scope: project-a
├── automatic-memory / M1@1：生产发布需要负责人审批。
└── automatic-memory / M2@1：默认部署区域为华东。
```

用户补充“生产发布还需要安全检查”后，修订 M1 得到 M1@2；M2 仍为 @1。搜索返回 M1@2 的正文和精确 ArtifactRef；
使用 M1@1 仍可读取原文。无需再提交集合 revision，或组合集合 ref、entry ID 与 entry version 来引用这条事实。

## 新输入与相关记忆协调

处理新的 Source 时，先生成候选，再检索同 Scope 和合法读取范围中的相关当前记忆。例如“生产发布要先做安全检查”应当
与 M1@2 协调，而不需要把 Scope 中全部活跃记忆交给模型：

| 判断 | 结果 |
| --- | --- |
| 新的独立事实 | create：创建独立 Artifact |
| 同一事实有补充或纠正证据 | revise：推进目标的 revision |
| 多条事实适合整合 | merge：提出保留对象、合并内容和停用对象，按评审策略执行 |
| 已充分表达且没有新证据 | noop：不产生版本 |
| 无法判断适用条件或先后关系的矛盾 | conflict：保留候选、相关 refs 和证据，等待处理 |

相似度用于选择候选，不能证明两条事实应合并。语义归并默认需要逐次评审，自动归并默认关闭；有限的 Scope 预授权例外
见参考级说明。预算耗尽时保留可继续处理的任务，不能把剩余工作当作 noop，也不承诺一次检索消除所有重复。

## 遗忘、恢复和归并不抹去历史

普通 `forget` 产生 inactive 版本，退出默认检索；`reactivate` 可以恢复同一身份。恢复 M1@1 的内容会创建 M1@3，
并记录恢复来源，而不是把 head 从 2 倒退到 1。已 retired 的身份不能普通 reactivate，重新采用内容需要新身份及来源。

归并 M3 到 M1 后，M3 的旧精确引用仍返回它当时的正文，不跳转到 M1 的最新内容。向量只服务当前检索，恢复历史正文可能
需要重新 embedding；读取历史正文不依赖 embedding 服务。

## 旧部署通过维护窗口升级

```text
plan：盘点和检查
  → 停止全部 API 写者及 Worker，备份
  → apply：转换历史、建立映射与检索投影
  → verify：校验身份、正文、状态、引用、授权和处理水位
  → cutover：写入完成标记，启动新版本
```

迁移工具可以与公共检索迁移在同一个维护窗口运行。迁移后新 Runtime 的当前业务只使用 `automatic-memory`；旧
`memory` 保留有限的精确历史读取。旧 Source 不重新抽取，旧 Handoff、Experience 的正文与引用也不改写。

旧 API 中作为当前 family 选择器的 `memory` 名称可以集中解析为 `automatic-memory`，但旧集合 ID、revision、ETag
和 manifest 写入不是新单条契约。没有明确适配规则的请求返回 `upgrade_required`；历史引用绝不做字符串替换。

# 参考级说明

## 不变量与 RFC 关系

1. 每条逻辑记忆使用 `(scope_id, family="automatic-memory", artifact_id)`，精确版本使用 ArtifactRef。
2. 正文、直接证据或生命周期有效变化时，只生成受影响对象的 revision；不维护全 Scope manifest。
3. 已提交历史和引用不可变；当前权威 head 与派生检索投影遵守 RFC #1803，缺失投影不能用临时正文 JOIN 掩盖。
4. Source 处理、语义协调和操作记录均有预算；Source cursor 的推进必须有已提交结果或持久待处理记录。
5. 迁移是显式离线工具的职责；新服务启动依赖格式、迁移和投影的持久完成状态。

| 既有 RFC | 本 RFC 的影响 |
| --- | --- |
| 0014、0019 | 保留不可变历史、精确证据和消费边界；以新 family 替代集合 manifest 与 entry 专用版本 |
| 1345 | 保留 Scope 归属；新 family 不再只有唯一集合 head，Source cursor 不按每条记忆拆分 |
| 1549 | 使用 Family writer 和普通 Artifact 能力接入独立记忆 |
| 1652 | 关系改用精确 ArtifactRef，状态由独立 revision 表达；新增下文有限的归并预授权例外 |
| 1718 | 保留确定性容量拒绝和补救操作；集合目录预算改为 Scope 当前记忆容量，旧 compact 转入历史兼容 |
| #1803 | 是本 RFC 的前置契约；定义检索宽表、字段、一致性、重建和后端验收 |

## 内容、证据与状态

新内容格式为 `powercontext.automatic-memory.v1`，至少包含 kind、text、state 和可选的 superseded_by。正文放在
`pc_artifacts.content`，当前指针继续由 `pc_artifact_heads` 保存。两个公共表不因本提案新增 search_content。
旧 entry 表只供历史读取与迁移，新 family 不向旧 entry 表写入。

| 状态 | 默认检索 | 恢复语义 | 通用 head 治理值 |
| --- | --- | --- | --- |
| active | 参与 | 可正常修订 | active |
| inactive | 不参与 | 可显式 reactivate | deprecated |
| retired | 不参与 | 采用内容需显式新建身份并保留来源 | retired |

状态属于 revision，通用治理入口必须调用 Family writer，不能仅修改 head。revise 继承前驱直接证据并追加本次证据；
仅改状态仍保留正文证据；merge 保存参与对象精确 refs 及有效直接证据；restore 保存所恢复版本证据和 restored_from。
保留历史来源不表示按今天的规则重新授予生成资格，未知发生时间也不能用迁移时间填充。

归并后的 inactive 对象可以引用同 Scope 的精确 superseded_by，禁止自引用和替代关系环。reactivate 必须显式清除或
重新确认该关系。操作记录只保存受影响对象的 before/after refs、原因、执行者和幂等身份。整组补偿要求全部对象仍匹配
原操作 after refs；存在后续变更时报告冲突，不覆盖后续工作。retired 身份不能通过补偿绕过不可恢复语义。

## 抽取、授权与并发

Source 仍由 Scope 内既有处理绑定消费；绑定选择处理范围和策略，不选择唯一集合。一次处理可以改变多条独立记忆，
不会为每条记忆创建独立消费 cursor。

```text
Source window → 候选生成 → 本批去重 → Scope 内相关检索
              → create/revise/merge/noop/conflict → 校验 → 原子提交
```

预算覆盖输入、候选数、每候选检索量、相关对象总数、上下文、模型调用和累计 token。无 embedding 时使用公共契约支持的
有界全文检索；不能以缺少向量为由加载全部 active 记忆或放宽归并授权。检索资格遵守公共契约；未决冲突及其在 Prepare 中的表达由本 RFC 的动作契约与 RFC 1652 约束。

普通 create/revise 沿用既有抽取授权与 write gate。RFC 1652 的 L2 语义维护默认仍须逐次评审。本 RFC 明确修订其中的
一个边界：Scope 管理者可以预授权“同一对象、同一适用范围、内容兼容且完整保留证据”的归并，并限定参与数量与操作预算。
自动 merge 默认关闭；每次执行保存授权者、策略版本和适用理由。未决冲突、HOLD、强制评审策略、超出预授权范围和不可逆
退休仍须逐次评审。显式 merge 指令同样受权限与强制评审规则约束，模型评分不是授权。

每个 revise/merge 目标校验 expected revision，多目标操作整体成功或失败。幂等键独立于相似度，同一次请求重试不能
产生第二条记忆。自动协调还必须防止检索后发生并发 create：初始实现使用 Scope Memory 写入代数，从相关检索开始到提交
若代数变化就重新协调。它是 O(1) 元数据，不是集合 revision。独立显式单条修订只校验本条 CAS，但仍推进代数。

证据、授权和治理变化若影响候选资格或生成资格，也必须使协调前提失效，或在提交前通过对应 generation 复核。模型与
embedding 在事务外完成；提交时校验资格、lease、CAS、代数和幂等，再原子写入 revision、lineage、head、容量、检索
投影、操作记录及处理状态。只有最终结果或可恢复 pending/conflict 记录与 cursor 一起持久化后，才推进输入位置。

## 容量与公共检索接入

Scope active Automatic Memory 上限提议延续 5,000 条，允许显式配置。计数与 create、reactivate、deactivate、merge
按整组净变化原子更新。已超限时允许不增加超限维度的修订与减量补救；不扫描全 Scope 计算每次写入额度。
manifest 项数/字节上限不再适用，历史容量仍需单独治理，不以物理删除隐式满足活跃容量。

Automatic Memory 使用自己的当前检索宽表，每个可检索 Artifact 一行，含当前 ref、正文、kind、状态相关信息，以及公共
契约要求的完整匹配、过滤、排序、返回字段；启用向量时同一检索单元携带匹配的 embedding。FTS-only 部署仍返回完整结果。
可见性、标签、生命周期等字段的更新和重建必须遵守 #1803，不允许搜索时关联权威表补条件或正文。后端物理布局、快照、
索引准入和测量要求由 #1803 定义，本 RFC 不另作例外。现有旧 Memory 投影不复用为新 family 的业务写入表。

## API 与消费者

新 list/get/replace/history 采用单条身份；search 返回正文与精确 ref；flush 返回处理状态、操作 ID 和受影响 refs，
不再返回唯一集合 revision。Prepare 去掉同 Scope 命中共享集合 ref 的假设；新的 Handoff、Experience、Dream、Tags、
ACL 使用新 Artifact 身份。HTTP、SDK、MCP 和 Prompt 动作 schema 同步升级，OpenAPI 仍为 HTTP 契约源。

旧名称兼容集中在 Artifact API 共用的解析入口：

| 旧请求 | 切换后的处理 |
| --- | --- |
| `memory` 作为当前 list/search 的 family 选择器 | 解析到 `automatic-memory`，使用新的返回契约和分页 cursor |
| `memory` 名称携带明确支持的新单条写入协议 | 解析到 `automatic-memory`，按新协议鉴权与执行 |
| 指定旧 ArtifactRef、MemoryCitation 或历史 revision | 保持旧 family 和精确历史身份，不重写引用 |
| 旧集合 ID/latest、集合 ETag/CAS、manifest 或 entry 写入协议 | 仅执行明确列出的专用适配；其余返回 `upgrade_required` |

鉴权和执行必须使用同一个解析结果，响应明确返回真实 `automatic-memory` family。新旧分页 cursor 不互用；纯名称
兼容不承诺旧响应结构等价。离线切换后没有按 Scope 的新旧业务路由，也没有两个可写权威。旧纯集合 ArtifactRef 仍表示
当时的集合快照，不能随意映射到其中一条新记忆。

## 离线升级与精确历史

迁移工具与服务分开发行并明确支持的源格式和目标格式。只要承诺旧版本升级，就必须提供可获得的工具与版本路径；
“迁移一次”不意味着可以删除仍被支持版本需要的工具。普通 Runtime 不自动转换旧库，也不包含双写、在线追平或切换状态机。

执行 apply 前实际停止所有旧 API 写者和 Worker，并保存一致备份；旧进程不认识新 marker，不能仅靠 marker 阻止写入。
工具按 plan/apply/verify/cutover 分阶段运行，保存有界检查点、配置摘要和目标摘要，重复执行已完成批次必须核对而非重复创建。
DDL 结果单独验证，不能依赖一个事务回滚全部 OceanBase DDL。新服务对未迁移、部分迁移、配置不匹配或投影未就绪的数据
拒绝普通业务启动；空库初始化也必须写入明确就绪记录。迁移中仅允许受控工具及明确支持的精确历史读取。

身份映射包含旧容器，不能假设 entry ID 在 Scope 内唯一：

```text
(scope_id, legacy_collection_id, legacy_entry_id) → automatic-memory artifact_id
```

映射确定且可恢复，碰撞阻止提交。工具盘点全部容器、回放全部集合 revision，仅在某条 entry 的正文、直接证据、状态或
成员资格变化时生成它自己的 revision：

| 旧历史 | 新历史 |
| --- | --- |
| C@1：E1_v1 active | M1@1 active |
| C@2：仅 E2 改变 | M1 仍为 @1 |
| C@3：E1_v2 active | M1@2 active |
| C@4：E1_v2 inactive | M1@3 inactive |
| C@5：compact 移除 E1 | M1@4 retired，保留全部历史 |

新 revision 不等于旧 entry version。不能只迁最终 manifest，也不能把 compact 过的条目恢复成普通 inactive。
未解释的成员消失、缺失版本、hash 不匹配和历史断档阻止 cutover；孤立但可能被引用的旧记录保留并报告。
迁移验证旧证据，不调用模型，不用当前资格重写旧 provenance；imported_at 与历史发生时间分开保存。

旧 MemoryCitation 先在指定旧 manifest 中校验成员、entry version 与 hash，再按变化区间映射：

```text
(Scope, 旧集合, entry, [起始集合 revision, 结束集合 revision)) → 精确新 ArtifactRef
```

区间只覆盖实际成员关系，compact 缺席区间不能接受旧 entry citation，退休事件另存迁移来源。映射不展开 entry × 全部
集合 revision 的矩阵。旧集合、entry、Handoff 和 Experience 的不可变正文与 hash 保持不变，alias 不绕过现行访问控制。

entry 标签与授权的主体、权限、到期和撤销语义一对一迁移；集合标签保留原作用域，不复制为每条授权。处理 cursor、
高水位、已接受任务和计数保留，旧 lease 失效，不重跑已消费 Source。不兼容自定义 Prompt 必须升级或阻塞迁移。
多容器合并后超过 Scope 容量须由 plan 报告并显式配置，不能丢数据。匹配输入和 profile 的当前向量可复用，不要求生成
全部历史向量；这些要求与公共检索迁移在同一维护窗口协调完成。

cutover 前校验冻结快照未变、全部映射与投影就绪，再写入完成标记。新版尚未接受写入时可以恢复备份；接受新写入后旧
集合不再同步，默认只做前向修复，降级需要单独设计的停写逆迁移。记忆内容恢复与部署降级是不同操作。

# 缺点

- 升级改变身份、API、Prompt、消费者和授权目标，需要停服与历史转换。窗口与磁盘空间必须按真实数据演练。
- 保留旧历史、新 Artifact 历史和 alias 会增加迁移后的存储；停止新增全量 manifest 不会立即释放旧磁盘。
- 有界检索可能漏掉相关记忆，语义归并可能丢失限定条件，需要证据、评审、冲突记录与后续维护，不能承诺全局无重复。
- 独立 CAS 减少无关事实的冲突，但初始 Scope 代数可能让繁忙 Scope 的自动协调反复重试，需测量和预算控制。
- 直接证据、状态 revision、投影及 API 兼容集中到 Family writer，遗漏一个入口仍会破坏一致性；应按公共契约验收。
- 旧引用兼容会保留有限历史解析代码，独立迁移工具也需要明确支持周期，不能把停服升级理解成无需维护升级资产。

# 设计理由与替代方案

**直接提升 entry 为 Artifact。** Scope 管归属、Artifact 管身份和版本，与其他制品一致；实际变化决定历史增长。

**沿用 `memory` family，改内容 schema。** 可减少 family 名称，但使同 family 同时有集合和单条语义，列表、能力发现和
历史解析都需区分格式。新 family 明确区分领域身份，旧名称只在受控 API 入口兼容；命名不是在线迁移机制。

**保留集合并做增量 manifest，或拆成多个集合。** 前者降低目录复制，后者限制单容器规模，但仍保留集合 CAS、entry 专用
引用、容器路由和跨容器查重。如果集合快照是核心需求，这是可比较方案；本 RFC 选择独立事实作为维护单位。

**只迁当前内容，旧历史完全旁路。** 可缩短初次升级，但新身份历史从导入才开始，用户必须跨两套历史查询。完整回放为
每条记忆提供连续的正文和状态历史，旧集合快照仍保留；因此接受更多离线迁移工作。

**运行时在线搬运、双写和按 Scope 切换。** 能减少维护窗口，却要求服务长期支持迁移中的追平、混版本、失败恢复及
新旧写入协调。分发二进制的产品只要支持历史版本直接升级，后续发布就仍需携带或调用该能力；不能因某个实例迁完就删除。
已有维护窗口可接受，因此当前选择独立离线工具。

**不做改造。** 可继续用容量和 compact 控制单集合大小，单独补相关检索，但两层版本、全量目录历史和专用引用持续存在。

# 先例

本 RFC 复用 PowerContext 已有职责，不假设这些先例已经实现本提案：

- [RFC 1345](1345_scope_organization_and_agent_integration.md)：Scope 归属边界。
- [RFC 1417](1417_topic_memory.md)：候选生成、相关 Topic 选择和原子发布，为有界协调提供参考。
- [RFC 1549](1549_artifact_family_unification.md)：Family 能力与统一 Artifact 读写入口。
- [RFC 1652](1652_memory_quality_and_lifecycle.md)：证据、有效性、相似与冲突的区分，以及评审与可恢复停用。
- [RFC 1718](1718_memory_capacity_contract.md)：full manifest 的成本、容量拒绝和 compact 历史保护。
- [RFC #1803](https://github.com/oceanbase/powercontext/pull/1803)：各 family 当前检索宽表的公共契约，先于本提案实施。

完整迁移步骤、存储成本与验收清单见[实现方案](../design/memory-artifact-and-search-projection.md)。

# 未解决问题

以下问题须在 RFC 合并前明确：

1. 新 HTTP/SDK 协议的版本表达、旧 `memory` 名称允许的写入适配清单，以及旧精确读取与工具支持的版本范围。
2. Scope 容量配置的层级、多旧容器合并时的配置入口，以及 5,000 条默认值对目标部署的适用性。
3. pending/conflict/review 动作使用既有 Candidate 的何种扩展，以及合并补偿无法覆盖的下游影响如何表示。
4. 首批协调预算、最大重试量及高并发验收负载；更细粒度失效机制可以替换 Scope 代数，但不能省略其正确性责任。

目标后端版本、索引、单表过滤支持及读一致性由前置 RFC #1803 确定。物理删除、跨 Scope 自动归并、在线升级与隐式全
Scope 快照回滚不属于当前交付；实现批大小、命令名称和排期不由本 RFC 自动确定。

# 后续可能性

- 增加有界后台维护，发现即时抽取未召回的近重复与冲突，继续沿用证据与评审边界。
- 增加按精确输入和 profile 缓存的历史向量，降低恢复成本；历史正文仍独立可读。
- 单独定义历史保留、物理清理和显式 Scope 导出，避免在每次单条写入时重新引入全量 manifest。
- 商业版可提供有支持范围和服务保障的在线升级。为使后续二进制删除历史在线迁移代码，可定义**栅栏版本 B**：
  历史版本必须先升级到 B，由 B 携带完整搬运、追平、切换和恢复能力；只有迁移完成并持久记录完成状态，才能继续升级。
  后续版本仅保留数据格式和完成状态的轻量门禁，跳过 B 或未完成迁移时拒绝启动。B 的发布物、工具和升级说明在承诺支持
  的期间保持可获得并作必要维护。“安装过 B”不是通过栅栏，单个实例完成迁移也不解除产品对其他旧实例的升级承诺。
  这项商业能力不属于本 RFC 当前实现要求。
