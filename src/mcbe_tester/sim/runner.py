"""AI-facing sessions, assertions, reproducible reports and bounded exploration."""

import base64
import hashlib
import html
import io
import json
import time
import zipfile
from copy import deepcopy
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageStat

from ..packs import safe_name
from . import PROFILE, VERSION
from .catalog import Catalog
from .diagnostics import Unsupported
from .render import render
from .runtime import ScriptRuntime
from .world import World


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def pointer(value, path):
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("Assertion path must be a JSON pointer, e.g. /player/menu")
    for part in path.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


class Simulation:
    def __init__(self, catalog, seed=0, target_version="unspecified"):
        if not isinstance(target_version, str) or len(target_version) > 80:
            raise ValueError("Target version must be a short string")
        self.catalog = catalog
        self.target = target_version
        self.world = World(catalog, seed)
        self.assertions = []
        self.requirements = {}
        self.operations = []
        self.frames = {}
        self.script = ScriptRuntime(self.world)
        self.world.script = self.script
        self.script.start()
        self.elapsed = 0.0

    def close(self):
        self.script.close()

    @classmethod
    def open(cls, path, seed=0, target_version="unspecified"):
        return cls(Catalog.open(path), seed, target_version)

    @classmethod
    def from_files(cls, files, seed=0, target_version="unspecified"):
        if not isinstance(files, dict) or len(files) > 2000:
            raise ValueError("Use at most 2,000 inline files; larger packs should use an archive")
        output, names, total = {}, set(), 0
        for name, value in files.items():
            name = safe_name(name)
            if isinstance(value, str):
                data = value.encode()
            elif isinstance(value, dict) and isinstance(value.get("base64"), str):
                data = base64.b64decode(value["base64"], validate=True)
            else:
                raise ValueError("Each file must be UTF-8 text or {base64: ...}")
            total += len(data)
            if total > 16 * 1024 * 1024:
                raise ValueError("Inline pack exceeds 16 MiB")
            if name.casefold() in names:
                raise ValueError("Duplicate/case-conflicting inline path")
            names.add(name.casefold())
            output[name] = data
        return cls(Catalog(output), seed, target_version)

    def action(self, action):
        if len(self.operations) >= 3000:
            raise ValueError("Session operation limit; export and start a new session")
        start = time.perf_counter()
        self.operations.append({"action": deepcopy(action)})
        try:
            result = self.world.action(action)
            state_hash = digest(self.world.snapshot())
            return {
                "status": "simulated",
                "result": result,
                "state_sha256": state_hash,
                "tick": self.world.tick,
                "native_compatibility": "unverified",
            }
        except (ValueError, KeyError, TypeError, ArithmeticError, RecursionError) as exc:
            status = "blocked" if isinstance(exc, Unsupported) else "failed"
            self.catalog.findings.add(
                "unsupported" if status == "blocked" else "error",
                "action",
                str(len(self.operations)),
                str(exc),
            )
            return {"status": status, "message": str(exc), "tick": self.world.tick}
        finally:
            self.elapsed += time.perf_counter() - start

    def assert_state(self, requirement, path, operator, expected=None):
        if not isinstance(requirement, str) or not requirement or len(requirement) > 256:
            raise ValueError("Assertion needs a named requirement (max 256 characters)")
        if operator not in {"eq", "ne", "gt", "ge", "lt", "le", "contains", "exists", "near"}:
            raise ValueError("Unknown assertion operator")
        if len(self.operations) >= 3000:
            raise ValueError("Session operation limit")
        spec = {"requirement": requirement, "path": path, "operator": operator, "expected": expected}
        self.operations.append({"assert": deepcopy(spec)})
        state = self.world.snapshot()
        try:
            actual = pointer(state, path)
            exists = True
        except (KeyError, IndexError):
            actual, exists = None, False
        if operator == "exists":
            passed = exists is bool(expected)
        elif not exists:
            passed = False
        else:
            try:
                if operator == "near":
                    passed = (
                        abs(actual - expected["value"]) <= expected["tolerance"]
                        and expected["tolerance"] >= 0
                    )
                else:
                    passed = {
                        "eq": lambda: actual == expected,
                        "ne": lambda: actual != expected,
                        "gt": lambda: actual > expected,
                        "ge": lambda: actual >= expected,
                        "lt": lambda: actual < expected,
                        "le": lambda: actual <= expected,
                        "contains": lambda: expected in actual,
                    }[operator]()
            except (TypeError, KeyError, ValueError):
                passed = False
        item = {
            **spec,
            "actual": actual,
            "tick": self.world.tick,
            "state_sha256": digest(state),
            "status": "simulation_passed" if passed else "failed",
            "method": "state-assertion",
        }
        self.assertions.append(item)
        return item

    def screenshot(self, width=640, height=400, camera="overview"):
        if len(self.operations) >= 3000:
            raise ValueError("Session operation limit")
        self.operations.append({"render": {"width": width, "height": height, "camera": camera}})
        data = render(self.world, width, height, camera)
        key = hashlib.sha256(data).hexdigest()
        if len(self.frames) >= 100 and key not in self.frames:
            raise ValueError("100-frame evidence limit; export and start a new session")
        self.frames[key] = data
        return {
            "id": key,
            "tick": self.world.tick,
            "width": width,
            "height": height,
            "camera": camera,
            "kind": "independent-simulation",
            "native_graphics_verified": False,
            "ui_regions": self.world.ui_regions(),
            "findings": self.catalog.findings.rows(),
        }, data

    def compare_image(self, requirement, frame_id, reference, tolerance=8, max_changed_fraction=0.01):
        if len(reference) > 3 * 1024 * 1024:
            raise ValueError("Reference exceeds 3 MiB")
        if (
            len(self.operations) >= 3000
            or sum(len(json.dumps(op)) for op in self.operations) > 20 * 1024 * 1024
        ):
            raise ValueError("Replay evidence limit")
        if not 0 <= tolerance <= 255 or not 0 <= max_changed_fraction <= 1:
            raise ValueError("Invalid image comparison tolerances")
        actual = Image.open(io.BytesIO(self.frames[frame_id])).convert("RGB")
        with Image.open(io.BytesIO(reference)) as loaded:
            if loaded.size != actual.size:
                raise ValueError("Reference dimensions must match the simulated frame")
            reference_image = loaded.convert("RGB")
        diff = ImageChops.difference(actual, reference_image)
        changed = float(np.mean(np.max(np.asarray(diff), axis=2) > tolerance))
        item = {
            "requirement": requirement,
            "method": "simulation-image-regression",
            "frame_id": frame_id,
            "reference_sha256": hashlib.sha256(reference).hexdigest(),
            "changed_fraction": changed,
            "mean_absolute_error": sum(ImageStat.Stat(diff).mean) / 3,
            "status": "simulation_passed" if changed <= max_changed_fraction else "failed",
            "native_graphics_verified": False,
        }
        self.assertions.append(item)
        self.operations.append(
            {
                "compare": {
                    "requirement": requirement,
                    "frame_id": frame_id,
                    "reference_base64": base64.b64encode(reference).decode(),
                    "tolerance": tolerance,
                    "max_changed_fraction": max_changed_fraction,
                }
            }
        )
        return item

    def scenario(self, spec):
        if not isinstance(spec, dict) or not isinstance(spec.get("steps"), list) or len(spec["steps"]) > 1000:
            raise ValueError("Scenario needs up to 1,000 steps")
        for requirement in spec.get("requirements", []):
            if not isinstance(requirement, dict) or "id" not in requirement or "text" not in requirement:
                raise ValueError("Requirements need id and text")
            self.requirements[requirement["id"]] = requirement["text"]
        results = []
        for step in spec["steps"]:
            if "action" in step:
                result = self.action(step["action"])
            elif "assert" in step:
                result = self.assert_state(**step["assert"])
            elif "render" in step:
                result, _ = self.screenshot(**step["render"])
            elif "compare" in step:
                comparison = dict(step["compare"])
                encoded = comparison.pop("reference_base64")
                if not isinstance(encoded, str) or len(encoded) > 4 * 1024 * 1024:
                    raise ValueError("Invalid/oversized replay reference")
                result = self.compare_image(reference=base64.b64decode(encoded, validate=True), **comparison)
            else:
                raise ValueError("Each step must contain action, assert, render, or compare")
            results.append(result)
            if result.get("status") in {"failed", "blocked"} and spec.get("stop_on_failure", True):
                break
        return {"steps": results, "report": self.report()}

    def explore(self, max_permutations=64):
        if not 1 <= max_permutations <= 256:
            raise ValueError("Permutation budget must be 1–256")
        cases = []
        for identifier in self.catalog.blocks:
            try:
                for states in self.catalog.permutations(identifier, max_permutations):
                    components, chosen = self.catalog.block(identifier, states)
                    cases.append(
                        {
                            "type": "block-permutation",
                            "identifier": identifier,
                            "states": chosen,
                            "component_count": len(components),
                            "status": "evaluated",
                        }
                    )
            except (ValueError, KeyError, TypeError) as exc:
                self.catalog.findings.unsupported("permutation", identifier, str(exc))
                cases.append(
                    {
                        "type": "block-permutation",
                        "identifier": identifier,
                        "status": "blocked",
                        "message": str(exc),
                    }
                )
        return {
            "kind": "smoke-exploration",
            "cases": cases,
            "assertions_created": 0,
            "note": "Enumeration checks evaluation, not intended behavior. Add specification-based assertions.",
            "findings": self.catalog.findings.rows(),
        }

    def report(self):
        from .audit import coverage

        failed = any(a["status"] == "failed" for a in self.assertions)
        tested = {a["requirement"] for a in self.assertions}
        untested = sorted(set(self.requirements) - tested)
        blockers = self.catalog.findings.blockers()
        status = (
            "failed"
            if failed or any(f["severity"] == "error" for f in blockers)
            else "incomplete"
            if blockers or untested or not self.assertions
            else "simulation_passed"
        )
        return {
            "schema_version": 1,
            "simulator_version": VERSION,
            "simulation_profile": PROFILE,
            "requested_bedrock_version": self.target,
            "native_compatibility": "unverified",
            "accuracy_percent": None,
            "coverage": coverage(self.catalog),
            "accuracy_note": "No native conformance corpus has established a compatibility percentage.",
            "overall": status,
            "content_sha256": self.catalog.hash,
            "seed": self.world.seed,
            "tick": self.world.tick,
            "assertions": deepcopy(self.assertions),
            "requirements": deepcopy(self.requirements),
            "untested_requirements": untested,
            "findings": self.catalog.findings.rows(),
            "operation_count": len(self.operations),
            "simulation_cpu_wall_seconds": self.elapsed,
            "performance_note": "Simulator elapsed time is not Minecraft FPS, memory use, or tick performance.",
            "frames": list(self.frames),
            "events": deepcopy(self.world.events[-100:]),
        }

    def export(self, destination):
        destination = Path(destination).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        if any(destination.iterdir()):
            raise ValueError("Evidence destination must be empty; existing reports are never overwritten")
        report = self.report()
        (destination / "report.json").write_text(
            json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
        )
        replay = {
            "seed": self.world.seed,
            "target_version": self.target,
            "requirements": [{"id": k, "text": v} for k, v in self.requirements.items()],
            "steps": self.operations,
            "stop_on_failure": False,
            "content_sha256": self.catalog.hash,
        }
        (destination / "replay.json").write_text(
            json.dumps(replay, indent=2, allow_nan=False), encoding="utf-8"
        )
        with zipfile.ZipFile(destination / "input.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in self.catalog.files.items():
                archive.writestr(name, data)
        for key, data in self.frames.items():
            (destination / f"{key}.png").write_bytes(data)
        escaped = html.escape(json.dumps(report, indent=2))
        images = "".join(f'<img src="{key}.png" alt="Independent simulator preview">' for key in self.frames)
        (destination / "report.html").write_text(
            '<!doctype html><meta charset="utf-8"><title>Bedrock Addon Tester</title>'
            "<style>body{font:16px system-ui;max-width:1000px;margin:40px auto;background:#14202e;color:#e0edf3;padding:20px}pre{white-space:pre-wrap}img{max-width:100%}</style>"
            f"<h1>Simulation: {html.escape(report['overall'])}</h1>"
            "<p>Independent simulation evidence. Native Bedrock compatibility and graphics fidelity remain unverified.</p>"
            f"{images}<pre>{escaped}</pre>",
            encoding="utf-8",
        )
        return {
            "directory": str(destination),
            "report": str(destination / "report.html"),
            "overall": report["overall"],
        }
