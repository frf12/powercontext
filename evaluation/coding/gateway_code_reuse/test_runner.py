"""Regression checks for recovered Codex stream errors."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("gateway_code_runner", Path(__file__).with_name("runner.py"))
assert _spec is not None and _spec.loader is not None  # noqa: S101 - test module loader prerequisite
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


class CodexTerminalOutcomeTest(unittest.TestCase):
    def parse(self, events):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            path.write_text("".join(json.dumps(event) + "\n" for event in events))
            return runner.parse_codex(path)

    def test_reconnected_error_then_completed_is_successful(self):
        parsed = self.parse([
            {"type": "error", "message": "Reconnecting... 1/3"},
            {"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 3}},
        ])
        self.assertFalse(parsed["failed_event"])
        self.assertEqual(parsed["terminal_event"], "turn.completed")
        self.assertEqual(parsed["completed_turns"], 1)
        self.assertEqual(parsed["recovered_error_events"], 1)
        self.assertEqual(parsed["usage"], {"input_tokens": 12, "output_tokens": 3})

    def test_terminal_failure_is_not_hidden_by_an_earlier_completion(self):
        parsed = self.parse([
            {"type": "turn.completed", "usage": {"input_tokens": 12}},
            {"type": "error", "message": "Connection failed"},
            {"type": "turn.failed", "error": {"message": "Retry budget exhausted"}},
        ])
        self.assertTrue(parsed["failed_event"])
        self.assertEqual(parsed["terminal_event"], "turn.failed")
        self.assertEqual(parsed["recovered_error_events"], 0)


if __name__ == "__main__":
    unittest.main()
