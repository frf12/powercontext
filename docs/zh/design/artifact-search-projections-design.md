---
title: 制品检索宽表实现方案
---

# 制品检索宽表实现方案

本文落实 [RFC 1803：制品检索宽表与单表召回](../rfcs/1803-artifact-search-projections.md)。实现目标是每种支持 search
的 Artifact Family 拥有完整的当前搜索数据，OceanBase 每个召回通道从单张物理表完成匹配、过滤、排序与返回。
本文中的表名、列名和工具阶段为实现设计，尚不代表已发布接口。公共 RFC 规定行为，具体后端设计需提供执行计划和容量证据。

## 1. 职责与数据流

```text
Family 领域写入 / 审核 / 治理变更
  → 校验 revision 与治理前提
  → 生成目标当前搜索单元
  → 一个事务：权威数据 + head + lineage + 本 Family 搜索单元

search 请求
  → 解析 Family 过滤条件、校验 Scope 访问资格
  → 事务外生成可选 query embedding
  → 一致快照内执行各有界单表召回通道
  → 结束读事务
  → 折叠、融合、rerank → 已含正文与精确引用的结果

精确版本读取 / 历史读取 → 权威存储
停服迁移 / 重建 → 确切权威 revision → 相同搜索单元生成逻辑
```

`pc_artifacts` 和 `pc_artifact_heads` 保留职责，不向公共 head 增加完整 `search_content`。
Family 负责内容解释、资格规则、单元生成和结果适配；基础持久化负责事务与后端执行。共享稳定的一致性约束和配置摘要，
不为相似 SQL 引入跨 Family 稀疏 payload 或绕过领域写入的通用投影服务。

## 2. Family 布局

| Family | 搜索单元 | 内容与资格字段 | 现有能力与变更 |
| --- | --- | --- | --- |
| Topic Memory | 一条 topic，零到多条 detail | 标题、摘要、主题输入或片段原文、位置、标签与资格 | 四个全文/向量通道统一读本 Family 宽表 |
| Experience | 每个当前 Artifact 一条 | 完整 ExperienceContent、匹配文本、类型、时间、治理字段 | 从公共 head 全文 + revision JOIN 迁为自己的全文宽表 |
| Skill | 每个当前 Artifact 一条 | SkillContent、package ref、包检索文本、治理 generation、资格 | 从共用 Experience 路径迁为自己的全文宽表 |
| 旧 Memory（如果仍支持 search） | 每个当前可检索 entry 一条 | collection/entry/version 引用、原文、状态、标签等 | 保留旧引用粒度，在本 Family 内完成单表召回 |
| Automatic Memory | 每个当前可检索 Artifact 一条 | 独立 Artifact ref、原文、状态和过滤字段 | 由依赖 RFC 定义身份和迁移，遵守本方案的存储契约 |

表名可选 `pc_topic_memory_search`、`pc_experience_search`、`pc_skill_search` 等。Memory 两种 Family 如果均受支持，
必须拥有各自物理投影，不能把 family 字段塞进旧 entry 表后共用索引。是否继续提供旧 Family 的 search，由发布组合决定；
保留历史精确读取不等于保留旧实时搜索。

每表的逻辑键为 `(scope_id, artifact_id, unit_kind, unit_id)`，不是历史 revision 键；revision 是该单元的当前值。
Topic detail 的 unit_id 只在对应发布单元内使用，不成为新的公共 Artifact ID。旧 Memory 的 unit_id 包含 entry 身份。

### 字段清单

每个 Family 提交一份从请求模型到 SQL 的字段矩阵，覆盖：

- 身份：Scope、Artifact ID、revision、unit_kind/unit_id、权威内容 hash。
- 匹配：全文输入、可选 embedding、embedding 输入 hash、完整 profile 标识。
- 过滤与排序：请求实际使用的类型、状态、标签、时间、质量和可见性属性。
- 返回：API 承诺的原文、标题、摘要、结构化内容、引用、证据元数据与片段位置。
- 校验：投影 schema/config digest、analyzer、chunking、治理 generation 等。

正文 payload 与 searchable_text 分开定义。Skill 行保存包引用和契约要求的可检索文本，不复制整个包目录。Topic detail 行
只保存本片段正文；标题、摘要和资格冗余在每行，必须记录大 Topic 下的写放大。

## 3. 查询与索引

### OceanBase

每通道的 FROM 只包含对应 Family 宽表；WHERE、ORDER BY 和 SELECT 所需的业务字段均在该表。
禁止回查公共 head/revision，也禁止通过相关子查询、逐命中详情调用或无界 allowed-ID 列表规避限制。

索引设计按支持的 OB 补丁完成，不把逻辑 SQL 当作索引已命中的证明：

1. 主键支持按 Scope 和 Artifact 原子替换其单元。
2. 全文索引只包含 Family 明确声明的匹配输入；选择与当前契约兼容的 analyzer。
3. 向量列维度、距离函数、归一化和 ANN 排序必须与 profile 一致。
4. Scope、状态、标签和对象资格在候选截断前生效；确定实际后端可以实现的过滤方式和索引。
5. 稳定次级排序、分页与 ANN 语法一并验收，不能为了稳定排序导致向量路径不可用。

Scope 授权在搜索前完成；对象级资格必须有本表可表达的谓词。标签与授权编码需要在合并前给出具体列、谓词、索引和
权限撤销的同步更新路径。不能先限制全库候选再过滤，也不能只靠结果返回前鉴权补救召回率或权限一致性。

一个 hybrid 请求内的各通道使用真实一致快照。查询 embedding 在事务外准备；获取候选后结束读事务，再进行模型 rerank。
数据库结果已经携带所有 search 必需字段，融合阶段不再访问变化中的 head 或权威正文。

### Topic 四通道

topic FTS、topic vector、detail FTS、detail vector 分别带 `unit_kind` 限定和独立预算。应用层在有界结果中按确切
Topic revision 折叠、选片段和融合。不要将所有单元共用一次 top-k，也不要重复计算 chunk 为独立 Artifact 的相关度。
保留既有返回引用、评分和渐进披露契约；预算调整要给出独立依据。

### SQLite

Family 宽表持有完整搜索单元，FTS5/vec0 可以作为其辅助索引。索引到同一单元的映射不得读取公共历史/head 或其他 Family
业务数据。为过滤冗余的列必须与宽表原子维护。检查 vec0 实际版本支持的过滤组合，禁止用全库邻居数补偿缺失的 Scope
过滤。SQLite 辅助索引方案不改变 OB 单物理表约束。

## 4. 写入边界与配置

梳理每个 Family 的 Create/Replace、后台生成、审核通过、恢复、停用、标签、授权和跨 Scope 发布入口。领域校验完成后，
在同一事务内提交权威 revision/head/lineage 与搜索单元；独立治理变化也必须更新资格字段。Topic 以一个 Artifact 为
单位替换所有相关单元，失败时不能留下新旧混合数据。

Skill 内容 revision 不替代治理 generation。仅以正文 hash 决定是否更新投影会漏掉停用、授权撤销和标签变化；实现应将
匹配输入、返回内容和资格的失效依据分别列出。异步生成 embedding 可以在提交前完成，但提交仍需检查计划的版本前提。

首版每个 Family 的活动向量索引使用一个明确的有效 profile。若现有 Scope 配置不同，迁移规划必须显式报告不兼容并
要求选择受支持目标，不能因向量维度恰好相同而混排。是否扩大支持多 profile 属于后续设计。

profile/analyzer/chunking 不兼容变更采用停服重建。持久化 readiness 包含目标格式、配置摘要和完成状态；重建前关闭，
验证完整后开启。新进程启动不能依据表存在就跳过未完成状态。无 embedding 部署只校验并开放其声明的全文能力。

## 5. 独立停服迁移工具

工具与 Runtime 运行代码分离。提供规划、执行、验证、切换四个明确阶段，实际命令名由实现确定：

| 阶段 | 输入与动作 | 输出和恢复边界 |
| --- | --- | --- |
| plan | 读取格式、Family 能力、profile、当前单元规模、历史与包可用性 | 可执行计划、配置摘要、空间/时间估算、不兼容项 |
| apply | 确认旧 API 和所有 Worker 已停止，校验一致备份，关闭 readiness，分批回填并建索引 | 持久检查点、批次校验结果，允许断点续跑 |
| verify | 核对覆盖、hash、精确引用、资格、各通道与物理索引 | 完整报告；失败保持未就绪 |
| cutover | 写入目标格式及完成标记，确认新 Runtime 能通过门禁 | 启动新服务，记录开始接受新业务写入的边界 |

每批目标数据与检查点原子提交。重复执行根据确定的键与 hash 核对目标；遇到不一致停止，不能重解释历史生成不同身份。
DDL 是否可回滚按后端处理，备份/恢复不得依赖跨所有 DDL 的单事务。迁移数据重建来源是精确权威版本和其保存的证据，
不重放模型抽取，不推进 Source cursor，不新增权威 revision。

已有向量的输入与完整 profile 精确匹配时可以复用；否则重新生成并纳入窗口和费用估算。仅 revision 改变不强制重新
embedding；只看正文相同也不足以复用。历史内容与精确引用不依赖历史向量是否仍存在。

若同一发布包含 Automatic Memory，先按它的领域迁移工具完成独立 Artifact/history 转换，再调用本方案构建其投影。
共享一个维护窗口及切换门禁，避免重复停服；公共投影工具不重复实现 entry 到 Artifact 的历史映射。
如果本 RFC 单独先上线，旧 Memory 仍有 search，则必须同步完成旧 Family 的宽表改造。

切换前可以从备份恢复原部署。新版本接受业务写入后，降级需要明确的数据格式兼容或逆迁移；不能用旧备份丢弃新数据。
旧搜索布局的清理属于版本化工具，权威历史和兼容引用不在可清理范围。

正常发行版仅保留格式及 readiness 门禁，不携带运行时双写、追平或每 Scope 在线切换逻辑。未来商业版若提供在线迁移，
需要栅栏版本、强制升级路径、完成标记和可持续取得的栅栏发布物，另行确定支持期限。

## 6. 实现顺序与模块边界

1. 完成字段矩阵、授权/标签谓词、后端版本和 profile 支持边界，确定是否与 Automatic Memory 同版交付。
2. 在各 Family 内容服务附近定义可重用的搜索单元生成逻辑，使正常写入与迁移使用相同解释规则。
3. 在后端持久化模块实现各表及单表查询，逐 Family 接入全部写入、治理和读取入口。
4. 独立工具实现有检查点的回填、校验、readiness 和恢复；保持权威版本及引用不变。
5. 在真实支持后端完成正确性、索引和成本验收，再允许迁移切换。

相关实现入口：

- [Memory OB 索引](../../../src/powercontext/builtin/persistence/oceanbase/memory_index.py)。
- [Topic Memory OB 索引](../../../src/powercontext/builtin/persistence/oceanbase/topic_memory_index.py)。
- [Experience/Skill OB 索引](../../../src/powercontext/builtin/persistence/oceanbase/experience_index.py)。
- [Skill 搜索适配](../../../src/powercontext/builtin/artifacts/skill/search.py)。
- [Family 写入分派](../../../src/powercontext/builtin/persistence/family_management.py)。

本方案不要求先修改所有制品的公共存储结构，也不因本次重构给 Experience/Skill 添加向量 API。

## 7. 验收与成本证据

这是实现交付的验收要求，当前提案尚未执行这些验证：

- 行为：每个入口的发布、停用、恢复、标签和授权撤销；无旧行残留；history 与精确引用不变。
- 并发：单次多通道读取的快照一致；Topic 不混合 revision；过期 writer 不能覆盖新投影。
- 迁移：中断续跑、重复执行、不同批大小、缺失证据、配置不匹配、重启后的 readiness 拒绝服务。
- 能力：无 embedding 时全文完整；Experience/Skill 保持现有 FTS 能力；SQLite 辅助索引同步。
- 真实 OB：记录发行版/补丁、DDL、SQL、EXPLAIN/执行指标与数据集；包括多 Scope、标签及权限选择性、大量历史和大 Topic。
- 召回：用过滤后合法集合计算 recall@k；全文问题补充 PMC 所反馈场景的可复核证据。
- 成本：记录每次增量更新的行数、字节、embedding 次数、事务时长，查询 p50/p95、扫描量、内存，以及重建临时空间。

每个 Family 给出当前规模与增长假设、单位正文大小、平均/最大单元数、标签/授权扇出和候选预算。容量预估需覆盖权威
历史加当前宽表加索引，不能仅统计正文，也不能用 SQL 无 JOIN 推导出延迟必然降低。
