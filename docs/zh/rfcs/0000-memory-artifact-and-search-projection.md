---
title: Memory 单条制品模型与当前检索投影
---

- 提案名称：`memory_artifact_and_search_projection`
- 起始日期：2026-09-30
- RFC PR：尚未提交
- 修订 RFC：[0014](0014_memory_layer_design.md)、[0019](0019_local_source_memory_runtime.md)、
  [1345](1345_scope_organization_and_agent_integration.md)、[1652](1652_memory_quality_and_lifecycle.md)、
  [1718](1718_memory_capacity_contract.md)
- 相关 RFC：[1417](1417_topic_memory.md)、[1549](1549_artifact_family_unification.md)
- 实现参考：[Memory 单条制品模型与当前检索投影实现方案](../design/memory-artifact-and-search-projection.md)

# 摘要

本 RFC 提议将 Memory 的版本化单位从“一个 Scope 的记忆集合”改为“一条独立记忆”：Scope 下直接存放多个 Memory
Artifact，原来的逻辑 Memory Entry 成为 Artifact，其正文、证据和状态变化由独立 revision 表达。抽取通过有界检索查找
同 Scope 的相关记忆，再提出新增、修订、归并或冲突处理动作。检索复用现有当前投影，补齐结果所需内容，避免每次召回
再关联历史正文或 head。迁移保留旧版本和精确引用，并为旧客户端提供明确的升级边界。

# 动机

## 集合曾经承担的职责，与 Scope 和 Artifact 的现有职责重叠

[RFC 0014](0014_memory_layer_design.md) 定义的 Memory Artifact 是一个带完整 manifest 的集合。每个 manifest 项引用
一个不可变 entry version；修改 entry 后，再提交新的集合 revision。这个设计能提供集合快照和精确引用，但也建立了两层
身份和版本：集合 Artifact 及其 revision、entry 及其 version。

[RFC 1345](1345_scope_organization_and_agent_integration.md) 已经用 Scope 表达持久归属，但仍明确保留一个 Scope
只有一条 active Memory 推进线的约束。本 RFC 修订这个约束，让记忆内容直接使用项目现有的 Scope → Artifact → Revision
模型。Scope 负责归属，一条 Memory Artifact 负责一条可独立维护的事实，不再用集合 Artifact 重复组织这些事实。

## 修改一条记忆仍要提交整个集合的目录

当前实现已经支持 entry 修订、历史读取、精确内容去重、容量限制和可选 compact，也已经按变化 entry 增量更新检索投影。
因此，问题并非每次更新都会复制所有 entry 正文或重写所有向量。

仍然存在的成本是完整 manifest：修改一条 entry，需要构造、排序、序列化并保存全部目录项。若平均目录大小为 N、写入
次数为 R，目录历史量级约为 O(R × N)。两个任务分别修改不同 entry，也共享同一个集合 head 并发前提。
[RFC 1718](1718_memory_capacity_contract.md) 为其提供了容量边界，但没有改变版本化粒度。

对于长期积累的记忆，用户需要的是“更新这条事实”，以及“查看这条事实过去的内容”。让未变化的其他事实出现在每次
集合快照中，不是提供这两项能力的必要条件。

## 抽取已有跨窗口修订能力，但缺少有界的相关记忆协调

当前内置 extraction 会把所选 Memory head 中全部 active entry 交给模型，模型可以 add 或 revise。因此它能够处理以前
Source window 产生的记忆，并非只能在单次 window 内去重。

这个路径的局限是：旧记忆输入随活跃集合增长；没有先生成候选、再检索相关旧记忆的独立步骤；多条近似记忆的归并与
无法确定的冲突也缺少完整动作契约。用户反馈中的“同一 Scope 内按相关度聚合去重”，需要解决这条处理链路，而不只是
增加一个覆盖正文的接口。

## 当前内容已经有投影，搜索仍需要关联补全结果

Memory、Topic Memory 已经维护当前全文或向量投影；Experience、Skill 使用当前文本做全文检索。但部分路径还需要关联
正文表，才能返回完整内容。在 OceanBase 中，ANN 与关联的组合还需要考虑特定版本的优化器与执行计划；现有 Topic Memory
实现已经为此采用有界 ANN 子查询后再关联的方式。

这不意味着 OceanBase 不支持 JOIN，也不意味着 Experience、Skill 存在向量检索问题。后两者当前没有向量检索。本 RFC
希望让各通道已有的当前投影同时承载搜索返回所需内容，明确其一致性责任，减少召回路径对历史存储的依赖。

## 预期结果与非目标

该设计落地后，用户可以按独立 Artifact 维护、检索和引用一条记忆；单条更新不再写 Scope 全量目录；抽取读取的相关旧
记忆有明确预算；旧引用仍能解析到原来的精确内容。

本 RFC 不引入跨 Scope 自动归并，不删除历史正文，不改变 Profile、Handoff、Prompt 的内容模型，也不承诺在线无停写
升级。它不提供整个 Scope 的隐式快照回滚，不要求所有查询都没有 JOIN，不为减少表数量而合并权威记录与派生索引。

# 使用说明

## 一条记忆就是一个 Artifact

假设项目 Scope 中有两条记忆：

```text
Scope: project-a
├── Memory M1@1：生产发布需要负责人审批。
└── Memory M2@1：默认部署区域为华东。
```

后来用户补充“生产发布还需要完成安全检查”。修订 M1 后得到：

```text
Scope: project-a
├── Memory M1@2：生产发布需要负责人审批，并完成安全检查。
└── Memory M2@1：默认部署区域为华东。
```

M1 的身份保持不变，revision 从 1 推进到 2；M2 不产生新 revision。调用方可以读取 M1 的当前版本，也可以用普通
ArtifactRef 精确读取 M1@1。无需再携带一个集合引用、entry ID 和 entry version ID。

搜索默认返回当前可检索的版本，并随结果返回正文和精确 ref。按 Scope 列举 Memory 时，列举的是这些独立 Artifact。

## 新 Source 会与已有记忆进行协调

处理一段新的 Source 时，系统先生成候选事实，再在同一 Scope 和授权范围内检索相关当前记忆。例如候选事实是
“生产发布要先做安全检查”，系统应检索到 M1@2，并判断是否已有充分表达、是否需要补充新证据。

协调可以得到以下结果：

- 没有相关事实：创建新的 Memory。
- 已有事实需要补充或纠正：修订对应 Memory，保留其身份和证据历史。
- 多条记忆应当整合：提出归并，保留一个身份，将其余对象移出活跃检索面。
- 内容已经充分表达且没有新证据：不产生新版本。
- 事实矛盾且无法判断适用条件或先后关系：保存未决冲突，不用相似度分数决定覆盖谁。

这里的检索是有预算的候选选择，不承诺一次 ANN 查询发现所有重复记忆。未完成的工作必须保留可继续处理的状态。
普通新增和修订沿用现有抽取授权；多条语义归并还受下文的评审与预授权规则约束。

## 遗忘与恢复仍然保留历史

`forget` 使一条 Memory 不再参与默认搜索，历史引用仍可读取。`reactivate` 可以恢复普通停用的记忆。

恢复 M1@1 的内容不会将 head 从 2 倒退到 1，而是创建 M1@3，并记录其恢复来源。M1@2 的内容和引用仍然存在。
如果当前向量投影已经替换，恢复时可能需要重新生成向量；读取 M1@1 的历史正文不依赖 embedding 服务。

归并也不会让旧引用自动跳转到保留对象的新 head。引用描述的是当时的精确事实，替代关系描述的是当前维护结果。

## 已有部署需要显式升级

已有部署通过维护窗口迁移数据并升级客户端。迁移后，旧精确 MemoryCitation 仍然可读；旧集合 latest 和旧 entry 写入
协议不再代表当前 Memory，调用时返回明确的升级错误。

迁移不会重新抽取已消费的 Source，也不会把旧 Handoff、Experience 中的引用改写为新的正文。新用户从一开始就使用独立
Memory Artifact，不需要感知旧集合结构。

# 参考级说明

## 设计不变量

1. **独立身份。** 每条逻辑记忆有稳定 Artifact ID，只因自身正文、直接证据或状态发生有效变化而生成 revision。
2. **不可变历史。** 已提交内容、证据和精确引用不因修订、归并、停用或迁移被重写。
3. **权威与投影分开。** Artifact revision 保存事实；head 与当前检索投影指向该事实，可以从权威记录重建。
4. **发布原子。** 新 revision、head、生命周期和全部启用检索通道一起提交，不暴露半完成版本。
5. **处理有界。** 普通增量写入不构造 Scope 全量目录；抽取不能以加载全部活跃 Memory 代替相关检索。
6. **升级可判定。** 未完成迁移不能作为正常新部署运行；精确历史兼容不能冒充旧 latest 或旧写入协议兼容。

## 身份、内容与生命周期

Memory 身份为 `(scope_id, family="memory", artifact_id)`；精确版本采用现有 ArtifactRef，跨 Scope 地址使用
ArtifactAddress。新内容格式为 `powercontext.memory.v2`，至少包含 kind、text、state 和可选的 superseded_by。
Source 与上游 Artifact 证据使用现有 lineage；操作记录保存修改原因、执行者、幂等身份和恢复来源。

状态使用以下语义：

| 状态 | 默认检索 | 恢复方式 | head 治理投影 |
| --- | --- | --- | --- |
| active | 参与 | 正常修订 | active |
| inactive | 不参与 | 可以显式 reactivate | deprecated |
| retired | 不参与 | 重新采用内容须创建新身份并保留来源 | retired |

状态属于 Memory revision 的历史内容，通用治理入口必须经过 Memory writer，不能单独改 head 并绕开版本记录。
仅改变状态也必须保留正文证据。修订继承前驱证据并追加本次证据；归并保存参与对象的精确 refs 与有效直接证据；恢复保存
恢复来源及所恢复版本的证据。历史来源的保留不等于重新获得当前生成资格。

归并后的 inactive 对象可以记录同 Scope 的精确 superseded_by 引用，禁止自引用和替代关系环。整组撤销通过补偿版本
表达，并校验所有对象仍匹配该操作产生的 after refs；已有后续修改时报告冲突，不覆盖后续工作。

## Source 处理与协调动作

本 RFC 不为每条 Memory 新建 Source cursor。Source 仍由 Scope 下既有处理绑定消费，一次处理可以生成或修改多个 Memory
Artifact。绑定选择处理范围和策略，不再选择唯一的集合 Artifact；不同 Memory head 不意味着独立重复消费同一 Source。

处理链路为：

```text
Source window → 候选生成 → 本批去重 → Scope 内相关检索
              → 协调动作 → 证据与授权校验 → 原子提交
```

候选数、每候选检索量、合并后的相关对象数、模型上下文字节或 token，以及模型总调用量都必须有预算。权限、Scope 和
生命周期约束属于候选资格，不能在固定 top-k 截断后才过滤，并把剩余结果当作完整的带过滤召回。

| 动作 | 版本与证据要求 |
| --- | --- |
| create | 新身份、初始 revision 与直接证据 |
| revise | 目标当前 ref、保留的前驱证据、本次变化及其证据 |
| merge | 全部参与对象的当前 refs、保留对象、新内容、停用对象和补偿边界 |
| noop | 已有表达足够且无新证据；不产生 revision |
| conflict | 保存候选、矛盾证据和相关 refs；不静默覆盖 |

普通 create/revise 保持既有自动写入边界。[RFC 1652](1652_memory_quality_and_lifecycle.md) 定义的 L2 语义维护默认
仍须逐次评审。本 RFC 提议增加一个明确例外：Scope 管理者可以预授权“同一对象、相同适用范围、内容兼容且完整保留证据”
的归并，并限定参与条数及操作预算；自动归并默认关闭。每次执行必须记录授权者、策略版本与适用边界。

未决冲突、HOLD、强制评审策略、超出预授权范围或不可逆退休，仍须逐次评审。相似度或模型评分不能替代授权。显式 merge
表达调用方对该组变更的指令，也不能绕开权限和强制评审规则。这一预授权例外是对 RFC 1652 的实质修订。

无法在预算内完成的候选可以拆分、重试或保存为待处理动作。只有其最终结果或可恢复的待处理记录与 Source cursor 一起
持久化后，输入位置才能推进。已知冲突在搜索和 Prepare 中需要携带标识及相关引用，不能只依靠排序表现出来。

## 并发与容量

每个修订目标使用 expected revision；多条归并要么全部满足前提并提交，要么整体失败。操作幂等键独立于内容相似度，
响应丢失后的同一次重试不能创建第二个 Artifact。

自动协调还依赖“检索时没有发现相关变化”这一前提，仅做目标 CAS 不足以处理并发 create。一个可行的初始实现是 Scope
Memory 写入代数：检索开始时记录代数，提交时发现其他写入则重新协调。它是固定大小并发元数据，不是集合 revision。
独立显式修订只需校验自己的目标；它会推进代数，但不因无关对象变化而失败。更细粒度机制必须提供等价的失效检查。

RFC 1718 的容量约束改为 Scope 内 active Memory 数量，提议默认延续标准单容器部署的 5,000 条。manifest 项数和字节预算
退出新格式；历史存储不会因此自动变得有界。active 计数必须与 create、reactivate、deactivate 和 merge 原子更新，
并发提交不能共同越限。多条操作按净变化校验，已超限时仍允许不增加超限维度的修订和减量补救。

## 持久化与当前检索投影

`pc_artifacts` 继续保存不可变 revision，表结构不变。`pc_artifact_heads` 继续保存每个 Artifact 的当前 revision，
不把当前指针塞进每个历史正文行。新 Memory 的正文直接进入 `pc_artifacts.content`；旧 entry versions 表只服务历史兼容。

本 RFC 复用已有投影，不另建通用 current 表：

| 制品 | 当前投影的变化 | 搜索返回的内容 |
| --- | --- | --- |
| Memory | 现有 entry head 与向量投影改用独立 Artifact 身份，补齐正文与 revision | 原文、kind、精确 ref |
| Topic Memory | 在现有 Topic/Chunk 全文及向量投影补齐展示字段 | title、summary；chunk 命中另含 chunk_text 与位置 |
| Experience | OB head 增加可空 search_content；SQLite FTS 保存不分词的 content | 完整 ExperienceContent |
| Skill | 与 Experience 共用上述内容投影 | SkillContent 与包引用；不复制包文件 |

这是对公共表的一次有限扩展：`pc_artifact_heads` 的主键与 revision 结构不变，只增加可空内容投影列。SQLite 的副本保存
在 FTS 中，head.search_content 保持空值，避免重复缓存。Memory 和 Topic Memory 的向量内容不能替代全文投影；没有配置
embedding 的部署仍须提供完整全文搜索。

Topic chunk 不复制整个 detail。标题或摘要改变时，相关 chunk 的展示字段和 revision 一起更新，因此写入量仍随该 Topic
的 chunk 数增长。Experience、Skill 的 searchable_text 是匹配输入，不能代替其原始结构化内容。

## 一致性、重建与后端边界

Memory writer 在同一数据库事务中提交版本、lineage、head、容量变化、操作记录和所有启用投影；后台抽取还同步提交
cursor 与处理状态。通用 Create/Replace 和治理入口遵守相同规则。其他制品的手工、后台、审核发布和受支持的跨 Scope
发布入口，也必须同步维护其当前内容投影。

搜索的“当前”指查询快照内最新已提交且符合资格的版本，不意味着 Source 已经完成异步抽取。一次 hybrid 搜索的通道与
资格查询共享真实一致读快照。停用对象退出所有当前检索通道；SQLite 不能在去掉 head 校验后留下可命中的旧 FTS 行。

向量投影记录嵌入输入摘要和 profile，不能仅改 revision 就复用不匹配的向量。当前 ANN 索引只保留可检索的当前内容。
恢复历史时可复用匹配缓存，缺失则重新 embedding；本 RFC 不要求保存所有历史向量。

投影可从精确权威内容重建。重建或迁移未完成时，受影响搜索必须持续处于未就绪状态，包括进程重启后；完整校验通过后
才恢复。重建不生成新 Artifact revision，不推进 Source cursor，不重新解释历史事实。

“直接从投影返回结果”不禁止标签、权限或 SQLite vec0 元数据的必要关联。OceanBase 需要在目标发行版与补丁上验证
带 Scope 和权限过滤的实际 ANN 计划；不能因 DDL 有向量索引就断言查询会使用它。SQLite 也不能以全表 k 代替有界的 Scope
召回。不受支持的查询组合应明确失败，或采用有预算、可观察的替代策略。

## 公共 API 与兼容性

Memory 的 list/get/replace/history 使用单条 Artifact 身份；search 返回正文与精确 ref；flush 返回处理进度和本次
受影响 refs，不再返回唯一集合 revision。Prepare 去掉同 Scope Memory 命中必须共享集合 ref 的假设。Tags、ACL 和新建
Handoff、Experience 等消费者采用普通 Memory ArtifactRef。

这些属于不兼容 Memory 协议变更，必须使用显式新契约版本，同步 HTTP、SDK、MCP 和自定义 extraction Prompt。OpenAPI
仍是 HTTP 契约源，领域规则由 Runtime/Family service 统一实现。Experience、Skill、Topic Memory 的投影改造保持其
公开内容、引用和搜索评分契约不变。

切换后，旧集合写入、entry 写入及依赖集合 latest 的接口返回稳定的 `upgrade_required` 错误和迁移信息；不得把冻结
旧集合描述为当前结果，或伪造集合 revision 维持旧 CAS。

## 历史迁移

迁移采用显式维护窗口：停止旧写入者，保留一致备份，转换历史，校验后启用新协议。迁移检查点和完成标记必须可恢复，
启动时拒绝把未迁移或部分迁移的数据用于普通写入、当前列表和搜索。新旧格式通过显式格式与身份登记分流，不能靠 ID
前缀猜测，也不能每次加载全 Scope 正文识别。

每个旧逻辑 entry 使用稳定映射：

```text
(scope_id, legacy_collection_id, legacy_entry_id) → new_memory_artifact_id
```

所有旧容器都要被盘点，不能假设只有名为 memory 的标准容器，或假设 entry ID 在 Scope 内唯一。迁移回放完整集合历史，
只在某条 entry 的正文、证据、状态或成员资格变化时生成它自己的新 revision：

| 旧集合历史 | 新独立记忆历史 |
| --- | --- |
| C@1：E1_v1 active | M1@1 active |
| C@2：E1 未变，只修改 E2 | M1 仍为 @1 |
| C@3：E1_v2 active | M1@2 active |
| C@4：E1_v2 inactive | M1@3 inactive |
| C@5：compact 移除 E1 | M1@4 retired，保留历史 |

因此，新 revision 不直接等于旧 entry version。compact 后缺席的对象不能被迁移成普通可 reactivate 的 inactive 对象。
历史导入验证旧证据和摘要，不以今天的生成资格删除旧证据，也不把导入时间当作未知的历史发生时间。

旧 MemoryCitation 先在它指名的旧 manifest 中验证成员、版本与 hash，再映射到精确的新 ref。映射按变化区间保存，避免
展开 entry × 所有集合 revision 的矩阵；compact 后的缺席区间不接受旧 entry citation。单独的旧集合 ArtifactRef 仍表示
集合快照，不能任意指向其中一条新 Memory。旧不可变文档和 hash 不被改写。

entry 标签、授权、到期和撤销语义一对一迁移；集合标签保留集合语义，不复制为每条记忆的标签或 Scope 授权。处理 cursor、
已接受任务与输入高水位保留，旧 lease 失效；已消费 Source 不重新抽取。多容器合并后超出新 Scope 预算时报告差异并要求
明确配置，不能丢弃数据。旧自定义 Prompt 若不兼容，也必须显式升级。

切换前可以恢复备份返回旧版本；切换并接受新写入后，旧集合不再同步，直接恢复旧备份会丢失新数据。此后应前向修复，
降级需要单独的逆迁移设计。旧历史继续保留，因此迁移初期磁盘占用可能上升。

## 与已有 RFC 的关系

| RFC | 本提案的关系 |
| --- | --- |
| 0014、0019 | 保留不可变历史、证据、Scope 消费边界；替换集合 manifest 和 entry 专用版本模型 |
| 1345 | 保留 Scope 归属与授权；修订单条 active Memory 推进线，明确消费 cursor 不按每条记忆拆分 |
| 1417 | 保留 Topic 的内容、分块与原子发布；扩展已有当前投影的返回字段 |
| 1549 | 用既有 Family 能力机制接入 Memory 标准读取与写入边界 |
| 1652 | 保留冲突、证据和生命周期原则；关系改用 ArtifactRef，并提出受限的归并预授权例外 |
| 1718 | 保留确定性容量拒绝和不删除历史；将集合容量改为 Scope 当前记忆容量，旧 compact 转入历史兼容 |

# 缺点

**这是一次真实的协议与数据迁移。** 集合 ref 和 entry ref 已经进入客户端、Prompt、Handoff、权限与标签。统一模型能
降低以后新增能力的成本，但不能消除此次升级的成本。显式停写会影响可用性，窗口时长需要按数据量演练。

**当前内容副本增加写入与存储成本。** Memory 全文和向量通道各保存返回字段，Topic 的 title/summary 会随 chunk 复制，
Experience/Skill 增加当前结构化内容副本。减少查询关联并不自动意味着所有负载更快。

**有界检索可能漏掉相关记忆。** 全量上下文退出后，需要通过召回评测、冲突记录和后续维护弥补，而不是承诺自动获得全局
无重复集合。自动归并也有丢失限定条件的风险，因此必须保留证据、授权与补偿边界。

**一致性责任更集中。** 一条漏更新投影的写入路径就可能返回旧内容；去掉读时 head 校验，要求所有写入与治理入口遵守
原子发布契约。独立 Artifact 消除了集合 CAS，但初始 Scope 代数协调仍可能在繁忙 Scope 中导致重试。

**历史继续增长。** 本提案停止新增全量 manifest，不删除旧 manifest 或正文，也不提供历史保留策略。迁移期间还会同时
存在旧历史、新版本与映射，必须提前估算空间。

# 设计理由与替代方案

## 为什么把 entry 提升为 Artifact？

用户真正希望独立维护、检索和引用的是一条记忆。Artifact 已经有身份、revision、lineage、标签和授权机制，Scope 已经
提供归属。采用它们可以去掉 Memory 专有的第二层版本，同时让更新成本跟随实际变化的对象。

## 保留集合，改为增量 manifest

这是 RFC 1718 提出的自然后续方向。它能减少目录复制并保留现有 API，适合继续把集合快照作为核心能力的系统。
本提案没有选择它，因为集合身份、集合级 CAS、entry 专用引用和全量抽取上下文仍需另外解决。它优化旧结构的编码，
不能实现这里希望统一的 Scope → Artifact 模型。

## 将一个 Scope 拆成多个 Memory 集合

这可以限制单个 manifest 大小，但增加了集合路由、跨集合查重、Source 分配和引用选择。独立事实仍然处在容器之内。
本提案直接按事实版本化，不再引入第二套 Memory 容器分区。

## 新建通用 current 表，或改造所有历史表

通用 current 表可以集中正文缓存，但与现有 Memory/Topic 全文、向量投影重复维护。把全部历史和当前内容放在同一
检索表，则会把 is_current 过滤、历史向量与索引维护耦合起来，并扩大公共存储改造范围。本提案选择复用现有投影，只对
Experience/Skill 使用的 head 增加内容副本。

## 保持召回后关联正文

这种方式节省副本，也是合理的查询设计；现有 Topic Memory 的有界 ANN 子查询已经展示了可用实现路径。接受它意味着
继续维护后端特定的 ANN 与关联组合，并为每个命中补齐内容。本提案选择承担有限写放大，使当前搜索结果由当前投影直接
给出。最终性能差异必须在目标后端测量，不能仅凭表数量或 JOIN 数量判断。

## 不做这项变更

可以继续依靠容量上限和 compact 控制单个集合规模，也可以单独增加 entry replace 或相关检索。但长期仍要维护两层版本、
完整目录历史及其公共引用。若项目决定集合快照比模型统一更重要，增量 manifest 是应优先比较的替代方案。

# 先例

本 RFC 主要沿用 PowerContext 已有设计，而非引入新的持久化范式：

- [RFC 1345](1345_scope_organization_and_agent_integration.md) 确立 Scope 归属，并将多个 Memory 推进线列为后续议题。
- [RFC 1417](1417_topic_memory.md) 已有候选处理、相关 Topic 选择、当前投影和原子发布，可为有界协调与检索提供参考。
- [RFC 1549](1549_artifact_family_unification.md) 提供统一 Family 能力与标准 Artifact 读取边界。
- [RFC 1652](1652_memory_quality_and_lifecycle.md) 区分相似、冲突、有效性和可恢复停用，要求维护保留精确证据。
- [RFC 1718](1718_memory_capacity_contract.md) 明确 full manifest 的成本、容量拒绝与 compact 的历史保护。

这些设计提供可复用的边界，并不意味着本 RFC 的独立 Memory 模型或迁移已经实现。具体表布局、后端注意事项和迁移
执行步骤见[实现方案](../design/memory-artifact-and-search-projection.md)；本 RFC 定义需要达成共识的行为契约。

# 未解决问题

以下问题需要在 RFC 合并前明确：

1. **公共协议版本与兼容期限。** 使用新的 Memory 路径还是显式协议版本？旧精确读取支持多久，客户端如何发现迁移状态？
   无论选择哪种形式，都必须拒绝旧写入语义，不能让新旧集合同时成为权威。
2. **首批支持的后端版本。** 需要明确 OceanBase 发行版与补丁、SQLite/vec0 版本及允许的过滤组合；代表性执行计划和召回
   测量应作为支持声明依据。尚未测得的性能收益不能写入承诺。
3. **Scope 预算的配置归属。** 建议沿用 5,000 条 active 的初始值，但需要确定部署默认值与 Scope 覆盖的关系，以及多旧
   容器迁入时的配置接口。迁移不能默默扩大预算或丢弃历史。
4. **协调预算与并发代价。** 首批候选量、上下文量和重试上限需要代表性数据校准；Scope 代数是否足以承受目标负载，或
   需要更细粒度失效机制，应由测量决定。有界处理、幂等与失效检查本身不应被省略。

具体 SQL、索引列顺序、迁移批大小和命令命名可以在实现中确定，只要符合上述契约。实现顺序与验收清单保存在实现方案中，
不因 RFC 被接受而自动成为交付排期。

# 后续可能性

- 在当前投影和精确版本稳定后，提供不阻断搜索的索引代际切换与在线迁移；需要单独解决追平、切换及回退。
- 通过离线维护补充在线有界检索，发现长期积累的近重复与冲突；采用同一证据和授权契约，不因容量压力自动改写事实。
- 引入带精确 profile 的历史向量缓存，减少内容恢复与重建时的计算；它不能替代权威正文历史。
- 单独设计历史保留、物理清理和必要的 Scope 快照导出。它们不应重新引入每次单条写入都更新的全量集合 manifest。
