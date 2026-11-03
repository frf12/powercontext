# Fixed 66-task code graph experiment

The 2026-11-03 experiment did not show a benefit from automatic code graph injection
under this configuration. OFF solved 45/66 tasks (68.18%); ON solved 44/66 (66.67%).
The one-task difference is too small to establish a stable quality regression.
Recorded token use and mean agent time increased with injection enabled.

## Version and comparison

- SWE-bench Pro: 66 fixed tasks, six from each of 11 repositories, seed 2425;
  selection did not depend on historical solve outcomes.
- Codex 0.145.0, `gpt-5.6-sol`, medium reasoning, via the same subscription proxy,
  Gateway and Core in both arms. OFF still uses Gateway/Core.
- Memory recall, injection and sedimentation, plugins, Codex memories, steering,
  and web search disabled. Each arm has an isolated workspace, Scope, database,
  code cache and Codex home. Only code injection differs within a pair.
- Integrated revision: `a0d060a1cd7731b9a8d3ed476b060ac3cd864f4f`, plus the recorded
  benchmark patches. PR #2425 was tested at
  `02bd987c8ff3ed9958e7e49592935ae9d6513912`; PR #2429 at
  `84641150e5706d6b6556041fe38ebb3bb0b491fc`.
- PR #2425's later commit `2d324ac48bda75812061fdcac7a6a728fa2a278a` was **not**
  benchmarked. These results describe the tested snapshot, not that later revision.
- Model/task concurrency was mostly eight, briefly twelve; grading concurrency was
  two. The machine was shared, so timings include load and queue effects.

## Results

| Metric | Code OFF | Code ON | ON change |
| --- | ---: | ---: | ---: |
| Solved | 45/66 | 44/66 | -1 task |
| Solve rate | 68.18% | 66.67% | -1.52 percentage points |
| Recorded input tokens | 91,615,949 | 104,067,666 | +13.59% |
| Recorded cached input tokens | 88,775,424 | 99,161,856 | +11.70% |
| Recorded non-cached input tokens | 2,840,525 | 4,905,810 | +72.71% |
| Recorded output tokens | 647,127 | 676,097 | +4.48% |
| Mean agent time | 337.27 s | 360.77 s | +6.97% |
| Mean total attempt time, including grader retries | 481.52 s | 546.14 s | +13.42% |
| Mean indexing time, including waiting | 0 | 32.19 s | — |
| Mean index build time alone | 0 | 24.58 s | — |

43 tasks passed in both arms, one passed only with ON, two only with OFF, and 20
passed in neither arm. The [JSON results](main66.json) identify every selected
instance, outcome and batch, and include per-repository and cost breakdowns.

Token values are **known subtotals**, not complete usage or monetary cost. OFF has
nine missing usage observations and ON eight; unknown is never treated as zero.
Reported cached-input/input fractions are 96.90% and 95.29%, respectively, not
request-level cache hit rates. Reasoning tokens are included in output tokens and
are not added again. The proxy may fill omitted usage detail fields with zero.

Agent time includes model calls, tools and reconnects. Total attempt time also
includes setup, indexing and grading, including the recorded grading retry. It
does not include manual diagnosis or idle gaps between executions. Summed task
durations are not concurrent batch wall-clock time.

## Technical recovery and costs

The final comparison uses 64 original pairs and two separately repaired pairs.
Repairs were selected for technical invalidity, never for solve score. Each named
batch uses the first settled attempt in attempt-number order; old invalid results
and all costs are retained.

| Source index | Technical failure | Recovery | Final outcome |
| --- | --- | --- | --- |
| 438 | Retrieval query had 9,005 characters; Core allows 8,192 and returned 422 | Cap only the retrieval query at 8,192 Unicode characters; preserve the full model input; rerun both arms with an independent Gateway binary | Both unresolved; code treatment valid |
| 262 | Code query exceeded its five-second budget in a 16,542-file repository | A clean-base probe timed out at 5.016 s; a 30-second budget returned eight items in 5.666 s. Rerun both arms with that isolated query budget | Both resolved; code treatment valid |
| 728 | Both initial official graders failed to download Go dependencies | Repair grader proxy access and regrade the unchanged predictions only | Both unresolved due to candidate compile/test failures; no new model calls |

The current branch includes the 8,192-character query cap. It was absent from the
original main batch and from the case262 repair. The default runner still uses
the original query budget. The [case262 budget patch](../recovery/case262-query-budget-30.patch)
contains its only behavioral runner change. The
[original-query patch](../recovery/original-without-query-cap.patch) restores the
original Gateway query behavior. Apply patches only in an isolated checkout with
new output directories; do not overwrite prior results. The relocated case262
runner also bound its `SOURCE` to the original source directory; that machine path
is deliberately omitted here. Therefore the 66-pair result is not a uniform run
of the current branch with one configuration.

The three model batches retain 136 arm attempts: 132 selected and four superseded
original attempts. All 136 together have recorded input of 202,615,795 tokens,
cached input 194,671,360, non-cached input 7,944,435, and output 1,374,490, with 19
missing usage observations. The superseded four attempts contribute 6,932,180
recorded input and 51,266 output tokens, already included in those totals.

Twelve transport error events recovered within their existing model turns. The
case728 grader recovery added two grading attempts and 215.85 seconds, bringing
the three batches to 138 grading attempts; predictions and model evidence hashes
were unchanged. Grader infrastructure failure was not counted as an ordinary
unsolved result.

The pilot is separate from the 66-task result: the abandoned initial batch retained
19 attempt directories with no valid pairs and unknown usage; the connectivity
pilot retained 55 attempts and 12 valid pairs, with each arm solving 6/12. The
latter recorded 50,250,255 input and 330,165 output tokens, with 30 missing usage
observations. Across pilot and main runs there are 210 attempt records, not 210
model requests; recorded totals are 252,866,050 input and 1,704,655 output tokens,
with 68 missing usage observations. Setup/build/diagnostic costs were not fully
metered, and subscription spending is not estimated in dollars.

## Validation and interpretation limits

All 66 selected ON arms had actual code evidence admitted by Core. All 66 OFF arms
had no injected declarations. All 234 observed prepares had empty historical
sections, and memory tables in all 132 selected Core databases were empty. Gold
patches were available only to the official grader after the agent stopped.

This is a single run of a small fixed sample with two technical repair batches,
shared service caches and a temporary, default-off encrypted-content allowance.
It is not production security acceptance, a full-dataset result, or a directly
comparable score against a historical baseline using a different provider route.
The result does not establish a benefit for this tested configuration; an
independent reproduction should verify both the implementation and configuration.

The public JSON contains aggregate usage, timings, cohort IDs and selected results.
Raw task prompts, generated solutions, logs, credentials, internal account IDs and
machine-specific run paths are not included.
