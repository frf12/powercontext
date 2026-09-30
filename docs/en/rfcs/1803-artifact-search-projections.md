---
title: Artifact Search Wide Tables and Single-Table Retrieval
---

- Proposal Name: `artifact_search_projections`
- Start Date: 2026-09-30
- RFC PR: [oceanbase/powercontext#1803](https://github.com/oceanbase/powercontext/pull/1803)
- Amends RFCs: [0014](0014_memory_layer_design.md), [0051](0051_experience_skill_artifact_families.md),
  [0080](0080_memory_search_reranking.md), [1417](1417_topic_memory.md)
- Related RFCs: [1396](1396_handoff_access_control.md), [1467](1467_artifact_tags.md),
  [1549](1549_artifact_family_unification.md), [1652](1652_memory_quality_and_lifecycle.md)
- Dependent RFC: [Automatic Memory as Independent Artifacts, #1809](https://github.com/oceanbase/powercontext/pull/1809)
- Implementation reference: [implementation design (Chinese)](../../zh/design/artifact-search-projections-design.md)

# Summary

This RFC defines a common storage contract for every Artifact Family that supports search. Each Family owns a current
search wide table containing all fields needed for matching, filtering, ordering, and returning results. OceanBase
full-text and vector retrieval read only that table, without joins, correlated business-table subqueries, or per-hit reads
to complete results. Authoritative revisions and heads retain their identity and history responsibilities. Search rows
are rebuildable derived data updated atomically with publication. Topic Memory, Experience, Skill, and Memory follow this
contract; the separate Automatic Memory proposal must depend on this RFC.

# Motivation

## Retrieval correctness and index use should not depend on cross-table hydration

Memory, Topic Memory, Experience, and Skill already have forms of current projections, but search fields remain spread
across tables:

| Family | Current matching data | Additional data read for complete results |
| --- | --- | --- |
| Memory | Entry-head full-text projection and separate vector projection | Content in entry versions |
| Topic Memory | Topic/chunk full-text projections and separate vector projections | Current Topic display fields, chunk text and position |
| Experience | searchable_text on the common head | `pc_artifacts.content` |
| Skill | The full-text path shared with Experience | SkillContent in `pc_artifacts.content` |

The OceanBase Topic Memory implementation documents that, on CE 4.3.5.6, putting a distance-ordered vector scan directly
into a merge join can lose nearest neighbors. It therefore limits an ANN subquery before joining display content. Memory
vector retrieval still joins entry content directly, and tag filters also change its ANN path. Experience and Skill
currently offer full-text search only and join authoritative revision content after matching.

The PMC discussion also reported full-text index degradation in queries involving joins and established single-table
retrieval as an architectural requirement. The vector issue has evidence in existing code; the full-text issue is a
meeting report whose specific versions, SQL, and plans must be included in implementation acceptance evidence. Neither
establishes that every OceanBase join loses results or degrades performance. This design reduces retrieval correctness
and performance dependencies on these query combinations.

## Copying content alone does not define a complete search contract

Even with content in the vector table, joining tags, current state, or ordering attributes retains the same query
complexity. A complete search unit therefore includes eligibility filters, ordering and return fields, and rules for
updating them when authoritative state changes.

This design is independent of Memory identity changes. Whether a memory is an entry or an independent Artifact, Topic
Memory, Experience, and Skill still need complete current search projections. A common RFC gives new Families a contract
to adopt directly.

## Expected outcomes and non-goals

Returned content, references, filters, and scoring retain each Family's public contract. Maintainers can analyze retrieval
through one Family table and explicit query budgets. Any improvements in latency, throughput, or recall require measurement
on supported backends.

This RFC does not put all Families into one physical table, merge authoritative history with search storage, require
indexes for Families without search, or change Family content, generation authorization, or evidence semantics. Automatic
Memory identity, merging, and entry conversion belong to its dependent domain proposal. The initial upgrade uses downtime
and excludes runtime dual writes, online backfill, and per-Scope cutover.

# Guide-level explanation

## Each Family owns a search wide table

The following names illustrate responsibilities; implementation determines the final DDL names:

```text
Authoritative data                         Current search projections
pc_artifacts: all immutable revisions       pc_topic_memory_search
pc_artifact_heads: each Artifact's head      pc_experience_search
                                           pc_skill_search
                                           a search table for each Memory Family
```

Experience search performs matching, Scope and state filtering, ordering, and ExperienceContent return from its own wide
table. Reading a specific historical revision continues to use authoritative version reads. Search neither fetches
content per hit from history nor joins heads to determine the current version.

Vectors and full text are two ways to search the same units. A vector-capable Family stores embeddings and full-text
fields in the same table. Experience and Skill are not required to enable embeddings: supported modes still come from
Family capability declarations.

## One wide table does not mean one row per Artifact

A Topic can produce one topic unit and several detail units:

```text
pc_topic_memory_search
T1@3 / topic       title, summary, topic search text, topic vector, eligibility fields
T1@3 / detail / 0  title, summary, original chunk and position, chunk vector, eligibility fields
T1@3 / detail / 1  title, summary, original chunk and position, chunk vector, eligibility fields
```

Each hit can return the required fields directly. Chunks have no independent public Artifact identity; results still
reference T1@3 and include the chunk position. Topic full-text, topic vector, detail full-text, and detail vector channels
retrieve within their own budgets before existing Topic folding and fusion. A single global top-k over all rows would
allow Topics with many chunks to exhaust the candidates and is not equivalent.

## Publication, deactivation, and restoration stay consistent

Revising T1@3 to T1@4 commits the authoritative revision, head, and all current search units together. Readers see a complete
state before or after that commit, never a T1@4 title with T1@3 chunks. Deactivated objects leave default retrieval while
old exact references remain readable. The Family contract determines whether restoring historical content creates or
selects a revision; the wide table projects the resulting searchable current version.

## Upgrades use a maintenance window

After stopping service writes and background Workers, operators run a separate migration tool to create tables, backfill
current content, build indexes, and verify them. The new service opens after validation. Its Runtime does not perform
ongoing online backfill or dual writes. A projection-only change creates no Artifact revisions and re-extracts no Sources.

# Reference-level explanation

## Invariants

1. **Separate ownership.** Each searchable Family owns its wide table, unit definition, and indexes. There is no shared
   cross-Family search table.
2. **Complete search fields.** Matching, filtering, ordering, and result fields are available in that table, without
   hydrating results from other business tables.
3. **Current projections only.** Searchable units represent versions currently eligible under the Family contract;
   history remains available through exact-version interfaces.
4. **Atomic publication.** Every content, governance, and projection write respects the same transaction boundary.
   Removing read-time head checks must not weaken current-version guarantees.
5. **Explicit budgets.** Scope and authorization eligibility apply before candidate truncation. Each channel is bounded;
   neither collection-sized k nor unbounded per-item lookups are acceptable.
6. **Preserved history.** Rebuilding changes no authoritative identities, content hashes, evidence, Source cursors, or
   committed revisions.

## Authoritative and search storage

`pc_artifacts` and `pc_artifact_heads` retain their identity and version responsibilities. This RFC does not add full
`search_content` to the common head: Experience and Skill return content belongs in their respective wide tables. Common
tables still serve get, history, governance, and rebuilding, but not normal search hydration or current-version checks.

The old version uses existing searchable_text and specialized projections until migration. After validation, each Family
searches its new complete wide table. A request must not silently fall back to the old join path because a field is
missing. Versioned migration tools reclaim obsolete columns or tables without deleting authoritative history or records
needed by compatibility reads.

## Search units and fields

The logical key is `(scope_id, artifact_id, unit_kind, unit_id)`, with revision stored as the current projected value.
Family identity is established by the table. A family column may be retained for self-description, but does not justify
combining Families into a shared index.

| Field group | Required information |
| --- | --- |
| Exact identity | Scope, Artifact ID, revision, unit kind and ID, authoritative content digest |
| Matching inputs | Family full-text fields; embedding, input digest, and profile when vectors are enabled |
| Eligibility and ordering | Queried state, type, tags, timestamps, quality, or visibility attributes |
| Returned content | Promised original text, title, summary, structured payload, chunk position, and metadata |
| Projection validation | Schema, analyzer, chunking and embedding configuration versions; governance generations and other invalidation inputs |

An individual Memory, Experience, or Skill usually has one row per Artifact. If legacy Memory continues to support search,
its own unit_id incorporates entry identity and retains old citation fields. That exception describes the legacy Family's
search granularity; it does not determine new Memory domain identity.

Topic uses topic/detail units. Each detail row stores its chunk text, ordinal, offset, the revision's title and summary,
and eligibility fields, without copying the complete detail body. Title, summary, or governance changes may update every
unit of that Topic; this amplification must be measured.

Experience stores complete ExperienceContent; Skill stores SkillContent and package references. Searchable package text
can contribute to full-text inputs, without copying the complete package directory into each row. searchable_text cannot
replace original text or structured payload. Detail APIs can fetch fields not promised by search using exact references;
moving required search fields into per-item detail calls does not satisfy completeness.

## Queries and filters

Each OceanBase full-text or vector channel reads one Family wide table: no joins, subqueries correlated with other
business tables, or N+1 hydration after matching. A search may execute several independent single-table channel queries
and fuse, deduplicate, fold, and rerank their bounded results. Stable secondary ordering must respect the backend's
requirements for indexed distance ordering.

Scope, lifecycle, tags, and authorization conditions must determine eligibility before candidate truncation. Retrieving
a fixed global top-k and then removing out-of-Scope or unauthorized objects is invalid. Authorization can first validate
the caller and provide bounded query context; unbounded enumeration of allowed Artifact IDs, per-hit authorization, or
changed filter semantics cannot be used to imitate single-table search.

Object attributes such as tags and visibility are projected into the wide table. Authoritative authorization records
still determine eligibility. Projected attributes update synchronously with authorization changes; revocation must not
wait for an asynchronous backfill. Scope authorization can precede search, while object conditions need explicit predicates
expressible in the table. All existing filters and authorization scenarios need equivalent implementations. A difficult
layout must not silently broaden access or remove capabilities; a Family cannot switch until these are covered.

Each Family inventories every actual WHERE, ORDER BY, and returned field, mapping each to a wide-table column or explicit
query parameter. Tag and object-authorization encoding, indexes, and update costs require design before merge, rather than
an application-layer fallback.

## Current state and write consistency

The Family writer commits authoritative revisions, lineage, heads, and affected search units in one transaction.
Independent governance, tag, or authorization-attribute changes update their projections in the corresponding authoritative
transaction. Missing new Topic units, residual old units, or mixed revisions all constitute failed publication.

This covers manual Create/Replace, domain services, background extraction, review publication, restoration, deactivation,
and supported cross-Scope publication. Skill governance generation differs from content revision; search eligibility must
also update when content does not change. Concurrent commits use the Family's version and governance preconditions.
Rebuilding cannot bypass them to become a separate business-write path.

All database channels and related eligibility reads for one hybrid search share a real consistent snapshot. Reusing a
connection alone is insufficient. Generate the query embedding before the transaction; release the read transaction after
fetching bounded candidates, then fuse and model-rerank without depending on mutable database state. Current means the
latest committed eligible version within that snapshot, not that asynchronous Source processing has caught up.

## Vector and full-text capabilities

A Family declares FTS, vector, or hybrid support. Deployments without embeddings retain complete full-text behavior.
Experience and Skill currently support only FTS; this RFC does not add vector APIs for them. Vector-capable tables also
store original content and full-text inputs, and vectors must not become a prerequisite for reading content.

Initially, a Family's active vector index uses one explicit embedding profile covering model, dimension, distance metric,
normalization, and configuration version. Adding a fingerprint cannot solve a fixed VECTOR dimension. Equal dimensions
also do not imply the same semantic space. Different profiles must not participate in one distance ranking. Model,
dimension, or embedding-input changes require rebuilding and validating against the new configuration. A changed revision
number alone does not require re-embedding, and apparently identical content cannot justify reuse when actual inputs differ.

Incompatible profile, analyzer, or chunking changes initially require a maintenance window. A persistent readiness marker
closes affected retrieval before rebuilding and survives restarts. All enabled channels must pass version, digest, unit
coverage, and profile validation before becoming ready. Historical content reads do not depend on embedding availability.
This RFC does not require preserving all historical vectors.

## SQLite backend

The logical contract of one complete search dataset per Family also applies to SQLite. FTS5 and vec0 use separate virtual
index mechanisms, requiring a Family-owned wide table and auxiliary indexes rather than OceanBase-identical DDL.

Mapping an auxiliary FTS/vec0 hit to the same search unit in that Family's wide table is a SQLite backend boundary. It
must not join common history, heads, or another Family's business tables for content or eligibility. Fields duplicated
to filter before truncation also require atomic maintenance and validation. Collection-sized neighbor counts must not
simulate Scope filtering. Supported extension versions and filter combinations require explicit verification. This
backend exception does not relax OceanBase's single-physical-table retrieval requirement.

## Offline migration and rebuilding

A tool separate from the online service records supported source formats, target format, and configuration digests.
Stop all old API writes and background Workers, capture a consistent backup, and backfill current search units in batches.
Content comes from exact authoritative revisions. Skill packages, Topic chunks, and direct evidence retain their saved
contracts, without model reinterpretation of history. Reuse vectors only when embedding inputs and profiles match exactly.

Target data and checkpoints commit atomically. Reruns verify existing targets without duplicating units. Migration can
resume after interruption, while ordinary service remains unready. Coverage, content, references, filters, and indexes
must pass validation before recording completion. New Runtime startup checks the target format and completion marker.
DDL transaction capabilities are backend-specific; the design must not assume all DDL can roll back in one transaction.

Projection migration advances no Artifact revision or Source cursor. When Automatic Memory ships in the same release,
one maintenance window can first convert its authoritative model and then build projections under this RFC. The public
contract comes first in design review; correct target authoritative content comes first in data execution. If this RFC
ships alone while legacy Memory remains searchable, that Family also needs a conforming projection without waiting for
its domain redesign.

Before new business writes, operators can restore the backup. After new writes, deployment downgrade requires explicit
format compatibility or reverse migration; restoring an old backup must not discard new data. Families retain ownership
of history and old exact references, which projection tools cannot remove while rebuilding.

## Acceptance scope

- Exact references, content, eligibility, and public scoring contracts remain correct after all supported publication
  paths. Old revisions and deactivated objects do not remain retrievable.
- Channels do not join authoritative content, heads, tags, or authorization business tables, or disguise N+1 reads as
  join-free search.
- Topic channel budgets, chunk folding, and fusion follow its contract; one query never mixes old and new revisions.
- On actual target OceanBase releases and patches, inspect plans, scanned rows, filtered recall@k, latency, and memory.
  Include multiple Scopes, tags, authorization selectivity, and history much larger than current data. SQL text or mocks
  do not demonstrate index use.
- Measure rows, bytes, embedding calls, and transaction duration for content, tag, authorization, and state changes,
  especially updates to large multi-unit Topics.
- Verify interrupted migration recovery, FTS-only deployments, SQLite auxiliary-index consistency, profile rebuilds, and
  persistent readiness after restart.

# Drawbacks

**Storage and write amplification.** Return content and eligibility attributes are duplicated. Every Topic chunk also
carries title, summary, and eligibility fields. Frequent tag or authorization changes can create write hotspots. Separate
Family tables add index and migration objects; measurements must establish whether query benefits justify these costs.

**Stricter maintenance responsibility.** Search can no longer correct stale rows by joining heads. Any omitted governance
or write path can expose stale results. Projected permission attributes also require explicit revocation consistency.

**Different physical implementations.** OceanBase native indexes and SQLite virtual tables differ; vector dimensions and
profiles further constrain generalization. A shared behavioral contract does not imply one SQL template for all backends.

**Upgrade downtime.** Migration requires capacity planning, additional disk, and a maintenance window. Projecting current
content does not prevent authoritative history from growing.

# Rationale and alternatives

## Why one wide table per Family

Families have different search units, payloads, index capabilities, and update schedules. Separate tables align these
responsibilities with their services and avoid changing the common head for Experience and Skill return content. They
share a contract and necessary infrastructure rather than a cross-Family physical index.

## One shared search table

This simplifies a common query entry point but mixes granularity, embedding configuration, and lifecycle, creating sparse
fields and more complex eligibility filters. Cross-Family search can fuse bounded candidates from separate tables without
sharing their underlying physical storage.

## Keep joins and hydrate after retrieval

The bounded ANN subquery used by Topic Memory is an existing workaround with less duplication. However, it still requires
different combinations of full-text, vector, filtering, ordering, and hydration queries. The PMC has selected complete
single-table retrieval as the target, so the main search path will no longer depend on those combinations.

## Separate complete full-text and vector tables

This can make each channel independently join-free but duplicates return and eligibility fields and maintains two copies
of current data. Two indexes on one Family table reduce the opportunities for divergence. SQLite virtual-index constraints
are handled by its explicit backend contract.

## Do nothing

Individual SQL hotspots can continue to be optimized, but every new Family can reintroduce joins for content, state, or
permissions. Index behavior and current-version responsibilities remain scattered. Without a public contract, Automatic
Memory cannot establish search requirements for the other Families.

# Prior art

[RFC 1417](1417_topic_memory.md) establishes current projections, Topic/chunk units, progressive disclosure, and atomic
publication. [RFC 0051](0051_experience_skill_artifact_families.md) defines Experience/Skill content and admission;
[RFC 1467](1467_artifact_tags.md) and [RFC 1396](1396_handoff_access_control.md) define tag and authorization semantics
that must be preserved. This RFC extends their search storage layout without changing authoritative content or
authorization objects.

The existing [OceanBase Topic index](../../../src/powercontext/builtin/persistence/oceanbase/topic_memory_index.py),
[OceanBase Memory index](../../../src/powercontext/builtin/persistence/oceanbase/memory_index.py), and
[OceanBase Experience/Skill index](../../../src/powercontext/builtin/persistence/oceanbase/experience_index.py) show the
query paths addressed here. Code evidence and PMC feedback describe current issues, not implemented wide tables or
completed performance acceptance.

# Unresolved questions

The following need a design or explicit support boundary before merge:

1. **Filter encoding.** How tags, resource authorization, revocation, and cross-Scope reads map to single-table eligibility
   predicates and indexes, with bounded fan-out and update costs. Every existing authorization scenario must be covered.
2. **Backend support matrix.** Initial OceanBase patches, analyzers and vector indexes, plus SQLite/vec0 versions and
   filter paths. The reported full-text degradation needs reproducible SQL, plans, and dataset sizes.
3. **Profile constraints.** Whether current configuration permits different Scope profiles within one Family. Restricting
   the initial version to one effective profile requires explicit compatibility and migration rejection rules, not silent
   configuration replacement.
4. **Index and capacity budgets.** Representative data must calibrate initial limits for payload size, Topic units,
   tag/permission fan-out, candidates, and rebuild space.

Concrete DDL, batch sizes, tool commands, and module boundaries belong to the implementation design and cannot change the
single-table retrieval, authorization, or history invariants.

# Future possibilities

- Cross-Family fusion can reuse each table's bounded candidates without merging physical tables.
- Multiple profile indexes or online index generations need separate resource, cutover, and recovery contracts.
- A commercial edition may provide online upgrades through a barrier release that retains backfill, catch-up, dual-write,
  and cutover logic. Historical versions must finish migration there and persist completion before upgrading further.
  Later versions may retain only a lightweight gate. Barrier binaries and their support boundaries must remain available;
  one installation completing migration does not remove upgrade obligations for other users.
