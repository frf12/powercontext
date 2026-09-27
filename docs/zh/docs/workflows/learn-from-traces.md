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

Supervisor 先让模型生成有界的候选 inventory，优先为 Tool 生成初版，再处理 Experience 与 Skill。
修复按候选轮转，避免一个候选耗尽修复预算后，其他核心 Tool 还没有初版。
`max_candidates_per_family` 分别限制每个 Family 的候选数量。依赖 Tool 的 Skill 会在它引用的 Tool 候选完成校验后生成，
因此同一 Run 中的 `tool_keys` 可以引用已校验候选。

候选学习包含两次召回。生成前，在同一 Scope、同一 host profile 且当前调用方有权读取的 active 制品池中融合全文和向量召回。
候选生成后，再按同一 Family 做第二次召回，并纳入本 Run 中已经先发布的候选。独立 consolidator 判断候选是否描述同一能力；保存结果时保留
原始生成 messages，并与 consolidation messages 一起记录。同 ID 且方法兼容的候选生成新的 revision；不同方法保持分开，两个已有历史 ID
不会被合并为一个 ID。
如果原始生成 Agent 认为合并会丢失独立方法或破坏契约，可以在 `consolidation_conflict` 中明确提出异议；该候选延期并标记为
`consolidation_disputed`，旧制品不会被覆盖。

候选包含 Skill 时，发现模型会沿原始 conversation 在固定 key 前复核一次方法粒度。已加载的宿主指南、工具说明和
schema 是背景材料；Skill 应提炼实际执行所证明的具体解题方法，而不是重新发布通用指南。同方法换参数可以复用，
不同目标、输入输出语义或算法不应仅因来自同一数据库而合并。复核可修改候选 key 及关联的 `tool_keys`，不强制每条
trace 生成一个 Skill。生成的正文应紧凑，并围绕该方法保留有效知识。

复用旧 Skill key 时，候选必须提供 `skill_reuse.ref`（可见旧 Skill 的精确引用）与 `same_method_reason`。
服务检查引用是否匹配；模型负责比较目标、输入输出语义和执行方法，这不构成语义正确性的证明。缺少确认时，发现模型
先收到有界修复反馈，仍不满足要求的 Skill 不会发布。同 key head 不在有限目录中时，发布检查也会阻止静默覆盖。
复核的 inventory、messages、次数及错误会持久化。结构化输出或输入预算导致复核失败时，Skill 延期，独立 E/T 候选
仍可在剩余预算内继续。额外复核请求计入同一个 Run 预算，`max_candidate_repair_rounds` 也限制复核后的额外修复轮数。

`previous_artifact_limit` 是每次学习期召回所选择的相关历史 head 数量上限，不是“最近 N 条”的快捷选择；设为 `0` 会关闭历史候选。
当前 head 未被显式确认时，它也不会允许静默覆盖同 key 制品。

Skill description 说明能解决的业务问题及必要适用条件。工具名、表名、SQL 和操作步骤放在 instructions 或依赖结构中。
全文、向量及运行时回退检索均只使用 Skill 的 name 和 description。更新 Skill 时创建新 revision；学习制品召回跟随 active head，
历史 LearningRun 的制品引用保持不变。
制品来源可以包含 `skill-package-upload` 等已注册内部 Source。HTTP/SDK 的 `SourceTypeReference.source_type`
使用字符串表示，使带完整 package 的 revision 可以通过 Artifact API 正常读取。

上述候选流程使用提示词版本 `powercontext.trace-learning.v10`。升级前应完成已经排队或运行中的旧版 Run：它们不会换用新提示词续跑或静默迁移。
v4 和 v2 兼容流程继续保留；v3
不支持。版本不匹配的未完成任务会以 `capability_unavailable` 结束，已有制品不会因此删除。不要将旧任务标记为新版本来绕过检查。

每个候选都有独立保存的模型 `messages`、revision、review finding、validation 结果、修复次数和 outcome。Worker 重启后，
从已保存的 conversation 和候选检查点继续。流程还会保存 consolidation decision、目标引用和 generation checkpoint。普通首次 consolidation
不消耗 `max_candidate_repair_rounds`，但每个模型请求仍计入共享的 `max_model_calls` 预算；已经发布的候选会跳过，不会重新生成。

新 Tool 使用参数计划生成：模型选择完整成功 SQL 调用及字面值角色，代码按原文位置替换参数，生成绑定顺序和原值示例。
每个字面值必须恰好归入一个参数或保留常量；相同值的不同用途可以独立处理，联动位置共享参数。保留原 SQL 写法，
不通过重渲染改写数据库函数。参数 schema 必须接受原示例，返回 schema 必须接受可用的真实结果结构。计划编译错误进入
原生成 conversation 的结构化修复。全部参数必填，执行器不应用 schema default。历史完整 Tool 格式的检查点继续用原格式。

Tool reviewer 完全独立：接收候选的 `ToolContent`、从工具 SQL 提取的字面值清单，以及由 Tool 属性 schema 实际校验的一组有界样例。
样例标明哪些值被接受或拒绝，不执行 SQL、不推断业务取值域，也不覆盖根级及跨字段约束。复审额外接收上次被审查的 Tool、findings 和
生成者的处理意见，聚焦未解决问题及修改引入的新缺陷；看不到 trace、question、参考答案、examples 或生成 conversation。
候选生成器在修复时沿保存的原始 `messages` 继续。它必须对每条 finding 恰好返回一次 `accept`、`partial` 或 `reject`，并给出
理由。review 建议只是质量反馈；确定性的校验错误（包括硬 SQL 校验）不能靠 review decision 豁免。
生成者完成接受的修改并解释部分接受/拒绝的原因后，可以设置 `self_pass=true` 自行结束审查，包括修改与拒绝混合的情况，
无需 reviewer 再次同意。最新候选仍必须通过确定性校验，所有当前 finding 必须逐项处理。`review_resolution` 区分
`reviewer_pass` 与 `generator_self_pass`；这两个状态都不代表已执行生产数据库或证明全部参数正确。

审查聚焦会影响调用的契约缺陷，以及同一算法能够支持但遗漏的可变参数。把固定输入重复写进工具说明不能单独证明该值应固定；
改变后需要另一套算法的结构常量和业务定义仍应保留。可选重构、假设性边界情况及已准确说明的限制不作为强制修复要求。
参数化后的示例仍须还原原始已验证 SQL。

工具审查继承 `inference.generation_model_settings`。可选的
`inference.trace_learning_tool_review_model_settings` 只覆盖独立 reviewer 的模型参数，包括其结构化输出修正请求；
候选发现与生成仍使用原参数。例如，支持 OpenAI 推理参数的模型可配置 `{"openai_reasoning_effort": "low"}`，减少审查阶段的
思考量，同时保留生成模型。参数是否生效取决于模型服务。reviewer 的 `max_tokens` 可以进一步降低输出上限，但不能超过
`trace_learning_budget.max_output_tokens`；思考 token 也可能消耗同一输出预算。HTTP 200 但达到长度上限、正文为空的响应仍是
审查失败，不代表 Tool 获准发布。此配置不会跳过校验，也不会发布此前延期的候选。

省略配置或使用空对象会保留默认检查点身份。启用或修改非空 override 会改变推理配置身份，切换前应排空 queued/running Run。
已经结束但只发布部分候选的 Run 仍保持结束状态，配置变化不会使它自动重跑。

如果 consolidation 修改了 Tool key，只有新 Tool 通过校验并在同一事务中成功发布后，后续 Skill 的 `tool_keys` 才会重定向。校验或发布失败时不提交
重定向，也不能把旧 Tool 冒充为新 Tool 的成功替代。合并后的 Tool 保持 callable name 以及 input/output schema 不变；语义校验允许改善 description 等
文字说明，历史 SQL 示例仍必须继续通过校验。

候选失败相互隔离。被拒绝或延期的候选不会回滚已经发布的候选。Run 的 `status=succeeded` 只表示至少有一个候选产出了可发布
产物，并不表示所有计划候选都完成。检查 `candidate_outcomes`，确认候选是否全部为 `published`，以及是否存在 `rejected` 或
`deferred`。预算或截止时间中止时可以保留部分已发布产物，同时在候选 outcome 或 Run 的 error 状态中保留剩余工作被中止的原因。

## 继续完成未产出的候选

轮次耗尽或 reviewer 输出无效时，保留候选、原 messages、revision、findings、usage 和阶段错误。
`max_candidate_review_retries` 允许对无效结构化输出只重试审查操作；请求仍计入 Run 总预算，不占用质量修复轮次。
确定性校验修复使用独立的 `max_candidate_validation_repairs` 额度。

Run 结束后，原发起者具备 Scope contribute 权限时，可以通过
`POST /v1/scopes/{scope_id}/trace-learning/{run_id}/resume` 显式恢复选中的 `deferred` 或可修复的 `rejected` 候选：

```json
{
  "idempotency_key": "finish-unresolved-tools-1",
  "candidates": [{"family": "tool", "key": "count-country"}],
  "additional_model_calls": 16,
  "additional_repair_rounds": 2,
  "timeout_seconds": 1800,
  "use_current_configuration": false
}
```

既有 Supervisor 从保存的阶段与生成 conversation 继续，不重新发现候选，不重生成已发布候选，不丢弃已有制品引用。
Tool 发布后，同 Run 中仅因缺少其依赖而阻断的 Skill 会继续处理；其他拒绝原因不会被自动豁免。
同一幂等键和相同请求不会重复增加额度；运行中的任务不能恢复。排队返回 `202`，重复请求已完成的恢复返回 `200`。
新的截止时间从续跑开始计算，usage 持续累计。每次恢复给选中候选的两类修复额度各增加指定轮数，每类累计上限 64，
整个 Run 的模型请求上限仍为 1024。

提示词或模型配置已变化时，默认返回 `resume_configuration_changed`；只有显式设置 `use_current_configuration=true` 才采用当前配置。
原配置、outcome、usage 和 error 保存在恢复审计记录中，SQL 校验、审查和发布检查仍须通过。
恢复要求已有完整候选 inventory；发现阶段失败、选中不存在或已发布的候选、不可恢复的策略拒绝等情况不接受恢复。

## 检查校验和预算

校验会绑定每个 Tool 示例的参数，并把生成 SQL 的 AST 与已记录的成功查询比较。这只证明被示例覆盖的路径：不会重放变化中的
生产数据，不证明所有参数值，也不会因此把 `live_execution_verified` 设为 `true`。即使 reviewer 接受 finding，历史 SQL AST 校验仍然
必须通过。实际执行和答案效果需要在宿主环境中独立评测。

一次 Run 的预算由候选 inventory、候选生成、Tool review、结构化输出格式修复、质量修复以及后续模型请求共同消耗：

| 预算字段 | 默认值 | 上限 | 含义 |
| --- | ---: | ---: | --- |
| `max_model_calls` | `128` | `1024` | 整个 Run 的 provider 请求总数 |
| `max_candidates_per_family` | `32` | `128` | 每个 Experience、Tool 或 Skill Family 的 inventory 候选数 |
| `max_candidate_repair_rounds` | `4` | `16` | 单个候选额外质量修复轮数 |
| `max_candidate_validation_repairs` | `2` | `8` | 单个候选独立的确定性校验修复轮数 |
| `max_candidate_review_retries` | `1` | `4` | 结构化输出无效后的额外审查次数 |
| `max_output_tokens` | `16000` | `64000` | 每次 provider 请求的输出 token 上限 |
| `max_input_chars` | `400000` | `4194304` | 序列化输入和保存 messages 的预算 |
| `timeout_seconds` | `1800` | `7200` | Run 的总截止时间 |
| `previous_artifact_limit` | `30` | `100` | 每次学习期召回选择的相关历史 head 数量上限，不是最近 N 条 |
| `max_pending_per_scope` | `32` | `1000` | 单个 Scope 排队和执行中的 trace-learning Run 数 |

Supervisor 在发起模型请求前保守预留下一次额度。Worker 在模型调用中丢失响应或退出时，已预留额度不会退回，重启也不能再次消费。
`max_output_tokens` 按请求计算，而 Run 的 usage 会累计所有请求。

普通结构化 generation 的 `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MAX_REQUESTS=2` 表示 initial 1 次加 repair 1 次；改为 `3`
表示 initial 1 次加额外 2 次 repair。该单次 operation 限制仍受 trace-learning Run 总预算约束。

`usage.reserved_model_calls` 表示计入 `usage.model_calls` 的未确认预留；两者相减得到已确认请求数。已知在请求前发生的配置错误会释放预留；响应未知的请求保留额度，不冒充已确认模型用量。

## 准备一次推理

在 `/v1/context/prepare` 请求中传入 `learned_skill_rerank: true`，可仅为本次请求开启混合检索后的模型适用性筛选。
省略或传入 `false` 均关闭筛选，保留全文／向量融合及其语义阈值。即使设置了旧的
`runtime.learned_skill_rerank_enabled`，请求默认仍关闭；该旧配置已弃用，不再自动开启筛选。
`runtime.learned_skill_rerank_candidate_limit` 限制内部候选数量，默认 10、上限 100。一次批量判断只接收问题、Skill 名称及描述，
不接收正文、示例、工具定义或 trace。仅主题相同不足以入选；模型明确返回空集合时保持为空。
之后仍检查 active ref、精确 Tool 依赖、宿主兼容性及上下文字节预算。

开启筛选后，Skill 候选可以在低于语义注入阈值时进入判断；Experience、Tool 的阈值、学习合并时的检索和 Memory 重排策略均不变。
模型调用失败时，本次不下发 Skill，独立匹配的 Experience 和 Tool 仍可返回。有效空选择与推理失败在
`learned.skill_rerank` trace 中分别记录，用量归入 `skill_recall`。

筛选复用 `inference.rerank_model` 及 `rerank_*` 配置，未指定独立模型时继承 generation 模型，无需额外全局开关。
未配置这两类模型却请求开启筛选时，返回 HTTP 422 `capability_not_supported`；未选择学习 Skill 时，该参数不生效。
这些配置需要支持结构化生成的模型，
不是专用文档评分 HTTP 接口。新增的 PC 请求耗时和模型用量应单独统计，不计作宿主 Agent 的解题决策轮数。
此请求参数只作用于 `/v1/context/prepare` 的学习上下文，不改变 Skill library 搜索接口。

同一请求可传入 `learned_skill_min_similarity`（0～1），单独调整 Skill 的向量相似度阈值。例如
`"learned_skill_rerank": false, "learned_skill_min_similarity": 0.5` 使用 0.5 阈值且不调用筛选模型。
省略或传入 `null` 时，普通混合检索沿用服务端阈值（默认 0.3），开启模型筛选时沿用其原候选策略。
显式阈值在可选模型筛选之前生效，不改变 Experience／Tool 阈值、Skill library 搜索或其他请求。
向量检索不可用时仍走既有全文降级路径；全文降级不保证余弦阈值。

`POST /v1/context/prepare` 和 `POST /v1/tools/search` 均支持按请求传入融合参数：

```json
{
  "learned_retrieval_options": {
    "fts_weight": 1,
    "vector_weight": 2,
    "min_rrf_score": 0.45
  }
}
```

将这些字段加入完整请求即可。权重是有限非负数，至少一个大于 0。融合使用 `k=60`，归一化公式为
`61 × [fts_weight/(60+全文排名) + vector_weight/(60+向量排名)] / (fts_weight+vector_weight)`；
未命中的通道贡献为 0。分数范围为 0～1，既不是余弦相似度，也不是相关概率。提高向量占比会降低
主要依靠更好全文排名的候选分数。例如，只有全文首位命中的候选在权重 1:2 下得分为 1/3，会被 0.45 排除。

RRF 阈值按大于等于判断，在融合之后、可选 Skill 模型筛选与上下文预算之前作用于学习 E/S/T。
结果不足时不会补入低于阈值的匹配；被排除的学习 Experience 也不会通过普通上下文重新返回。
已入选 Skill 的精确 Tool 依赖仍按依赖关系携带，不要求其独立匹配分数达标。
既有向量相似度准入保持生效，向量融合权重设为 0 也不会关闭它。
已配置向量但本次调用失败时，归一化分母保留向量权重，避免降级抬分；没有配置向量时只按全文归一化，
此时若全文权重为 0 则返回空匹配。省略参数或传入 `null` 保持等权且不增加 RRF 过滤。
参数不改变 Memory、Topic Memory、Skill library 搜索或其他请求；RRF 阈值应与
`learned_skill_min_similarity` 及专用 Rerank 分数阈值分别校准。

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
不执行 SQL，也不调用生成模型；配置 embedding 后，检索会调用向量模型。宿主负责将检索结果绑定成可调用工具。

PC 的 `max_bytes` 默认仍为 8,000，上限为 32,768 字节。调用方可显式提高请求预算；增加预算不会触发额外模型请求，也不代表
每次必须填满上下文。

召回会分页读取含已发布候选的 Run 的全部 active head，先限定 Scope、host profile 和启用的 Family，再按相关性和字节预算选择。
未配置 embedding 时使用现有 Artifact 全文索引；配置后使用全文和语义两个召回通道，通过与 Memory 共用的 RRF 算法融合排名。
这里的双通道不增加 LLM 查询改写或重排。语义匹配不会被旧的词项相交规则再次过滤。生成阶段仍受 Run budget 对历史 head 数量的限制；
两个通道都继续使用相同的 Scope、host profile、Family 和字节预算过滤。

向量模型沿用 `inference.embedding_model`、`embedding_base_url`、认证 header、`embedding_profile_id`、`embedding_dimension`
和 batch/timeout 配置。模型、profile ID 和维度必须一起设置。例如 OpenAI-compatible 的 `text-embedding-v4` 可以使用模型标识
`openai:text-embedding-v4`、1024 维和 batch size 10，认证通过现有私密配置提供。

E/S/T 的权威制品仍在共享 `pc_artifacts` 表中。另有一份持久化的 E/S/T 共享向量投影，记录 Scope、Family、精确 Artifact revision、
投影内容摘要及完整 embedding profile 的指纹。SQLite 使用共享的 `pc_artifact_vector_entries` 元数据表及原生 `pc_artifact_vec` `vec0` sqlite-vec 索引；
OceanBase/SeekDB 使用一份共享 E/S/T `VECTOR` 投影及原生 HNSW 索引。业务 Artifact 表及其 content schema 不变。

Experience、Skill 或 Tool 发布、修订时，PowerContext 会在最终发布提交前准备投影向量。短事务共同写入 Artifact/head、全文投影和向量投影。
embedding 失败时不提交本次发布；数据库或向量索引写入失败时整体回滚。这里没有独立的 artifact-vector Supervisor binding、索引 Worker
或任务轮询。已有制品池或不兼容的 embedding profile 只能通过显式 `contexts.artifact_vectors.rebuild(scope_id)` 修复；这不会重新学习，
也不会在启动或查询中暗自补建。维度改变要求离线重建索引，不能静默删除或重建向量表。

单条向量投影最多使用原文前 8,000 个 UTF-8 字节，返回给 Agent 的制品内容不因此截断。当前针对较小的宿主候选池使用持久化原生索引，
不是大规模 ANN 服务。Skill 的全文和向量投影仅使用 `name` 和 `description`，正文、示例、验证说明、其他 metadata 和包文件不参与匹配；
检索返回的 Skill 和可下载包仍保留完整内容。调整投影后，旧池需显式重建向量索引，无须重新生成或发布 Skill。
Tool 的检索文本包含名称、说明及输入输出 schema，不包含 SQL 实现。语义通道在原生 unit-L2 距离换算后的
`runtime.artifact_recall_min_similarity` 边界上接纳匹配，默认 `0.3`，可配置范围为 `0` 到 `1`。它是余弦相似度下限，
不是概率或 RRF 分数。向量查询成功时，最终 RRF 候选必须通过这个下限，全文命中不能绕过它；未达到下限或缺少当前版本向量的
制品不会作为独立匹配返回。旧池应先显式补齐索引。提高阈值会减少弱相关结果，也可能漏掉有效匹配，需按模型和语料验证。
可通过 `POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_RECALL_MIN_SIMILARITY=0.3` 配置服务。

该规则同时用于 `prepare_context` 的学习上下文、主动 Tool 搜索和普通 Experience/Skill 检索。没有合格候选时允许返回空，
不会为了填满数量补入低分制品；匹配 Skill 的明确 Tool 依赖仍按依赖关系完整携带，不要求依赖工具本身独立命中整题。
未配置 embedding 或 query embedding 失败时仍使用原有全文降级路径，此时无法执行语义阈值校验；失败通过 `learned.search` tracing
的 `degraded` 字段及服务日志记录。查询只对 query 做 embedding，绝不在查询中计算缺失的制品向量。
embedding 是独立的模型请求，评测时需与宿主的解题模型请求分别记录。依赖版本、退役状态和字节预算仍在最终组装时检查。

对 Datus，传给 LLM 的 Tool description 会追加 `output_schema`，`args_schema` 保持原样。Tool 参数单独绑定，固定只读 SQL 通过当前请求的
Data Gateway 执行；数据源权限、结果限制和当前数据继续由 Gateway 控制。Tool 内没有隐藏的模型调用；评估宿主总模型请求数时，应计入
回退和最终答案请求。

候选一旦独立发布，即使同一 Run 仍在处理其他候选，也可以参与召回；草稿、拒绝及暂缓的候选不会被召回。

## 当前范围

当前宿主为 Datus，执行器为 MySQL/PostgreSQL 兼容的参数化只读 SQL，支持普通顺序的模型工具调用。动态 DAG、通用代码沙箱、跨宿主
MCP 执行和自动收集普通日志不在此流程中。Tool 读取受 Artifact 权限控制，没有通用 Tool 创建/替换接口。
