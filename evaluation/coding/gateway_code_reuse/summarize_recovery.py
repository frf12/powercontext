#!/usr/bin/env python3
"""Compose the fixed 66-case cohort with its two predetermined technical repairs.

Only metadata, outcomes, and content-free Core evidence are read. Raw runs are
never changed. Usage/timing rules come from the ordinary run summarizer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import summarize as base

REPAIRS = {438: "repair_438", 262: "repair_262"}
CONFIGURATIONS = {
    "main": "Original main66 configuration",
    "repair_438": "Technical repair: query limit 8192",
    "repair_262": "Technical repair: query budget 30",
}
PARTITIONS = (
    "selected_pair_attempts",
    "original_replaced_cases",
    "canonical_retry_or_incomplete_attempts",
    "other_discarded_attempts",
)


def load_run(root: Path, role: str) -> tuple[dict[str, Any], dict[int, dict[str, list[Any]]]]:
    manifest = base.read_json(root / "manifest.json")
    manifest = manifest if isinstance(manifest, dict) else {}
    cases = {}
    for directory in sorted(root.glob("case-*")):
        suffix = directory.name.removeprefix("case-")
        if not directory.is_dir() or not suffix.isdigit():
            continue
        index = int(suffix)
        if index in cases:
            raise ValueError("duplicate case index in a run")  # noqa: TRY003 - CLI validation
        arms = {}
        for arm in ("off", "on"):
            paths = [
                path
                for path in directory.glob(f"{arm}-*")
                if path.is_dir() and path.name.removeprefix(f"{arm}-").isdigit()
            ]
            paths.sort(key=lambda path: int(path.name.split("-")[-1]))
            arms[arm] = [
                {**base.read_attempt(path, arm), "run": role, "case_index": index, "arm": arm, "path": str(path)}
                for path in paths
            ]
        cases[index] = arms
    return manifest, cases


def first_settled(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    # Same deterministic rule as summarize.py; never look at resolved to select.
    return next(
        (attempt for attempt in attempts if attempt["status"] in {"completed", "invalid_code_treatment"}),
        attempts[-1] if attempts else {},
    )


def provenance(attempt: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in attempt.items() if key != "measurements"}


def compose(main: Path, repair_438: Path, repair_262: Path) -> dict[str, Any]:  # noqa: C901 - fixed cohort and disjoint ledger
    roots = {"main": main.resolve(), "repair_438": repair_438.resolve(), "repair_262": repair_262.resolve()}
    if len(set(roots.values())) != 3:
        raise ValueError("the three run directories must be distinct")  # noqa: TRY003 - CLI validation
    runs = {role: load_run(root, role) for role, root in roots.items()}
    manifest = runs["main"][0]
    indices = manifest.get("indices")
    if (
        not isinstance(indices, list)
        or len(indices) != 66
        or any(type(index) is not int or index < 0 for index in indices)
        or len(set(indices)) != 66
        or not set(REPAIRS).issubset(indices)
    ):
        raise ValueError("main manifest must define 66 unique indices including 262 and 438")  # noqa: TRY003 - CLI validation
    issues = []
    recovery_provenance = {}
    recovery_provenance_hashes = {}
    for index, role in REPAIRS.items():
        if runs[role][0].get("indices") != [index]:
            issues.append(f"{role}: repair manifest must contain exactly index {index}")
        provenance_path = roots[role] / "recovery-provenance.json"
        evidence = base.read_json(provenance_path)
        if not isinstance(evidence, dict) or not evidence:
            issues.append(f"{role}: recovery-provenance.json is missing or invalid")
        else:
            recovery_provenance[role] = evidence
            recovery_provenance_hashes[role] = hashlib.sha256(provenance_path.read_bytes()).hexdigest()
    for field in ("model", "reasoning_effort", "dataset_sha256"):
        if any(runs[role][0].get(field) != manifest.get(field) for role in REPAIRS.values()):
            issues.append(f"run manifests disagree on {field}")
    groups = {
        name: {"off": [], "on": []}
        for name in (*PARTITIONS, "canonical_pairs_with_all_attempts", "all_recorded_attempts")
    }
    counts: Counter[str] = Counter(
        dict.fromkeys(
            (
                "comparable",
                "both_resolved",
                "on_only",
                "off_only",
                "neither",
                "invalid",
                "infrastructure_error",
                "incomplete",
            ),
            0,
        )
    )
    reports = []
    selected_ids: set[str] = set()
    canonical_keys = {(REPAIRS.get(index, "main"), index) for index in indices}
    for index in indices:
        role = REPAIRS.get(index, "main")
        arms = runs[role][1].get(index, {"off": [], "on": []})
        finals = {arm: first_settled(attempts) for arm, attempts in arms.items()}
        comparable = all(base.valid(final) for final in finals.values())
        result = None
        if comparable:
            category = "comparable"
            off, on = finals["off"]["resolved"], finals["on"]["resolved"]
            result = "both_resolved" if off and on else "on_only" if on else "off_only" if off else "neither"
            counts[result] += 1
            for arm, attempts in arms.items():
                selected_ids.add(finals[arm]["path"])
                groups["canonical_pairs_with_all_attempts"][arm].extend(attempts)
        elif any(
            final.get("status") == "invalid_code_treatment"
            or (final.get("status") == "completed" and not final.get("code_treatment_valid"))
            for final in finals.values()
        ):
            category = "invalid"
        elif any(
            final.get("status") in {"infrastructure_error", "interrupted", "runner_error"} for final in finals.values()
        ):
            category = "infrastructure_error"
        else:
            category = "incomplete"
        counts[category] += 1
        if index in REPAIRS and not comparable:
            issues.append(
                f"case-{index:04d}: required repair is missing, incomplete, or invalid; original result is not substituted"
            )
        reports.append({
            "case_index": index,
            "case": f"case-{index:04d}",
            "origin": role,
            "technical_configuration": CONFIGURATIONS[role],
            "category": category,
            "result": result,
            "arms": {
                arm: {
                    "selected": provenance(finals[arm]),
                    "all_attempt_cost": base.aggregate(attempts, 1),
                    "attempts": [provenance(attempt) for attempt in attempts],
                }
                for arm, attempts in arms.items()
            },
        })
    for role, (_, cases) in runs.items():
        for index, arms in cases.items():
            for arm, attempts in arms.items():
                for attempt in attempts:
                    partition = (
                        "selected_pair_attempts"
                        if attempt["path"] in selected_ids
                        else "original_replaced_cases"
                        if role == "main" and index in REPAIRS
                        else "canonical_retry_or_incomplete_attempts"
                        if (role, index) in canonical_keys
                        else "other_discarded_attempts"
                    )
                    groups[partition][arm].append(attempt)
                    groups["all_recorded_attempts"][arm].append(attempt)
    n = counts["comparable"]
    scores = {"off": counts["both_resolved"] + counts["off_only"], "on": counts["both_resolved"] + counts["on_only"]}
    complete = n == 66 and not issues
    return {
        "status": "complete" if complete else "incomplete",
        "issues": issues,
        "cohort": {
            "case_count": 66,
            "repository_count": 11,
            "seed": 2425,
            "indices": indices,
            "design_source": "fixed benchmark cohort; indices verified against main manifest",
        },
        "selection_rule": "Predetermined source: main for 64 cases, repair_438 and repair_262 for their named cases; first settled completed/invalid attempt in numerical order, regardless of score. No fallback to originals.",
        "configuration_note": "Mixed technical configuration: main64 plus query-limit-8192 repair438 and query-budget-30 repair262. Repair settings are caller-declared batch provenance, not a single uniform-configuration rerun.",
        "counts": dict(counts),
        "pass_rates": {
            arm: {
                "resolved": score,
                "comparable_denominator": n,
                "comparable_rate": score / n if n else None,
                "fixed66_rate": score / 66 if complete else None,
            }
            for arm, score in scores.items()
        },
        "runs": {
            role: {
                "path": str(root),
                "technical_configuration": CONFIGURATIONS[role],
                "recovery_provenance": recovery_provenance.get(role),
                "recovery_provenance_sha256": recovery_provenance_hashes.get(role),
                "manifest_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
                if (root / "manifest.json").is_file()
                else None,
                "metadata": {
                    key: runs[role][0].get(key)
                    for key in (
                        "source_commit",
                        "uncommitted_diff_sha256",
                        "model",
                        "reasoning_effort",
                        "dataset_sha256",
                        "indices",
                    )
                },
            }
            for role, root in roots.items()
        },
        "cost_partition": list(PARTITIONS),
        "scopes": {
            name: {
                arm: base.aggregate(
                    attempts, n if name in {"selected_pair_attempts", "canonical_pairs_with_all_attempts"} else 0
                )
                for arm, attempts in arms.items()
            }
            for name, arms in groups.items()
        },
        "discarded_attempts": [
            provenance(attempt) for name in PARTITIONS[1:] for attempts in groups[name].values() for attempt in attempts
        ],
        "cases": reports,
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Fixed 66-case benchmark with technical repairs",
        "",
        f"Report status: **{report['status']}**. Cohort: 66 cases, 11 repositories, seed 2425.",
        "",
        report["configuration_note"],
        "",
        report["selection_rule"],
        "",
    ]
    if report["issues"]:
        lines.extend(["Incomplete evidence:", "", *[f"- {issue}" for issue in report["issues"]], ""])
    counts = report["counts"]
    lines.extend([
        "| Comparable | Both resolved | On only | Off only | Neither | Invalid | Infrastructure error | Incomplete |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        "| "
        + " | ".join(
            str(counts[key])
            for key in (
                "comparable",
                "both_resolved",
                "on_only",
                "off_only",
                "neither",
                "invalid",
                "infrastructure_error",
                "incomplete",
            )
        )
        + " |",
        "",
        "| Arm | Resolved / comparable | Comparable pass rate | Fixed 66 pass rate |",
        "| --- | --- | --- | --- |",
    ])
    for arm, value in report["pass_rates"].items():
        rates = [
            f"{value[key]:.2%}" if value[key] is not None else "incomplete"
            for key in ("comparable_rate", "fixed66_rate")
        ]
        lines.append(f"| {arm} | {value['resolved']} / {value['comparable_denominator']} | {' | '.join(rates)} |")
    lines.extend([
        "",
        "Provider usage includes rejected/interrupted responses and retries. Missing measurements remain unknown. Reasoning is already included in output, and net input is computed only when input/cache are both known. No subscription dollar estimate is made.",
        "",
        "The four cost-partition scopes are disjoint and together equal all recorded attempts across the three runs. Canonical pairs with all attempts is an additional overlapping comparison view; do not add it again. Original replaced cases remain in the expense ledger even if their scores were better.",
        "",
    ])
    for scope in (*PARTITIONS, "canonical_pairs_with_all_attempts", "all_recorded_attempts"):
        lines.extend([
            f"## {scope.replace('_', ' ')}",
            "",
            "| Arm | Attempts | Input | Cache read | Net input | Output | Reasoning (within output) |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ])
        for arm, data in report["scopes"][scope].items():
            values = [base.format_metric(data["metrics"][key]) for key in (*base.TOKEN_FIELDS, "net_input_tokens")]
            # Keep the displayed columns in the same order as the header.
            values = [values[0], values[1], values[4], values[2], values[3]]
            lines.append(f"| {arm} | {data['attempt_count']} | {' | '.join(values)} |")
        lines.extend([
            "",
            "| Arm | Agent seconds | Index incl. wait | Pure index build | Grade seconds | Total seconds |",
            "| --- | --- | --- | --- | --- | --- |",
        ])
        for arm, data in report["scopes"][scope].items():
            values = [
                base.format_metric(data["metrics"][key])
                for key in ("agent_seconds", "index_seconds", "index_build_seconds", "grade_seconds", "total_seconds")
            ]
            lines.append(f"| {arm} | {' | '.join(values)} |")
        lines.append("")
    lines.extend([
        "Index time includes queue waiting; pure build time is separate. Complete grading_history records include all grading attempts; absent or incomplete history retains unknown earlier grading time. Summed attempt durations are not concurrent run wall-clock time. Comparable per-case means are retained in summary.json; missing values never become zero.",
        "",
        "## Canonical case provenance",
        "",
        "| Case | Source | Category | Result | Off selected | On selected |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    for case in report["cases"]:
        selected = [case["arms"][arm]["selected"].get("attempt", "missing") for arm in ("off", "on")]
        lines.append(
            f"| {case['case']} | {case['origin']} | {case['category']} | {case['result'] or '—'} | {' | '.join(selected)} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("main_run", type=Path)
    parser.add_argument("--repair-438", type=Path, required=True)
    parser.add_argument("--repair-262", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    roots = [path.resolve() for path in (args.main_run, args.repair_438, args.repair_262)]
    if output.exists() or any(output == root or root in output.parents for root in roots):
        parser.error("output must be a new directory outside all input runs")
    try:
        report = compose(*roots)
    except ValueError as error:
        parser.error(str(error))
    output.mkdir(parents=True, exist_ok=False)
    base.atomic_write(output / "summary.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    base.atomic_write(output / "summary.md", markdown(report))
    print(json.dumps({"status": report["status"], "output": str(output), "counts": report["counts"]}))
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
