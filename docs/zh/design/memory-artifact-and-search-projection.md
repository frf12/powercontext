---
title: Automatic Memory 实现方案
---

# Automatic Memory 实现方案

- 提案名称：`automatic_memory`
- 起始日期：2026-09-30
- 文档类型：实现方案
- 对应 RFC：[Automatic Memory 独立记忆制品](../rfcs/0000-automatic-memory.md)
- 前置 RFC：[制品检索宽表与单表召回，#1803](https://github.com/oceanbase/powercontext/pull/1803)
- 代码核对基线：`ae952f7042eecc331441d05f5847e44815fa2dd8`
- 范围：Automatic Memory 身份、版本、抽取协调、公共检索接入及停服历史迁移。
- 相关 RFC：[Memory 设计](../rfcs/0014_memory_layer_design.md)、[Source/Memory Runtime](../rfcs/0019_local_source_memory_runtime.md)、
  [Scope 组织](../rfcs/1345_scope_organization_and_agent_integration.md)、[Topic Memory](../rfcs/1417_topic_memory.md)、
  [Memory 质量与生命周期](../rfcs/1652_memory_quality_and_lifecycle.md)、[Memory 容量契约](../rfcs/1718_memory_capacity_contract.md)、
  [Artifact Family 统一](../rfcs/1549_artifact_family_unification.md)。

## 1. 摘要

Scope 直接管理多条 `automatic-memory` Artifact。一个旧逻辑 Memory Entry 对应一个新 Artifact，其正文、直接证据和状态
分别由独立 revision 表达。新业务不维护外层集合及全量 manifest。Automatic Memory 支持自动抽取和有权限的手工写入。

抽取先生成候选，再有界检索同 Scope 的相关当前记忆，执行 create/revise/merge/noop/conflict。一次操作只写受影响对象，
不为其他记忆生成版本或成员清单；相似度不能代替证据、授权和评审。

检索遵守前置 RFC #1803：本 family 使用自己的当前检索宽表，一条可检索 Artifact 一行，携带完整匹配、过滤、排序和
返回字段。同一搜索单元支持全文与可选向量。正文权威仍在 `pc_artifacts`，head 仍在 `pc_artifact_heads`；不向公共 head
增加 search_content，也不把旧 entry 专有投影作为新写入表。公共索引、快照、同步与重建契约由 #1803 定义。

升级采用独立版本化工具完成停服迁移。所有旧历史、精确引用、授权及 Source 处理位置保留；新 Runtime 的业务只执行新
模型，旧 family 仅保留有限历史读取。运行时双写、在线搬运及按 Scope 灰度切换不在范围内。本文是实现方案，不代表改造
已经完成；依赖的公共检索能力必须先满足，再启用 Automatic Memory。

## 2. 当前方案及其问题

### 2.1 当前 Memory 的两层版本模型

标准 Runtime 在一个 Scope 内使用固定 Memory Artifact 身份。当前结构为：

```text
Scope
└── Memory 集合 Artifact
    ├── revision 1：完整 manifest
    ├── revision 2：完整 manifest
    └── revision 3：完整 manifest ← 集合 head
        ├── E1 → E1_v2，active
        ├── E2 → E2_v1，active
        └── E3 → E3_v1，inactive

pc_artifacts.content            保存集合 manifest 和 changes
pc_memory_entry_versions.text   保存不可变 entry 正文
pc_memory_entry_heads           保存当前活跃 entry 的检索投影
```

RFC 0014 明确定义了集合 revision 增长及 manifest 快照。当前行为符合这一旧契约。本提案主动改变版本化粒度：Scope
已经承担归属和组织职责，独立事实应直接使用 Artifact 身份，不再增加一层集合成员快照。

### 2.2 已存在的能力

以下能力在核对基线上已经存在，不能作为本提案的缺失能力：

- 按 entry 修订并保留旧正文版本；`forget()`、`reactivate()` 和精确历史引用。
- 提交与 `organize(dedupe)` 中的精确内容去重。其 hash 包含 kind、正文及证据引用，因此相同正文、不同证据不一定合并。
- 默认 5,000 条 active entry、10,000 个 manifest 项、4 MiB 规范内容的容量限制。
- 默认关闭的 `compact()`：从当前 manifest 移除合格的 inactive 条目，保护带标签条目，保留历史正文和旧快照。
- 按实际变化的 entry 增量删除、更新检索投影；未变化的 entry 不再每次重写向量行。

RFC 1652 中的语义邻居关系、冲突关系和分级生命周期策略属于需要衔接的设计，不能全部视为当前已交付实现。

### 2.3 仍存在的结构性成本

**集合 revision 的成本随成员数量增长。** 修改一条 entry，仍需要构造、排序、序列化和散列完整 manifest，再保存一个集合
revision。设平均目录项数为 N、有效写入次数为 R，目录历史量级约为 O(R × N)。容量上限约束单次大小，未改变这种结构。

**独立事实共享集合级并发前提。** 两个任务修改不同 entry，仍可能因为同一个集合 head 已推进而发生版本冲突。

**抽取上下文随活跃记忆增长。** 当前内置 extraction 把所选 head 的全部 active entry 交给模型。代码中的 `bounded`
变量只执行 active 过滤，没有按相关性、top-k 或独立 token 预算筛选旧记忆。Source window 的边界只约束新输入。

**语义协调主要依赖一次模型判断。** 模型已经可以修订以前 Source window 产生的条目，因此问题不能表述为“只处理本次
window”。缺失的是候选生成后独立的相关记忆检索、多条归并、取代和未决冲突处理步骤。现有抽取动作主要是 add/revise。

**公共引用与生命周期存在额外的 entry 层。** 一条事实同时涉及集合 ArtifactRef、entry ID、entry version ID、manifest
状态。检索、共享、标签和其他制品引用需要维持这套专用身份。

### 2.4 检索问题的公共边界

旧 Memory 使用 entry head/向量投影补读 entry 正文。Topic Memory 在目标 OceanBase 版本出现过 ANN 参与 merge join
丢近邻的兼容问题；Experience、Skill 的全文召回也依赖正文关联。公共 RFC #1803 统一约束每 family 的检索宽表与单表召回，
包括匹配、过滤、排序、返回字段，以及实际执行计划和召回验收。这里仅落实 Automatic Memory 的领域字段与发布责任。

这些问题不能概括为所有 OceanBase 版本都不能 JOIN，也不能仅凭声明索引就认定查询已经使用索引。公共 RFC 负责目标版本
支持声明与验收证据，Automatic Memory 不另建一套后端例外。

## 3. 目标、范围与 RFC 衔接

### 3.1 目标

1. 每条记忆有独立 Artifact ID、revision、证据和生命周期。
2. 一次增量更新只处理变化对象，不构造全 Scope 成员快照。
3. 新 Source 与既有记忆通过有界检索跨窗口协调，保留未决工作。
4. 按公共 RFC #1803 提供完整当前搜索，未配置 embedding 仍支持全文模式。
5. 旧历史、精确引用和授权不变，升级可恢复、可重复校验并有明确门禁。

### 3.2 范围边界

- 不重构 Profile、Handoff、Prompt 内容模型；必要的 Memory 引用解析与 Prompt 动作协议需要适配。
- 不引入跨 Scope 自动归并，不把 Context Reference 或跨 Scope 发布当作本地写入归属。
- 不删除历史正文、重写已提交制品，或以物理擦除隐式控制活跃容量。
- 不重新定义公共检索 SQL、索引或后端布局，不能为 Memory 保留关联权威表补字段的例外。
- 不提供运行时双写、在线搬运、按 Scope 新旧写入权交接或任意版本降级。
- 不新增 Scope 级版本化清单，不提供隐式全 Scope 快照回滚。

### 3.3 对已有 RFC 的修订

| RFC | 保留 | 本提案修订 |
| --- | --- | --- |
| 0014、0019 | 不可变历史、精确引用、Source 证据、原子写入 | 使用新 family，以单条 Artifact 替代集合 manifest 和 entry 专用版本 |
| 1345 | Scope 是持久归属边界 | 新 family 无唯一集合 head，Source cursor 仍归处理绑定 |
| 1652 | 相似度不证明可合并；证据、质量与状态分开 | 关系改用精确 ArtifactRef，状态进入独立 revision；新增受限 Scope 归并预授权例外 |
| 1718 | 显式容量、减量补救、历史不删除 | Scope 活跃记忆预算替代集合目录预算，compact 终止状态在迁移中保留 |
| 1549 | Family writer 和通用 Artifact 能力 | 接入普通 Artifact 读取、修订和历史契约 |
| #1803 | 每 family 检索宽表、单表召回及公共一致性 | 本文只提供 Automatic Memory 的字段映射和发布接入 |

## 4. Automatic Memory 模型

### 4.1 身份与正文

```text
Scope S
├── automatic-memory / M1
│   ├── revision 1
│   └── revision 2 ← M1 head
└── automatic-memory / M2
    └── revision 1 ← M2 head
```

身份使用 `(scope_id, family="automatic-memory", artifact_id)`；精确版本使用普通 ArtifactRef，跨 Scope 的完整地址使用
ArtifactAddress。新身份不向旧 entry 表写入。正文保存于 `pc_artifacts.content`，内容格式为 `powercontext.automatic-memory.v1`：

```json
{
  "schema": "powercontext.automatic-memory.v1",
  "kind": "constraint",
  "text": "发布前需要完成审批。",
  "state": "active",
  "superseded_by": null
}
```

Source 与上游 Artifact 证据进入现有 lineage；修改原因、执行者、幂等操作 ID 及恢复来源进入操作记录。正文、直接证据
或生命周期发生有效变化时，创建本条 Memory 的新 revision。不会因为其他 Memory 变化而增加 revision。

Family writer 显式维护每个新 revision 的证据，不能假设通用 Artifact 存储会自动继承 lineage：revise 保留前驱证据并
追加本次证据；merge 保存所有参与对象的精确 refs 及其有效直接证据；仅改变状态时保留原正文证据；restore 保存恢复来源
和所恢复版本的证据。失去当前生成资格的历史来源仍保留可追溯关系，不能通过复制 lineage 重新赋予其当前生成资格。

### 4.2 生命周期

| Memory 状态 | 默认检索 | 恢复语义 | 通用 head 状态投影 |
| --- | --- | --- | --- |
| active | 参与 | 可以继续修订 | active |
| inactive | 不参与 | 可显式恢复同一身份 | deprecated |
| retired | 不参与 | 不允许普通 reactivate；重新采用内容需显式创建新身份并保留来源 | retired |

`retired` 用于不可恢复到活跃集合的显式终止状态，也用于保留旧 compact 的语义。普通忘记和归并使用 inactive，不自动进入
retired。当前状态以 Memory revision 为权威，head 的治理字段及检索行是派生值；通用治理入口不能绕开 Memory writer
独立改变这些字段。

归并后的 inactive Memory 可记录 `superseded_by`，必须是同 Scope 的精确 Memory ArtifactRef。禁止自引用和替代关系环。
恢复被归并条目时，需要显式清除或重新确认替代关系。历史精确读取始终返回引用指定的正文，不自动跳转到替代者的新 head。

### 4.3 恢复与操作记录

恢复某条 Memory 的历史内容通过新 revision 实现，不回拨 head：`M1@1 → M1@2 → M1@3 → M1@4`，其中 M1@4 的内容
来自 M1@1，操作记录保存 `restored_from=M1@1`。退休身份的恢复走显式新建，不能绕开其不可 reactivate 语义。

一次多条归并保存受影响对象的 before/after refs。整组撤销仅在这些对象仍匹配操作 after refs 时执行补偿版本；发生后续
修改则报告冲突。操作记录只包含受影响成员，不保存全 Scope 快照。

## 5. 有界抽取与语义协调

### 5.1 处理链路

```text
Source window
  → 候选生成
  → 本批候选去重
  → 在同 Scope 检索相关当前 Memory
  → 有界协调：create / revise / merge / noop / conflict
  → 验证身份、证据、策略与并发前提
  → 准备各检索通道及 embedding
  → 原子提交受影响 Artifact、投影、操作记录和 Source cursor
```

候选生成主要使用新 Source 证据；协调阶段使用有界的相关旧记忆，不传入全部活跃 Memory。预算覆盖新输入、候选数、
每候选检索量、去重后的相关对象数、上下文字节/token、总模型请求数与累计 token。预算作为显式配置并可观测。无 embedding 的部署使用公共契约支持的有界全文检索，不能回退到全量 active 上下文。

所有检索限定在当前 Scope 和合法读取范围内。相似度只决定候选资格，不能证明两条事实相同或冲突。自动更新需要明确的
对象、条件、时间与来源关系；互补内容可合并，分别成立的历史事实保留适用范围。

### 5.2 动作契约

| 动作 | 行为 |
| --- | --- |
| create | 创建新的 Memory Artifact |
| revise | 指定目标精确当前 ref，生成下一 revision |
| merge | 指定所有被归并对象及保留对象，保留对象生成整合版本，其他对象生成 inactive 版本 |
| noop | 已有内容充分表达且无须新增证据，不产生版本 |
| conflict | 持久保存相互矛盾的候选、证据和相关 refs，等待进一步判断，不静默覆盖 |

普通 create/revise 沿用自动抽取的授权和 write gate。语义归并默认逐次评审。自动多条 merge 由 Scope 策略明确开启，首版默认关闭；关闭时把归并
建议保存为待处理动作。显式 merge 接口须满足调用方写权限及适用评审规则。开启自动归并后，仍不能越过已有 HOLD 或
评审约束；模型评分不构成授权。

这里明确修订 RFC 1652 中所有 L2 合并都需逐次批准的约束：Scope 管理者可以预授权同一对象、相同适用范围、内容兼容且
完整保留证据的归并，并设置参与条数和操作预算。策略必须记录授权者、策略版本与边界，每次自动归并记录适用的授权。
冲突未决、存在 HOLD、命中必须评审的策略、超出预授权范围或涉及不可逆退休时，仍须逐次评审；开启自动 merge 开关不能
覆盖这些条件。显式 merge 表达调用者对该组变更的指令，也必须满足权限及强制评审规则。

一个候选事实与旧状态无法可靠比较时，记录 unresolved conflict。已知冲突不能只作为普通文本参与排序，返回上下文时需要
附带冲突标识及相互引用。预算耗尽的工作必须被拆分、重试或持久化为待处理记录；只有该记录与 cursor 原子提交后才能推进
输入位置，禁止把未处理工作静默当作 noop。

### 5.3 并发与幂等

- 每个 revise/merge 目标校验 `expected_revision`；任意目标变化则整组提交失败并重新协调。
- Source window 和动作使用稳定的操作幂等键，响应丢失后的重试不得重复创建 Artifact。
- 只校验已有目标无法阻止两个任务同时“未检索到旧事实”后各自 create。首版使用 Scope Memory 写入代数校验：从相关检索
  开始到提交之间如有其他 Memory 写入，重新检索和规划。该计数是 O(1) 的并发元数据，不是集合 revision 或成员清单。
- Scope 代数前提用于依赖相关检索完整性的自动协调；独立的显式单条修订只校验目标 revision，不因其他 Memory 变化而
  失败。所有 Memory 写入都会推进代数，使正在运行的自动协调能够发现变化。影响候选资格或生成资格的标签、授权、治理和
  证据变化也要使该前提失效，或在提交前校验对应 generation。
- 模型调用、证据加载和 embedding 在事务外完成；提交事务只做授权复核、CAS、持久化和投影切换。
- 高并发 Scope 可能因代数变化反复重试，必须记录重试率；后续优化不能取消防止并发重复创建的前提检查。

## 6. 接入公共检索宽表

### 6.1 职责划分

`pc_artifacts` 继续保存 `(scope_id, family, artifact_id, revision)` 对应的不可变正文；`pc_artifact_heads` 继续保存
单条当前指针和治理状态。两个公共表都不为搜索补充 search_content。新 family 采用自己的当前检索宽表，不能与旧 Memory
投影共用业务写入或重建范围；旧表保留到历史兼容允许移除时。

公共 RFC #1803 是匹配、过滤、排序、返回、读快照、索引配置、治理同步与重建门禁的唯一公共契约。本文不重述其 SQL、
索引列顺序和后端物理布局；Experience、Skill、Topic Memory 的改造也由公共提案负责。

### 6.2 Automatic Memory 行映射

每个当前可检索 Artifact 对应一个搜索单元，不分 chunk。逻辑字段至少为：

| 字段组 | 来源与用途 |
| --- | --- |
| 身份 | Scope、真实 family、artifact_id、当前 revision、稳定搜索单元 ID |
| 内容 | kind、text、规范内容或完整公开返回所需字段、searchable_text |
| 资格与过滤 | 当前治理资格、授权所需值、标签、kind、有效时间及公开过滤字段 |
| 关系与展示 | 未决冲突标识和所需相关 refs、公开返回的来源摘要；不依赖召回后查权威表补字段 |
| 排序 | 公共协议支持的时间和其他排序字段；未知历史时间保留未知 |
| 向量 | 可选 embedding、嵌入输入摘要和 profile 标识，按公共配置与资格维护 |
| 一致性 | 精确内容摘要、投影 generation 及公共契约要求的校验信息 |

只将当前有资格的对象纳入默认候选面；inactive/retired 和归并退出对象不可被普通当前搜索召回。历史精确读取经权威版本
与授权路径实现，不依赖当前宽表。匹配文本不是规范正文的替代品；FTS-only 模式必须返回与公开契约一致的内容。

标签、授权、证据资格或状态变化时，Family writer 与相关治理入口按公共契约同步维护影响字段和代数。宽表中的冗余信息
不成为新的业务权威；重建只能使用精确历史内容和当时的证据，不生成新的业务 revision。

### 6.3 依赖验收

启用本 family 前必须通过 #1803 的单表召回、资格过滤、索引计划、快照与重建就绪检查。不得将“有向量字段”解释为
自动满足 HNSW 使用条件，也不能仅用无过滤小数据替代目标数据库的计划与召回测量。任何不受支持组合按公共契约处理，
不在本 family 中增加隐式 JOIN 或全 Scope 扫描补偿。

## 7. 写入、治理与恢复一致性

### 7.1 写入不变量

一次提交包括：

1. 复核来源资格、当前授权、处理 lease/fence、幂等键、revision 及协调 generation。
2. 按整组操作的 active 净变化原子校验 Scope 容量；显式写入同样参与。
3. 写受影响 Artifact 的不可变 revision、lineage、操作记录和待处理冲突。
4. 推进相应 head、治理 generation 和 Scope Memory 写入代数。
5. 按公共 RFC #1803 原子发布或撤下当前搜索单元。
6. 自动抽取同步提交 cursor、已处理状态和可继续执行的 pending 记录。

Memory 的通用 Create/Replace、领域操作、后台抽取和治理入口都通过 Family writer，同一数据库事务全部提交或回滚。
模型、候选检索及 embedding 在事务外准备，最终提交前重新验证其前提；不得把事务外旧权限视为当前授权。

Scope 默认 active 上限提议为 5,000，可显式配置；计数和写入代数是固定大小元数据，采用锁或 CAS。并发 create/reactivate
不得共同越限，多条操作按净变化校验，不能先逐条加量错误拒绝减量 merge。已超限 Scope 中不增加超限维度的修订、停用和
减量补救继续允许。计数可以从权威记录重建，普通写入不全量扫描。

### 7.2 向量绑定与历史恢复

向量是否可复用按公共契约校验精确输入摘要与 embedding profile。正文相同、仅状态或引用改变时，符合输入契约的缓存
可以复用；正文变化不能只改 revision 后沿用旧向量。当前 ANN 不承担历史索引职责。

本方案不要求新建历史向量表。部署若另有缓存，应以 Scope、输入摘要和 profile 定位，不参与普通召回，也不为每个状态
revision 重复保存同一向量。恢复历史内容时，缺失或 profile 不匹配则重新 embedding。读取历史正文和精确引用不依赖
embedding 服务，也不承诺旧系统没有保存的向量可以无计算恢复。

### 7.3 重建与业务就绪

模式变更和不兼容索引重建遵守 #1803 的维护窗口、配置记录和持久就绪门禁。受影响 family 在 rebuilding 时不提供当前
搜索；进程中断或重启不能将部分投影当作完整结果。精确历史可按明确支持的路径读取。只有完整覆盖、版本与 profile 校验
通过才置 ready。重建不生成 revision、不推进 Source cursor、不重跑抽取。在线重建不属于本次交付。

## 8. API 与消费链路

| 边界 | 目标契约 |
| --- | --- |
| Create/Get/Replace/History | 新 family 的单条 Artifact ID 与精确 ArtifactRef；Replace 使用独立 revision 前提 |
| Search | 单条宽表结果自带完整正文、资格/冲突信息与精确 ref，沿用公共评分契约 |
| flush | 返回操作 ID、处理进度和受影响 refs，不返回唯一集合 revision |
| Forget/Reactivate/Merge/Restore | 显式单条或多条对象，返回 before/after refs 并维护历史与幂等 |
| Prepare | 移除同 Scope Memory 命中共享集合 ref 的假设，保留未决冲突标识 |
| Handoff、Experience、Dream | 新引用使用 automatic-memory ArtifactRef，旧 citation 通过精确兼容读取 |
| Tags、ACL | 单条 Artifact 目标；旧 entry 权限和标签一对一迁移 |
| SDK、MCP、HTTP | 共享规范解析、身份、错误与能力声明，不能各自实现不同 family 别名语义 |
| 自定义 extraction Prompt | definition、输入和动作 schema 显式升级，不能静默替换用户 Prompt |

### 8.1 名称兼容

API artifact 层集中处理 `memory → automatic-memory` 的受限名称兼容，解析结果同时交给鉴权和 Runtime。响应返回真实
family，registry、存储、历史引用使用规范身份，不建立一套通用动态 family 别名系统。

- 当前 list/search 的 family 选择器可映射到新 family，响应采用新结构和新分页 cursor。
- 明确支持的新单条写入协议可携带旧 family 名称，但仍按新身份与新 CAS 执行。
- 旧集合 ID、latest、集合 revision/ETag、manifest 和 entry 写入须有逐项明确的适配器，否则返回稳定 upgrade_required。
- 旧 ArtifactRef、MemoryCitation 和指定历史 revision 保持原身份，不能把 family 字符串替换成新值。
- 不复用旧列表 cursor，不伪造集合 revision，不把冻结旧集合当作最新内容。

这不是按 Scope 渐进切换。离线 cutover 后，新 Runtime 的当前业务只使用新 family；旧 family 只提供约定的历史读取，
不再有旧写入权威。鉴权必须针对实际执行目标，不能先按旧名称授权、再在 handler 中换成另一个资源。

### 8.2 公共契约变更

OpenAPI 是 HTTP 源，生成代码通过契约生成工具更新，不手工改生成目录。该变更需要显式新协议，名称兼容不等于旧
响应语义兼容。领域规则由 Runtime/Family service 统一承担，适配器仅做协议转换。旧不可变制品和 hash 不改写；历史可读
也不意味着其中证据重新获得当前生成资格。

## 9. 旧版本升级

### 9.1 升级策略与前置条件

采用显式 `plan / apply / verify / cutover` 阶段。独立、版本化的迁移工具复用现有 processing migration 的分批检查点、配置摘要和完成标记
模式，不能仅依靠 `create_all(checkfirst=True)`。普通启动不自动执行旧数据转换。工具明确支持的源/目标格式与发布路径，固定所需依赖。
只要承诺旧版本升级，就继续提供对应工具；服务二进制无需携带运行时双写、在线追平和搬运状态机。

新版 Runtime 启动及开放业务入口前，强制校验数据库格式、迁移完成标记、配置摘要及投影就绪状态。发现未迁移的旧数据、
未完成阶段或摘要不匹配时，拒绝普通写入、当前列表和搜索，只开放迁移工具及明确允许的精确历史读取。只有 cutover 完成
才能开放新协议；迁移中断后重启不能把部分回填的新 family 对象当作完整当前集合。全新数据库须通过初始化记录明确标记为就绪。

迁移执行前停止所有 API 旧写入者和 Workers，保存一致备份。仅写 migration marker 无法阻止不认识 marker 的旧程序，因此必须
实际停止旧进程。维护窗口是本提案的升级前提；其时长根据 plan 盘点和演练测量确定，不预先承诺“短时间完成”。

旧 `memory` family 的 `powercontext.memory.v1` 与新 `automatic-memory` family 的
`powercontext.automatic-memory.v1` 历史记录共存。保留旧集合、entry versions 和 lineage 为只读
历史，防止破坏已有外键和引用。通过真实 family、显式格式/身份登记及历史兼容读取器分流；当前列表、统计、搜索和 rebuild 排除旧
集合。不能靠 ID 前缀猜格式，或先加载整个 Scope 的 JSON 再识别新旧对象。

### 9.2 身份映射

使用稳定的映射：

```text
(scope_id, legacy_memory_artifact_id, legacy_entry_id)
    → automatic_memory_artifact_id
```

扫描所有旧 Memory 容器，不能只扫描标准 ID `memory`，也不能假设 entry_id 在整个 Scope 内唯一。新 ID 确定性生成并
记录迁移凭证；出现碰撞则阻止该映射提交，不能合并不相关旧身份。

### 9.3 回放正文、状态与 compact 历史

按完整旧集合 revision 顺序回放。某 entry 的正文版本、证据版本、active/inactive 状态或当前成员资格变化时，生成该条
新 Artifact revision；其他 entry 的变化不生成本条版本。

| 旧集合快照 | E1 状态 | 新 M1 历史 |
| --- | --- | --- |
| C@1 | E1_v1，active | M1@1，active |
| C@2 | E1 不变，仅 E2 变化 | 仍为 M1@1 |
| C@3 | E1_v2，active | M1@2，active |
| C@4 | E1_v2，inactive | M1@3，inactive |
| C@5 | E1_v2，active | M1@4，active |
| C@6 | E1_v2，inactive | M1@5，inactive |
| C@7 | E1 经 compact 从当前 manifest 移除 | M1@6，retired；历史仍可读 |

因此新 revision 不等于旧 entry.version。迁移必须包含 inactive 和已 compact 的历史条目，不能仅遍历最终 manifest。
compact 导致的终止状态不应被迁移成普通可 reactivate 条目；新默认召回仍与旧有效集合一致。

保存每次变化的旧集合 revision、原因和直接证据。迁移时间使用 imported_at；无法确定的旧发生时间留空，不能把迁移日期
当作记忆产生时间。集合操作级来源保存在迁移来源记录中，不能一律冒充每个 entry 的直接证据。

历史导入使用专门的迁移写入边界：验证旧记录、精确引用和摘要完整性，不调用当前生成模型，也不以今天的 Source 生成资格
重新筛除历史证据。迁移写入边界仅在维护状态可用，不能成为普通业务绕过来源资格和权限检查的接口。

缺失版本、摘要不符、非法版本链、未解释的成员消失或历史断档均为阻塞项。孤立但仍可能被引用的记录先保留并在 plan 中
报告，不能静默删除或赋予新的当前含义。

### 9.4 旧精确引用解析

兼容层先通过旧 manifest 校验 entry、entry version 和 hash 在该集合 revision 中确实存在，再映射到确切的新 ArtifactRef。
映射采用变化区间：

```text
(Scope, 旧集合, entry, [起始集合 revision, 结束集合 revision))
    → 新 ArtifactRef
```

区间仅覆盖 entry 实际属于旧 manifest 的版本范围，互斥且连续覆盖该成员关系；compact 后的缺席区间不能接受旧 entry
citation。此时新 retired revision 由迁移来源记录关联到 compact 操作。不得存储 entry × 全部集合 revision 的笛卡尔积
映射矩阵；区间数量随正文、状态和成员变化次数增长。

单独的旧集合 ArtifactRef 表示一个集合快照，继续返回兼容集合视图或明确的展开结果，不能随意指向某条新 Memory。
旧 Handoff、Experience 的正文、hash 和引用保持不变。解析旧引用时仍校验迁移后的权限，不因为存在 alias 而绕过访问控制。

### 9.5 其他历史数据与容量

- entry 标签、授权主体、权限、到期和撤销语义一对一迁移。集合标签保留集合语义，不复制到每条记忆，也不升级成 Scope 授权。
- 处理 cursor、高水位、已接受任务与请求计数原样保留；旧 lease 失效，已消费 Source 不重新抽取。
- 保存的自定义 extraction Prompt、demonstration 或 policy 若不兼容新动作 schema，必须显式升级或报告阻塞，不能静默换成
  默认 Prompt。
- Scope 当前 active Memory 上限默认沿用标准单容器部署的 5,000 条，并可显式配置；多旧容器迁入同 Scope 导致汇总超限时，
  plan 报告容量差异，由迁移配置明确预算，不丢弃数据。当前已超预算时允许不增加超限维度的修订与补救操作。
- 新模型不再设置 manifest 数量/字节上限，也不再暴露旧集合 compact 作为常规新写接口。提供当前数量、历史版本数量、
  正文与投影字节量、待处理数和操作预算指标；历史保留与物理清理由独立策略处理。

### 9.6 执行阶段

| 阶段 | 具体工作与完成条件 |
| --- | --- |
| plan | 识别数据库和格式版本；盘点所有容器、历史、引用、compact、权限、标签、Prompt、索引 profile 和容量；输出阻塞项及稳定配置摘要 |
| freeze | 停旧进程和写入，保存备份、head/cursor/任务快照，确认输入不再变化 |
| schema | 创建新 family 的检索宽表及映射、receipt、完成标记；按公共迁移创建所需结构；验证每个 DDL 的实际结果 |
| backfill | 分批回放 Memory 历史，写入新版本与映射；目标记录和检查点同事务提交；已存在目标必须核对摘要后才可跳过 |
| projections | 从确切当前版本回填 Automatic Memory 宽表；与 #1803 公共检索迁移协调；复用匹配内容/profile 的当前向量，补齐缺失向量 |
| verify | 核对每个旧 manifest、身份、正文、证据、状态、引用、权限、cursor 和各检索通道覆盖；失败时保持未完成状态 |
| cutover | 校验冻结快照未变化后标记完成，启用新版 Runtime，允许新协议写入；旧数据继续只读 |

SQLite 不支持的原地列/虚拟表变更使用维护期替换和检查点；OceanBase DDL 可能独立提交，不能依赖一个大事务回滚所有 DDL。
迁移中可以使用临时或影子物理表；完成后的业务检索只使用公共契约规定的 Automatic Memory 宽表，不维持双写到旧 entry 投影。
公共检索迁移和本迁移可以同一维护窗口完成，但任一必要阶段未完成都不能开放相应新业务入口。

迁移不会通过 LLM 重新抽取旧 Source。旧历史向量不完整时，不要求批量重新生成全部历史向量；历史恢复时按实际需要计算。
旧集合 manifest 被冻结保留，迁移初期的磁盘占用可能上升，不能把停止新增目录快照描述为立即释放旧存储。

### 9.7 升级回退

新版尚未接受写入时，可以恢复备份并重新启动旧版本。新版接受写入后，旧集合不再同步变化，直接回退备份会丢失新数据。
此后默认采用前向修复；如需降级，必须停写并执行单独设计、验证的逆迁移。Memory 内容的历史恢复与部署降级是不同操作。

## 10. 成本、风险与备选方案

### 10.1 成本模型

设 N 为 Scope 当前记忆数，R 为有效写入次数，k 为本次变化数，H 为正文/证据/状态/成员资格的有效历史变化总数。

| 维度 | 旧结构 | 目标结构 |
| --- | --- | --- |
| 目录历史 | 每次保存全 manifest，约 O(R × N) | 无目录快照，操作及变化映射随 H 增长 |
| 正文历史 | 不可变 entry version | 单条 Artifact revision；状态变更也生成自身 revision |
| 投影更新 | 已按变化 entry 增量维护 | 按 k 条对象维护公共宽表，增加返回与过滤字段副本 |
| 抽取旧输入 | 随 active 数量增长 | 受检索、上下文和总模型预算限制 |
| 并发 | 不同 entry 共享集合 CAS | 显式单条 CAS 独立；自动协调受 Scope 代数失效约束 |
| 迁移 | 旧历史已有完整 manifest | 完整读取旧历史；新增正文/状态版本、区间映射、宽表，临时空间增加 |

消除新写全量 manifest 不会消除扫描旧 manifest 的迁移成本。旧目录按全部历史读取，必须测量总行/字节量；映射不得再
展开 N × 全部集合 revision。保留旧历史和新 revision 可能复制正文，迁移后磁盘不会立即下降。

上述是结构分析，不是实测性能保证。应测量单写行数/字节数、hash、embedding 次数、事务时长、重试率、空间、迁移时间
及公共检索验收指标。宽表的冗余与维护成本遵循 #1803，同样不能仅凭 JOIN 减少就宣称更快。

### 10.2 备选方案

| 方案 | 优点 | 未采用的原因 |
| --- | --- | --- |
| 沿用旧 family 换 schema | 少一个 family 名称 | 同一 family 同时承载集合和单条，能力发现与引用分流复杂 |
| 增量 manifest | 减少目录复制，API 迁移较小 | 集合 CAS、entry 引用及抽取全量上下文仍需另外解决 |
| 多个小集合 | 有界单容器规模 | 增加容器路由、跨集合去重与 Source 分配 |
| 只迁当前状态 | 初次升级较快 | 新 Artifact 无连续正文/状态历史，用户跨两套历史查询 |
| 在线双写迁移 | 减少维护窗口 | 发布二进制需长期保留仍受支持旧版本的搬运、追平、恢复与切换能力 |

当前选择完整历史离线迁移，停服是明确前提。商业在线升级可采用栅栏版本 B：旧版本必须经过 B，B 完成迁移并持久记录
完成后才能继续升级；后续版本只保留格式/完成检查，B 发布物及升级资产在支持期内保留。安装过 B 不等于迁移完成。
这个方向不属于本方案实施范围，不为它添加运行时双写或在线切换代码。

## 11. 实施顺序与验收

### 11.1 实施顺序

1. 先落实 #1803 的公共检索契约、治理同步、就绪门禁和目标后端支持要求。
2. 明确新 family 的内容/动作/引用/API 格式、支持的旧版本和离线工具版本路径；完成 plan 与迁移数据盘点。
3. 实现独立 Family writer、revision、状态、lineage、归并评审与预授权、冲突、预算、幂等和并发控制。
4. 接入 Automatic Memory 宽表；适配 OpenAPI、SDK、MCP、Prepare、旧名称解析、授权、消费者和 Prompt。
5. 完成历史回放、区间映射、旧引用兼容、容量、cursor/lease 处理与维护窗口迁移。
6. 完成正确性、故障恢复、规模和后端验收后，才允许 cutover 启用新业务。

两个提案可以在同一版本交付、同一维护窗口迁移，但不能跳过公共契约前置。中间未完成版本不得接受新模型业务写入。

### 11.2 正确性验收

- 修改 M1 不增加 M2 revision，不生成 Scope 成员快照；正文、证据和状态均可精确历史读取。
- 新增、修订、治理、归并、恢复、通用 Replace 均经 Family writer；active 当前对象可检索，inactive/retired 退出召回。
- 严格遵守 #1803 各通道快照、过滤、返回和就绪规则，不以权威表 JOIN 掩盖投影缺失。
- 相同 Source window 重试、响应丢失、并发 create、CAS 失败和 lease 失效不会重复写入、部分归并或错误推进 cursor。
- 权限、标签、证据或治理变化后，陈旧协调不得提交；显式无关单条修订不因 Scope 其他对象写入而失败。
- revise、merge、状态变化、restore 保留直接证据；自动 merge 默认关闭且只在可验证的预授权范围内执行。
- 未决冲突和预算耗尽记录可继续处理，并随 cursor 原子持久化；Prepare 不把矛盾隐藏为普通排名差异。
- 并发新增与激活不能共同超限；按整组净变化校验，超限时仍允许减量补救。
- 旧名称解析的鉴权与执行对象一致，返回真实 family；旧集合 ETag/manifest 不会被误当单条 CAS；新旧 cursor 不混用。
- 每条旧 entry 的正文、证据、状态和 compact 历史可追溯；逐个 manifest 验证区间，不仅比较最终 head。
- compact 缺席区间拒绝伪造 entry citation；旧纯集合 ref 继续表示集合快照；别名不会绕过访问控制。
- 标签、授权到期/撤销、cursor、高水位及任务计数保留，旧 lease 失效；不重新抽取已消费 Source。
- 历史导入不提升来源资格、不伪造历史时间，不兼容 Prompt 阻止切换而非静默替换。
- 各迁移阶段中断后可续跑，重复 apply 摘要相同、不重复生成身份；缺失版本或 hash 不符阻止 cutover。
- 部分迁移、配置不符或投影未就绪时，新 Runtime 重启仍拒绝普通业务；空库初始化显式就绪。
- 恢复历史正文无需 embedding；恢复到当前搜索缺少匹配向量时按公共契约补算，不伪称历史向量已保存。
- 新版接受写入前可用备份回退；之后不提供丢弃新写的无提示回退。

### 11.3 规模与迁移验收

- 固定 Source、Scope 大小和历史分布，报告每次写入行/字节量、模型输入、embedding 次数、事务时长和并发重试率。
- 验证有界协调不会加载全部活跃记忆，且超过预算的工作有可恢复状态；报告检索遗漏、错误归并与未决冲突质量。
- 比较 Scope 当前数相同而历史版本量不同的负载，确认增量新写不扫描完整历史或重写全 Scope。
- 对多旧容器、inactive、reactivate、compact、未知时间、过期/撤销授权与自定义 Prompt 进行迁移演练。
- 测量旧 manifest 读取量、映射区间数、新旧正文并存空间、投影空间、耗时、embedding 成本和故障恢复时长。
- 使用 #1803 规定的真实后端执行计划、索引覆盖与召回验收；本地 mock 或 SQL 文本不证明目标 OB 索引有效。

验收应覆盖公开可观察行为；不为固定内部调用顺序、表名或辅助函数增加测试。方案记录验收要求，不代表本次文档工作运行
了实现测试或得到了性能结果。

## 12. 依据

### 现状源码

| 事实 | 代码入口 |
| --- | --- |
| 公共版本、head、entry 和当前投影定义 | [tables.py](../../../src/powercontext/builtin/persistence/tables.py) |
| manifest、容量和 entry 模型 | [memory/models.py](../../../src/powercontext/builtin/artifacts/memory/models.py) |
| 全 active 抽取输入、manifest、状态和 compact | [memory/service.py](../../../src/powercontext/builtin/artifacts/memory/service.py) |
| add/revise 动作 | [memory/extraction.py](../../../src/powercontext/builtin/artifacts/memory/extraction.py) |
| 增量 entry 投影 | [persistence/memory.py](../../../src/powercontext/builtin/persistence/memory.py) |
| Family 写入分派 | [family_management.py](../../../src/powercontext/builtin/persistence/family_management.py) |
| 可恢复离线迁移模式 | [processing_migration.py](../../../src/powercontext/builtin/persistence/processing_migration.py) |

### 设计依赖

公共检索的现状问题、OceanBase 执行边界、每 family 宽表、一致性和后端验收依据集中在
[RFC #1803](https://github.com/oceanbase/powercontext/pull/1803)。Automatic Memory 的领域契约以
[对应 RFC](../rfcs/0000-automatic-memory.md) 为准；本文提供其执行步骤、迁移细节和验收清单。
