"""Grading-only recovery must retain time without counting another model run."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("gateway_code_summary", Path(__file__).with_name("summarize.py"))
assert _spec is not None and _spec.loader is not None  # noqa: S101 - test module loading
summary = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(summary)


class GradingHistorySummaryTest(unittest.TestCase):
    def metrics(self, history):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for arm in ("off", "on"):
                path = root / "case-0728" / f"{arm}-1"
                path.mkdir(parents=True)
                outcome = {
                    "status": "completed",
                    "code_treatment_valid": True,
                    "resolved": True,
                    "grading_attempts": 2,
                    "evaluation_seconds": 4,
                    "elapsed_seconds": 44,
                    "grading_history": history,
                    "code_treatment_evidence": {
                        "provider_usage_attempts": [{"input_tokens": 100, "cached_tokens": 50, "output_tokens": 20}]
                    },
                }
                (path / "outcome.json").write_text(json.dumps(outcome))
            result = summary.summarize(root)
            self.assertEqual(result["counts"]["both_resolved"], 1)
            self.assertEqual(result["scopes"]["all_recorded_attempts"]["off"]["attempt_count"], 1)
            return result["scopes"]["comparable_pairs"]["off"]["metrics"]

    def test_complete_history_counts_failed_grade_and_successful_regrade_once(self):
        metrics = self.metrics([
            {"attempt": 1, "status": "infrastructure_error", "seconds": 10, "report_dir": "official"},
            {"attempt": 2, "status": "completed", "seconds": 4, "report_dir": "grade-retries/002"},
        ])
        self.assertEqual(metrics["grade_seconds"]["total"], 14)
        self.assertEqual(metrics["grade_seconds"]["unknown_measurements"], 0)
        self.assertEqual(metrics["total_seconds"]["total"], 44)
        self.assertEqual(metrics["input_tokens"]["total"], 100)

    def test_missing_or_incomplete_history_keeps_previous_time_unknown(self):
        for history in (
            None,
            [{"attempt": 2, "seconds": 4}],
            [{"attempt": 1, "seconds": -1}, {"attempt": 2, "seconds": 4}],
        ):
            with self.subTest(history=history):
                metrics = self.metrics(history)
                self.assertIsNone(metrics["grade_seconds"]["total"])
                self.assertEqual(metrics["grade_seconds"]["known_subtotal"], 4)
                self.assertEqual(metrics["grade_seconds"]["unknown_measurements"], 1)
                self.assertIsNone(metrics["total_seconds"]["total"])
                self.assertEqual(metrics["input_tokens"]["total"], 100)


if __name__ == "__main__":
    unittest.main()
