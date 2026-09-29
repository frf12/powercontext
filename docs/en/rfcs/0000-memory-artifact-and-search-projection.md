---
title: Individual Memory Artifacts and Current Search Projections
---

- Proposal Name: `memory_artifact_and_search_projection`
- Start Date: 2026-09-30
- RFC PR: Not submitted
- Amends RFCs: [0014](0014_memory_layer_design.md), [0019](0019_local_source_memory_runtime.md),
  [1345](1345_scope_organization_and_agent_integration.md), [1652](1652_memory_quality_and_lifecycle.md),
  [1718](1718_memory_capacity_contract.md)
- Related RFCs: [1417](1417_topic_memory.md), [1549](1549_artifact_family_unification.md)
- Implementation reference: [implementation design (Chinese)](../../zh/design/memory-artifact-and-search-projection.md)

# Summary

This RFC proposes changing the unit of Memory versioning from a collection of memories within a Scope to an individual
memory. A Scope directly contains multiple Memory Artifacts; each former logical Memory Entry becomes an Artifact, with
changes to its content, evidence, and state represented by its own revisions. Extraction uses bounded retrieval to find
related memories in the same Scope, then proposes creation, revision, merging, or conflict handling. Search reuses existing
current projections and adds the content needed for results, avoiding a join to historical content or heads on every
retrieval. Migration preserves historical versions and exact references, with an explicit upgrade boundary for old clients.

# Motivation

## The collection's responsibilities overlap with those of Scope and Artifact

The Memory Artifact defined by [RFC 0014](0014_memory_layer_design.md) is a collection with a complete manifest. Each
manifest item references an immutable entry version; changing an entry then requires committing a new collection revision.
This design provides collection snapshots and exact references, but also introduces two layers of identity and versioning:
the collection Artifact and its revisions, and the entry and its versions.

[RFC 1345](1345_scope_organization_and_agent_integration.md) already uses Scope to express persistent ownership, but
explicitly retains the constraint that a Scope has only one active Memory progression line. This RFC revises that constraint
so that memory content directly uses the project's existing Scope → Artifact → Revision model. Scope owns the memories,
and each Memory Artifact represents a fact that can be maintained independently. A collection Artifact no longer duplicates
the organization of those facts.

## Changing one memory still requires committing the entire collection directory

The current implementation already supports entry revision, historical reads, exact-content deduplication, capacity limits,
and optional compact operations. It also updates search projections incrementally for changed entries. The problem is
therefore not that every update copies all entry content or rewrites all vectors.

The remaining cost is the complete manifest: changing one entry requires constructing, sorting, serializing, and storing
every directory item. With an average directory size of N and R writes, directory history is approximately O(R × N). Two
tasks changing different entries also share the same collection head as a concurrency precondition.
[RFC 1718](1718_memory_capacity_contract.md) provides a capacity boundary without changing the versioning granularity.

For memories accumulated over time, users need to update an individual fact and inspect its previous content. Including
every unchanged fact in each collection snapshot is not necessary to provide either capability.

## Extraction already supports revision across windows, but lacks bounded coordination with related memories

The current built-in extraction passes all active entries from the selected Memory head to the model, which can add or
revise entries. It can therefore handle memories produced by earlier Source windows; deduplication is not limited to a
single window.

The limitations of this path are that the old-memory input grows with the active collection, there is no separate step to
generate candidates before retrieving related existing memories, and there is no complete action contract for merging
similar memories or handling conflicts that cannot be resolved. The user request to aggregate and deduplicate by relevance
within the same Scope requires addressing this processing pipeline, beyond adding an interface that overwrites content.

## Current content already has projections, but search still needs joins to complete results

Memory and Topic Memory already maintain current full-text or vector projections; Experience and Skill use current text
for full-text search. Some paths still need to join content tables to return complete content. In OceanBase, combining ANN
with joins also requires considering the optimizer and execution plans of specific versions. The existing Topic Memory
implementation already uses a bounded ANN subquery followed by a join for this reason.

This does not mean that OceanBase lacks JOIN support or that Experience and Skill have vector search problems. The latter
two do not currently provide vector search. This RFC aims to make the existing current projections in each channel also
carry the content needed for search results, clarify their consistency responsibilities, and reduce the retrieval path's
dependence on historical storage.

## Expected outcomes and non-goals

With this design implemented, users can maintain, retrieve, and reference each memory as an independent Artifact. Updating one
memory no longer writes a complete Scope directory; extraction has explicit budgets for reading related existing memories;
and old references still resolve to their original exact content.

This RFC does not introduce automatic merging across Scopes, delete historical content, change the content models of
Profile, Handoff, or Prompt, or promise an online upgrade without pausing writes. It does not provide implicit snapshot
rollback for an entire Scope, require every query to avoid JOINs, or combine authoritative records with derived indexes
merely to reduce the number of tables.

# Guide-level explanation

## One memory is one Artifact

Suppose a project Scope contains two memories:

```text
Scope: project-a
├── Memory M1@1: Production releases require approval from the owner.
└── Memory M2@1: The default deployment region is East China.
```

The user later adds that production releases must also pass a security check. Revising M1 produces:

```text
Scope: project-a
├── Memory M1@2: Production releases require approval from the owner and a completed security check.
└── Memory M2@1: The default deployment region is East China.
```

M1 retains its identity, and its revision advances from 1 to 2. M2 does not receive a new revision. Callers can read the
current version of M1 or read M1@1 exactly with a normal ArtifactRef. They no longer need to carry a collection reference,
an entry ID, and an entry version ID.

Search returns the current searchable versions by default, including their content and exact refs. Listing Memory within
a Scope lists these independent Artifacts.

## New Sources are coordinated with existing memories

When processing a new Source segment, the system first generates candidate facts, then retrieves related current memories
within the same Scope and authorized boundary. For example, if a candidate fact says that production releases require a
security check first, the system should retrieve M1@2 and determine whether it already expresses the fact sufficiently and
whether new evidence needs to be added.

Coordination can produce the following outcomes:

- No related fact exists: create a new Memory.
- An existing fact needs an addition or correction: revise that Memory, preserving its identity and evidence history.
- Several memories should be combined: propose a merge, retain one identity, and remove the other objects from active
  search.
- The content is already sufficiently expressed and there is no new evidence: produce no new version.
- Facts contradict one another and their applicable conditions or temporal order cannot be determined: save an unresolved
  conflict, without using a similarity score to decide which fact to overwrite.

Retrieval here is budgeted candidate selection. It does not promise that one ANN query will discover every duplicate memory.
Unfinished work must retain a state from which processing can continue. Ordinary creation and revision follow existing
extraction authorization; semantic merging of multiple memories is also subject to the review and preauthorization rules
below.

## Forgetting and restoration still preserve history

`forget` removes a Memory from default search while preserving reads through historical references. `reactivate` can
restore an ordinarily deactivated memory.

Restoring the content of M1@1 does not move the head backward from 2 to 1. It creates M1@3 and records the restoration
source. The content and reference of M1@2 remain available. If the current vector projection has already been replaced,
restoration may require generating the vector again; reading the historical content of M1@1 does not depend on the
embedding service.

Merging likewise does not automatically redirect old references to the new head of the retained object. References describe
the exact facts at the time, while supersession relationships describe the result of current maintenance.

## Existing deployments require an explicit upgrade

Existing deployments migrate their data and upgrade clients during a maintenance window. After migration, old exact
MemoryCitations remain readable. Legacy collection latest and entry write protocols no longer represent current Memory
and return an explicit upgrade error when called.

Migration does not extract already consumed Sources again or rewrite references in old Handoffs and Experiences to new
content. New users use independent Memory Artifacts from the outset and do not need to understand the legacy collection
structure.

# Reference-level explanation

## Design invariants

1. **Independent identity.** Each logical memory has a stable Artifact ID and receives a revision only when its own content,
   direct evidence, or state changes meaningfully.
2. **Immutable history.** Committed content, evidence, and exact references are not rewritten by revision, merging,
   deactivation, or migration.
3. **Separate authority from projections.** Artifact revisions store facts; heads and current search projections point to
   those facts and can be rebuilt from authoritative records.
4. **Atomic publication.** A new revision, head, lifecycle state, and all enabled search channels commit together, without
   exposing a partially completed version.
5. **Bounded processing.** Ordinary incremental writes do not construct a complete Scope directory; extraction cannot
   substitute loading all active Memory for retrieval of related memories.
6. **Detectable upgrade state.** An incomplete migration cannot operate as a normal new deployment. Exact historical read
   compatibility cannot stand in for compatibility with legacy latest reads or write protocols.

## Identity, content, and lifecycle

A Memory's identity is `(scope_id, family="memory", artifact_id)`. Exact versions use the existing ArtifactRef, and
cross-Scope addressing uses ArtifactAddress. The new content format is `powercontext.memory.v2`, containing at least kind,
text, state, and optional superseded_by. Source and upstream Artifact evidence use existing lineage; operation records
store the reason for the change, the actor, the idempotency identity, and the restoration source.

States have the following semantics:

| State | Default search | Restoration | Head governance projection |
| --- | --- | --- | --- |
| active | Included | Normal revision | active |
| inactive | Excluded | Explicit reactivate is allowed | deprecated |
| retired | Excluded | Reusing the content requires a new identity with provenance preserved | retired |

State is part of a Memory revision's historical content. Generic governance entry points must go through the Memory writer
and cannot change the head separately while bypassing version records. A state-only change must also preserve the content's
evidence. Revision inherits predecessor evidence and appends evidence for the current change; merging preserves the exact
refs of participating objects and valid direct evidence; restoration preserves its source and the evidence of the restored
version. Preserving historical provenance does not restore current eligibility for generation.

Objects made inactive by a merge may record an exact superseded_by reference within the same Scope. Self-references and
cycles in supersession relationships are prohibited. Undoing the whole group uses compensating versions and verifies that
every object still matches the after refs produced by the operation. Subsequent changes cause a conflict to be reported
instead of being overwritten.

## Source processing and coordination actions

This RFC does not create a Source cursor for each Memory. Existing processing bindings within a Scope continue to consume
Sources, and one processing run can create or modify multiple Memory Artifacts. Bindings select the processing range and
policy rather than a unique collection Artifact. Separate Memory heads do not imply independently consuming the same Source
again.

The processing pipeline is:

```text
Source window → Candidate generation → Deduplication within the batch → Related retrieval within the Scope
              → Coordination actions → Evidence and authorization validation → Atomic commit
```

Budgets are required for candidate count, retrieval volume per candidate, the number of related objects after combining
results, model context bytes or tokens, and total model calls. Permissions, Scope, and lifecycle constraints determine
candidate eligibility. They cannot be applied only after a fixed top-k cutoff and the remaining results treated as complete
filtered retrieval.

| Action | Version and evidence requirements |
| --- | --- |
| create | New identity, initial revision, and direct evidence |
| revise | Target's current ref, preserved predecessor evidence, the current change, and its evidence |
| merge | Current refs for all participants, retained object, new content, deactivated objects, and compensation boundary |
| noop | Existing expression is sufficient and there is no new evidence; no revision is produced |
| conflict | Save the candidate, contradictory evidence, and related refs; do not silently overwrite |

Ordinary create/revise actions retain the existing automatic write boundary. L2 semantic maintenance defined by
[RFC 1652](1652_memory_quality_and_lifecycle.md) still requires individual review by default. This RFC proposes one explicit
exception: a Scope administrator may preauthorize merges concerning the same subject, with the same applicability,
compatible content, and complete preservation of evidence, while limiting the number of participants and the operation
budget. Automatic merging is disabled by default. Each execution must record the authorizing party, policy version, and
applicable boundary.

Unresolved conflicts, HOLD, mandatory review policies, actions outside the preauthorized boundary, and irreversible
retirement still require individual review. Similarity or model scores cannot replace authorization. An explicit merge
expresses the caller's instruction for that group of changes, but also cannot bypass permissions or mandatory review rules.
This preauthorization exception is a substantive amendment to RFC 1652.

Candidates that cannot be completed within budget may be split, retried, or saved as pending actions. The input position may
advance only after their final outcomes or recoverable pending records are persisted together with the Source cursor.
Known conflicts must carry a marker and related references in search and Prepare; ranking alone cannot communicate them.

## Concurrency and capacity

Each revision target uses an expected revision. A merge of multiple memories either satisfies every precondition and
commits, or fails as a whole. Operation idempotency keys are independent of content similarity: retrying the same operation
after a lost response must not create a second Artifact.

Automatic coordination also depends on the precondition that retrieval did not miss a related change. Target CAS alone
cannot handle concurrent create actions. One feasible initial implementation is a Scope Memory write generation: record the
generation when retrieval starts and coordinate again if another write is detected at commit time. This is fixed-size
concurrency metadata, not a collection revision. An independent explicit revision only needs to validate its own target; it
advances the generation but does not fail because an unrelated object changed. A finer-grained mechanism must provide
equivalent invalidation checks.

RFC 1718's capacity constraint becomes the number of active Memory Artifacts within a Scope. The proposed default remains
5,000, following the standard single-container deployment. Manifest item and byte budgets no longer apply to the new format;
historical storage does not automatically become bounded as a result. The active count must be updated atomically with
create, reactivate, deactivate, and merge, and concurrent commits must not jointly exceed the limit. Operations involving
multiple memories validate the net change. When already over capacity, revisions and corrective reductions remain allowed
if they do not increase the dimension that exceeds its limit.

## Persistence and current search projections

`pc_artifacts` continues to store immutable revisions with no schema changes. `pc_artifact_heads` continues to store the
current revision of each Artifact, without placing a current pointer in every historical content row. New Memory content
goes directly into `pc_artifacts.content`; the legacy entry versions table serves historical compatibility only.

This RFC reuses existing projections rather than adding a generic current table:

| Artifact | Change to current projections | Content returned by search |
| --- | --- | --- |
| Memory | Existing entry head and vector projections adopt independent Artifact identities and add content and revision | Original text, kind, exact ref |
| Topic Memory | Existing Topic/Chunk full-text and vector projections add display fields | title, summary; chunk hits also include chunk_text and location |
| Experience | OB heads add nullable search_content; SQLite FTS stores untokenized content | Complete ExperienceContent |
| Skill | Shares the content projection described for Experience | SkillContent and package references; package files are not copied |

This is a limited extension to a shared table: the primary key and revision structure of `pc_artifact_heads` remain
unchanged, with only a nullable content projection column added. SQLite stores the copy in FTS and leaves
head.search_content null to avoid duplicate caching. Vector content for Memory and Topic Memory cannot replace full-text
projections; deployments without configured embeddings must still provide complete full-text search.

Topic chunks do not copy the entire detail. When a title or summary changes, display fields and revisions for the related
chunks update together, so write volume still grows with the number of chunks in that Topic. The searchable_text of
Experience and Skill is matching input and cannot replace their original structured content.

## Consistency, rebuilding, and backend boundaries

The Memory writer commits versions, lineage, heads, capacity changes, operation records, and all enabled projections in the
same database transaction. Background extraction also commits the cursor and processing state in that transaction. Generic
Create/Replace and governance entry points follow the same rules. Manual, background, review publication, and supported
cross-Scope publication entry points for other Artifacts must also maintain their current content projections atomically.

For search, “current” means the latest committed eligible version within the query snapshot, not that a Source has already
completed asynchronous extraction. Channels and eligibility queries within a hybrid search share a real consistent read
snapshot. Deactivated objects leave every current search channel. SQLite cannot retain searchable stale FTS rows after
removing head validation.

Vector projections record the digest of the embedding input and its profile. Merely changing the revision cannot justify
reusing a mismatched vector. Current ANN indexes retain only searchable current content. Restoring history may reuse a
matching cache or generate embeddings again when no cache is available. This RFC does not require retaining all historical
vectors.

Projections can be rebuilt from exact authoritative content. While a rebuild or migration remains incomplete, affected
search must remain not ready, including after process restarts, and resume only after full validation passes. Rebuilding
does not create new Artifact revisions, advance Source cursors, or reinterpret historical facts.

Returning results directly from projections does not prohibit necessary joins for tags, permissions, or SQLite vec0
metadata. OceanBase requires validation of actual ANN plans with Scope and permission filters on the target release and
patch; the presence of a vector index in DDL is not evidence that a query uses it. SQLite likewise cannot substitute a
whole-table k for bounded retrieval within a Scope. Unsupported query combinations should fail explicitly or use a budgeted,
observable alternative strategy.

## Public API and compatibility

Memory list/get/replace/history operations use individual Artifact identities. Search returns content and exact refs; flush
returns processing progress and refs affected by the current run instead of a unique collection revision. Prepare removes
the assumption that Memory hits from the same Scope must share a collection ref. Tags, ACLs, and consumers such as newly
created Handoffs and Experiences use ordinary Memory ArtifactRefs.

These are incompatible Memory protocol changes and require an explicit new contract version, synchronized across HTTP,
SDKs, MCP, and custom extraction Prompts. OpenAPI remains the source of the HTTP contract, and the Runtime/Family service
implements domain rules consistently. Projection changes for Experience, Skill, and Topic Memory preserve their public
content, reference, and search scoring contracts.

After cutover, legacy collection writes, entry writes, and interfaces that depend on collection latest return a stable
`upgrade_required` error with migration information. They must neither describe a frozen legacy collection as current
results nor fabricate collection revisions to maintain legacy CAS.

## Historical migration

Migration uses an explicit maintenance window: stop old writers, retain a consistent backup, convert history, and enable
the new protocol after validation. Migration checkpoints and completion markers must be recoverable. On startup,
unmigrated or partially migrated data must be rejected for normal writes, current listings, and search. Explicit format and
identity registration route old and new formats; routing cannot guess from ID prefixes or load all content in a Scope on
every call to identify the format.

Each legacy logical entry uses a stable mapping:

```text
(scope_id, legacy_collection_id, legacy_entry_id) → new_memory_artifact_id
```

All legacy containers must be inventoried. Migration cannot assume that only the standard container named memory exists or
that entry IDs are unique within a Scope. It replays the complete collection history and generates a new revision for an
entry only when that entry's content, evidence, state, or membership changes:

| Legacy collection history | New independent memory history |
| --- | --- |
| C@1: E1_v1 active | M1@1 active |
| C@2: E1 unchanged; only E2 changed | M1 remains @1 |
| C@3: E1_v2 active | M1@2 active |
| C@4: E1_v2 inactive | M1@3 inactive |
| C@5: compact removes E1 | M1@4 retired; history preserved |

A new revision therefore does not directly equal a legacy entry version. Objects absent after compact must not be migrated
as ordinary inactive objects that can be reactivated. Historical import validates old evidence and digests without deleting
old evidence based on today's generation eligibility or substituting import time for an unknown historical event time.

A legacy MemoryCitation first validates membership, version, and hash against the legacy manifest it names, then maps to
an exact new ref. Mappings are stored by intervals between changes, avoiding expansion into a matrix of entries × all
collection revisions. Intervals in which an entry is absent after compact do not accept legacy entry citations. A legacy
collection ArtifactRef on its own still represents a collection snapshot and cannot arbitrarily point to one new Memory
within it. Old immutable documents and hashes are not rewritten.

Entry tags, authorization, expiration, and revocation semantics migrate one to one. Collection tags retain their collection
semantics and are not copied into tags on every memory or into Scope authorization. Processing cursors, accepted tasks, and
input high-water marks are preserved; old leases are invalidated. Already consumed Sources are not extracted again. If
combining multiple containers exceeds the new Scope budget, migration reports the difference and requires explicit
configuration; it cannot discard data. Incompatible old custom Prompts also require an explicit upgrade.

Before cutover, restoring the backup can return the deployment to the old version. Once cutover has accepted new writes,
legacy collections are no longer synchronized, and directly restoring the old backup would lose new data. Repairs should
then move forward; downgrade requires a separate reverse-migration design. Legacy history remains stored, so disk usage may
increase during the initial migration period.

## Relationship to existing RFCs

| RFC | Relationship to this proposal |
| --- | --- |
| 0014, 0019 | Preserve immutable history, evidence, and Scope consumption boundaries; replace collection manifests and the entry-specific version model |
| 1345 | Preserve Scope ownership and authorization; revise the single active Memory progression line and clarify that consumption cursors are not split per memory |
| 1417 | Preserve Topic content, chunking, and atomic publication; extend the return fields of existing current projections |
| 1549 | Use existing Family capabilities to integrate Memory with standard read and write boundaries |
| 1652 | Preserve conflict, evidence, and lifecycle principles; use ArtifactRefs for relationships and propose a limited merge preauthorization exception |
| 1718 | Preserve deterministic capacity rejection and historical retention; change collection capacity to current Memory capacity within a Scope, with legacy compact handled by historical compatibility |

# Drawbacks

**This requires a real protocol and data migration.** Collection and entry refs already appear in clients, Prompts,
Handoffs, permissions, and tags. Unifying the model can reduce the cost of adding capabilities later, but cannot eliminate
the cost of this upgrade. Explicitly pausing writes affects availability, and the window duration needs to be rehearsed
against the deployment's data volume.

**Copies of current content increase write and storage costs.** Memory full-text and vector channels each store return
fields, Topic title/summary fields are duplicated across chunks, and Experience/Skill gain copies of current structured
content. Fewer query joins do not automatically make every workload faster.

**Bounded retrieval may miss related memories.** Once full-context input is removed, retrieval evaluation, conflict records,
and subsequent maintenance must address the gap, without promising an automatically duplicate-free global collection.
Automatic merging also risks losing qualifying conditions, making evidence, authorization, and compensation boundaries
necessary.

**Consistency responsibilities become more concentrated.** A single write path that misses a projection update can return
stale content. Removing read-time head validation requires every write and governance entry point to honor atomic
publication. Independent Artifacts eliminate collection CAS, but the initial Scope generation coordination may still cause
retries in a busy Scope.

**History continues to grow.** This proposal stops adding complete manifests but deletes neither old manifests nor content,
and provides no historical retention policy. Legacy history, new versions, and mappings coexist during migration, requiring
space estimates in advance.

# Rationale and alternatives

## Why promote an entry to an Artifact?

An individual memory is what users want to maintain, retrieve, and reference independently. Artifacts already have identity,
revision, lineage, tags, and authorization mechanisms, and Scope already provides ownership. Using these removes Memory's
second, specialized versioning layer and makes update costs follow the objects that actually change.

## Retain collections with incremental manifests

This is a natural follow-up direction proposed by RFC 1718. It can reduce directory copying while preserving existing APIs
and suits systems that continue to treat collection snapshots as a core capability. This proposal does not select it
because collection identity, collection-level CAS, entry-specific references, and full extraction context still require
separate solutions. It optimizes the encoding of the old structure without achieving the unified Scope → Artifact model
proposed here.

## Split a Scope into multiple Memory collections

This can limit individual manifest sizes, but adds collection routing, deduplication across collections, Source assignment,
and reference selection. Independent facts still reside inside containers. This proposal versions facts directly instead
of introducing another Memory container partitioning scheme.

## Add a generic current table or change all historical tables

A generic current table can centralize content caching, but duplicates maintenance of existing Memory/Topic full-text and
vector projections. Putting all historical and current content in one search table would couple is_current filtering,
historical vectors, and index maintenance while widening the changes to shared storage. This proposal reuses existing
projections and adds a content copy only to the heads used by Experience/Skill.

## Keep joining content after retrieval

This avoids copies and is a reasonable query design. The existing bounded ANN subquery in Topic Memory already demonstrates
a viable implementation path. Accepting it means continuing to maintain backend-specific combinations of ANN and joins,
and completing the content of each hit. This proposal accepts limited write amplification so that current projections
directly provide current search results. The eventual performance difference must be measured on the target backends;
neither table counts nor JOIN counts alone can establish it.

## Do not make this change

Capacity limits and compact can continue to control individual collection sizes, and entry replace or related retrieval
can be added separately. Over time, however, the system still needs to maintain two versioning layers, complete directory
history, and their public references. If the project decides that collection snapshots matter more than model unification,
incremental manifests are the alternative to compare first.

# Prior art

This RFC primarily follows existing PowerContext designs rather than introducing a new persistence paradigm:

- [RFC 1345](1345_scope_organization_and_agent_integration.md) establishes Scope ownership and lists multiple Memory
  progression lines as a future topic.
- [RFC 1417](1417_topic_memory.md) already has candidate processing, related Topic selection, current projections, and atomic
  publication, offering a reference for bounded coordination and retrieval.
- [RFC 1549](1549_artifact_family_unification.md) provides unified Family capabilities and standard Artifact read boundaries.
- [RFC 1652](1652_memory_quality_and_lifecycle.md) distinguishes similarity, conflict, validity, and recoverable deactivation,
  and requires maintenance to preserve exact evidence.
- [RFC 1718](1718_memory_capacity_contract.md) specifies full manifest costs, capacity rejection, and preservation of history
  during compact.

These designs provide reusable boundaries. They do not mean that this RFC's independent Memory model or migration is
already implemented. See the [implementation design (Chinese)](../../zh/design/memory-artifact-and-search-projection.md)
for concrete table layouts, backend considerations, and migration execution steps. This RFC defines the behavioral
contract on which consensus is required.

# Unresolved questions

The following questions need resolution before this RFC is merged:

1. **Public protocol version and compatibility period.** Should Memory use new paths or an explicit protocol version? How
   long will old exact reads remain supported, and how will clients discover migration state? Either form must reject old
   write semantics and cannot allow old and new collections to be authoritative simultaneously.
2. **Initially supported backend versions.** The OceanBase release and patch, SQLite/vec0 versions, and permitted filter
   combinations need to be specified. Support claims should be based on representative execution plans and retrieval
   measurements. Unmeasured performance gains cannot become promises.
3. **Ownership of Scope budget configuration.** The suggested initial value remains 5,000 active memories, but the
   relationship between deployment defaults and Scope overrides, and the configuration interface for importing multiple
   legacy containers, must be determined. Migration cannot silently expand budgets or discard history.
4. **Coordination budgets and concurrency cost.** Initial candidate counts, context limits, and retry limits need calibration
   with representative data. Measurements should determine whether Scope generations can handle the target workload or
   require finer-grained invalidation. Bounded processing, idempotency, and invalidation checks themselves must not be
   omitted.

Concrete SQL, index column order, migration batch sizes, and command names can be determined during implementation as long
as they satisfy this contract. Implementation order and acceptance checklists reside in the implementation design; accepting
the RFC does not automatically turn them into a delivery schedule.

# Future possibilities

- Once current projections and exact versions are stable, provide index generation cutover and online migration without
  blocking search. Catch-up, cutover, and rollback require separate solutions.
- Supplement bounded online retrieval with offline maintenance to discover near-duplicates and conflicts accumulated over
  time. Use the same evidence and authorization contract without automatically rewriting facts under capacity pressure.
- Introduce historical vector caches with exact profiles to reduce computation during content restoration and rebuilding.
  They cannot replace authoritative content history.
- Separately design historical retention, physical cleanup, and Scope snapshot exports where needed. They should not
  reintroduce a complete collection manifest updated on every individual write.
