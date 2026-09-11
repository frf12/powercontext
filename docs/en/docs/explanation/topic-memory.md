---
title: Understanding Topic Memory
description: Learn how Topic Memory incrementally maintains long-lived topics from Sources and progressively discloses them during retrieval.
---

# Understanding Topic Memory

Topic Memory is PowerContext's Artifact Family for maintaining long-lived topics. It organizes related Sources across
sessions and tasks into an evolving topic instead of splitting every piece of evidence into unrelated Memory entries. A
Topic contains a title, summary, and detail; updates preserve the stable `artifact_id` and publish a new immutable Revision.

## What it is for

Regular Memory is suited to independently retrievable facts, preferences, decisions, constraints, and work notes. A
long-lived topic needs several pieces of evidence to be organized into one continuously maintained whole, such as an
architecture topic containing its background, key decisions, current implementation, and sources.

The boundaries between Topic Memory, Memory, and Handoff are:

| Content to preserve | Use |
| --- | --- |
| A fact, decision, or constraint that later work can use independently | Memory |
| A topic organized across Sources and evolved over time | Topic Memory |
| The current task goal, progress, blockers, and next steps for a successor | Handoff |

Topic Memory does not replace Memory or change the rule that Handoff is explicitly selected and continued by the user. All
three can coexist in one Scope.

## Lifecycle from Source to retrieval

The main Topic Memory path is:

```text
Source → Pending → background Worker → Topic Revision → retrieval projections
```

1. **Source**: A message, conversation, or document is written to the immutable Source Journal for a Scope and receives a
   stable SourceRef.
2. **Pending**: The Source write only marks Topic Memory as potentially behind; it does not call the model synchronously.
3. **Background Worker**: The Worker uses its own Topic Memory Cursor to select a bounded, contiguous Source Window, combines
   it with historical Topic candidates, and decides whether to create, update, or do nothing. Processing, chunking, retrieval,
   and vector generation happen in the background.
4. **Topic Revision**: The server controls identity, Revision, operation type, and evidence association. Revisions are
   immutable; the old searchable Revision is replaced atomically only after all retrieval projections enabled by the deployment
   are ready.
5. **Retrieval projection**: The current Revision becomes searchable within that Scope. A failed process retains the Cursor and
   retries instead of replacing the current Revision with an incomplete result.

A Source Window is the input boundary for one processing pass. It is not a persisted Artifact and is not the complete evidence
set for any Topic. The server accepts only evidence IDs that refer to Sources inside the Window and maps them to exact SourceRefs.

## Progressive disclosure

Title, summary, and detail chunks all participate in Topic relevance, but the default response stays compact:

1. `search_topic_memory` or the HTTP search operation returns the exact `ArtifactRef`, title, summary, and an optional detail
   snippet.
2. The Agent decides whether the Topic is worth expanding.
3. When full content is needed, it calls `get_topic_memory` with the same `ArtifactRef` to read the exact Revision and Source
   lineage.

Do not discard the `revision` and read the latest Head using only `artifact_id`. A topic may receive a new Revision between
search and retrieval; using the exact reference keeps the result and expansion consistent. `POST /v1/context/prepare` also
adds only compact hits and does not inject full detail into every context.

## Automatic processing and explicit flush

Automatic Topic Memory waves are disabled by default. To process periodically, set a positive
`POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS`. To request background processing promptly, call:

```http
POST /v1/topic-memory/flush
Content-Type: application/json

{"scope_id":"project:quickstart"}
```

The request persists processing intent without waiting for the model or indexes:

- `{"status":"accepted"}` means the request was accepted; background processing may still be running.
- `{"status":"idle"}` means the Cursor already covered the current Source Head when the call was made; a Scheduler or another
  flush may already have processed it.

Concurrent flushes are coalesced. Each call does not create a queryable one-off Job. See the
[configuration reference](../reference/configuration.md) for the complete runtime settings.

## Searching and expanding a Topic

Search the current Scope:

```http
POST /v1/topic-memory/search
Content-Type: application/json

{"scope_id":"project:quickstart","query":"artifact processing","limit":10}
```

Pass the complete `artifact` reference from the search result to exact retrieval:

```http
POST /v1/topic-memory/get
Content-Type: application/json

{
  "scope_id":"project:quickstart",
  "artifact":{
    "family":"topic-memory",
    "artifact_id":"<from-search-result>",
    "revision":<from-search-result>
  }
}
```

See the [HTTP API](../reference/http-api.md) for request, response, and error contracts. The Agent-facing MCP projection is
read-only and provides `search_topic_memory` and `get_topic_memory`; `flush_topic_memory` remains HTTP-only.

## Scope, retrieval shape, and Dashboard

- The first version limits generation, retrieval, and exact reads to one `scope_id`; it does not perform cross-Scope retrieval.
- Without Embedding configuration, the deployment uses FTS-only retrieval. Vector or hybrid retrieval requires a complete,
  matching Embedding profile, dimension, and vector infrastructure at startup. Existing FTS Topics are not backfilled
  automatically when Embedding configuration is added later.
- The deployment owns the retrieval shape; callers cannot arbitrarily switch retrieval modes in a Topic search request.
- The Dashboard `/topics` page is a read-only management view. It can browse by publication time or search, then read an exact
  Revision; it does not create, edit, review, flush, publish, retire, or delete Topics.

The Dashboard scope list is a UI discovery list, not a general authorization boundary. See
[Server Web UI: Browse Topic Memory](../../development/server-web-ui.md) for the page and private support-route behavior.

## Verification path and common problems

Start with the [Full-capability Quick Start](../how-to/full-capability-runtime.md) to configure Generation, Scope, the database,
and Dashboard. Then verify the following sequence:

1. Confirm that the Source `scope_id` exactly matches the Scope used for retrieval and Dashboard discovery.
2. Write a Source that clearly describes a durable topic, request a Topic Memory flush, or wait for the configured automatic wave.
3. After `accepted`, wait for the background Worker; it is not confirmation that a Revision has been published.
4. Search for the Topic, record the exact returned `artifact`, and use it to read the full detail and SourceRefs.
5. Inspect the actual `mode` in the search response; a non-empty result does not prove that vector or hybrid retrieval is enabled.

If no Topic appears, check Generation configuration, Worker logs, Cursor, and Pending before changing data. If the deployment only
offers FTS, check the Embedding model, profile ID, dimension, and completeness of existing projections. The
[Topic Memory RFC](../../rfcs/0000_topic_memory.md) contains the full design and recovery boundaries; do not treat RFC intent as
proof that every deployment is already configured for it.
