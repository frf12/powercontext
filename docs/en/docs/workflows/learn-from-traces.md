---
title: Learn Experience, Skills and Tools from correct traces
description: Explicitly import complete correct traces and reuse executable SQL through the existing Supervisor.
---

# Learn Experience, Skills and Tools from correct traces

This experimental workflow learns from complete, correct traces explicitly selected by a user.
Experience, Skill and Tool share the existing Artifact tables. A Skill contains usage instructions
and references exact Tool revisions. A Tool contains its callable contract and parameterized SQL program.

## Enable an isolated pilot

Set the following Server Runtime configuration, with a configured generation model, its credentials,
and an independent persistence database:

```json
{
  "trace_learning_enabled": true,
  "artifact_processing_families": ["tool"],
  "dream_enabled": false,
  "tool_max_workers": 1,
  "tool_worker_timeout_seconds": 600
}
```

Existing deployments retain their processing capability and binding manifest migration requirements.
Split API/background deployments must declare matching Tool capabilities and share persistence.
The `tool.trace-learning.v1` binding uses the existing ArtifactProcessingSupervisor for scheduling,
worker subprocesses, quotas and fencing. LearningRun stores domain progress and checkpoints.
Ordinary Source ingestion does not trigger Tool learning.

## Import and inspect

Use `POST /v1/scopes/{scope_id}/trace-learning` with an `idempotency_key`, a Datus `host_profile`
(actual SQL dialect and database name), and `traces`. Each trace preserves its ID, question,
final answer, context, and every tool call's ID, name, arguments, result and success flag.
An ultimately correct trace may include failed intermediate attempts.

Preserve Datus `read_queries` arguments and result artifacts. Do not invent split call identities.
Generated examples select an original query with `query_index`; their `arguments` are values for the
generated Tool's input schema. Complete correctness is an admission premise supplied by the caller,
not inferred from a completion label.

Accepted imports return 202. Poll `GET /v1/scopes/{scope_id}/trace-learning/{run_id}` for completion.
Identical repeated submissions reuse the Run; conflicting contents under one key are rejected.
Successful runs return exact E/S/Tool references. Later explicit imports can reuse stable generated
keys and update artifact revisions after checking the original user's permissions and expected heads.

Validation binds example parameters and compares the generated SQL AST with a successful recorded
query. This checks covered paths without rerunning changing production data. It does not claim that
all parameter values are correct or set `live_execution_verified=true`. A failed run preserves the
previously published pool. Assess execution and effectiveness separately in the host environment.

When a generated SQL template does not reproduce a recorded example, the same Run can send the
rejected candidate and exact comparison feedback to the model for correction. Rejected candidates
remain in the Run checkpoint and are never published as Artifacts. Every attempt consumes the Run's
`max_model_calls` budget (three by default) and shares its original deadline, including after a Worker
restart. `max_output_tokens` limits each generation request; reported usage accumulates all attempts.
Corrections must still pass the full validation, including previously verified examples. Permission,
trace completeness and other validation failures do not enter this SQL correction loop.

## Prepare one inference turn

Opt into `POST /v1/context/prepare` with `learned_tools=true` and the actual `host_profile`.
Use `assembly: {"sections": []}` for a learned-only request. The returned `learned_context` contains
Experience text, Skill instructions and complete executable Tool descriptors. Inject the text before
the model call, expose Tool names/descriptions/input schemas, and bind implementations in the host.

A structured-only response is ready with `content=null` and `content_bytes=0`. Legacy requests omit
the new field. Preparation prioritizes complete Skill/Tool dependencies within the byte budget, uses
remaining space for ordinary text, and deduplicates Experience references. Incompatible hosts or
missing exact dependencies do not expose a partial Skill. Tool contracts are never truncated.

Datus binds parameters separately and executes read-only SQL through the current request's Data Gateway,
which retains datasource permissions and result limits. There is no hidden model call inside the Tool.
Count fallback and final-answer requests when evaluating total model calls.

## Scope

The initial host is Datus with MySQL/PostgreSQL-compatible parameterized read-only SQL. Agent decisions
handle ordinary sequential calls. Dynamic DAGs, general code sandboxes, cross-host MCP execution and
automatic log ingestion are outside this workflow. Retrieval uses a bounded recent successful-Run
catalog and lexical matching in the current Scope, rather than a full historical semantic Tool index.
Tools support Artifact read operations without generic create/replace endpoints.
