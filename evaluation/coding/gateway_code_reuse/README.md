# Gateway code injection experiment

The fixed 66-task experiment is complete. See [results and limitations](results/main66.md),
the [machine-readable results](results/main66.json), and the
[fixed cohort](results/main-cohort.json). This branch includes the experimental
patches used to make the comparison run; it is not a production-ready proposal.

This runner measures paired SWE-bench Pro outcomes with Gateway code evidence off
and on. Both arms use the same integrated build, Codex 0.145.0,
`gpt-5.6-sol`, medium reasoning, and subscription proxy. It does not reuse historical
Luna baselines. The integrated build includes an explicitly experimental encrypted
content allowance; this experiment is not production security acceptance.

Both arms disable historical context (`PC_GATEWAY_INJECTION_MEMORY=off`, encoded as
`assembly.sections=[]`), Gateway conversation capture, steering, Codex plugins,
Codex memory generation/use, and web search. Each arm has its own database, Scope,
code cache, workspace, Gateway process, and Codex home. The agent receives only the
public task prompt. Gold patches and test patches are passed only to the official
grader, after the agent container has stopped. Future Git commits are removed from
the agent workspace using the existing SWE-bench Pro harness safeguard.

Run under Linux with the integrated PowerContext virtualenv and Docker access.
The runner uses a disposable task container on the precreated internal Docker
network `powercontext-codegraph-bench`. Gateway binds its bridge address
`172.31.240.1`; Core and its evidence hop remain on loopback. Task containers have
no external route. Override these values with `--docker-network` and
`--gateway-host`. The official grader retains its existing separate network. Only the task workspace, fresh Codex home, and Codex
binary are mounted. The subscription refresh credentials are never mounted.
The original evaluation deployment and its workers are not used or modified.

```sh
BENCH_SOURCE=/absolute/path/to/this/checkout
BENCH_RUN=/absolute/path/to/private/benchmark-directory
BENCH_HARNESS=/absolute/path/to/swebench-pro
BENCH_COHORT=$(python3 -c 'import json; print(",".join(map(str, json.load(open("evaluation/coding/gateway_code_reuse/results/main-cohort.json"))["indices"])))')
sudo -n "$BENCH_SOURCE/.venv/bin/python" \
  "$BENCH_SOURCE/evaluation/coding/gateway_code_reuse/runner.py" \
  --output "$BENCH_RUN/runs/main66" \
  --dataset "$BENCH_HARNESS/helper_code/sweap_eval_full_v2.jsonl" \
  --core-binary "$BENCH_SOURCE/.venv/bin/powercontext" \
  --core-env "$BENCH_RUN/core.env" \
  --gateway-binary "$BENCH_SOURCE/gateway/target/release/pc-gateway" \
  --gateway-env "$BENCH_RUN/gateway.env" \
  --codex-binary /absolute/path/to/codex \
  --harness-root "$BENCH_HARNESS" \
  --harness-python /absolute/path/to/harness-venv/bin/python \
  --indices "$BENCH_COHORT" \
  --profile codex-benchmark \
  --concurrency 8 --max-concurrency 16
```

Run the example from the checkout root and replace the absolute paths. The runner
retains the measured deployment's machine-specific defaults; override those flags,
`--registry`, `--docker-network`, and `--gateway-host` for your environment. The command
above selects the fixed cohort for a **new run**; it does not recreate the historical
mixed repair batches by itself. See the results document for the exact batch differences.

Use `--plan-only` to validate the pinned dataset and write provenance without
starting services or contacting a model. Source archives must contain
`SOURCE_REVISION` and `BENCHMARK_PATCH.diff`; Git checkouts use their current commit
and diff. `--indices` are zero-based original JSONL source positions; the default
pilot includes 12 tasks across 11 repositories. An existing completed arm is reused
when continuing the same output directory. Failed model/setup attempts have a
bounded retry budget; an official grading failure never reruns the model.

The Core environment template must configure enforcing protection and the
benchmark-only Codex encrypted-content flag, with the selected upstream profile.
The runner overrides only the per-arm home, SQLite database, and code repository
configuration. The Gateway environment template supplies the Responses upstream
URL, upstream key, and optional Core token. Keep both files private.

For flow control, create `PAUSE` beside `core.env` to stop new tasks/arms and model
invocations; already active turns continue. Pass `--pause-file` to choose another
location. A valid, nonempty `quota.json` beside the pause file must have been updated
within 90 seconds before any new task, arm, or model invocation can start. Missing
or stale guard evidence pauses admission. Override its path with `--quota-file`.
Write an integer from 1 to `--max-concurrency` to the output directory's
`CONCURRENCY` file to adjust active task admission. Codex request and stream retry budgets are three to tolerate transient provider
failures; every observed before-release response retains its usage evidence.

Every arm retains its model/reasoning settings, image base, code-index outcome,
Codex event stream, usage, elapsed time, generated patch, and official test results.
`pair.json` and `results.json` distinguish infrastructure errors from scored
solutions. Raw prompts, events, and service logs stay in the private run directory.
An unsuccessful official test is a benchmark result, not an infrastructure failure.

The runner observes actual Gateway-to-Core traffic through an identical local
transparent hop in both arms. It retains only prepare status/byte counts and check
decisions, correlating reuse bindings by in-memory hashes. Both arms require actual allowed sends and releases, plus empty historical
sections on every prepare. A code-on arm additionally needs an
actual allowed send declaring a successful code-only prepare; otherwise its result
is marked `invalid_code_treatment` and excluded from valid comparisons. The hop
never persists request bodies, response bodies, credentials, or reuse bindings.

Provider token measurements are retained from each actual before-release payload,
including refused releases. Missing usage stays unknown; no zero is invented.
Resuming an official-grader infrastructure failure reuses the saved patch and
retries only grading, without rerunning Codex.

If an arm was interrupted before writing its outcome, resumption preserves its
logs and usage evidence, marks that attempt interrupted, and starts the next
bounded attempt. If a generated patch already exists, only grading resumes.
