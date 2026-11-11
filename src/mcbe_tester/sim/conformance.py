"""Run explicit reference cases without a game or an AI service.

Built-in expectations are hand-authored from published contracts. They are not
native observations or a representative sample of all Bedrock behavior. Every
case, including unsupported and invalid cases, stays in the denominator.
"""

import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path

from . import PROFILE, VERSION
from .animations import channel
from .diagnostics import Unsupported
from .molang import evaluate
from .runner import Simulation

BUILTIN = Path(__file__).resolve().parents[1] / "assets/conformance.json"


def matches(actual, expected, tolerance):
    if type(actual) in (int, float) and type(expected) in (int, float):
        return math.isfinite(actual) and abs(actual - expected) <= tolerance
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(
            matches(a, b, tolerance) for a, b in zip(actual, expected, strict=True)
        )
    return type(actual) is type(expected) and actual == expected


def case_result(case):
    tolerance = case.get("absolute_tolerance", 1e-6)
    if type(tolerance) not in (int, float) or not 0 <= tolerance <= 0.01:
        raise ValueError("Absolute tolerance must be between zero and 0.01")
    kind = case["kind"]
    if kind == "molang":
        actual = evaluate(case["expression"], deepcopy(case.get("context", {})), {})
    elif kind == "animation_channel":
        actual = channel(case["value"], case["time"], {}, {}, case.get("default", [0, 0, 0])).tolist()
    elif kind == "scenario":
        # No external paths, plugins, downloads or arbitrary host commands in a suite.
        steps = case["scenario"].get("steps", [])
        if not isinstance(steps, list) or len(steps) > 100:
            raise ValueError("A reference scenario supports at most 100 steps")
        if sum(step.get("action", {}).get("ticks", 1) for step in steps) > 1000:
            raise ValueError("Reference scenario tick budget exceeded")
        sim = Simulation.from_files(case["files"], seed=case.get("seed", 0))
        try:
            report = sim.scenario(case["scenario"])["report"]
            status = report["overall"]
            return {
                "status": {"simulation_passed": "matched", "incomplete": "unsupported", "failed": "mismatch"}[
                    status
                ],
                "assertions": report["assertions"],
                "findings": report["findings"],
            }
        finally:
            sim.close()
    else:
        raise Unsupported(f"Reference case kind not implemented: {kind}")
    return {
        "status": "matched" if matches(actual, case["expected"], tolerance) else "mismatch",
        "actual": actual,
        "expected": case["expected"],
    }


def run_suite(suite=None):
    bundled = suite is None
    if bundled:
        suite = json.loads(BUILTIN.read_text(encoding="utf-8"))
    encoded = json.dumps(suite, sort_keys=True, allow_nan=False).encode()
    if len(encoded) > 2 * 1024 * 1024:
        raise ValueError("Conformance suite exceeds 2 MiB")
    if not isinstance(suite, dict) or suite.get("schema_version") != 1:
        raise ValueError("Conformance suite requires schema_version: 1")
    cases = suite.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 128:
        raise ValueError("Use 1–128 reference cases")
    identifiers = [c.get("id") if isinstance(c, dict) else None for c in cases]
    if any(not isinstance(k, str) or not k or len(k) > 128 for k in identifiers) or len(
        set(identifiers)
    ) != len(identifiers):
        raise ValueError("Cases require distinct nonempty string IDs (max 128 characters)")
    if sum(case.get("kind") == "scenario" for case in cases) > 16:
        raise ValueError("A reference suite supports at most 16 script scenarios")
    results = []
    for case in cases:
        try:
            result = case_result(case)
        except Unsupported as exc:
            result = {"status": "unsupported", "reason": str(exc)}
        except (
            ValueError,
            TypeError,
            KeyError,
            ArithmeticError,
            IndexError,
            AttributeError,
            RecursionError,
        ) as exc:
            result = {"status": "error", "reason": str(exc)}
        results.append(
            {
                "id": case["id"],
                "area": case.get("area", case.get("kind", "unknown")),
                "source": case.get("source"),
                **result,
            }
        )
    counts = {
        status: sum(r["status"] == status for r in results)
        for status in ("matched", "mismatch", "unsupported", "error")
    }
    areas = {}
    for result in results:
        key = str(result["area"])
        area = areas.setdefault(key, {"total": 0, **dict.fromkeys(counts, 0)})
        area["total"] += 1
        area[result["status"]] += 1
    return {
        "suite": suite.get("name", "user supplied reference suite"),
        "suite_sha256": hashlib.sha256(encoded).hexdigest(),
        "simulator_version": VERSION,
        "profile": PROFILE,
        "reference_basis": "published contracts; project-authored expectations"
        if bundled
        else "user supplied expectations; provenance unverified",
        "sources": suite.get("sources", {}),
        "total": len(results),
        **counts,
        "by_area": areas,
        "documented_contract_pass_percent": round(100 * counts["matched"] / len(results), 2)
        if bundled
        else None,
        "overall": "failed"
        if counts["mismatch"] or counts["error"]
        else "incomplete"
        if counts["unsupported"]
        else "contracts_matched",
        "native_compatibility": "unverified",
        "native_reference_cases": 0,
        "accuracy_percent": None,
        "representative_of_all_addons": False,
        "results": results,
    }
