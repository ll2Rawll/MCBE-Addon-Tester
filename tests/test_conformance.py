import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from mcbe_tester.sim.conformance import BUILTIN, run_suite


class ConformanceTests(unittest.TestCase):
    def test_documented_cases_have_no_unexpected_mismatches(self):
        result = run_suite()
        self.assertGreaterEqual(result["total"], 50)
        self.assertEqual(result["mismatch"], 0, result)
        self.assertEqual(result["error"], 0, result)
        self.assertEqual(
            {r["id"] for r in result["results"] if r["status"] == "unsupported"},
            {"ease-in-quad", "degenerate-random", "catmullrom-constant"},
        )
        self.assertIsNone(result["accuracy_percent"])
        self.assertEqual(result["native_reference_cases"], 0)
        self.assertFalse(result["representative_of_all_addons"])
        self.assertEqual(
            result["total"], sum(result[k] for k in ["matched", "mismatch", "unsupported", "error"])
        )
        self.assertEqual(result["overall"], "incomplete")

    def test_mismatch_error_and_unknown_case_stay_in_total(self):
        suite = {
            "schema_version": 1,
            "cases": [
                {"id": "ok", "kind": "molang", "expression": "2+3", "expected": 5},
                {"id": "wrong", "kind": "molang", "expression": "2+3", "expected": 6},
                {"id": "gap", "kind": "unimplemented", "expected": 42},
                {"id": "bad", "kind": "molang"},
            ],
        }
        result = run_suite(suite)
        self.assertEqual(result["total"], 4)
        self.assertEqual([result[k] for k in ["matched", "mismatch", "unsupported", "error"]], [1, 1, 1, 1])
        self.assertEqual(result["overall"], "failed")
        self.assertIsNone(result["documented_contract_pass_percent"])
        self.assertIsNone(result["accuracy_percent"])

    def test_empty_or_duplicate_suite_cannot_manufacture_perfect_score(self):
        for cases in ([], [{"id": "same"}, {"id": "same"}], [{"id": None}]):
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                run_suite({"schema_version": 1, "cases": cases})

    def test_reference_scenario_requires_assertions_and_retains_compile_errors(self):
        suite = json.loads(BUILTIN.read_text())
        case = deepcopy(next(c for c in suite["cases"] if c["kind"] == "scenario"))
        case["scenario"]["steps"] = []
        result = run_suite({"schema_version": 1, "cases": [case]})
        self.assertEqual(result["unsupported"], 1)
        case["files"]["scripts/main.js"] = "syntax is invalid @"
        result = run_suite({"schema_version": 1, "cases": [case]})
        self.assertNotEqual(result["matched"], 1)

    def test_cli_preserves_incomplete_exit_and_never_overwrites_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            suite = Path(folder) / "suite.json"
            out = Path(folder) / "result.json"
            suite.write_text(
                json.dumps({"schema_version": 1, "cases": [{"id": "gap", "kind": "not-supported"}]})
            )
            command = [sys.executable, "-m", "mcbe_tester.sim.cli", "conformance", "--suite", str(suite)]
            first = subprocess.run([*command, "--out", str(out)], capture_output=True, text=True)
            self.assertEqual(first.returncode, 2, first.stderr)
            saved = out.read_bytes()
            allowed = subprocess.run([*command, "--allow-unsupported"], capture_output=True, text=True)
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            repeated = subprocess.run([*command, "--out", str(out)], capture_output=True, text=True)
            self.assertEqual(repeated.returncode, 1)
            self.assertEqual(out.read_bytes(), saved)


if __name__ == "__main__":
    unittest.main()
