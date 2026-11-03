#!/usr/bin/env python3
"""Summarize paired Gateway runs without reading prompts, patches, or model bodies."""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

TOKEN_FIELDS = {
    "input_tokens": "input_tokens",
    "cache_read_tokens": "cached_tokens",
    "output_tokens": "output_tokens",
    "reasoning_tokens": "reasoning_tokens",
}
TIME_FIELDS = {
    "agent_seconds": "agent_seconds",
    "index_seconds": "index_seconds",
    "grade_seconds": "evaluation_seconds",
    "total_seconds": "elapsed_seconds",
}
METRICS = (*TOKEN_FIELDS, "net_input_tokens", *TIME_FIELDS, "index_build_seconds")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def number(value: Any, *, integer: bool = False) -> int | float | None:
    if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return value


def read_attempt(directory: Path, arm: str) -> dict[str, Any]:
    outcome = read_json(directory / "outcome.json")
    outcome = outcome if isinstance(outcome, dict) else {}
    events = read_json(directory / "core-evidence.json")
    if isinstance(events, list) and all(isinstance(event, dict) for event in events):
        releases = [event for event in events if event.get("phase") == "before_release"]
        usages = [event.get("provider_usage") for event in releases]
        sends = sum(event.get("phase") == "before_send" and event.get("decision") == "allow" for event in events)
        usages.extend([None] * max(0, sends - len(releases)))
        usage_source = "core-evidence"
    else:
        evidence = outcome.get("code_treatment_evidence") or {}
        recorded = evidence.get("provider_usage_attempts") if isinstance(evidence, dict) else None
        usages = list(recorded) if isinstance(recorded, list) else []
        unmatched = (
            number(evidence.get("allowed_sends_without_release_check"), integer=True)
            if isinstance(evidence, dict)
            else None
        )
        usages.extend([None] * int(unmatched or 0))
        usage_source = "outcome-evidence" if isinstance(recorded, list) else "unknown"
    # No provider measurement is not evidence of zero spending. Codex's cumulative
    # turn usage and the duplicate outcome totals are deliberately never added.
    if not usages:
        usages = [None]
    measurements: dict[str, list[Any]] = {key: [] for key in METRICS}
    for usage in usages:
        usage = usage if isinstance(usage, dict) else {}
        for metric, key in TOKEN_FIELDS.items():
            measurements[metric].append(number(usage.get(key), integer=True))
        prompt = number(usage.get("input_tokens"), integer=True)
        cached = number(usage.get("cached_tokens"), integer=True)
        measurements["net_input_tokens"].append(
            prompt - cached if prompt is not None and cached is not None and cached <= prompt else None
        )
    for metric, key in TIME_FIELDS.items():
        measurements[metric].append(0 if arm == "off" and metric == "index_seconds" else number(outcome.get(key)))
    index = outcome.get("index")
    measurements["index_build_seconds"].append(
        0 if arm == "off" else number(index.get("elapsed_seconds")) if isinstance(index, dict) else None
    )
    grades = number(outcome.get("grading_attempts"), integer=True)
    history = outcome.get("grading_history")
    complete_history = (
        isinstance(history, list)
        and grades is not None
        and grades > 0
        and len(history) == grades
        and all(
            isinstance(row, dict) and type(row.get("attempt")) is int and number(row.get("seconds")) is not None
            for row in history
        )
        and {row["attempt"] for row in history} == set(range(1, int(grades) + 1))
    )
    if complete_history:
        measurements["grade_seconds"] = [row["seconds"] for row in history]
    elif grades is not None and grades > 1:
        # Legacy outcomes retain only the latest grade duration and do not
        # establish whether elapsed time includes all resumed grading.
        measurements["grade_seconds"].extend([None] * (int(grades) - 1))
        measurements["total_seconds"].append(None)
    return {
        "attempt": directory.name,
        "status": outcome.get("status", "incomplete"),
        "code_treatment_valid": outcome.get("code_treatment_valid") is True,
        "resolved": outcome.get("resolved") if isinstance(outcome.get("resolved"), bool) else None,
        "usage_source": usage_source,
        "measurements": measurements,
    }


def valid(attempt: dict[str, Any]) -> bool:
    return (
        attempt.get("status") == "completed"
        and attempt.get("code_treatment_valid") is True
        and isinstance(attempt.get("resolved"), bool)
    )


def aggregate(attempts: list[dict[str, Any]], case_count: int) -> dict[str, Any]:
    result: dict[str, Any] = {"case_count": case_count, "attempt_count": len(attempts), "metrics": {}}
    for metric in METRICS:
        samples = [sample for attempt in attempts for sample in attempt["measurements"][metric]]
        known = [sample for sample in samples if sample is not None]
        total = sum(known) if len(known) == len(samples) else None
        result["metrics"][metric] = {
            "total": total,
            "known_subtotal": sum(known),
            "known_measurements": len(known),
            "unknown_measurements": len(samples) - len(known),
            "mean_per_case": total / case_count if total is not None and case_count else None,
        }
    return result


def summarize(root: Path) -> dict[str, Any]:
    names = {path.name for path in root.glob("case-*") if path.is_dir()}
    manifest = read_json(root / "manifest.json")
    if isinstance(manifest, dict) and isinstance(manifest.get("indices"), list):
        names.update(f"case-{index:04d}" for index in manifest["indices"] if type(index) is int and index >= 0)
    groups: dict[str, dict[str, list[Any]]] = {
        name: {"off": [], "on": []}
        for name in ("comparable_pairs", "excluded_pairs", "retry_and_failure_overhead", "all_recorded_attempts")
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
    cases = []
    for name in sorted(names):
        arms = {}
        for arm in ("off", "on"):
            directories = [
                path
                for path in (root / name).glob(f"{arm}-*")
                if path.is_dir() and path.name.removeprefix(f"{arm}-").isdigit()
            ]
            directories.sort(key=lambda path: int(path.name.split("-")[-1]))
            arms[arm] = [read_attempt(path, arm) for path in directories]
        # Match the runner's first settled attempt, including a recovered success.
        # A later accidental retry must not replace it or select a better score.
        finals = {
            arm: next(
                (attempt for attempt in attempts if attempt["status"] in {"completed", "invalid_code_treatment"}),
                attempts[-1] if attempts else {},
            )
            for arm, attempts in arms.items()
        }
        comparable = all(valid(final) for final in finals.values())
        if comparable:
            category = "comparable"
            off, on = finals["off"]["resolved"], finals["on"]["resolved"]
            result = "both_resolved" if off and on else "on_only" if on else "off_only" if off else "neither"
            counts[result] += 1
        else:
            result = None
            if any(
                final.get("status") == "invalid_code_treatment"
                or (final.get("status") == "completed" and not final.get("code_treatment_valid"))
                for final in finals.values()
            ):
                category = "invalid"
            elif any(
                final.get("status") in {"infrastructure_error", "interrupted", "runner_error"}
                for final in finals.values()
            ):
                category = "infrastructure_error"
            else:
                category = "incomplete"
        counts[category] += 1
        report_arms = {}
        for arm, attempts in arms.items():
            groups["comparable_pairs" if comparable else "excluded_pairs"][arm].extend(attempts)
            groups["all_recorded_attempts"][arm].extend(attempts)
            groups["retry_and_failure_overhead"][arm].extend(
                attempt for attempt in attempts if attempt is not finals[arm] or not valid(attempt)
            )
            report_arms[arm] = {
                "final_status": finals[arm].get("status", "not_started"),
                "code_treatment_valid": finals[arm].get("code_treatment_valid", False),
                "resolved": finals[arm].get("resolved"),
                "attempts": [
                    {key: value for key, value in attempt.items() if key != "measurements"} for attempt in attempts
                ],
                **aggregate(attempts, 1),
            }
        cases.append({"case": name, "category": category, "result": result, "arms": report_arms})
    scopes = {}
    for name, arms in groups.items():
        n = (
            counts["comparable"]
            if name == "comparable_pairs"
            else len(cases) - counts["comparable"]
            if name == "excluded_pairs"
            else 0
        )
        scopes[name] = {arm: aggregate(attempts, n) for arm, attempts in arms.items()}
    return {"case_count": len(cases), "counts": dict(counts), "scopes": scopes, "cases": cases}


def format_metric(metric: dict[str, Any], *, mean: bool = False) -> str:
    value = metric["mean_per_case" if mean else "total"]
    if value is None:
        return f"unknown (known subtotal {metric['known_subtotal']:,.2f}; missing {metric['unknown_measurements']})"
    return f"{value:,.2f}" if mean or isinstance(value, float) else f"{value:,}"


def markdown(summary: dict[str, Any]) -> str:
    counts = summary["counts"]
    lines = [
        "# Gateway code reuse benchmark",
        "",
        f"Cases: {summary['case_count']}; comparable completed pairs: {counts['comparable']}.",
        "",
        "| Both resolved | On only | Off only | Neither | Invalid | Infrastructure error | Incomplete |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        "| "
        + " | ".join(
            str(counts[key])
            for key in (
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
        "Comparisons include only pairs whose final attempts both completed with valid code treatment and recorded grading results. Each arm's costs include every attempt, including rejected releases, interruptions, and retries. Codex cumulative usage is not added to provider measurements.",
        "",
        "Reasoning tokens are included in output tokens; do not add them again. Net input is input minus cache-read tokens only when both measurements are present. Unknown values are not zero. No subscription dollar cost is estimated.",
        "",
    ]
    for title, scope in (
        ("Comparable pairs: totals", "comparable_pairs"),
        ("Excluded pairs: recorded expenditure", "excluded_pairs"),
        ("Retry and failed/invalid attempt overhead (subset, do not add again)", "retry_and_failure_overhead"),
        ("All recorded attempts", "all_recorded_attempts"),
    ):
        lines.extend([
            f"## {title}",
            "",
            "| Arm | Attempts | Input | Cache read | Net input | Output | Reasoning (within output) |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ])
        for arm in ("off", "on"):
            data = summary["scopes"][scope][arm]
            values = [
                format_metric(data["metrics"][key])
                for key in (
                    "input_tokens",
                    "cache_read_tokens",
                    "net_input_tokens",
                    "output_tokens",
                    "reasoning_tokens",
                )
            ]
            lines.append(f"| {arm} | {data['attempt_count']} | " + " | ".join(values) + " |")
        lines.append("")
        if scope in {"comparable_pairs", "retry_and_failure_overhead"}:
            lines.extend([
                "| Arm | Agent seconds | Index incl. wait | Pure index build | Grade seconds | Total seconds |",
                "| --- | --- | --- | --- | --- | --- |",
            ])
            for arm in ("off", "on"):
                metrics = summary["scopes"][scope][arm]["metrics"]
                lines.append(
                    f"| {arm} | "
                    + " | ".join(
                        format_metric(metrics[key])
                        for key in (
                            "agent_seconds",
                            "index_seconds",
                            "index_build_seconds",
                            "grade_seconds",
                            "total_seconds",
                        )
                    )
                    + " |"
                )
            lines.append("")
    lines.extend([
        "## Comparable pairs: per-case means",
        "",
        "| Arm | Input | Cache read | Net input | Output | Agent seconds | Index seconds | Grade seconds | Total seconds |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ])
    for arm in ("off", "on"):
        metrics = summary["scopes"]["comparable_pairs"][arm]["metrics"]
        lines.append(
            f"| {arm} | "
            + " | ".join(
                format_metric(metrics[key], mean=True)
                for key in (
                    "input_tokens",
                    "cache_read_tokens",
                    "net_input_tokens",
                    "output_tokens",
                    "agent_seconds",
                    "index_seconds",
                    "grade_seconds",
                    "total_seconds",
                )
            )
            + " |"
        )
    lines.extend([
        "",
        "Index time includes index queue waiting; pure build time is reported separately. Off has no indexing, so its index time is zero. Missing stage durations remain unknown. Complete grading history includes every recorded grading duration; otherwise repeated grading leaves grade/total duration incomplete. Summed attempt durations are not run wall-clock time. Failure overhead is a subset of the other totals, and must not be added again.",
        "",
        "## Cases",
        "",
        "| Case | Category | Pair result | Off final status | On final status |",
        "| --- | --- | --- | --- | --- |",
    ])
    for case in summary["cases"]:
        lines.append(
            f"| {case['case']} | {case['category']} | {case['result'] or '—'} | {case['arms']['off']['final_status']} | {case['arms']['on']['final_status']} |"
        )
    return "\n".join(lines) + "\n"


def atomic_write(path: Path, text: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    if not args.run_dir.is_dir():
        parser.error("run_dir must be an existing directory")
    result = summarize(args.run_dir)
    atomic_write(args.run_dir / "summary.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    atomic_write(args.run_dir / "summary.md", markdown(result))
    print(json.dumps({"cases": result["case_count"], **result["counts"]}))


if __name__ == "__main__":
    main()
