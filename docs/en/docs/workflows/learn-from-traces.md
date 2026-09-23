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

The Supervisor first asks the model for a bounded inventory. It then processes candidates independently in Experience,
Tool, and Skill order. `max_candidates_per_family` limits the inventory for each family. A dependent Skill is generated
after the Tool candidates it names, so its `tool_keys` can refer to validated candidates in the same Run.

Each candidate has its own saved model messages, revisions, review findings, validation result, repair count, and
outcome. On a Worker restart, the next attempt resumes the saved conversation and candidate checkpoint. Already
published candidates are skipped; the Worker does not regenerate them.

Tool review is deliberately independent. The reviewer receives only the candidate's `ToolContent`: it cannot see the
trace, question, reference answer, examples, or the generating conversation. The candidate generator receives the
original saved `messages` when it repairs a candidate. It must answer every finding exactly once with `accept`,
`partial`, or `reject` and a reason. Review suggestions are advisory. Deterministic validation errors, including hard
SQL validation, cannot be waived by a review decision.

Candidate failures are isolated. A rejected or deferred candidate does not roll back candidates that were already
published. A Run with `status=succeeded` means that at least one candidate produced a publishable artifact; it does not
mean that every planned candidate finished. Inspect `candidate_outcomes` to determine whether all candidates are
`published` or whether any are `rejected` or `deferred`. A budget or deadline stop can leave partial publications and
records why the remaining work stopped in the candidate outcomes or the Run error state.

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
| `max_candidate_repair_rounds` | `2` | `8` | Additional quality-repair rounds for one candidate |
| `max_output_tokens` | `16000` | `64000` | Output-token limit for each provider request |
| `max_input_chars` | `400000` | `4194304` | Serialized input and saved message budget |
| `timeout_seconds` | `1800` | `7200` | Overall Run deadline |
| `previous_artifact_limit` | `30` | `100` | Number of reusable previous artifact heads selected after retrieval |
| `max_pending_per_scope` | `32` | `1000` | Queued and running trace-learning Runs in one Scope |

The Supervisor reserves the next request before dispatching it. If a Worker loses a response or exits during a model
call, that reservation is retained; a restart cannot spend it again. `max_output_tokens` applies per request while the
Run's reported usage accumulates all requests.

For ordinary structured generation, `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MAX_REQUESTS=2` means one initial
request plus one repair request. Setting it to `3` permits one initial request plus two repairs. This per-operation
limit is still subject to the trace-learning Run budget.

`usage.reserved_model_calls` identifies unconfirmed reservations included in `usage.model_calls`. Subtract it to obtain confirmed requests. A known pre-request configuration failure releases its reservation; uncertain calls retain theirs and are not reported as confirmed model usage.

## Prepare one inference turn

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
and Artifact read authorization, filters by execution host, and does not execute SQL or invoke a model. The host binds
returned tools to its executor.

PC's `max_bytes` default remains 8,000 bytes, with a maximum of 32,768. Callers may explicitly request a larger budget;
this neither adds model requests nor requires filling the available context.

Retrieval reads Runs with published candidates in pages, collects all active heads, and then ranks related artifacts by the query and
the available byte budget. It uses lexical matching in the current Scope, not a semantic vector index. It is therefore
not limited to the latest thirty Runs with published candidates, although the Run budget still bounds the number of previous heads
selected for generation.

For Datus, `output_schema` is appended to the Tool description supplied to the LLM, while `args_schema` is passed
through unchanged. Bind Tool parameters separately and execute the fixed read-only SQL through the current request's
Data Gateway. The Gateway retains datasource permissions, result limits, and current data access. No hidden model call
occurs inside a Tool; count fallback and final-answer requests when evaluating the host's total model calls.

Individually published candidates are eligible for recall even while their Run is still processing other candidates. Draft, rejected and deferred candidates are never recalled.

## Scope

The initial host is Datus with MySQL/PostgreSQL-compatible parameterized read-only SQL. The workflow supports ordinary
sequential model tool calls. Dynamic DAGs, general code sandboxes, cross-host MCP execution, and automatic raw-log
ingestion are outside this workflow. Tool reads use Artifact permissions; there is no generic Tool create/replace API.
