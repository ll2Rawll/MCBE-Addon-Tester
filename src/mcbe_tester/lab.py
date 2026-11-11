import html
import json
import operator as comparisons
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import logs, packs, visual
from .desktop import Desktop, launch
from .process import run_bounded
from .util import contained, read_json, sha256, slug, utc_now, write_json

CATEGORIES = {
    "pack",
    "gameplay",
    "graphics",
    "controls",
    "menus",
    "audio",
    "persistence",
    "performance",
    "multiplayer",
}


class Lab:
    def __init__(self, root=".mcbe-lab", desktop_factory=Desktop):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.desktop_factory = desktop_factory

    @contextmanager
    def exclusive(self):
        """One tool operation per workspace. A crashed operation leaves an explicit recoverable lock."""
        lock = self.root / "busy.lock"
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RuntimeError(
                "Another operation is active. After a crash, remove .mcbe-lab/busy.lock"
            ) from None
        try:
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            yield
        finally:
            lock.unlink(missing_ok=True)

    def load(self):
        if not (self.root / "active.json").is_file():
            raise ValueError("Start a run first")
        active = read_json(self.root / "active.json")["run_id"]
        folder = self.root / "runs" / slug(active)
        return folder, read_json(folder / "run.json")

    def save(self, state):
        write_json(self.root / "runs" / slug(state["run_id"]) / "run.json", state)

    def audit(self, action, details):
        folder, _ = self.load()
        with (folder / "actions.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": utc_now(), "action": action, "details": details}) + "\n")

    def evidence(self, path, kind):
        folder, state = self.load()
        path = Path(path).resolve()
        if not path.is_relative_to(folder) or not path.is_file():
            raise ValueError("Evidence must be a file in the current run")
        key = uuid.uuid4().hex[:12]
        item = {
            "path": path.relative_to(folder).as_posix(),
            "kind": kind,
            "sha256": sha256(path),
            "time": utc_now(),
        }
        state["evidence"][key] = item
        self.save(state)
        return {"evidence_id": key, **item, "absolute_path": str(path)}

    def start(self, plan_path, addon_path, log_dirs=None):
        plan = read_json(plan_path)
        requirements = plan.get("requirements", [])
        if plan.get("schema_version") != 1 or not requirements:
            raise ValueError("Plan needs schema_version: 1 and a non-empty requirements list")
        ids = set()
        for req in requirements:
            key = slug(req["id"])
            if key in ids or req.get("category") not in CATEGORIES or not req.get("expected"):
                raise ValueError(
                    "Each requirement needs a unique id, supported category, and expected behavior"
                )
            ids.add(key)
        inspection = packs.inspect_addon(addon_path)
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        folder = self.root / "runs" / run_id
        folder.mkdir(parents=True)
        directories = list(log_dirs) if log_dirs is not None else logs.default_log_dirs()
        state = {
            "run_id": run_id,
            "created": utc_now(),
            "started_epoch": time.time(),
            "plan": plan,
            "addon": inspection,
            "log_dirs": directories,
            "log_cursors": logs.cursors(directories),
            "runtime_errors": [],
            "telemetry_seen": 0,
            "results": {},
            "evidence": {},
            "window": None,
            "world": None,
            "latest_release_verified": False,
        }
        self.save(state)
        write_json(folder / "plan.json", plan)
        write_json(folder / "inventory.json", inspection)
        write_json(self.root / "active.json", {"run_id": run_id})
        item = self.evidence(folder / "inventory.json", "static-inspection")
        return {
            "run_id": run_id,
            "folder": str(folder),
            "inventory": item,
            "requirements": len(ids),
            "static_errors": inspection["errors"],
            "next": "Prepare/import a disposable world, bind the game window, then exercise the requirements",
        }

    def doctor(self):
        result = {
            "platform": os.name,
            "python": os.sys.version.split()[0],
            "node": shutil.which("node"),
            "root": str(self.root),
            "default_log_dirs": logs.default_log_dirs(),
            "stop_file": str(self.root / "STOP"),
            "windows": [],
            "latest_release_verified": False,
        }
        try:
            result["windows"] = self.desktop_factory(self.root / "STOP").windows()
        except RuntimeError as exc:
            result["client_blocker"] = str(exc)
        return result

    def bind(self, hwnd=None):
        _, state = self.load()
        desktop = self.desktop_factory(self.root / "STOP")
        selected = desktop.select(hwnd)
        expected = state["plan"].get("target", {}).get("file_version")
        if expected and selected.get("file_version") != expected:
            raise ValueError(f"Game build mismatch: plan {expected}, observed {selected.get('file_version')}")
        state["window"] = selected
        self.save(state)
        self.audit("bind", selected)
        return selected

    def desktop(self):
        _, state = self.load()
        selected = state["window"]
        if not selected:
            raise ValueError("Bind a Minecraft window first")
        desktop = self.desktop_factory(self.root / "STOP", selected["hwnd"])
        current = desktop.select(selected["hwnd"])
        if current["exe"] != selected["exe"] or current.get("file_version") != selected.get("file_version"):
            raise ValueError("Minecraft executable/build changed during the run; start a new run")
        return desktop

    def action(self, action, **arguments):
        allowed = {"focus", "press", "click", "look", "scroll", "type_text", "wait"}
        if action not in allowed:
            raise ValueError("Unknown desktop action")
        try:
            getattr(self.desktop(), action)(**arguments)
        except Exception as exc:
            self.audit(action, {"arguments": arguments, "status": "blocked", "reason": str(exc)})
            raise
        self.audit(action, {"arguments": arguments, "status": "performed"})
        return {
            "status": "performed",
            "note": "Input delivery does not establish that the game handled it correctly",
        }

    def capture(self, name="screen"):
        folder, _ = self.load()
        path = folder / f"{slug(name)}-{uuid.uuid4().hex[:8]}.png"
        metadata = self.desktop().capture(path)
        result = self.evidence(path, "screenshot")
        self.audit("screenshot", {**result, **metadata})
        return result

    def read_logs(self):
        folder, state = self.load()
        result = logs.tail(state["log_dirs"], state["log_cursors"], state["run_id"], state["started_epoch"])
        state["runtime_errors"].extend(result["errors"])
        state["telemetry_seen"] += len(result["telemetry"])
        self.save(state)
        path = folder / f"logs-{uuid.uuid4().hex[:8]}.json"
        write_json(path, result)
        return {**result, "evidence": self.evidence(path, "content-log")}

    def prepare_world(self, template_path, api_version=None):
        folder, state = self.load()
        current = packs.inspect_addon(state["addon"]["source"])
        if current["content_sha256"] != state["addon"]["content_sha256"]:
            raise ValueError("Add-on changed since run start; start a new run")
        destination = folder / ("test-observed.mcworld" if api_version else "test-clean.mcworld")
        result = packs.build_world(
            template_path, state["addon"]["source"], destination, state["run_id"], api_version
        )
        state["world"] = result
        self.save(state)
        self.audit("prepare_world", result)
        return result

    def open_world(self):
        _, state = self.load()
        world = state["world"]
        if not world or not Path(world["path"]).is_file() or sha256(world["path"]) != world["sha256"]:
            raise ValueError("Prepare an unchanged test world first")
        if os.name != "nt":
            raise RuntimeError("Importing a test world requires Windows Minecraft Bedrock")
        os.startfile(world["path"])
        self.audit("import_world", world)
        return {
            "status": "import_requested",
            "name": world["name"],
            "note": "Use screenshots to verify import and open this world",
        }

    def launch_game(self):
        launch()
        return {"status": "launch_requested", "next": "Wait for the game, then bind its window"}

    def record(self, requirement, status, observed, evidence_ids, method="ai-review"):
        folder, state = self.load()
        if requirement not in {r["id"] for r in state["plan"]["requirements"]}:
            raise ValueError("Requirement is not in the frozen test plan")
        if status not in {"passed", "failed", "blocked", "inconclusive"}:
            raise ValueError("Invalid result status")
        if method not in {"ai-review", "human-review", "automated"} or not observed.strip():
            raise ValueError("Supply a review method and an actual observation")
        if status in {"passed", "failed"} and not evidence_ids:
            raise ValueError("Pass/fail requires evidence")
        for key in evidence_ids:
            item = state["evidence"].get(key)
            if not item or sha256(contained(folder, item["path"])) != item["sha256"]:
                raise ValueError("Missing or changed evidence")
        result = {
            "status": status,
            "observed": observed,
            "evidence_ids": evidence_ids,
            "method": method,
            "time": utc_now(),
        }
        state["results"][requirement] = result
        self.save(state)
        self.audit("verdict", {"requirement": requirement, **result})
        return result

    def compare_image(
        self, requirement, actual_id, baseline, tolerance=12, max_changed_fraction=0.01, region=None
    ):
        folder, state = self.load()
        item = state["evidence"].get(actual_id)
        if not item or item["kind"] != "screenshot":
            raise ValueError("Actual image must be a screenshot captured in this run")
        actual = contained(folder, item["path"])
        if sha256(actual) != item["sha256"]:
            raise ValueError("Screenshot was changed")
        approval = read_json(str(baseline) + ".approval.json")
        if approval["sha256"] != sha256(baseline) or not approval.get("reason"):
            raise ValueError("Baseline is unapproved or changed")
        if approval.get("source_run") == state["run_id"]:
            raise ValueError("Baseline must come from an earlier reviewed run, not the run being judged")
        if approval.get("settings") != state["plan"].get("target", {}).get("settings"):
            raise ValueError("Baseline test settings differ; review a matching reference")
        if approval.get("file_version") != (state.get("window") or {}).get("file_version"):
            raise ValueError("Baseline and target game builds differ; review a new baseline")
        prefix = uuid.uuid4().hex[:8]
        baseline_copy = folder / f"baseline-{prefix}.png"
        shutil.copyfile(baseline, baseline_copy)
        reference = self.evidence(baseline_copy, "approved-baseline")
        result = visual.compare(
            actual, baseline_copy, folder / f"diff-{prefix}.png", tolerance, max_changed_fraction, region
        )
        result["baseline_approval"] = approval
        path = folder / f"comparison-{prefix}.json"
        write_json(path, result)
        comparison = self.evidence(path, "pixel-comparison")
        ids = [actual_id, reference["evidence_id"], comparison["evidence_id"]]
        if result.get("diff"):
            ids.append(self.evidence(result["diff"], "image-diff")["evidence_id"])
        return self.record(requirement, result["status"], json.dumps(result), ids, "automated")

    def approve_baseline(self, screenshot_id, destination, reason, reviewer):
        folder, state = self.load()
        if not reason.strip() or reviewer not in {"human", "ai"}:
            raise ValueError("Baseline needs a reason and reviewer (human or ai)")
        item = state["evidence"].get(screenshot_id)
        if not item or item["kind"] != "screenshot":
            raise ValueError("Baseline must come from a captured screenshot")
        source = contained(folder, item["path"])
        if sha256(source) != item["sha256"]:
            raise ValueError("Screenshot was changed")
        version = (state.get("window") or {}).get("file_version")
        if not version:
            raise ValueError("Bind a game with a detected file version before approving a baseline")
        destination = Path(destination).resolve()
        if destination.exists() or Path(str(destination) + ".approval.json").exists():
            raise ValueError("Baseline exists; use a new filename to preserve review history")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        approval = {
            "sha256": sha256(destination),
            "reason": reason,
            "reviewer": reviewer,
            "file_version": version,
            "time": utc_now(),
            "source_run": state["run_id"],
            "settings": state["plan"].get("target", {}).get("settings"),
        }
        write_json(str(destination) + ".approval.json", approval)
        self.audit("approve_baseline", {"destination": str(destination), **approval})
        return approval

    def assert_telemetry(self, requirement, kind, field, operator, expected, after_tick, timeout=10):
        if not 0 <= timeout <= 15 or type(after_tick) is not int or after_tick < 0:
            raise ValueError("Timeout must be 0–15 seconds; after_tick must be a nonnegative checkpoint tick")
        if operator not in {"eq", "ne", "gt", "ge", "lt", "le", "contains"}:
            raise ValueError("Unsupported comparison operator")
        deadline, ids, seen = time.monotonic() + timeout, [], []
        while True:
            self.desktop().guard()
            chunk = self.read_logs()
            ids.append(chunk["evidence"]["evidence_id"])
            for event in chunk["telemetry"]:
                if event.get("kind") != kind or event.get("tick", -1) <= after_tick:
                    continue
                try:
                    value = event
                    for part in field.split("."):
                        value = value[int(part)] if isinstance(value, list) else value[part]
                    seen.append(value)
                    checks = {
                        "eq": comparisons.eq,
                        "ne": comparisons.ne,
                        "gt": comparisons.gt,
                        "ge": comparisons.ge,
                        "lt": comparisons.lt,
                        "le": comparisons.le,
                        "contains": comparisons.contains,
                    }
                    if checks[operator](value, expected):
                        return self.record(
                            requirement,
                            "passed",
                            f"{kind}.{field} {operator} {expected!r}; observed {value!r}",
                            ids,
                            "automated",
                        )
                except (KeyError, IndexError, ValueError, TypeError):
                    continue
            if time.monotonic() >= deadline:
                status = "failed" if seen else "blocked"
                return self.record(
                    requirement,
                    status,
                    f"No matching observation before timeout. Observed values: {seen[-10:]}",
                    ids,
                    "automated",
                )
            time.sleep(0.1)

    def report(self):
        folder, state = self.load()
        blockers = []
        target = state["plan"].get("target", {}).get("file_version")
        observed = (state["window"] or {}).get("file_version")
        if not target or observed != target:
            blockers.append("Exact target game file version has not been pinned and observed")
        external = state.get("external_checks", [])
        blockers.extend(
            f"{r['tool']}: {r.get('reason', 'run blocked')}" for r in external if r["status"] == "blocked"
        )
        for key, item in state["evidence"].items():
            path = contained(folder, item["path"])
            if not path.is_file() or sha256(path) != item["sha256"]:
                blockers.append(f"Evidence changed or missing: {key}")
        rows = []
        for req in state["plan"]["requirements"]:
            result = state["results"].get(
                req["id"], {"status": "untested", "observed": "No evidence recorded"}
            )
            rows.append({**req, **result})
        failures = sum(row["status"] == "failed" for row in rows)
        passed = sum(row["status"] == "passed" for row in rows)
        assessed = sum(row["status"] in {"passed", "failed"} for row in rows)
        if (
            failures
            or state["runtime_errors"]
            or state["addon"]["errors"]
            or any(r["status"] == "failed" for r in external)
        ):
            overall = "failed"
        elif passed != len(rows) or blockers:
            overall = "incomplete"
        else:
            overall = "passed_for_defined_scope"
        report = {
            "run_id": state["run_id"],
            "overall": overall,
            "target_file_version": target,
            "observed_file_version": observed,
            "latest_release_verified": False,
            "external_checks": external,
            "requirements_total": len(rows),
            "requirements_assessed": assessed,
            "requirement_coverage_percent": round(100 * assessed / len(rows), 2),
            "requirements_passed": passed,
            "ai_reviewed_passes": sum(
                r["status"] == "passed" and r.get("method") == "ai-review" for r in rows
            ),
            "blockers": blockers,
            "runtime_errors": state["runtime_errors"],
            "static_errors": state["addon"]["errors"],
            "requirements": rows,
            "evidence": state["evidence"],
            "world": state["world"],
            "limitation": "Coverage is of this plan only. It is not a probability of correctness or a measured bug-detection rate.",
        }
        write_json(folder / "report.json", report)

        def escape(value):
            return html.escape(str(value))

        body = "".join(
            f"<tr><td>{escape(r['id'])}</td><td>{escape(r['category'])}</td>"
            f"<td>{escape(r['status'])}</td><td>{escape(r['expected'])}</td>"
            f"<td>{escape(r['observed'])}</td></tr>"
            for r in rows
        )
        links = "".join(
            f'<li><a href="{escape(e["path"])}">{escape(k)}: {escape(e["kind"])}</a></li>'
            for k, e in state["evidence"].items()
        )
        page = (
            "<!doctype html><meta charset='utf-8'><title>MCBE Addon Tester report</title>"
            "<style>body{font:16px system-ui;max-width:1200px;margin:3rem auto;padding:1rem;background:#101820;color:#eee}"
            "table{border-collapse:collapse;width:100%}td,th{border:1px solid #40505b;padding:.7rem;text-align:left}"
            "a{color:#8ce8be}td{overflow-wrap:anywhere}pre{white-space:pre-wrap}</style>"
            f"<h1>{escape(overall)}</h1><p>{passed}/{len(rows)} requirements passed. "
            f"{report['requirement_coverage_percent']}% assessed.</p><p>{escape(report['limitation'])}</p>"
            f"<p>Target: {escape(target)}. Observed: {escape(observed)}. Latest release verification: not performed.</p>"
            f"<pre>{escape(json.dumps(blockers + state['runtime_errors'] + state['addon']['errors'], indent=2))}</pre>"
            "<table><tr><th>Requirement</th><th>Category</th><th>Status</th><th>Expected</th><th>Observed</th></tr>"
            f"{body}</table><h2>Evidence</h2><ul>{links}</ul>"
        )
        (folder / "report.html").write_text(page, encoding="utf-8")
        return {**report, "html_path": str(folder / "report.html"), "json_path": str(folder / "report.json")}

    def scenario(self, path):
        scenario = read_json(path)
        steps = scenario.get("steps", [])
        if not steps or len(steps) > 100:
            raise ValueError("Scenario needs 1–100 steps")
        for step in steps:
            if step.get("action") in {"assert_telemetry", "compare_image"}:
                self.record(
                    step["args"]["requirement"], "blocked", "Scenario assertion has not completed", []
                )
        results = []
        for index, step in enumerate(steps):
            name = step["action"]
            args = step.get("args", {})
            try:
                if name == "capture":
                    value = self.capture(**args)
                elif name == "read_logs":
                    value = self.read_logs()
                elif name == "assert_telemetry":
                    value = self.assert_telemetry(**args)
                elif name == "compare_image":
                    args = dict(args)
                    if args.get("actual_id") == "$last_capture":
                        args["actual_id"] = next(
                            r["value"]["evidence_id"] for r in reversed(results) if r["action"] == "capture"
                        )
                    value = self.compare_image(**args)
                else:
                    value = self.action(name, **args)
                results.append({"index": index, "action": name, "value": value})
                if value.get("status") in {"failed", "blocked", "inconclusive"}:
                    break
            except Exception as exc:
                results.append({"index": index, "action": name, "status": "blocked", "reason": str(exc)})
                break
        self.audit("scenario", {"path": str(path), "results": results})
        return {
            "status": "blocked"
            if any(
                r.get("status") == "blocked" or r.get("value", {}).get("status") == "blocked" for r in results
            )
            else "failed"
            if any(r.get("value", {}).get("status") == "failed" for r in results)
            else "completed",
            "steps": results,
            "note": "Only assertions/review verdicts count; completing actions is not a passing test",
        }

    def external(self, tool, input_path, tag=None, expected_tests=None, bds_version=None, timeout=300):
        """Fixed adapters; no shell command supplied by an AI or pack is executed."""
        if not 1 <= timeout <= 900:
            raise ValueError("External tool timeout must be 1–900 seconds")
        folder, state = self.load()
        node = shutil.which("node")
        packages = {"mct": "@minecraft/creator-tools", "bds": "@bedrock-core/bds-runner"}
        if tool not in packages:
            raise ValueError("Supported external tools: mct, bds")
        project = Path(__file__).resolve().parents[2]
        package_root = project / "tools" / "node_modules" / packages[tool]
        if not node or not (package_root / "package.json").is_file():
            raise ValueError("Install Node 22+ and run npm --prefix tools install from the repository")
        package = read_json(package_root / "package.json")
        binary = package["bin"]
        if isinstance(binary, dict):
            binary = binary["mct" if tool == "mct" else "bc-bds"]
        entry = contained(package_root, binary)
        source = str(Path(input_path).resolve())
        if (
            tool == "mct"
            and packs.inspect_addon(source)["content_sha256"] != state["addon"]["content_sha256"]
        ):
            raise ValueError("Validator input differs from the current run's add-on; start a new run")
        attempt = folder / f"{tool}-{uuid.uuid4().hex[:8]}"
        attempt.mkdir()
        if tool == "mct":
            args = ["validate", "-i", source, "-o", str(attempt / "reports"), "--json"]
        else:
            if not tag or type(expected_tests) is not int or expected_tests < 1 or not bds_version:
                raise ValueError(
                    "BDS needs a test tag, positive expected test count, and an exact server build"
                )
            import re

            if not re.fullmatch(r"\d+(?:\.\d+){2,3}", bds_version):
                raise ValueError("Pin an exact BDS build; 'latest' is not reproducible")
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", tag):
                raise ValueError("Invalid GameTest tag")
            # Mob behavior must not silently run on Peaceful; retain production watchdog defaults.
            config = attempt / "bds-runner.json"
            write_json(
                config,
                {
                    "version": bds_version,
                    "channel": "stable",
                    "properties": {
                        "difficulty": "normal",
                        "gamemode": "survival",
                        "force-gamemode": False,
                        "script-watchdog-hang-threshold": 10000,
                        "script-watchdog-enable-shutdown": True,
                    },
                },
            )
            args = [
                "run",
                "--packs",
                source,
                "--tag",
                tag,
                "--expect-registered",
                str(expected_tests),
                "--bds-version",
                bds_version,
                "--config",
                str(config),
                "--fresh",
                "--timeout",
                str(max(1, timeout - 10)),
                "--json",
                str(attempt / "bds-results.json"),
            ]
        path = attempt / "console.log"
        result = run_bounded([node, str(entry), *args], attempt, path, self.root / "STOP", timeout)
        result.update(tool=tool, tool_version=package["version"], input_path=source)
        if result["status"] == "completed":
            from .tool_results import classify

            try:
                if tool == "mct":
                    payload = json.loads(path.read_text(encoding="utf-8"))
                else:
                    payload = read_json(attempt / "bds-results.json")
                result.update(classify(tool, result["exit_code"], payload, expected_tests, bds_version))
            except (ValueError, KeyError, OSError, TypeError) as exc:
                result.update(status="blocked", reason=f"Tool report missing or unrecognized: {exc}")
        result["evidence"] = self.evidence(path, "external-tool-log")
        result["note"] = (
            "Review the tool's report. Exit code alone does not certify the add-on. GameTest needs a separate test pack."
        )
        _, state = self.load()
        state.setdefault("external_checks", []).append(result)
        self.save(state)
        self.audit("external", result)
        return result
