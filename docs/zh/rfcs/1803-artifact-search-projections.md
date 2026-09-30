---
title: 制品检索宽表与单表召回
---

- 提案名称：`artifact_search_projections`
- 起始日期：2026-09-30
- RFC PR：[oceanbase/powercontext#1803](https://github.com/oceanbase/powercontext/pull/1803)
- 修订 RFC：[0014](0014_memory_layer_design.md)、[0051](0051_experience_skill_artifact_families.md)、
  [0080](0080_memory_search_reranking.md)、[1417](1417_topic_memory.md)
- 相关 RFC：[1396](1396_handoff_access_control.md)、[1467](1467_artifact_tags.md)、
  [1549](1549_artifact_family_unification.md)、[1652](1652_memory_quality_and_lifecycle.md)
- 后续 RFC：[Automatic Memory 独立记忆制品，#1809](https://github.com/oceanbase/powercontext/pull/1809)
- 实现参考：[制品检索宽表实现方案](../design/artifact-search-projections-design.md)

# 摘要

本 RFC 为所有提供 search 能力的 Artifact Family 定义共同的检索存储契约：每种制品维护自己的当前检索宽表，将匹配、
过滤、排序和结果返回所需的字段保存在同一表中。OceanBase 的全文与向量召回只读取该表，不通过 JOIN、关联子查询或
逐命中回查其他业务表补齐结果。权威 revision 与 head 继续负责历史和当前身份，检索宽表是与发布原子更新、可重建的
派生数据。Topic Memory、Experience、Skill 和 Memory 采用这一契约；新的 Automatic Memory 提案必须依赖本 RFC。

# 动机

## 召回正确性和索引使用不应依赖跨表补齐

当前 Memory、Topic Memory、Experience 和 Skill 已经有不同形式的当前投影，但检索所需字段仍分布在多张表：

| 制品 | 当前匹配位置 | 为返回完整结果进行的关联 |
| --- | --- | --- |
| Memory | entry head 全文投影、独立向量投影 | entry versions 中的正文 |
| Topic Memory | Topic/Chunk 全文投影、独立向量投影 | 当前 Topic 的展示字段、chunk 正文与位置 |
| Experience | 公共 head 的 searchable_text | `pc_artifacts.content` |
| Skill | 与 Experience 共用的全文路径 | `pc_artifacts.content` 中的 SkillContent |

Topic Memory 的 OceanBase 实现明确记录：在 CE 4.3.5.6 中，距离排序向量扫描直接参与 merge join 可能丢失近邻，因此
采用 ANN 子查询先 LIMIT、再关联展示内容的方式。Memory 向量召回仍直接关联 entry 正文；带标签查询还会改变 ANN 路径。
Experience 和 Skill 当前只做全文检索，全文命中后再关联权威版本内容。

PMC 讨论同时反馈了 JOIN 组合导致全文索引路径退化的问题，并确定单表召回为架构约束。向量问题有现有代码说明作为
依据；全文退化在本提案中记录为会议反馈，具体版本、SQL 和执行计划应补入实现验收证据，不能据此宣称所有 OB JOIN
都会丢失结果或退化。选择单表设计，是为了减少检索正确性与性能对这些查询组合的依赖。

## 只补正文不能形成完整的检索契约

如果正文已在向量表里，但标签、当前状态或结果排序仍需关联其他表，召回路径仍然存在相同的组合复杂度。因此需要定义
完整的搜索单元：除了检索文本和向量，还包括资格过滤、排序和返回字段，以及这些字段如何随权威状态改变。

这项设计独立于 Memory 的身份重构。无论一条记忆采用 entry 还是独立 Artifact，Topic Memory、Experience 和 Skill
都需要完整的当前检索投影。将其作为公共 RFC，可以让新的 Family 直接遵守共同契约。

## 预期结果与非目标

用户得到的正文、引用、过滤结果和搜索评分保持各 Family 既有契约；维护者能够从一张 Family 宽表和明确的查询预算分析
一次召回。减少关联是否改善具体负载的延迟、吞吐和召回率，仍需在支持的后端上测量。

本 RFC 不将所有制品放进同一张物理表，不合并权威历史表与搜索表，不为没有 search 能力的制品强行建立索引，也不改变
各 Family 的内容定义、生成授权或证据含义。Automatic Memory 的新身份、合并策略和旧 entry 转换由依赖本 RFC 的领域
提案定义。本次升级采用停服迁移，不包含服务运行期间的双写、增量搬运和按 Scope 灰度切换。

# 使用说明

## 每种制品有自己的检索宽表

以下表名表示职责，最终 DDL 名称由实现确定：

```text
权威数据                              当前检索投影
pc_artifacts：全部不可变 revision       pc_topic_memory_search
pc_artifact_heads：每个 Artifact head   pc_experience_search
                                      pc_skill_search
                                      每个 Memory Family 自己的 search 表
```

搜索 Experience 时，全文匹配、Scope 与状态过滤、排序和 ExperienceContent 返回都从 Experience 的宽表完成。读取它的
指定历史 revision，则继续使用权威版本读取接口。搜索不用逐条回到历史表取正文，也不再关联 head 才能确认当前版本。

向量和全文是对同一份搜索单元的两种检索方式。支持向量的 Family 在同一宽表保存 embedding 和全文字段；只支持全文
的 Experience、Skill 不因此被要求启用 embedding。是否支持某种搜索模式仍由 Family 能力声明决定。

## 一张宽表不要求一个 Artifact 只有一行

一个 Topic 可以产生一个主题单元和多个正文片段单元：

```text
pc_topic_memory_search
T1@3 / topic       标题、摘要、主题检索文本、主题向量、过滤字段
T1@3 / detail / 0  标题、摘要、片段原文与位置、片段向量、过滤字段
T1@3 / detail / 1  标题、摘要、片段原文与位置、片段向量、过滤字段
```

命中任一片段都能直接返回所需信息。片段没有独立公开 Artifact 身份；结果仍引用 T1@3，并携带片段位置。
主题全文、主题向量、片段全文和片段向量按各自预算召回，然后按现有 Topic 规则折叠与融合。不能把所有行混成一次
全局 top-k，让拥有较多片段的 Topic 占满候选。

## 发布、停用和恢复保持一致

将 T1@3 修订为 T1@4 时，权威版本、head 和全部当前搜索单元一起提交。调用方看到提交前或提交后的完整结果，不能得到
T1@4 的标题和 T1@3 的片段。停用对象退出默认召回，旧精确引用继续可读。恢复历史内容时创建或选择哪个权威版本，遵循
对应 Family 的领域契约；宽表只投影最终可检索的当前版本。

## 升级期间使用维护窗口

运维停止服务写入及后台 Worker 后，由独立迁移工具创建宽表、回填当前内容、重建索引并验证。校验通过后再开放新版本
服务。新 Runtime 不执行长期在线搬运或双写。只有检索投影变化时，不产生新的 Artifact revision，也不重新抽取 Source。

# 参考级说明

## 设计不变量

1. **每 Family 独立。** 每种提供 search 的制品拥有自己的检索宽表、搜索单元定义和索引；不采用跨 Family 的通用大表。
2. **召回字段完整。** 匹配、过滤、排序、搜索结果返回字段在本表可用；不依赖其他业务表进行结果补齐。
3. **仅投影当前。** 只对该 Family 当前允许搜索的版本建立可召回单元；历史通过精确版本接口读取。
4. **发布原子。** 所有正文、治理和投影写入入口遵守相同事务边界；移除读时 head 校验不能削弱当前性。
5. **预算明确。** Scope 与授权资格在候选截断前生效；每个通道有界召回，不采用全库 k 或无界逐项回查。
6. **历史保持。** 重建不改变权威身份、内容 hash、证据、Source cursor 或已提交 revision。

## 权威存储与搜索存储

`pc_artifacts` 和 `pc_artifact_heads` 保留现有身份与版本职责。本 RFC 不向公共 head 增加完整的 `search_content`，
Experience 和 Skill 的返回内容进入各自宽表。公共表仍可用于 get、history、治理及重建，不参与普通 search 的正文补齐
或当前版本确认。

现有 searchable_text 和专用投影在迁移前继续由旧版本使用。迁移完成后，每个 Family 的搜索只读取完成校验的新宽表，
旧布局退出普通搜索；不能让缺失字段的请求静默退回旧 JOIN 路径。相关旧列或旧表的回收由版本化迁移工具负责，不能删除
仍用于权威历史或兼容读取的记录。

## 搜索单元与字段

宽表的逻辑主键为 `(scope_id, artifact_id, unit_kind, unit_id)`，revision 是当前投影值。Family 已由表身份确定，
可以保留 family 列用于自描述，但不能据此把多个 Family 合并到一个共享索引。

| 字段组 | 必须表达的内容 |
| --- | --- |
| 精确身份 | Scope、Artifact ID、revision、搜索单元类型与标识、对应权威内容摘要 |
| 匹配输入 | Family 定义的全文字段；启用向量时的 embedding、嵌入输入摘要与 profile |
| 资格与排序 | 查询使用的状态、类型、标签、时间、质量或可见性属性 |
| 返回内容 | API 已承诺的原文、标题、摘要、结构化 payload、片段位置和必要元数据 |
| 投影校验 | schema、analyzer、chunking 与 embedding 配置版本，以及治理 generation 等失效依据 |

新单条 Memory、Experience 和 Skill 通常每个 Artifact 一行。旧 Memory 如继续提供 search，须以 entry 身份扩展自己的
unit_id 并保留旧 citation 字段；该例外只表达旧 Family 的搜索粒度，不决定新 Memory 的领域身份。

Topic 使用 topic/detail 单元类型。每个 detail 行保存本片段正文、ordinal、offset，以及对应 revision 的 title/summary
和过滤字段；不复制完整 detail。标题、摘要或治理字段变化可能需要更新同 Topic 的全部单元，写放大需要计量。

Experience 行保存完整 ExperienceContent；Skill 行保存 SkillContent 和包引用。包内可检索文本可以进入全文输入，
但不将整个包目录复制进每行。searchable_text 是匹配输入，不能当作原文或结构化 payload 的替代品。详情读取接口可以
按精确 ref 另行取未在 search 契约中承诺的内容；不能把 search 必需的字段移到逐项详情调用来规避完整性要求。

## 查询和过滤

OceanBase 每个全文或向量通道只查询一张 Family 宽表，不使用 JOIN、关联其他业务表的子查询，或命中后的 N+1 补齐。
同一次 search 可以执行多个独立的单表通道查询，然后在有界结果上融合、去重、折叠和 rerank。稳定的次级排序不得破坏
后端对向量索引距离排序的要求。

Scope、生命周期、标签及授权相关条件必须成为召回前的合法资格约束。不能先从全库取固定 top-k，再过滤不属于 Scope
或无权读取的对象。授权层可以先验证调用方并得到有界的查询上下文，但不能通过无界枚举所有可见 Artifact ID、逐命中
鉴权，或修改过滤含义来伪装单表检索。

标签与资源可见性等对象属性在宽表中投影。权威授权记录继续决定访问资格；需要投影的属性必须随授权变更同步更新，
权限撤销不能等待异步回填。Scope 级授权可在进入搜索前执行，而对象级条件需要明确可在本表表达的谓词。已有过滤和授权
场景必须获得等价实现，不能因新布局难以表达而静默扩大权限或删除能力；不满足时不得切换该 Family。

每个 Family 必须列出全文与向量实际使用的全部 WHERE、ORDER BY、返回字段，逐一对应到宽表列或明确的查询参数。
标签和对象级授权的具体编码、索引方式及更新成本属于合并前需要完成的设计，而非可留给应用层兜底的问题。

## 当前性与写入一致性

Family writer 在一个事务内提交权威 revision、lineage、head 和受影响搜索单元。治理、标签和授权属性的独立变化，也
在对应权威事务中同步投影。更新 Topic 时，缺失的新单元、残留的旧单元或新旧 revision 混合均为发布失败。

这覆盖手工 Create/Replace、领域服务、后台抽取、审核发布、恢复、停用及受支持的跨 Scope 发布。Skill 的治理 generation
与内容 revision 是不同维度，不能仅在正文变化时更新其搜索资格。并发提交使用现有 Family 的版本与治理前提，重建不能
绕过这些前提成为另一条业务写入路径。

一次 hybrid 搜索的所有数据库通道和相关资格读取共享真实一致快照；仅复用同一连接不足以保证这一点。查询 embedding
先在事务外生成；取完有界候选后结束读事务，再执行不依赖可变数据库状态的融合和模型 rerank。搜索的“当前”指该查询
快照内最新已提交的合格版本，不代表 Source 已经完成异步处理。

## 向量与全文能力

Family 声明支持 FTS、vector 或 hybrid。未配置 embedding 的部署必须保留完整全文行为；Experience、Skill 当前仅 FTS，
本 RFC 不额外开放其向量 API。使用向量的宽表同时保存原文和全文输入，向量值不能成为正文读取的前置条件。

首版一个 Family 的活动向量索引使用一个明确的 embedding profile，涵盖模型、维度、距离算法、归一化和配置版本。
固定 VECTOR 维度不能通过增加 fingerprint 列解决；相同维度也不代表来自相同语义空间。不同 profile 不进入同一次距离
排名。模型、维度或嵌入输入变化，需要匹配新配置的重建与完整性校验；不因 revision 数值变化就盲目重新 embedding，也
不因正文表面相同就复用实际输入不同的向量。

首版 profile、分词配置或分块策略的不兼容变化采用维护窗口重建。持久化 readiness 标记在重建前关闭受影响检索，进程
重启也不能开放半完成索引；所有启用通道通过版本、摘要、单元覆盖和 profile 校验后才标记 ready。历史正文读取不依赖
embedding 可用性。本 RFC 不要求保存全部历史向量。

## SQLite 后端

“一种制品一个完整搜索数据集”的逻辑契约适用于 SQLite。SQLite FTS5 与 vec0 是不同的虚拟索引机制，物理实现需要
由 Family 自己拥有的宽表及辅助索引承接，不能据此承诺它们与 OceanBase 使用相同 DDL。

辅助 FTS/vec0 与本 Family 宽表之间为取得同一搜索单元进行的索引映射，是 SQLite 的后端边界；不得关联公共历史、head
或其他 Family 的业务表补正文或资格。若必须冗余字段以在候选截断前过滤，其副本也要原子维护并可校验。不得采用全表
邻居数模拟 Scope 过滤。支持的扩展版本和每种过滤组合需要被明确验证，不能用 SQLite 例外放宽 OceanBase 的单物理表
召回要求。

## 停服迁移与重建

迁移工具与在线服务分离，记录支持的起始格式、目标格式和配置摘要。停止所有旧 API 写入及后台 Worker，保存一致备份，
再分批回填当前搜索单元。正文来自确切权威 revision；Skill package、Topic chunk 和直接证据按其保存的契约读取，
不重新调用模型解释历史。已有向量仅在嵌入输入和 profile 精确匹配时复用。

目标数据与检查点原子提交；重复执行核对既有目标，不能重复创建单元。迁移中断可恢复，但普通服务保持未就绪。通过
覆盖、内容、引用、过滤和索引校验后写入完成标记，新 Runtime 启动时检查目标格式和标记。变更 DDL 的事务能力按实际
后端处理，不能以大事务可以回滚所有 DDL 为前提。

投影迁移不推进 Artifact revision 或 Source cursor。若同一发布同时引入 Automatic Memory，可在一个维护窗口中先完成
它的权威模型转换，再按本 RFC 构建检索宽表。契约评审上公共 RFC 在先；数据执行上必须先有正确的目标权威内容。
如果仅交付本 RFC 而旧 Memory 仍可搜索，也必须给旧 Family 提供符合契约的投影，不等待领域重构。

尚未接受新业务写入时可恢复备份。新版本开始写入后的部署降级，需要明确的格式兼容或逆迁移，不能通过恢复旧备份
抹掉新数据。保留权威历史与旧精确引用的职责属于各 Family；投影工具不能借重建删除它们。

## 验收范围

- 所有受支持入口发布后的精确引用、内容、资格与现有公开评分契约一致；没有旧版本或停用对象残留召回。
- 各通道不依赖权威正文、head、标签或授权业务表的查询关联；不把 N+1 调用伪装成无 JOIN。
- Topic 各通道的预算、片段折叠和融合与其契约一致；同一查询不混合旧、新 revision。
- 在真实目标 OB 发行版与补丁上检查执行计划、扫描量、过滤后 recall@k、延迟和内存；包含多 Scope、标签、不同权限
  选择性及历史量远大于当前量的情形。SQL 文本或 mock 不证明索引实际生效。
- 计量每次正文、标签、授权和状态变化更新的行数、字节数、embedding 次数及事务时长，特别覆盖大 Topic 的多单元更新。
- 验证迁移中断续跑、无向量部署、SQLite 辅助索引一致性、profile 重建与重启后的 readiness 门禁。

# 缺点

**存储与写放大增加。** 返回内容与过滤属性在宽表冗余，Topic 每个片段还携带标题、摘要和资格字段。频繁标签或授权变化
也可能成为写热点。各 Family 一张表增加索引与迁移对象数量，必须用测量确认查询收益值得承担这些成本。

**维护责任更严格。** 搜索无法再依靠 JOIN head 修正旧行，任何漏更新的治理或写入入口都会造成过期结果。把权限属性
放入投影还要求明确撤销一致性，不能只解决内容更新。

**物理实现不能完全统一。** OceanBase 原生索引与 SQLite 虚拟表不同；向量维度和 profile 也限制通用化。公共契约统一
行为，不代表一个 SQL 模板适合所有后端。

**停服迁移有可用性成本。** 需要容量盘点、额外磁盘和维护窗口。新投影只保存当前内容，权威历史仍会增长。

# 设计理由与替代方案

## 为什么每 Family 一张宽表

不同 Family 有不同搜索单元、返回 payload、索引能力和更新节奏。独立表让这些职责与所属服务一致，也避免 Experience
与 Skill 为全文返回内容而改造所有制品共用的 head。共享的是契约和必要的基础能力，不是跨 Family 的物理索引。

## 所有制品共用一张搜索表

这便于统一查询入口，但会混合不同粒度、不同 embedding 配置和生命周期，产生稀疏字段及更复杂的资格过滤。跨 Family
检索可以在各自有界候选上融合，本提案不为统一入口而强行共享底层表。

## 保留 JOIN，先召回再补正文

有界 ANN 子查询是现有 Topic Memory 使用的可行绕行方式，冗余也更少。但仍需为全文、向量、过滤、排序和结果补齐维护
不同查询组合。PMC 已将完整单表召回确定为目标，因此不继续依赖这些组合来维护主搜索路径。

## 全文与向量各一张完整表

可以让每个通道独立无 JOIN，但要重复保存返回与过滤字段，并持续维护两份当前数据。首选同一 Family 宽表上的两种索引，
以减少不同步的面；SQLite 虚拟索引的物理限制由专门的后端契约处理。

## 不做此事

可以继续逐条优化热点 SQL，但每个新 Family 都可能重新引入正文、状态或权限关联，索引路径和当前性责任仍然分散。
没有公共契约，Automatic Memory 的实现也无法约束其他制品的检索。

# 先例

[RFC 1417](1417_topic_memory.md) 已确立当前投影、Topic/Chunk 单元、渐进披露与原子发布。
[RFC 0051](0051_experience_skill_artifact_families.md) 定义 Experience/Skill 的内容与准入边界；
[RFC 1467](1467_artifact_tags.md) 和 [RFC 1396](1396_handoff_access_control.md) 提供需要保持的标签及授权语义。
本 RFC 扩展这些机制的检索存储布局，不改变它们已经定义的权威内容或授权对象。

现有 [OB Topic 索引](../../../src/powercontext/builtin/persistence/oceanbase/topic_memory_index.py)、
[OB Memory 索引](../../../src/powercontext/builtin/persistence/oceanbase/memory_index.py) 和
[OB Experience/Skill 索引](../../../src/powercontext/builtin/persistence/oceanbase/experience_index.py)
展示了本 RFC 要收敛的查询路径。代码证据和 PMC 反馈描述当前问题，不代表新宽表已实现或性能验收已经完成。

# 未解决问题

以下问题需要在合并前完成设计或明确支持边界：

1. **过滤字段编码。** 标签、资源级授权、撤销和跨 Scope 读取如何形成单表资格谓词，采用哪些索引，怎样约束扇出与更新
   成本；现有授权语义必须逐项覆盖。
2. **后端支持矩阵。** 首批 OB 补丁、全文分词和向量索引组合，以及 SQLite/vec0 版本与过滤路径；全文退化反馈需附可复核
   的 SQL、计划与数据规模。
3. **Profile 约束。** 当前配置是否允许同 Family 不同 Scope 使用不同 profile；首版限制为一个有效 profile 时，需要明确
   兼容性与迁移拒绝行为，不能直接覆盖配置。
4. **索引与容量预算。** 宽表 payload、Topic 单元数、标签/权限扇出、候选量和重建空间的初始限额，需要代表性数据校准。

具体 DDL、批大小、工具命令和模块拆分见实现方案；它们不得改变本文的单表召回、授权与历史不变量。

# 后续可能性

- 跨 Family 融合搜索可以复用各表有界候选，但不要求合并物理表。
- 多 profile 索引或在线索引代际切换需要单独定义资源、切换与恢复契约，不属于首版。
- 商业版可以提供在线升级，并由栅栏版本保留回填、追平、双写和切换能力。历史版本必须先完成栅栏版本的数据迁移，
  持久化完成标记后才能继续升级；后续版本可只保留轻量门禁。栅栏发布物和支持范围须持续维护，不能因为某个实例迁完
  就删除仍对其他用户承诺的升级能力。
