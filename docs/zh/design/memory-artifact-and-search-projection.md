---
title: Memory 单条制品模型与当前检索投影实现方案
---

# Memory 单条制品模型与当前检索投影实现方案

- 提案名称：`memory_artifact_and_search_projection`
- 起始日期：2026-09-30
- 文档类型：实现方案
- 对应 RFC：[Memory 单条制品模型与当前检索投影](../rfcs/0000-memory-artifact-and-search-projection.md)
- 代码核对基线：`ae952f7042eecc331441d05f5847e44815fa2dd8`
- 修订范围：Memory 身份、版本、抽取协调、检索投影及历史升级；Experience、Skill、Topic Memory 的当前检索投影。
- 相关 RFC：[Memory 设计](../rfcs/0014_memory_layer_design.md)、[Source/Memory Runtime](../rfcs/0019_local_source_memory_runtime.md)、
  [Scope 组织](../rfcs/1345_scope_organization_and_agent_integration.md)、[Topic Memory](../rfcs/1417_topic_memory.md)、
  [Memory 质量与生命周期](../rfcs/1652_memory_quality_and_lifecycle.md)、[Memory 容量契约](../rfcs/1718_memory_capacity_contract.md)、
  [Artifact Family 统一](../rfcs/1549_artifact_family_unification.md)。

## 1. 摘要

Scope 直接管理多条独立的 Memory Artifact。原来的一个逻辑 Memory Entry 对应一个 Memory Artifact，Entry Version
改由 Artifact Revision 表达；普通读写不再维护外层 Memory 集合 Artifact 及其全量 manifest。

每条 Memory 独立保存正文、证据和状态历史，独立推进 head。抽取流程先生成候选，再在同一 Scope 内有界检索相关旧记忆，
执行新增、修订、归并或冲突记录。一次操作只写入受影响的 Memory，不为其他记忆生成版本或重写成员清单。

检索复用现有投影表，补齐返回结果所需的原文和结构化字段。Memory、Topic Memory 的向量召回直接读取向量投影；
Experience、Skill 的全文检索直接读取当前正文投影。权威版本、head 和启用的检索通道在同一事务中维护，普通搜索无需
关联权威历史表取正文，也无需关联 head 才能判定当前版本。

`pc_artifacts` 保留现有表结构。`pc_artifact_heads` 为 Experience、Skill 增加可空的 `search_content` 投影列，
其主键、revision 和治理模型保持不变。不另建一套通用当前检索表；迁移映射、幂等操作和待处理冲突可以使用专门的记录表。

升级采用显式停写迁移，保留所有旧不可变记录与精确引用。旧 Memory 写入协议在切换后停止接受，新写入使用单条 Artifact
身份。本文定义目标行为与验收要求，不代表这些改造已经实现。

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

### 2.4 其他制品的检索现状

| 制品 | 当前权威内容 | 当前检索路径 | 本提案处理 |
| --- | --- | --- | --- |
| Memory | 集合 manifest 与 entry 正文分开保存 | 当前 entry 投影及向量投影关联正文表 | 单条 Artifact；现有全文、向量投影直接返回正文 |
| Topic Memory | `pc_artifacts.content` 中的 title/summary/detail | 当前 Topic/Chunk 投影；OB 的 ANN 先 LIMIT 再关联展示字段 | 补齐现有各通道投影中的返回字段 |
| Experience | `pc_artifacts.content` | OB 对 head 的 searchable_text 做全文检索，再关联正文 | head 增加当前结构化内容投影 |
| Skill | `pc_artifacts.content` 与 package 存储 | 与 Experience 共用全文检索，再关联正文 | 缓存 SkillContent；包文件仍由 package 存储负责 |
| Profile、Handoff、Prompt | `pc_artifacts.content` | 按固定或选定身份读取 head、精确 revision | 保持现有读取模型 |

Experience、Skill 当前没有向量检索，其正文关联不能直接认定为 ANN 故障。它们已有当前文本投影，也没有先搜索所有历史
再计算最大 revision。这里的改造目标是让搜索直接返回所需内容，并减少热路径对正文表的依赖。

### 2.5 OceanBase 的执行边界

Topic Memory 的实现记录了一个具体兼容问题：OB CE 4.3.5.6 的距离排序向量扫描直接参与 merge join 时可能丢失邻居，
因此使用有界 ANN 子查询后再关联。旧 Memory 普通向量查询仍直接关联 entry 正文；带标签的分支会去掉 `APPROXIMATE`。

这不能推导出“OB 不支持 JOIN”，也不能证明当前所有部署都有相同故障。提案选择让主召回尽量使用具备完整返回字段的
单表投影，并在目标版本上验收执行计划。DDL 声明了 HNSW、SQL 包含 `APPROXIMATE`，都不能替代实际计划和性能证据。

## 3. 目标、范围与 RFC 衔接

### 3.1 目标

1. Scope 下每条 Memory 有独立 Artifact ID、revision、证据和生命周期。
2. 更新成本由本次变化内容决定，不生成 Scope 全量成员快照。
3. 新 Source 与已有 Memory 通过有界检索进行跨窗口协调。
4. 各检索通道直接读取当前投影，保留已有返回内容和精确引用能力。
5. 不启用 embedding 的部署仍具备完整全文检索链路。
6. 保留旧历史、旧引用和授权边界，提供可中断恢复、可校验的升级路径。

### 3.2 范围边界

- 不重构 Profile、Handoff、Prompt 的持久化模型。
- 不引入跨 Scope 自动归并，不把跨 Scope 发布或 Context Reference 当作写入归属。
- 不删除历史正文、重写已提交制品，或以物理擦除控制容量。
- 不保证所有 SQL 都没有 JOIN。标签、权限和 SQLite 向量扩展的内部元数据关联可以保留；消除的是搜索时为补正文、确认
  head 而必须进行的权威表关联。
- 首版不提供无停写升级、混版本双写或任意版本降级。
- 不为 Scope 新建一个版本化“Memory 清单制品”，也不提供隐式的整个 Scope 快照回滚。

### 3.3 对已有 RFC 的修订

| RFC | 保留 | 本提案修订 |
| --- | --- | --- |
| 0014、0019 | 不可变历史、精确引用、Source 证据、原子写入 | 集合身份、manifest 和 entry 专用版本改为单条 Artifact |
| 1345 | Scope 是唯一持久归属边界 | Memory 直接采用该边界 |
| 1417 | Topic 内容模型、分块、原子发布、渐进式披露 | 在已有检索投影中冗余返回字段，去掉补正文的查询关联 |
| 1652 | 相似度不等于可合并证明；证据、质量与状态分开 | 关系目标改用精确 ArtifactRef；允许 Scope 对限定条件的归并预授权，其余继续逐次评审 |
| 1718 | 显式容量、补救操作、历史不删除 | 集合 manifest 预算退出新模型，改为 Scope 当前记忆容量与操作预算 |
| 1549 | Family writer 和通用 Artifact 能力边界 | Memory 接入独立 Artifact 的通用读取、修订和历史契约 |

## 4. 新 Memory 模型

### 4.1 身份与正文

```text
Scope S
├── memory / M1
│   ├── revision 1
│   └── revision 2 ← M1 head
└── memory / M2
    └── revision 1 ← M2 head
```

身份使用 `(scope_id, family="memory", artifact_id)`；精确版本使用普通 ArtifactRef，跨 Scope 的完整地址使用
ArtifactAddress。正文保存于 `pc_artifacts.content`，内容格式为 `powercontext.memory.v2`：

```json
{
  "schema": "powercontext.memory.v2",
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
每候选检索量、去重后的相关对象数、上下文字节/token、总模型请求数与累计 token。预算作为显式配置并可观测。

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

普通 create/revise 沿用自动抽取的授权和 write gate。自动多条 merge 由 Scope 策略明确开启，首版默认关闭；关闭时把归并
建议保存为待处理动作。显式 merge 接口在调用方具备对应写权限时可直接执行。开启自动归并后，仍不能越过已有 HOLD 或
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
  失败。所有 Memory 写入都会推进代数，使正在运行的自动协调能够发现变化。
- 模型调用、证据加载和 embedding 在事务外完成；提交事务只做授权复核、CAS、持久化和投影切换。
- 高并发 Scope 可能因代数变化反复重试，必须记录重试率；后续优化不能取消防止并发重复创建的前提检查。

## 6. 复用现有检索投影

### 6.1 公共表职责

`pc_artifacts` 的复合主键仍为 `(scope_id, family, artifact_id, revision)`，保存全部不可变内容。表结构不变。

`pc_artifact_heads` 的复合主键仍为 `(scope_id, family, artifact_id)`，保存当前 revision 和治理状态。新增可空的
`search_content`，供 OceanBase 存 Experience、Skill 当前 revision 的规范结构化内容副本；其他 family 不要求填充此字段。
SQLite 在自己的 FTS 表保存内容副本，head.search_content 保持空值，不重复缓存第二份正文。
`searchable_text` 用于分词和匹配，不能代替原始正文或结构化 content。

所有投影可从权威内容及精确来源重建。缺失投影不得通过搜索时逐条加载历史正文来掩盖；应明确报告未就绪或执行受控重建。

### 6.2 OceanBase 布局

| 现有表 | 改造内容 | 检索方式 |
| --- | --- | --- |
| `pc_memory_entry_heads` | 改为单条 Memory 当前投影；键使用 scope_id + artifact_id，保存 revision、kind、text、searchable_text | 全文路径从本表返回正文 |
| `pc_memory_vector_entries` | 改为同一 Memory 身份；补 kind、text、revision 及内容/profile 校验字段 | 单表 ANN 返回正文与精确 ref |
| `pc_topic_memory_active_topics` | 保持当前 title/summary 等投影 | Topic 全文检索保持单表 |
| `pc_topic_memory_active_chunks` | 补齐对应 revision 的 title/summary；保留 chunk_text 和位置 | Chunk 全文检索无需关联 Topic 表取展示字段 |
| `pc_topic_memory_vector_topics` | 补 title/summary | Topic 向量检索直接返回展示内容 |
| `pc_topic_memory_vector_chunks` | 补 title/summary、chunk_text 及位置 | Chunk 向量检索直接返回片段及展示内容 |
| `pc_artifact_heads` | 增加 Experience/Skill 的 search_content | 全文检索直接返回现有结构化结果 |

Memory 专用旧表名可在本次迁移中保留，但新版列和内部对象不再对外暴露 collection/entry 身份。一次逻辑 Memory 最多有
一份当前全文行和一份当前向量行；只保存 active 状态。旧 entry versions 表退出新写入与普通搜索，保留在兼容读取中。

Topic 的 chunk 不复制完整 detail。title/summary 更新时，同一 Topic 的所有 chunk 展示字段与 revision 一起切换；
其更新成本随该 Topic 的 chunk 数量增长，必须计入写放大。是否重新 embedding 取决于该通道实际嵌入输入是否变化，不能
只看 chunk 正文是否相同。

Experience 的 search_content 保存完整 ExperienceContent；Skill 保存 SkillContent 和包引用，不复制整个包目录。
包中允许检索的文本仍进入 searchable_text。搜索返回契约、准入、融合分数和排序语义不因存储副本改变。

### 6.3 SQLite 与无向量部署

SQLite 的 FTS5、vec0 具有各自的物理存储布局，不要求模拟 OB 的单张 VECTOR 列表：

- `pc_artifact_fts` 增加不参与分词的结构化 content 字段，供 Experience/Skill 直接解码。
- Memory FTS 行补原始正文与单条 Artifact revision；Topic FTS 行补齐现有返回契约所需字段。
- 向量元数据表补对应返回字段；vec0 与其元数据的有限关联可以保留，但不再关联 `pc_artifacts`、entry versions 或 head
  才能取得正文或判定当前版本。
- Scope 限制需要进入向量召回；当前 Memory vec0 的全表邻居策略必须替换为受支持的 Scope 约束路径，不能以全表 k 实现
  “过滤后足够候选”。目标扩展版本不支持时明确拒绝该向量配置，不能悄悄采用无界扫描。

FTS-only 部署始终构建全文投影，不能依赖是否创建向量表。hybrid 部署要求两个通道都准备完成后才发布新 revision。
向量表缺少正文副本时不能静默退回历史表 JOIN；这属于未完成的投影升级。

### 6.4 过滤、当前性与查询边界

普通搜索的“当前”指查询快照内最新已提交、且符合生命周期和权限条件的版本，不表示 Source 已完成异步抽取。

一次 hybrid 搜索中的全文、向量和资格查询必须共享真实的一致读快照，不能把“复用同一连接”当作快照保证。SQLite 需要
实际开启读事务，不能依赖驱动对普通 SELECT 的隐式行为；OceanBase 需要显式选择并验证提供跨语句一致快照的事务配置。
查询 embedding 在事务外准备，取得投影结果后结束读事务，再进行不依赖当前库状态的融合和 rerank。若某后端不能提供该
读契约，则该组合检索配置不得标记为可用，不能返回同一 Artifact 新旧 revision 混合的结果。

Scope、family、标签和权限资格必须在最终候选截断前生效。不能先取全库或整个 Scope 的固定 top-k，再靠应用层过滤来
冒充带过滤条件的 top-k。关联标签、授权记录的必要条件可以保留；它们不属于补正文 JOIN。

对于目标 OB 版本不支持的 ANN 过滤组合，显式选择经验证的预过滤路径，或在有界候选集合内精确计算距离。记录实际检索
策略与成本；超过预算应明确失败或返回契约声明的未完成状态，不能以少量结果声称完整检索。不能仅因返回 mode=vector
就宣称用了 HNSW。标签字段若冗余进投影，其更新必须与标签权威变更同步。

OB 的 ANN 查询维持单个距离排序表达式，配套匹配的索引距离算法、`APPROXIMATE` 和 LIMIT。稳定次序、跨通道融合以及
Topic collapse 放在有界召回之后。检索主查询不再为了取得内容或确认 head 关联权威表；正常写入通过下一节的不变量保证
投影当前性，不需要逐请求遍历全部 head 做完整性检查。

## 7. 写入、治理与重建一致性

### 7.1 写入不变量

一次提交包括：

1. 校验来源资格、当前授权、处理 lease/fence、幂等键和版本前提。
2. 按操作前后状态计算 Scope active Memory 净变化，原子校验容量并更新计数；显式写入同样参与。
3. 写入受影响 Artifact 的不可变 revision、lineage 和操作记录。
4. 推进对应 head；更新相关治理 generation。
5. 替换各当前投影的 revision、返回内容、全文字段和向量，停用对象则删除可召回行。
6. 若来自自动抽取，同步提交 cursor、处理状态和待处理动作。

以上在同一数据库事务完成。Memory 的通用 Create/Replace、领域操作、后台抽取和治理入口都通过 Family writer。
Experience/Skill/Topic 的手工、审核发布、后台生成及受支持的跨 Scope 发布入口同样维护各自投影，不允许仅写公共表。

容量计数与 Memory 写入代数属于 Scope 的固定大小元数据，使用行锁或 CAS 保证并发 create/reactivate 不共同越限。
多条操作按整组净变化校验，不能先逐条加量再错误拒绝可减少总量的 merge。事务失败时计数一起回滚；已超限 Scope 中不增加
超限维度的修订、停用及减量归并仍可执行。计数可由权威 head 重建，普通增量写入不扫描整个 Scope。

Skill 的 lifecycle 与内容 revision 是不同并发维度，沿用治理 generation 校验。OB 查询使用同一 head 行的 active 条件；
SQLite 当前 FTS 只保留可召回行，deprecated/retired 时删除，重新启用时从确切 head 和 package 重建。不能移除查询时的
head 关联，却仍只更新通用治理状态而让旧 FTS 行继续命中。

### 7.2 向量绑定与历史恢复

每份向量必须可核验其嵌入输入摘要和 embedding profile 指纹，指纹包含模型、维度、距离算法、归一化与配置版本。
正文未变化但仅状态或引用变化时，可以复用符合该通道输入契约的向量；正文变化不能只改 revision 后沿用旧向量。

当前 ANN 索引仅保存当前可检索内容。本提案不要求新增历史向量存储表；部署若另有持久向量缓存，其键必须包含 Scope、
嵌入输入摘要和 profile，且不参与普通召回，不为每个状态 revision 重复保存相同向量。

恢复历史内容时，命中匹配 profile 的缓存则复用；未命中或 profile 已改变则重新 embedding。旧系统没有完整保存历史向量，
迁移不承诺恢复旧内容时永远无需重新计算。历史正文与精确引用的可读性不依赖 embedding 服务。

### 7.3 重建与模式切换

首版采用显式维护窗口进行 profile 变更、分块策略变更及不兼容索引重建：停止写入，记录精确 head 快照和检索配置，
重建受影响投影，验证身份、revision、内容摘要及通道覆盖后恢复服务。普通读取不得把半完成的新索引与旧索引混合使用。

重建前将受影响 family 的检索状态持久标记为 rebuilding，关闭其搜索入口，保留精确历史读取。进程中断或重启后，该状态
仍阻止搜索，不得把部分行误判为完整结果。全部启用通道通过覆盖、版本和 profile 校验后，才能在完成事务中标记 ready；
该状态可复用迁移/检索配置记录，不需要新增业务检索表。

重建不会生成 Artifact revision，不改 Source cursor，不重跑历史抽取。仅恢复派生数据时使用已保存的确切历史内容和原始
证据，不能按当前模型重新解释旧事实。后续若支持在线重建，需要独立的索引代际切换协议。

## 8. API 与消费链路

| 边界 | 目标契约 |
| --- | --- |
| Memory Create/Get/Replace/History | 采用单条 Artifact ID 与精确 ArtifactRef；Replace 使用当前版本前提 |
| Memory Search | 每个命中自带正文、状态相关信息与精确 ref，保持 Scope 约束和既有评分含义 |
| Memory flush | 返回操作 ID、处理进度及本次受影响 refs，不再返回唯一集合 revision |
| Forget/Reactivate/Merge/Restore | 操作单条或显式多条 Artifact；返回变更 refs，维护历史与幂等语义 |
| Prepare | 移除同 Scope 所有 Memory 命中必须共享一个集合 ref 的假设 |
| Handoff、Experience、Dream | 新记录使用普通 Memory ArtifactRef；补齐 Memory family 的证据解析和历史资格判断 |
| Tags、ACL | 使用单条 Artifact 目标；旧 entry 授权与标签一对一迁移 |
| SDK、MCP、HTTP | 同步身份、结构化返回、错误与能力声明，语义由 Runtime/Family service 统一实现 |
| 抽取 Prompt | 新输入和动作 schema 使用新 definition 版本；自定义 Prompt 必须显式适配 |

OpenAPI 是 HTTP 契约源。实现阶段只修改本提案要求的不兼容 Memory 契约；为其提供显式新版本，并保留旧精确历史读取。
切换后的旧集合写入、旧 entry 写入、依赖集合 latest 的接口返回稳定的 `upgrade_required` 错误和可解析的新地址；不能返回
冻结集合并把它描述为最新，也不能伪造集合 revision 来兼容旧的集合级 CAS。

Experience、Skill、Topic Memory 的此次投影改造保持公开内容和引用格式不变。Handoff 等旧不可变制品中的 MemoryCitation
不改写，通过兼容解析器读取。精确历史可读不自动意味着该历史事实仍有资格作为当前生成证据。

## 9. 旧版本升级

### 9.1 升级策略与前置条件

采用显式 `plan / apply / verify / cutover` 阶段。工具复用现有 processing migration 的分批检查点、配置摘要和完成标记
模式，不能仅依靠 `create_all(checkfirst=True)`。普通启动不自动执行旧数据转换。

新版 Runtime 启动及开放业务入口前，强制校验数据库格式、迁移完成标记、配置摘要及投影就绪状态。发现未迁移的旧数据、
未完成阶段或摘要不匹配时，拒绝普通写入、当前列表和搜索，只开放迁移工具及明确允许的精确历史读取。只有 cutover 完成
才能开放新协议；迁移中断后重启不能把部分回填的 v2 对象当作完整当前集合。全新数据库须通过初始化记录明确标记为就绪。

迁移执行前停止所有旧写入和 Workers，保存一致备份。仅写 migration marker 无法阻止不认识 marker 的旧程序，因此必须
实际停止旧进程。维护窗口是本提案的升级前提；其时长根据 plan 盘点和演练测量确定，不预先承诺“短时间完成”。

旧 `powercontext.memory.v1` 与新 `powercontext.memory.v2` 在兼容期共存。保留旧集合、entry versions 和 lineage 为只读
历史，防止破坏已有外键和引用。通过显式格式/身份登记及 Memory 兼容读取器分流；当前列表、统计、搜索和 rebuild 排除旧
集合。不能靠 ID 前缀猜格式，或先加载整个 Scope 的 JSON 再识别新旧对象。

### 9.2 身份映射

使用稳定的映射：

```text
(scope_id, legacy_memory_artifact_id, legacy_entry_id)
    → new_memory_artifact_id
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
| schema | 为现有投影补列或建立迁移中的替换结构；创建映射、receipt 和完成标记；验证每个 DDL 的实际结果 |
| backfill | 分批回放 Memory 历史，写入新版本与映射；目标记录和检查点同事务提交；已存在目标必须核对摘要后才可跳过 |
| projections | 从确切当前版本回填 Memory、Topic、Experience、Skill 投影；复用匹配内容/profile 的当前向量；只对缺失向量计算 embedding |
| verify | 核对每个旧 manifest、身份、正文、证据、状态、引用、权限、cursor 和各检索通道覆盖；失败时保持未完成状态 |
| cutover | 校验冻结快照未变化后标记完成，启用新版 Runtime，允许新协议写入；旧数据继续只读 |

SQLite 不支持的原地列/虚拟表变更使用维护期替换和检查点；OceanBase DDL 可能独立提交，不能依赖一个大事务回滚所有 DDL。
迁移中可以使用临时或影子物理表，但完成后的检索职责仍落在上述已有投影中，不增加另一套长期业务检索表。

迁移不会通过 LLM 重新抽取旧 Source。旧历史向量不完整时，不要求批量重新生成全部历史向量；历史恢复时按实际需要计算。
旧集合 manifest 被冻结保留，迁移初期的磁盘占用可能上升，不能把停止新增目录快照描述为立即释放旧存储。

### 9.7 升级回退

新版尚未接受写入时，可以恢复备份并重新启动旧版本。新版接受写入后，旧集合不再同步变化，直接回退备份会丢失新数据。
此后默认采用前向修复；如需降级，必须停写并执行单独设计、验证的逆迁移。Memory 内容的历史恢复与部署降级是不同操作。

## 10. 成本、风险与备选方案

### 10.1 成本模型

设 N 为 Scope 当前记忆数，R 为写操作数，k 为一次操作实际变化的记忆数，H 为独立内容/状态变化总数，C 为一个 Topic 的
chunk 数量。

| 维度 | 当前结构 | 目标结构 |
| --- | --- | --- |
| Memory 目录历史 | 每次有效变更保存全 manifest，约 O(R × N) | 无目录快照；操作记录约 O(H) |
| Memory 正文历史 | 保存实际 entry 版本 | 保存实际单条 Artifact revision；状态变化也生成本条版本 |
| Memory 投影写入 | 已按变化 entry 增量更新 | 按 k 条 Memory 更新，并增加返回字段副本 |
| 抽取旧记忆输入 | 随 active entry 数增长 | 由检索与上下文预算限制 |
| Topic 投影更新 | 更新受影响 Topic 的当前 chunks | 展示字段冗余约增加 O(C × 标题摘要字节数)，仍按受影响 Topic 更新 |
| Experience/Skill | head 检索文本，另取当前正文 | OB 在 head、SQLite 在 FTS 各增加一份当前结构化正文，读取关联减少 |
| 迁移 | 保留旧数据 | 临时同时保留旧历史、新版本和映射，需预估额外磁盘 |

这些是结构上的成本变化，不是性能实测结论。需要测量投影副本、hash、向量写入、事务时长、索引规模、计划和召回率；
不能因减少 JOIN 就宣称所有场景更快，也不能忽略 Topic chunk 标题/摘要冗余的放大。

### 10.2 备选方案

| 方案 | 优点 | 未采用的原因 |
| --- | --- | --- |
| 保留集合，仅优化 manifest 编码 | API 兼容成本较小 | 集合身份、集合 CAS 和 entry 引用层仍存在，无法得到直接的 Scope → Artifact 模型 |
| 每个 family 新增一张统一 current 表 | 当前读取路径整齐 | 与已有全文、向量投影重复，增加迁移和维护面；选择复用现有表 |
| 保持搜索时关联正文和 head | 冗余少 | 召回依赖更多查询组合，ANN 与关联执行边界复杂；不能满足本提案直接返回当前内容的目标 |
| 把全部历史、当前内容和向量混在一表并用 is_current 过滤 | 表数量少 | 历史向量进入同一索引及标记维护更复杂，也改变所有制品共用的版本存储 |
| 在线双写迁移 | 减少停写窗口 | 同时维护两种版本语义和引用，冲突、追平、切换及逆迁移成本高；首版采用离线迁移 |

## 11. 实施顺序与验收

### 11.1 实施顺序

1. **契约与迁移规划**：明确 schema、动作、引用、旧格式支持范围和数据盘点工具；冻结验收数据集。
2. **投影直接返回内容**：完成 Experience/Skill 内容副本、Topic 字段补齐及全文/向量模式一致性；这部分保持公开契约。
3. **单条 Memory 与协调链路**：实现 Family writer、独立 revision、状态、归并、冲突记录、预算及幂等并发控制。
4. **消费者与兼容读取**：更新 OpenAPI、SDK、MCP、Prepare、证据解析、标签、权限及自定义 Prompt 升级。
5. **离线迁移与后端验收**：完成分批回放、重建、旧引用校验和恢复演练，通过后整体启用新版 Memory。

这些步骤可分别评审；中间版本不得让不完整的新 Memory 协议接受业务写入。涉及的 OpenAPI 变更通过生成工具产生代码，
不手工修改生成目录。

### 11.2 正确性验收

- 修改 M1 不增加 M2 revision，不写 Scope 成员快照；内容、证据与状态历史均可精确读取。
- 新增、修订、归并、停用、恢复及 generic Replace 成功后，active 目标版本在全部启用通道中可召回；inactive、retired
  和被归并对象退出召回；所有提交版本仍可按权限进行精确历史读取。
- 并发更新与治理变化期间，一次 hybrid 搜索的各通道共享一致快照，不混合相同 Artifact 的前后 revision。
- 同时修改、并发 create、响应丢失重试、处理 lease 失效时，不发生重复写入、部分归并或错误 cursor 推进。
- revise、merge、状态变化和 restore 均保留约定的精确证据；自动 merge 只在可核验的预授权范围内执行。
- 并发新增与重新激活不能共同超过 Scope active 容量；多条归并按净变化校验，减量补救不被超限状态阻断。
- Scope、标签、ACL、inactive/retired 和 unresolved conflict 语义在搜索、Prepare、精确历史读取中一致。
- FTS-only、vector、hybrid 均保留返回契约；Skill 治理变化不会留下可召回的旧 SQLite FTS 行。
- Topic 摘要更新后，各 chunk 返回相同 revision 的标题和摘要；不返回半更新分块。
- 每个旧逻辑 entry、正文版本和状态变化可追溯；逐个旧 manifest 验证区间映射，不只比较最终 head。
- compact 历史、旧集合引用、Handoff/Experience 的旧 citation、entry 授权和标签均保持原有语义。
- 迁移与索引重建每个阶段中断后可继续；重复 apply 不新增重复对象，不重跑已消费 Source，不改旧 hash。
- 未完成迁移或配置摘要不匹配时，新版 Runtime 重启仍拒绝普通写入、当前列表和搜索。
- 原地重建中断或重启后搜索仍保持未就绪，只有完整校验通过才恢复；精确历史读取不依赖该索引就绪状态。

### 11.3 规模和 OceanBase 验收

固定目标 OB 发行版、补丁号、SQLite/vec0 版本、embedding profile、SQL 与数据集后，分别验证：

- 不同 Scope 数量、大小与过滤选择性下的实际计划、P50/P95/P99、扫描量和内存；包含历史版本远多于当前对象的情况。
- 大 Scope ANN 使用目标向量索引；小 Scope 经普通索引筛选后精确计算可作为明确策略，不能出现未声明的全库暴力搜索。
- 主召回的排序、LIMIT 和过滤符合目标后端约束；没有为了回填正文而把 ANN 与历史表关联在同一执行阶段。
- 标签、权限过滤后的 recall@k、结果数量及延迟；不能只用无过滤的单 Scope 小数据证明可扩展性。
- 每条 Memory 更新和每个 Topic 更新的行数、字节数、embedding 次数、事务时长；识别 Scope 全量扫描或重写。
- 新旧索引重建、迁移临时空间与历史向量恢复的实际资源成本。

优先通过公开接口验证行为。后端物理执行计划使用目标真实实例采集，模拟器、SQL 字符串断言和本地 mock 不作为实际索引
使用的证据。测试不得只为固定内部调用顺序或表名而增加；计划验收服务于明确的性能和有界工作量要求。

## 12. 依据

### 当前代码

| 事实 | 代码入口 |
| --- | --- |
| 公共版本、head、entry、Topic 投影表 | [tables.py](../../../src/powercontext/builtin/persistence/tables.py) |
| Memory manifest、容量与 entry 模型 | [memory/models.py](../../../src/powercontext/builtin/artifacts/memory/models.py) |
| 抽取 active 输入、manifest 构造、生命周期 | [memory/service.py](../../../src/powercontext/builtin/artifacts/memory/service.py) |
| add/revise 抽取契约 | [memory/extraction.py](../../../src/powercontext/builtin/artifacts/memory/extraction.py) |
| Memory 增量投影提交 | [persistence/memory.py](../../../src/powercontext/builtin/persistence/memory.py) |
| OB Memory 的正文 JOIN 和标签精确查询 | [oceanbase/memory_index.py](../../../src/powercontext/builtin/persistence/oceanbase/memory_index.py) |
| Topic ANN 有界召回与 merge join 兼容说明 | [oceanbase/topic_memory_index.py](../../../src/powercontext/builtin/persistence/oceanbase/topic_memory_index.py) |
| Experience/Skill 结果内容与投影 | [experience_index.py](../../../src/powercontext/builtin/persistence/experience_index.py) |
| OB head 全文索引与正文关联 | [oceanbase/experience_index.py](../../../src/powercontext/builtin/persistence/oceanbase/experience_index.py) |
| SQLite 当前 FTS 与 head 校验 | [sqlite/experience_index.py](../../../src/powercontext/builtin/persistence/sqlite/experience_index.py) |
| 向量可选、FTS 必备的组件装配 | [runtime/composition.py](../../../src/powercontext/builtin/runtime/composition.py) |
| 可恢复的离线迁移范式 | [processing_migration.py](../../../src/powercontext/builtin/persistence/processing_migration.py) |

### OceanBase 文档

- [HNSW 系列索引](https://www.oceanbase.com/en/docs/common-oceanbase-database-standalone-1000000005411366)：ANN 语法与距离匹配约束。
- [OB 4.3.5 向量最近邻搜索](https://www.oceanbase.com/docs/common-oceanbase-database-cn-1000000003376605)：精确与索引搜索的计划示例。
- [OB 4.6.2 VECTOR_INDEX Hint](https://www.oceanbase.com/docs/common-oceanbase-database-ai-1000000006782252)：索引与前后过滤选择；使用前需核对部署版本支持。

文档规则用于定义验证点，不作为本项目查询已经获得特定执行计划的证明。
