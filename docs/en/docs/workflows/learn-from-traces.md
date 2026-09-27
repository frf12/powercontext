---
title: Learn Experience, Skills and Tools from correct traces
description: Explicitly import complete correct traces and build independently validated artifacts through the Supervisor.
---

# Learn Experience, Skills and Tools from correct traces

This workflow accepts complete, correct traces explicitly selected by the caller. PowerContext uses the existing
Artifact Processing Supervisor to build reusable Experience, Skills and read-only SQL Tools. Each published artifact
keeps its exact Artifact revision, source lineage, and permission boundary.
All three families use the existing shared Artifact storage; learning does not create separate artifact tables.

## Enable an isolated pilot

Set the following Server Runtime configuration together with a generation model, its credentials, and a persistent
database:

```json
{
  "trace_learning_enabled": true,
  "artifact_processing_families": ["tool"],
  "dream_enabled": false,
  "tool_max_workers": 1,
  "tool_worker_timeout_seconds": 1860
}
```

The default trace-learning deadline is 1,800 seconds. A 1,860-second Tool Worker timeout leaves a small amount of
time for worker startup and checkpoint acknowledgement around that deadline. The `tool.trace-learning.v1` binding
uses the existing Supervisor for scheduling, subprocess isolation, leases, quotas, and fencing. Ordinary Source
ingestion does not trigger trace learning.

## Import a trace

Call `POST /v1/scopes/{scope_id}/trace-learning` with an `idempotency_key`, a Datus `host_profile` containing the
actual SQL dialect and database name, and `traces`. Every trace keeps its `trace_id`, question, final answer, context,
and every tool call's ID, name, arguments, result, and success flag. A complete correct trace may contain failed
intermediate attempts; correctness is an admission premise supplied by the caller.

Preserve Datus `read_queries` arguments and result artifacts. Do not invent split call identities. A generated Tool
example points to an original call with `trace_id`, `call_id`, and `query_index`; its `arguments` are values for the
generated Tool's input schema, never a copy of the source call's `queries` or `database_name` fields.

Accepted imports return `202`. Poll `GET /v1/scopes/{scope_id}/trace-learning/{run_id}` for progress. Repeating the
same idempotency key and content reuses the Run; conflicting content is rejected. Successful work returns exact
Experience, Skill, and Tool references. A later explicit import can reuse a stable key and revise the corresponding
Artifact after checking permissions and the expected head revision.

## Understand the candidate workflow

The Supervisor first asks the model for a bounded inventory. It drafts Tools first, before auxiliary Experiences and Skills.
Repairs rotate across candidates so one conversation cannot consume every opportunity before other Tools have a draft. `max_candidates_per_family` limits the inventory for each family. A dependent Skill is generated
after the Tool candidates it names, so its `tool_keys` can refer to validated candidates in the same Run.

The candidate workflow uses two retrieval passes. Before generation, it fuses full-text and vector retrieval over the
authorized active pool in the same Scope and host profile. After a candidate is generated, it performs a second retrieval
within the same family, including candidates that were already published earlier in the same Run. An independent
consolidator decides whether candidates describe the same capability. It preserves the original generation messages and
combines them with the consolidation messages when saving the result. Candidates with the same ID and compatible method
produce a new revision; different methods remain separate, and two existing historical IDs are never merged into one.
If the original generating Agent believes consolidation would discard an independent method or break a contract, it can
record an explicit objection in `consolidation_conflict`; the candidate is deferred with `consolidation_disputed`, and
existing artifacts are not overwritten.

An inventory containing Skills receives a capability-boundary review in the original discovery conversation before
keys become fixed. Loaded host guides, tool descriptions and schemas are background evidence. Extract the specific
method demonstrated by execution, rather than republishing a general manual. Reuse methods across changed parameters;
do not merge different goals, input/output meanings or algorithms merely because they share a database. Review can
revise keys and associated `tool_keys`; it does not require one Skill per trace. Keep the resulting procedure compact
and preserve useful knowledge within that method.

A Skill description states the business problem it solves and necessary applicability conditions. Keep tool names,
table names, SQL and execution steps in the instructions or dependency metadata. Both full-text and vector retrieval
use only the Skill name and description; the runtime fallback follows the same rule. Updating an active Skill creates
a new revision: learning-based recall follows that active head while historical LearningRun references remain unchanged.
Artifact provenance may include internal registered sources such as `skill-package-upload`. The HTTP/SDK
`SourceTypeReference.source_type` is a string, so package-backed revisions remain readable through the Artifact API.

Reusing a previous Skill key requires `skill_reuse.ref`, its exact visible Artifact reference, and
`same_method_reason`. The service validates the reference; the model compares goals, input/output meanings and
procedure. This is an explicit assessment, not proof of semantic equivalence. Missing confirmation receives bounded
discovery feedback, and unresolved Skill candidates remain unpublished. Publication also prevents silently replacing
a same-key head omitted from the bounded catalog. Inventory, messages, review counts and errors are checkpointed.
Structured-output or input-budget failure defers Skills while independent Experience/Tool candidates can continue
within the remaining budget. Review requests share the Run budget; `max_candidate_repair_rounds` also bounds additional
inventory repair rounds after its initial review.

`previous_artifact_limit` bounds related historical heads selected by each learning-time lookup; it is not a
request for the latest N artifacts. A value of `0` disables historical candidates. It never permits a silent same-key
overwrite when the current head is not explicitly confirmed.

The prompt version for this candidate workflow is `powercontext.trace-learning.v10`. Finish queued or running older Runs
before upgrading: they are not resumed under different prompts or silently migrated. The v4 and v2 compatibility flows
remain available; v3 is unsupported. Unfinished
work with a mismatched version ends with `capability_unavailable`; already published artifacts remain. Do not relabel an
old Run to bypass the version check.

Each candidate has its own saved model messages, revisions, review findings, validation result, repair count, and
outcome. The workflow also saves consolidation decisions, target references and the generation checkpoint. A normal first
consolidation pass does not consume `max_candidate_repair_rounds`, but every model request still consumes the shared
`max_model_calls` budget. On a Worker restart, the next attempt resumes the saved conversation and candidate checkpoint.
Already published candidates are skipped; the Worker does not regenerate them.

New Tools use parameter plans: the model selects a whole successful statement and classifies its literal positions.
Code replaces selected source spans without re-rendering SQL functions, builds lexical bindings and the original example,
and checks schema compatibility with recorded inputs and available results. Each literal is assigned exactly once;
coupled positions share a parameter while equal values in different roles remain independent. Compiler errors enter the
original structured-output repair conversation. All parameters are required; schema defaults are not applied.
Existing complete-Tool checkpoints retain their original generation format.

After implementing accepted findings and explaining partial acceptance or rejection, the generator may set
`self_pass=true` to close advisory review without another reviewer's approval, including mixed decisions on a changed Tool.
Every current finding must be addressed and the latest candidate must pass deterministic validation. The saved
`review_resolution` distinguishes `reviewer_pass` from `generator_self_pass`; neither proves live execution or universal correctness.
When a Tool is published, Skills in the same Run blocked solely on its dependency resume; other rejection reasons remain enforced.

Tool review is deliberately independent. The reviewer receives the candidate's `ToolContent`, a SQL literal inventory,
and bounded property-schema validation samples derived from that Tool. The samples show accepted and rejected values;
they do not execute SQL, infer a domain, or cover root-level and cross-field constraints. A follow-up also receives the
previously reviewed Tool, findings and the generator's decisions,
so it can focus on unresolved issues and defects introduced by the repair. It cannot see the
trace, question, reference answer, examples, or the generating conversation. The candidate generator receives the
original saved `messages` when it repairs a candidate. It must answer every finding exactly once with `accept`,
`partial`, or `reject` and a reason. Review suggestions are advisory. Deterministic validation errors, including hard
SQL validation, cannot be waived by a review decision.

Review targets material contract defects and missed scalar inputs that the same algorithm supports. Repeating a fixed
input in a Tool's description does not justify freezing it. Structural constants and business definitions remain fixed
when changing them would require a different algorithm. Optional redesigns, speculative edge cases and already
documented limitations are not repair requirements. A parameterized example must still reproduce the recorded SQL.

Tool review inherits `inference.generation_model_settings`. Optional
`inference.trace_learning_tool_review_model_settings` overrides only the independent reviewer, including its
structured-output repair requests; discovery and candidate generation retain their original settings. For a compatible
OpenAI model, `{"openai_reasoning_effort": "low"}` can reduce review deliberation without changing the generation model.
Provider support determines which settings have an effect. A reviewer `max_tokens` can lower its output ceiling but
cannot exceed `trace_learning_budget.max_output_tokens`; reasoning tokens can consume this same output budget.
HTTP 200 with a length-limited empty answer is still a failed review, not an accepted Tool. This setting does not
bypass validation or publish previously deferred candidates.

An omitted or empty review override preserves the default checkpoint identity. Enabling or changing a nonempty
override changes the inference identity: drain queued/running Runs before changing it. Completed partial Runs remain
completed; they are not automatically reprocessed after a configuration change.

If consolidation changes a Tool key, later Skill `tool_keys` are redirected only after the new Tool passes validation and
its publication succeeds in the same transaction. A failed validation or publication does not commit the redirect and
does not treat the old Tool as a successful replacement. A consolidated Tool keeps its callable name and input/output
schemas fixed; semantic validation permits improvements to descriptive text. Historical SQL examples remain required for
validation.

Candidate failures are isolated. A rejected or deferred candidate does not roll back candidates that were already
published. A Run with `status=succeeded` means that at least one candidate produced a publishable artifact; it does not
mean that every planned candidate finished. Inspect `candidate_outcomes` to determine whether all candidates are
`published` or whether any are `rejected` or `deferred`. A budget or deadline stop can leave partial publications and
records why the remaining work stopped in the candidate outcomes or the Run error state.

## Continue unfinished candidates

Budget exhaustion and invalid reviewer output do not discard a candidate. Messages, revisions, findings, usage and
stage failures are checkpointed. An invalid review can retry only the review operation, within
`max_candidate_review_retries`; these requests still consume the Run's model-call budget. They do not consume a
quality-repair round. Deterministic validation repairs use a separate `max_candidate_validation_repairs` allowance.

After a Run terminates, its original principal with Scope contribute access can explicitly continue selected
`deferred` or repairable `rejected` candidates through
`POST /v1/scopes/{scope_id}/trace-learning/{run_id}/resume`:

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

The existing Supervisor resumes the saved stage and generation conversation. It does not rerun discovery, regenerate
published candidates or discard their Artifact references. The same idempotency key and payload do not add budget twice;
an active Run cannot be resumed. The response is `202` when queued and `200` when replaying an already completed request.
The new deadline applies to the continuation; usage remains cumulative. Each resume adds the requested repair allowance
to both selected-candidate limits, bounded at 64 each, and total Run model calls remain bounded at 1024.

If prompts or model configuration changed, the request fails with `resume_configuration_changed` unless
`use_current_configuration=true` explicitly accepts the current configuration. The prior configuration, outcomes,
usage and error remain in the resume audit history. This does not waive SQL validation, review or publication checks.
Not every rejection is recoverable: invalid candidate selections, already published candidates and permanent policy
failures are rejected. A completed candidate inventory is required; failed discovery cannot use this endpoint.

## Inspect validation and budget

Validation binds each Tool example's parameters and compares its generated SQL AST with the successful recorded query.
This proves only the covered recorded path. It does not rerun changing production data, prove every parameter value, or
set `live_execution_verified=true`. Historical SQL AST checks remain required even when a reviewer accepts a finding.
Evaluate live execution and answer quality separately in the host environment.

The Run budget is shared by inventory, candidate generation, Tool review, structured-output format repairs, quality
repairs, and any later model request made by the workflow:

| Budget field | Default | Maximum | Meaning |
| --- | ---: | ---: | --- |
| `max_model_calls` | `128` | `1024` | Total provider requests for the Run |
| `max_candidates_per_family` | `32` | `128` | Inventory candidates per Experience, Tool, or Skill family |
| `max_candidate_repair_rounds` | `4` | `16` | Additional quality-repair rounds for one candidate |
| `max_candidate_validation_repairs` | `2` | `8` | Separate deterministic-validation repair rounds per candidate |
| `max_candidate_review_retries` | `1` | `4` | Additional review operations after invalid structured output |
| `max_output_tokens` | `16000` | `64000` | Output-token limit for each provider request |
| `max_input_chars` | `400000` | `4194304` | Serialized input and saved message budget |
| `timeout_seconds` | `1800` | `7200` | Overall Run deadline |
| `previous_artifact_limit` | `30` | `100` | Related historical heads per learning-time lookup; not a latest-N count |
| `max_pending_per_scope` | `32` | `1000` | Queued and running trace-learning Runs in one Scope |

The Supervisor reserves the next request before dispatching it. If a Worker loses a response or exits during a model
call, that reservation is retained; a restart cannot spend it again. `max_output_tokens` applies per request while the
Run's reported usage accumulates all requests.

For ordinary structured generation, `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MAX_REQUESTS=2` means one initial
request plus one repair request. Setting it to `3` permits one initial request plus two repairs. This per-operation
limit is still subject to the trace-learning Run budget.

`usage.reserved_model_calls` identifies unconfirmed reservations included in `usage.model_calls`. Subtract it to obtain confirmed requests. A known pre-request configuration failure releases its reservation; uncertain calls retain theirs and are not reported as confirmed model usage.

## Prepare one inference turn

For learned Skills, pass `learned_skill_rerank: true` in a `/v1/context/prepare` request to enable an optional
model-based applicability check after hybrid retrieval for that request only. Omit it or pass `false` to disable the
check and retain hybrid retrieval with its semantic threshold. The default is always off, including when the legacy
`runtime.learned_skill_rerank_enabled` option is set; that option is deprecated and no longer activates the check.
`runtime.learned_skill_rerank_candidate_limit` bounds its internal candidate pool (default
10, maximum 100). Only query, Skill names and descriptions enter one batch selection; instructions, examples, Tool
definitions and trace data do not. Sharing a topic is insufficient, and a valid empty selection stays empty. The existing
context selector still checks active references, exact Tool dependencies, host compatibility and the byte budget.

With this check enabled, Skill candidates can reach selection below the semantic injection threshold; Experience and
Tool thresholds, learning-time consolidation retrieval, and Memory reranking retain their policies. Inference failure
omits Skills for that request while preserving independently matched Experience and Tools. An empty accepted selection
and an inference failure are distinct in the `learned.skill_rerank` trace. Usage is attributed to `skill_recall`.

The check reuses `inference.rerank_model` and `rerank_*` settings, falling back to the generation model when no separate
model is configured. No additional global enable switch is required. Requesting the check without either model returns
HTTP 422 `capability_not_supported`; the option has no effect when learned Skills are not selected.
These settings expect a model that supports structured generation, not a dedicated document-scoring
HTTP endpoint. Measure its added context-preparation latency and model usage separately from host decision rounds.
This request option applies to learned context in `/v1/context/prepare`; it does not change the Skill library search API.

Pass `learned_skill_min_similarity` (0 through 1) to override only the Skill cosine threshold for this request.
For example, `"learned_skill_rerank": false, "learned_skill_min_similarity": 0.5` uses a 0.5 threshold without a
selection-model call. Omitted or `null` retains the server threshold (default 0.3) for hybrid recall, or the original
candidate policy when model selection is enabled. An explicit threshold applies before optional model selection;
Experience/Tool thresholds, Skill library searches and other requests are unaffected. The existing lexical fallback
still applies when vector retrieval is unavailable and cannot enforce a cosine threshold.

Both `POST /v1/context/prepare` and `POST /v1/tools/search` accept request-local fusion controls:

```json
{
  "learned_retrieval_options": {
    "fts_weight": 1,
    "vector_weight": 2,
    "min_rrf_score": 0.45
  }
}
```

These are fields to add to a complete request. The weights are finite, nonnegative relative contributions;
at least one must be positive. With `k=60`, the fused score is
`61 * (fts_weight/(60+fts_rank) + vector_weight/(60+vector_rank)) / (fts_weight+vector_weight)`.
A missing hit contributes zero. The score ranges from 0 to 1; it is neither cosine similarity nor a relevance
probability. Increasing the vector share lowers the scores of candidates relying on better full-text ranks.
For example, a full-text-only rank-one match scores 1/3 with weights 1:2 and is excluded by 0.45.

The inclusive RRF cutoff applies to learned E/S/T after fusion and before optional Skill model selection and
context budgeting. No below-threshold matches fill empty results; rejected learned Experiences cannot re-enter
ordinary assembly. An admitted Skill still carries its exact Tool dependencies regardless of their independent
match scores. Existing semantic admission remains in effect, even with a zero vector fusion weight.
If a configured vector channel fails, its weight stays in the denominator so fallback cannot inflate scores.
With no vector configuration, normalization uses only FTS; zero FTS weight then yields no matches.
Omitting the options or passing `null` preserves equal weights and a zero additional cutoff.
The controls do not change Memory, Topic Memory, Skill library search, or another request. Tune RRF cutoffs
separately from `learned_skill_min_similarity` and any dedicated reranker score cutoff.

Opt into `POST /v1/context/prepare` with `learned_tools=true` and the actual `host_profile`:

```json
{
  "scope_id": "your-scope",
  "query": "How many stations are in CZE?",
  "max_bytes": 16000,
  "assembly": {"sections": []},
  "learned_tools": true,
  "host_profile": {"kind": "datus", "dialect": "mysql", "database_name": "your-db"}
}
```

The response's `learned_context` contains selected Experience text, Skill instructions, and complete Tool descriptors.
When only learned context is requested, `status=ready`, `content=null`, and `content_bytes=0`. Legacy requests omit
the new field. Preparation keeps Skill dependencies complete and applies the byte budget before adding Experience text;
it never exposes a Skill with a missing exact Tool revision.

Backend callers can select learned families independently with `learned_families`. Selection happens before ranking and
budget allocation and overrides the legacy `learned_tools` switch; omitting it preserves legacy behavior. An empty array
disables all three learned families. `["tool"]` enables only learned tools; `["experience", "skill"]` disables them.
Disabling Experience also prevents ordinary Experience assembly from restoring it. Other Memory and Profile sections
remain controlled by `assembly`. A nonempty learned selection requires the actual `host_profile`.

```json
{
  "scope_id": "your-scope",
  "query": "How many stations are in CZE?",
  "max_bytes": 32768,
  "assembly": {"sections": []},
  "learned_families": ["tool"],
  "tool_limit": 3,
  "host_profile": {"kind": "datus", "dialect": "mysql", "database_name": "your-db"}
}
```

`tool_limit` defaults to 3 and accepts up to 8 tools, including Skill dependencies and independent matches. Selecting a
Skill does not prevent independent Tool matches. A long standalone Skill cannot displace a matching tool that would
otherwise fit. When tools are disabled, Skills requiring learned tools are skipped; independent methods remain eligible.
Dependencies, callable contracts and programs must fit completely; tools are never truncated into partial contracts.

For active tool discovery, call `POST /v1/tools/search` with `scope_id`, `query`, `host_profile`, and optional `limit` and
`max_bytes`. It returns `{"tools": [...]}` with complete callable contracts, programs and exact Artifact revisions, or an
empty list for no match. The Python client exposes `client.search_tools(SearchToolsRequest(...))`. Search retains Scope
and Artifact read authorization, filters by execution host, and does not execute SQL or invoke a generation model.
With embeddings configured, retrieval calls the embedding provider. The host binds
returned tools to its executor.

PC's `max_bytes` default remains 8,000 bytes, with a maximum of 32,768. Callers may explicitly request a larger budget;
this neither adds model requests nor requires filling the available context.

Retrieval reads Runs with published candidates in pages and restricts active heads by Scope, host profile and enabled
Family before ranking and budget allocation. Without embeddings it uses the existing Artifact full-text index. With
embeddings it combines full-text and semantic channels using the same RRF scoring as Memory. It adds no LLM query
rewrite or reranker. Semantic-only hits are not rejected by the old token-overlap rule. The Run budget still bounds
the number of previous heads selected for generation. The same Scope, host-profile, Family and byte-budget filters
apply to both channels.

The embedding provider uses the existing `inference.embedding_model`, base URL, private headers, profile ID, dimension,
batch size and timeout settings. Model, profile ID and dimension must be configured together. An OpenAI-compatible
`text-embedding-v4` deployment can use `openai:text-embedding-v4`, 1024 dimensions and batch size 10, with credentials
supplied through the existing private configuration.

Authoritative E/S/T artifacts remain in the shared `pc_artifacts` storage. A separate persistent E/S/T vector projection
stores Scope, Family, exact Artifact revision, projected-content identity and a fingerprint of the complete embedding profile. SQLite
uses shared `pc_artifact_vector_entries` metadata with the native `pc_artifact_vec` `vec0` sqlite-vec index. OceanBase/SeekDB
uses one shared E/S/T `VECTOR` projection with a native HNSW index. The business Artifact table and its content schema
remain unchanged.

When an Experience, Skill or Tool is published or revised, PowerContext prepares its projected vector before the final
publication commit. The short commit writes the Artifact/head, full-text projection and vector projection together. An
embedding failure prevents that publication; any database or vector-index write failure rolls the publication back. There is no
separate artifact-vector Supervisor binding, index Worker or task-polling loop. Existing pools or incompatible
embedding profiles are repaired only through the explicit `contexts.artifact_vectors.rebuild(scope_id)` operation; this
does not rerun teaching or learning and is never hidden inside startup or query handling. A dimension change requires
an offline index rebuild and must not silently drop or recreate the vector table.

Each vector projection uses at most the first 8,000 UTF-8 bytes; this does not truncate artifacts delivered to the
Agent. Search uses the native persistent index within the small host-specific pool, not a large-corpus ANN service.
Skill full-text and vector projections use only `name` and `description`. Instructions, examples, validation,
other metadata and package files do not contribute to matching; the retrieved Skill and its downloadable package
retain their complete content. Existing pools require an explicit vector rebuild after this projection change;
Skills do not need to be generated or published again.
Tool search text includes its name, description and input/output schemas, not SQL. The semantic channel admits matches
at `runtime.artifact_recall_min_similarity` after converting the native unit-L2 distance. This configurable cosine
similarity floor defaults to `0.3` and accepts values from `0` to `1`; it is neither a probability nor an RRF score.
When vector retrieval succeeds, final RRF candidates must pass this floor: a lexical match cannot bypass it. Candidates
below the floor or without a vector for their current revision are not independently admitted. Explicitly backfill old
pools first. Raising the floor reduces weak matches but may also exclude useful ones; validate it for the model and corpus.
Configure the server with `POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_RECALL_MIN_SIMILARITY=0.3`.

The floor applies to learned context preparation, active Tool search and ordinary Experience/Skill search. No qualifying
candidate means an empty result, rather than padding the requested limit. An admitted Skill still includes its exact Tool
dependencies even when those tools do not independently match the whole question. With no embedding model, or when query
embedding fails, existing full-text fallback remains available without a semantic-floor guarantee. Embedding failure sets
the `learned.search` tracing `degraded` attribute and writes a server log entry. Query retrieval embeds only the query;
it never computes missing artifact vectors. Embedding requests are separate provider calls and should be counted
separately from host reasoning requests. Final assembly continues to enforce exact dependencies, lifecycle state and
byte budgets.

For Datus, `output_schema` is appended to the Tool description supplied to the LLM, while `args_schema` is passed
through unchanged. Bind Tool parameters separately and execute the fixed read-only SQL through the current request's
Data Gateway. The Gateway retains datasource permissions, result limits, and current data access. No hidden model call
occurs inside a Tool; count fallback and final-answer requests when evaluating the host's total model calls.

Individually published candidates are eligible for recall even while their Run is still processing other candidates. Draft, rejected and deferred candidates are never recalled.

## Scope

The initial host is Datus with MySQL/PostgreSQL-compatible parameterized read-only SQL. The workflow supports ordinary
sequential model tool calls. Dynamic DAGs, general code sandboxes, cross-host MCP execution, and automatic raw-log
ingestion are outside this workflow. Tool reads use Artifact permissions; there is no generic Tool create/replace API.
