---
title: Automatic Memory as Independent Artifacts
---

- Proposal Name: `automatic_memory`
- Start Date: 2026-09-30
- RFC PR: [oceanbase/powercontext#1809](https://github.com/oceanbase/powercontext/pull/1809)
- Depends on: [Artifact Search Wide Tables and Single-Table Retrieval, #1803](https://github.com/oceanbase/powercontext/pull/1803)
- Amends: [0014](0014_memory_layer_design.md), [0019](0019_local_source_memory_runtime.md),
  [1345](1345_scope_organization_and_agent_integration.md), [1652](1652_memory_quality_and_lifecycle.md),
  [1718](1718_memory_capacity_contract.md)
- Related: [1417](1417_topic_memory.md), [1549](1549_artifact_family_unification.md)
- Implementation reference: [Automatic Memory implementation design (Chinese)](../../zh/design/memory-artifact-and-search-projection.md)

# Summary

This RFC introduces the `automatic-memory` family, representing each independently maintained memory as an Artifact.
Memories in a Scope have their own identities, revisions, evidence, and lifecycles. Extraction uses bounded retrieval to
coordinate creation, revision, consolidation, and conflicts. The design follows the prerequisite search contract in
RFC #1803: each currently searchable memory occupies one row in its family's search wide table. Legacy `memory`
collections and exact historical references remain available. An independent, versioned offline migration tool converts
the complete history during a maintenance window; the new Runtime does not perform online copying, dual writes, or
transfers of write ownership between the two models.

# Motivation

## Collections introduce a second identity and version layer

[RFC 0014](0014_memory_layer_design.md) stores a complete manifest in each Memory Artifact revision. Each member refers
to an immutable entry version; changing an entry also commits a collection revision.
[RFC 1345](1345_scope_organization_and_agent_integration.md) introduced Scope while retaining a single active Memory
progression line. A fact therefore has a collection ArtifactRef, entry ID, entry version, and collection membership state.

Scope already owns organization and membership. Artifact already provides identity, history, lineage, authorization,
and tags. Representing the independently maintained fact directly as an Artifact reduces Memory-specific identity,
version, and reference rules.

## A single change still stores the whole directory

The current implementation already supports entry revision, exact deduplication, historical reads, capacity limits,
optional compaction, and incremental search projection updates for changed entries. It does not copy all bodies or rewrite
all vectors on every update. It does, however, construct, sort, serialize, and persist a complete manifest on each effective
change. With average directory size N and R writes, directory history is approximately O(R × N). Two writers updating
different entries also share the same collection-head concurrency precondition.

[RFC 1718](1718_memory_capacity_contract.md) bounds a directory's size without changing the versioning unit. Independent
memories let each update add history for the affected facts without copying directory entries for unrelated facts.

## Extraction needs bounded coordination across windows

Built-in extraction currently supplies every active entry of the selected Memory head to the model. It supports add/revise
and can revise memories from earlier Source windows; it is not limited to the current window. The gaps are growing prior
memory context, the absence of a separate related-memory search after candidate generation, and incomplete action contracts
for consolidation and unresolved conflicts. An entry replacement endpoint alone does not solve them.

## Search already has a prerequisite contract

[RFC #1803](https://github.com/oceanbase/powercontext/pull/1803) defines colocated search fields, current projection
consistency, indexes, and backend acceptance. Automatic Memory depends on and implements that contract. This RFC owns the
memory domain model, extraction, and upgrade; it neither introduces another search contract nor takes ownership of the
Experience, Skill, and Topic Memory search redesign.

# Guide-level explanation

## One memory is one Artifact

Automatic Memory can be extracted from Source or written manually by an authorized caller. The word `automatic` does not
restrict how an Artifact is produced.

```text
Scope: project-a
├── automatic-memory / M1@1: Production releases require owner approval.
└── automatic-memory / M2@1: The default deployment region is East China.
```

When the user adds that production releases also require a security check, M1 becomes M1@2; M2 stays at @1. Search returns
M1@2's body and exact ArtifactRef. Reading M1@1 still returns its original text. There is no collection revision to submit
and no need to combine a collection ref, entry ID, and entry version to cite the fact.

## New input is coordinated with related memories

Processing a new Source first generates candidates, then retrieves related current memories within the same Scope and
authorized read boundary. A candidate stating that production releases need a security check should be coordinated with
M1@2 without sending every active memory in the Scope to the model:

| Finding | Result |
| --- | --- |
| An independent new fact | create: create an Artifact |
| Additional or corrective evidence about the same fact | revise: advance the target revision |
| Several facts should be consolidated | merge: propose the survivor, combined content, and deactivated objects; follow review policy |
| Already expressed with no new evidence | noop: create no revision |
| Contradictory facts with uncertain applicability or ordering | conflict: preserve the candidate, related refs, and evidence for resolution |

Similarity selects candidates; it does not establish that facts should be merged. Semantic consolidation requires review
per operation by default, and automatic consolidation is disabled by default. The limited Scope preauthorization exception
is defined below. Exhausted budgets leave recoverable pending work rather than treating the remainder as noop. One search
does not promise to eliminate every duplicate.

## Forgetting, restoration, and consolidation preserve history

Ordinary `forget` creates an inactive revision excluded from default search; `reactivate` can restore the same identity.
Restoring M1@1's content creates M1@3 with its restoration source instead of moving the head from 2 back to 1. A retired
identity cannot be ordinarily reactivated; adopting its content requires a new identity with preserved provenance.

After M3 is merged into M1, an exact old reference to M3 still returns its historical content, not M1's latest content.
Vectors serve current retrieval. Restoring historical content may require embedding it again; reading historical text does
not depend on the embedding service.

## Existing deployments upgrade during a maintenance window

```text
plan: inventory and checks
  → stop all API writers and Workers; take a backup
  → apply: convert history and build mappings and search projections
  → verify: check identity, content, state, references, authorization, and processing positions
  → cutover: persist completion and start the new release
```

The tool may run together with the public search migration in one maintenance window. Afterward, current Runtime operations
use only `automatic-memory`; legacy `memory` retains limited exact historical reads. Previously consumed Source is not
extracted again, and old Handoff and Experience content and references are not rewritten.

The old `memory` name, when used as a current-family selector, may be centrally resolved to `automatic-memory`. A legacy
collection ID, revision, ETag, or manifest write is not the new single-item contract. Requests without an explicit adapter
return `upgrade_required`; historical references are never transformed by replacing the family string.

# Reference-level explanation

## Invariants and relationships with other RFCs

1. A logical memory is identified by `(scope_id, family="automatic-memory", artifact_id)` and uses an exact ArtifactRef.
2. Effective changes to content, direct evidence, or lifecycle create revisions only for affected objects, without a Scope manifest.
3. Committed history and references are immutable. Authoritative heads and derived search projections follow RFC #1803;
   missing projections cannot be hidden with ad hoc joins to historical content.
4. Source processing, semantic coordination, and operation records are bounded. Cursor advancement requires committed
   results or durable pending work.
5. Migration belongs to an explicit offline tool. Starting the new service requires durable format, migration, and projection readiness.

| Existing RFC | Effect of this RFC |
| --- | --- |
| 0014, 0019 | Preserve immutable history, exact evidence, and consumption boundaries; replace manifests and entry-specific versions with a new family |
| 1345 | Preserve Scope ownership; remove the single collection head for the new family without creating a cursor per memory |
| 1549 | Use Family writers and standard Artifact capabilities |
| 1652 | Use exact ArtifactRefs for relations and independent revisions for state; add the limited merge preauthorization exception below |
| 1718 | Preserve deterministic capacity rejection and remediation; replace directory budgets with Scope active-memory capacity and retain old compact semantics historically |
| #1803 | Prerequisite contract for search wide tables, fields, consistency, rebuilding, and backend acceptance |

## Content, evidence, and state

The new content schema is `powercontext.automatic-memory.v1`, containing at least kind, text, state, and optional
superseded_by. Content is stored in `pc_artifacts.content`; `pc_artifact_heads` retains current pointers. This RFC does not
add search_content to either common table. Legacy entry tables serve historical reads and migration only; the new family
does not write to them.

| State | Default search | Restoration | Generic head lifecycle |
| --- | --- | --- | --- |
| active | Eligible | Ordinary revision | active |
| inactive | Excluded | Explicit reactivate | deprecated |
| retired | Excluded | Adopt content through a new identity with provenance | retired |

State belongs to the revision. Generic governance must call the Family writer rather than update only the head. Revise
inherits direct predecessor evidence and adds evidence for the change; state-only changes retain the body's evidence.
Merge records exact participating refs and valid direct evidence. Restore preserves the restored revision's evidence and
restored_from. Retaining historical provenance does not regrant present generation eligibility, and import time cannot
substitute for an unknown historical occurrence time.

An inactive merged object may contain an exact superseded_by ref within the same Scope. Self-references and replacement
cycles are prohibited. Reactivation must explicitly clear or reconfirm the relation. Operation records contain only
affected before/after refs, reasons, actors, and idempotency identities. Group compensation requires every object to still
match the operation's after refs; later changes produce a conflict rather than being overwritten. Compensation cannot
circumvent the non-reactivatable semantics of a retired identity.

## Extraction, authorization, and concurrency

Source remains consumed by the existing processing binding within a Scope. The binding selects processing scope and policy,
not a unique collection. One operation may change several memories without allocating a separate cursor to each one.

```text
Source window → candidate generation → batch deduplication → related search within Scope
              → create/revise/merge/noop/conflict → validation → atomic commit
```

Budgets cover input, candidate count, results per candidate, total related objects, context, model calls, and aggregate
tokens. Deployments without embeddings use bounded full-text retrieval supported by the public contract. Missing vectors
do not justify loading every active memory or relaxing merge authorization. Retrieval eligibility follows the public contract. This RFC's action contract and RFC 1652 govern unresolved conflicts
and their representation in Prepare.

Ordinary create/revise retains existing extraction authorization and write gates. RFC 1652 L2 semantic maintenance still
requires per-operation review by default. This RFC explicitly amends one boundary: a Scope administrator may preauthorize
consolidation of the same subject under the same applicability conditions when content is compatible and evidence is fully
preserved, subject to participant and operation budgets. Automatic merge is disabled by default. Each operation records
the authorizer, policy version, and justification for applicability. Unresolved conflicts, HOLD, mandatory review policies,
operations outside the authorization, and irreversible retirement still require individual review. Explicit merge commands
also obey permissions and mandatory review. Model scores do not grant authority.

Every revise/merge target checks an expected revision, and multiple targets commit or fail together. Idempotency keys are
independent of similarity, so retries do not create another memory. Automatic coordination must also detect concurrent
creation after retrieval: an initial implementation uses a Scope Memory write generation, retrying coordination if it
changes between related retrieval and commit. This is O(1) metadata, not a collection revision. Independent explicit
single-item revisions check only their own CAS while still advancing the generation.

Evidence, authorization, and governance changes affecting candidate or generation eligibility must also invalidate the
coordination precondition or be checked through their corresponding generations before commit. Models and embeddings run
outside the transaction. Commit rechecks eligibility, lease, CAS, generation, and idempotency, then atomically writes
revisions, lineage, heads, capacity, projections, operation records, and processing state. Input advances only when final
results or recoverable pending/conflict records are persisted together with the cursor.

## Capacity and integration with public search

The proposed initial Scope active Automatic Memory limit is 5,000, with explicit configuration. Create, reactivate,
deactivate, and merge update the count atomically using the net change of the whole operation. An over-limit Scope may
still perform revisions and remediation that do not increase the exceeded dimension. Incremental writes do not scan the
whole Scope to calculate capacity. Manifest item/byte limits no longer apply. Historical retention remains separate;
physical deletion is not an implicit way to satisfy the active limit.

Automatic Memory owns a current search wide table with one row per searchable Artifact. It carries the current ref, text,
kind, state-related data, and all matching, filtering, sorting, and response fields required by the public contract. When
vector retrieval is enabled, the same search unit carries the matching embedding. FTS-only deployments return complete
results. Visibility, tags, and lifecycle updates and rebuilds follow #1803; search must not join authoritative tables to
complete predicates or content. Physical backend layout, snapshots, index eligibility, and measurement requirements belong
to #1803, with no separate exceptions here. Old Memory projections are not reused as the new family's business write tables.

## APIs and consumers

New list/get/replace/history operations use individual Artifact identity; search returns content and exact refs; flush
returns processing state, operation identity, and affected refs rather than a collection revision. Prepare removes the
assumption that all memories in one Scope share a collection ref. New Handoff, Experience, Dream, Tags, and ACL records use
the new Artifact identity. HTTP, SDK, MCP, and Prompt action schemas change together; OpenAPI remains the HTTP source of truth.

Old-name compatibility is centralized in the shared Artifact API resolution entry point:

| Old request | Handling after cutover |
| --- | --- |
| `memory` as the family selector for current list/search | Resolve to `automatic-memory`, using the new response and pagination cursor |
| `memory` carrying an explicitly supported new single-item write protocol | Resolve to `automatic-memory`; authorize and execute the new protocol |
| Exact old ArtifactRef, MemoryCitation, or historical revision | Preserve the old family and historical identity without rewriting the ref |
| Old collection ID/latest, collection ETag/CAS, manifest, or entry write protocol | Use only a specifically documented adapter; otherwise return `upgrade_required` |

Authorization and execution use the same resolved identity. Responses expose the real `automatic-memory` family. Old and
new pagination cursors are not interchangeable; name compatibility does not imply identical old responses. Offline cutover
does not introduce per-Scope old/new business routing or two writable authorities. A plain old collection ArtifactRef still
denotes that collection snapshot and cannot arbitrarily designate one of its new memories.

## Offline upgrade and exact history

The migration tool is released separately from the service and declares supported source and target formats. As long as
upgrades from an old version remain supported, the tool and upgrade path must remain available; running migration once does
not remove that product obligation. Normal Runtime startup neither converts legacy databases nor implements dual writes,
online catch-up, or a migration cutover state machine.

Before apply, all old API writers and Workers must actually stop and a consistent backup must be taken. Old processes do
not understand the new marker, so the marker alone cannot fence them. The tool runs plan/apply/verify/cutover stages with
bounded checkpoints, configuration digests, and target digests; replay verifies completed batches rather than recreating
them. DDL postconditions are checked separately, without assuming one transaction can roll back all OceanBase DDL. The new
service refuses normal business startup for unmigrated, partial, configuration-mismatched, or unready data. Empty database
initialization also records readiness explicitly. During migration, only controlled tools and explicitly supported exact
historical reads are allowed.

Identity mapping includes the old container; entry IDs need not be unique across a Scope:

```text
(scope_id, legacy_collection_id, legacy_entry_id) → automatic-memory artifact_id
```

Mappings are deterministic and recoverable; collisions block commit. The tool inventories every collection and replays
every collection revision. A memory gains its own revision only when its body, direct evidence, state, or membership changes:

| Old history | New history |
| --- | --- |
| C@1: E1_v1 active | M1@1 active |
| C@2: only E2 changes | M1 stays at @1 |
| C@3: E1_v2 active | M1@2 active |
| C@4: E1_v2 inactive | M1@3 inactive |
| C@5: compact removes E1 | M1@4 retired, with complete history preserved |

New revisions are not equal to old entry versions. Migrating only the final manifest or turning compacted entries into
ordinary inactive entries is prohibited. Unexplained disappearance, missing versions, hash mismatches, and historical gaps
block cutover. Orphan records that may still be referenced are retained and reported. Migration validates old evidence
without calling a model or reinterpreting historical provenance using current eligibility. imported_at is separate from
historical occurrence time.

Old MemoryCitation resolution first validates membership, entry version, and hash in the named old manifest, then maps
through change intervals:

```text
(Scope, old collection, entry, [start collection revision, end collection revision)) → exact new ArtifactRef
```

Intervals cover actual membership only. Absence intervals after compact cannot accept an old entry citation; a migration
provenance record separately identifies the retirement event. Mappings do not expand an entry × all collection revisions
matrix. Immutable old collections, entries, Handoff, and Experience bodies and hashes remain unchanged. Aliases do not
bypass current access controls.

Entry tags and authorization subjects, permissions, expiration, and revocation semantics migrate one-to-one. Collection
tags retain their original scope rather than becoming per-memory grants. Processing cursors, high-water marks, accepted
work, and counters are preserved; old leases are invalidated and consumed Source is not extracted again. Incompatible
custom Prompts must be upgraded or block migration. Combined collections exceeding a Scope limit require explicit
configuration reported by plan, never data loss. Current vectors matching the exact input and profile may be reused;
generating all historical vectors is not required. These requirements can be coordinated with the public search migration
within one maintenance window.

Before cutover, verify that the frozen snapshot is unchanged and mappings and projections are ready, then persist
completion. Before the new version accepts writes, restoring the backup permits rollback. After new writes, old collections
are no longer synchronized; forward repair is the default. Downgrading requires a separately designed offline reverse
migration. Restoring memory content is distinct from downgrading a deployment.

# Drawbacks

- Identity, APIs, Prompts, consumers, and authorization targets change. Upgrade needs downtime and historical conversion;
  the maintenance window and disk budget must be rehearsed with representative data.
- Retaining old history, new Artifact history, and aliases increases storage. Stopping new full manifests does not
  immediately reclaim old disk usage.
- Bounded retrieval can miss related memories, and semantic consolidation can lose qualifications. Evidence, review,
  conflict records, and later maintenance are needed; a globally duplicate-free collection is not promised.
- Independent CAS reduces conflicts between unrelated facts, but Scope generations may cause repeated coordination
  retries in busy Scopes and require measurement and limits.
- Evidence, state revisions, projections, and compatibility concentrate responsibilities in the Family writer. Missing
  one write entry point can break consistency; acceptance must follow the public contract.
- Exact old references require limited compatibility code, and independent tools still need a declared support lifecycle.
  Offline upgrade does not eliminate the obligation to maintain upgrade assets.

# Rationale and alternatives

**Promote entries directly to Artifacts.** Scope owns membership, and Artifact owns identity and versions, matching other
families. Actual changes determine historical growth.

**Keep the `memory` family and change its content schema.** This avoids another family name but leaves collection and
single-item semantics under one family, requiring format distinctions in listing, capabilities, and historical reads.
A new family makes domain identity explicit. The old name is compatible only at controlled API entry points; naming is
not an online migration mechanism.

**Incremental manifests or multiple smaller collections.** The former reduces directory copying; the latter bounds
container size. Both retain collection CAS, entry-specific references, container routing, and cross-container matching.
They remain alternatives when collection snapshots are a primary capability. This RFC chooses independent facts as the
maintenance unit.

**Migrate current content only and keep all history on a separate old path.** This can shorten the first migration, but
new identities have history only from import, requiring users to cross two histories. Full replay provides continuous body
and state history for each memory while retaining old collection snapshots, at the cost of more offline migration work.

**Online copying, dual writes, and per-Scope cutover.** These reduce downtime but require ongoing support for catch-up,
mixed versions, recovery, and write coordination during migration. A distributed binary product supporting direct upgrades
from historical versions must continue to carry or invoke that capability in later releases; completion by one deployment
does not permit removal. Since a maintenance window is acceptable, this RFC chooses an independent offline tool.

**Do nothing.** Capacity and compaction can continue bounding individual collections, and related retrieval can be added
separately. Two version layers, full directory history, and special reference handling would remain.

# Prior art

This RFC reuses existing PowerContext responsibilities without claiming they already implement this proposal:

- [RFC 1345](1345_scope_organization_and_agent_integration.md): Scope ownership boundaries.
- [RFC 1417](1417_topic_memory.md): candidate generation, related Topic selection, and atomic publication as coordination precedents.
- [RFC 1549](1549_artifact_family_unification.md): Family capabilities and common Artifact read/write boundaries.
- [RFC 1652](1652_memory_quality_and_lifecycle.md): evidence, validity, similarity, conflicts, review, and recoverable deactivation.
- [RFC 1718](1718_memory_capacity_contract.md): full-manifest cost, capacity rejection, and historical protection during compact.
- [RFC #1803](https://github.com/oceanbase/powercontext/pull/1803): the prerequisite current search wide-table contract for each family.

Detailed migration steps, storage costs, and acceptance criteria are in the
[implementation design (Chinese)](../../zh/design/memory-artifact-and-search-projection.md).

# Unresolved questions

The following must be settled before merging this RFC:

1. How the new HTTP/SDK protocol is versioned, the explicit list of write adapters allowed under the old `memory` name,
   and the supported version ranges for exact old reads and migration tools.
2. Scope capacity configuration hierarchy, configuration for combined old containers, and whether the proposed 5,000
   active limit fits the target deployments.
3. Which extension of existing Candidate facilities represents pending/conflict/review actions, and how downstream
   effects that cannot be compensated are represented.
4. Initial coordination and retry budgets and concurrency acceptance workloads. A finer invalidation mechanism may
   replace Scope generations, but not their correctness responsibility.

Supported backends, indexes, single-table filtering, and read consistency belong to prerequisite RFC #1803. Physical
deletion, cross-Scope automatic consolidation, online upgrade, and implicit whole-Scope snapshot rollback are outside this
delivery. Batch sizes, command names, and delivery schedules are not prescribed by acceptance of the RFC.

# Future possibilities

- Bounded background maintenance can find near-duplicates and conflicts missed by immediate extraction, preserving the
  same evidence and review boundaries.
- Historical vector caching keyed by exact input and profile can reduce restoration costs while historical text remains
  independently readable.
- Historical retention, physical deletion, and explicit Scope export can be specified separately without bringing back
  full manifests on every individual write.
- A commercial edition may provide supported online upgrades with explicit service guarantees. To let later binaries
  remove historical online migration code, define a **barrier release B**: older releases must first upgrade to B, which
  contains copying, catch-up, cutover, and recovery. Only after migration finishes and durable completion is recorded may
  the installation upgrade further. Later releases retain lightweight format and completion gates, rejecting skipped B
  or incomplete migration. B's releases, tools, and instructions remain available with necessary maintenance for the
  promised support period. Merely installing B does not cross the barrier, and completion by one installation does not
  end the product's upgrade obligation to other old installations. This commercial capability is outside the current RFC's
  implementation requirements.
