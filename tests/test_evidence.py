import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from mcbe_tester import logs, visual
from mcbe_tester.benchmark import evaluate
from mcbe_tester.lab import Lab
from tests.helpers import FakeDesktop, addon, plan, template


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.source = addon(self.root / "addon")
        self.plan = plan(self.root / "plan.json")
        self.lab = Lab(self.root / "lab", FakeDesktop)
        self.lab.start(self.plan, self.source, [])

    def test_untested_requirements_never_pass(self):
        report = self.lab.report()
        self.assertEqual(report["overall"], "incomplete")
        self.assertEqual(report["requirement_coverage_percent"], 0)

    def test_action_and_capture_do_not_count_as_pass(self):
        self.lab.bind()
        self.lab.action("press", keys=["e"])
        self.lab.capture()
        self.assertEqual(self.lab.report()["requirements_passed"], 0)

    def test_reviewed_pass_is_explicit_and_html_is_escaped(self):
        self.lab.bind()
        capture = self.lab.capture()
        self.lab.record("visual", "passed", "Green cube <script>alert(1)</script>", [capture["evidence_id"]])
        report = self.lab.report()
        self.assertEqual(report["overall"], "passed_for_defined_scope")
        self.assertEqual(report["ai_reviewed_passes"], 1)
        self.assertFalse(report["latest_release_verified"])
        self.assertNotIn("<script>", Path(report["html_path"]).read_text())

    def test_build_mismatch_blocks_binding(self):
        _, state = self.lab.load()
        state["plan"]["target"]["file_version"] = "9.9.9.9"
        self.lab.save(state)
        with self.assertRaises(ValueError):
            self.lab.bind()

    def test_tampered_evidence_invalidates_report(self):
        self.lab.bind()
        capture = self.lab.capture()
        self.lab.record("visual", "passed", "Observed", [capture["evidence_id"]])
        Path(capture["absolute_path"]).write_bytes(b"changed")
        self.assertEqual(self.lab.report()["overall"], "incomplete")
        with self.assertRaises(ValueError):
            self.lab.record("visual", "passed", "Observed", [capture["evidence_id"]])

    def test_unknown_requirement_and_missing_evidence_rejected(self):
        with self.assertRaises(ValueError):
            self.lab.record("not-planned", "passed", "Observed", [])
        with self.assertRaises(ValueError):
            self.lab.record("visual", "passed", "Observed", [])

    def test_changed_source_cannot_be_tested_under_old_hash(self):
        (self.source / "new.json").write_text("{}")
        with self.assertRaises(ValueError):
            self.lab.prepare_world(template(self.root / "base.mcworld"))

    def test_baseline_requires_approval_and_same_version(self):
        self.lab.bind()
        capture = self.lab.capture()
        baseline = self.root / "baseline.png"
        self.lab.approve_baseline(
            capture["evidence_id"], baseline, "Verified against the specification", "human"
        )
        with self.assertRaises(ValueError):
            self.lab.compare_image("visual", capture["evidence_id"], baseline)
        self.lab.start(self.plan, self.source, [])
        self.lab.bind()
        capture = self.lab.capture()
        result = self.lab.compare_image("visual", capture["evidence_id"], baseline)
        self.assertEqual(result["status"], "passed")
        Image.new("RGB", (32, 32), "pink").save(baseline)
        with self.assertRaises(ValueError):
            self.lab.compare_image("visual", capture["evidence_id"], baseline)

    def test_stop_prevents_actions(self):
        self.lab.bind()
        (self.lab.root / "STOP").touch()
        with self.assertRaises(RuntimeError):
            self.lab.action("press", keys=["w"])

    def test_workspace_lock_is_exclusive(self):
        with self.lab.exclusive():
            with self.assertRaises(RuntimeError):
                with Lab(self.lab.root).exclusive():
                    pass

    def test_visual_difference_and_resolution_mismatch(self):
        a, b, diff = [self.root / name for name in ("a.png", "b.png", "diff.png")]
        Image.new("RGB", (10, 10), "green").save(a)
        Image.new("RGB", (10, 10), "pink").save(b)
        self.assertEqual(visual.compare(a, b, diff)["status"], "failed")
        Image.new("RGB", (20, 20), "green").save(b)
        self.assertEqual(visual.compare(a, b, diff)["status"], "blocked")

    def test_new_log_lines_only_and_partial_utf8(self):
        path = self.root / "content.log"
        path.write_bytes(b"[Scripting][error] old\n")
        cursor = logs.cursors([self.root])
        line = (
            "BEDROCKLAB "
            + json.dumps({"runId": "wanted", "kind": "snapshot", "name": "é"}, ensure_ascii=False)
            + "\n"
        ).encode()
        split = line.index("é".encode()) + 1
        with path.open("ab") as out:
            out.write(line[:split])
        self.assertFalse(logs.tail([self.root], cursor, "wanted", 0)["telemetry"])
        with path.open("ab") as out:
            out.write(line[split:])
            out.write(b'BEDROCKLAB {"runId":"other","kind":"snapshot"}\n')
        result = logs.tail([self.root], cursor, "wanted", 0)
        self.assertEqual(len(result["telemetry"]), 1)
        self.assertEqual(result["telemetry"][0]["name"], "é")
        self.assertFalse(result["errors"])

    def test_benchmark_inconclusive_is_not_dropped(self):
        for name, outcome in (
            ("broken", "failed"),
            ("unknown", "incomplete"),
            ("good", "passed_for_defined_scope"),
        ):
            (self.root / f"{name}.json").write_text(json.dumps({"overall": outcome}))
        cases = {
            "cases": [
                {"id": n, "report": f"{n}.json", "buggy": n != "good", "description": n}
                for n in ("broken", "unknown", "good")
            ]
        }
        path = self.root / "cases.json"
        path.write_text(json.dumps(cases))
        report = evaluate(path)
        self.assertEqual(report["bug_detection_percent"], 50)
        self.assertEqual(report["false_alarm_percent"], 0)
        self.assertFalse(report["eligible_for_target_claim"])

    def test_state_assertions_reject_stale_ticks(self):
        self.lab.bind()
        _, state = self.lab.load()
        state["log_dirs"] = [str(self.root)]
        self.lab.save(state)
        record = {"runId": state["run_id"], "kind": "snapshot", "tick": 99, "health": 20}
        path = self.root / "observer.log"
        path.write_text("BEDROCKLAB " + json.dumps(record) + "\n")
        outcome = self.lab.assert_telemetry(
            "visual", "snapshot", "health", "eq", 20, after_tick=100, timeout=0
        )
        self.assertEqual(outcome["status"], "blocked")
        record["tick"] = 101
        with path.open("a") as stream:
            stream.write("BEDROCKLAB " + json.dumps(record) + "\n")
        outcome = self.lab.assert_telemetry(
            "visual", "snapshot", "health", "eq", 20, after_tick=100, timeout=0
        )
        self.assertEqual(outcome["status"], "passed")

    def test_logged_runtime_error_prevents_a_green_report(self):
        self.lab.bind()
        _, state = self.lab.load()
        state["log_dirs"] = [str(self.root)]
        self.lab.save(state)
        (self.root / "error.log").write_text("[Scripting][error] TypeError: broken\n")
        self.lab.read_logs()
        item = self.lab.capture()
        self.lab.record("visual", "passed", "Green cube", [item["evidence_id"]])
        self.assertEqual(self.lab.report()["overall"], "failed")
