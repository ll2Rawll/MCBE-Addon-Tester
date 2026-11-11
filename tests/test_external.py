import sys
import tempfile
import unittest
from pathlib import Path

from mcbe_tester.process import run_bounded
from mcbe_tester.tool_results import classify


class ExternalTests(unittest.TestCase):
    def test_mct_report_errors_do_not_pass(self):
        result = classify("mct", 0, {"command": "validate", "projects": [{}], "errors": 1})
        self.assertEqual(result["status"], "failed")

    def test_empty_mct_report_rejected(self):
        with self.assertRaises(ValueError):
            classify("mct", 0, {"command": "validate", "projects": [], "errors": 0})

    def test_bds_missing_tests_and_wrong_build_block(self):
        payload = {"verdicts": [{"outcome": "pass"}], "bdsVersion": "1.26.63.1"}
        self.assertEqual(classify("bds", 0, payload, 2, "1.26.63.1")["status"], "blocked")
        self.assertEqual(classify("bds", 0, payload, 1, "1.26.62.1")["status"], "blocked")
        self.assertEqual(classify("bds", 0, payload, 1, "1.26.63.1")["status"], "passed")

    def test_script_error_outside_test_prevents_pass(self):
        payload = {
            "verdicts": [{"outcome": "pass"}],
            "bdsVersion": "1.26.63.1",
            "scriptErrors": ["TypeError"],
        }
        self.assertEqual(classify("bds", 0, payload, 1, "1.26.63.1")["status"], "failed")

    def test_timeout_and_existing_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            command = [sys.executable, "-c", "import time; time.sleep(30)"]
            result = run_bounded(command, root, root / "log", root / "STOP", 0.15)
            self.assertEqual(result["status"], "blocked")
            self.assertIn("timed out", result["reason"])
            (root / "STOP").touch()
            result = run_bounded(["not-a-real-program"], root, root / "log", root / "STOP", 1)
            self.assertEqual(result["status"], "blocked")
