"""Evaluate labelled test runs. This does NOT manufacture a measured reliability score."""

from .util import read_json


def evaluate(path):
    data = read_json(path)
    cases = data.get("cases", [])
    if not cases or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Benchmark needs cases with unique IDs")
    tp = fp = good = buggy = inconclusive = 0
    details = []
    for case in cases:
        if type(case.get("buggy")) is not bool or not case.get("description"):
            raise ValueError("Each case needs a boolean buggy label and a description")
        report = read_json(path.parent / case["report"])
        outcome = report.get("overall")
        if outcome not in {"failed", "incomplete", "passed_for_defined_scope"}:
            raise ValueError("Case does not refer to an MCBE tester report")
        if case["buggy"]:
            buggy += 1
            tp += outcome == "failed"
        else:
            good += 1
            fp += outcome == "failed"
        inconclusive += outcome == "incomplete"
        details.append({"id": case["id"], "buggy": case["buggy"], "outcome": outcome})
    return {
        "cases": details,
        "buggy_cases": buggy,
        "known_good_cases": good,
        "detected_buggy_cases": tp,
        "false_alarms": fp,
        "inconclusive_cases": inconclusive,
        "bug_detection_percent": round(100 * tp / buggy, 2) if buggy else None,
        "false_alarm_percent": round(100 * fp / good, 2) if good else None,
        "eligible_for_target_claim": bool(buggy and good and not inconclusive),
        "limitation": "Describes this labelled benchmark only. Inconclusive buggy cases remain in the denominator. "
        "Requires independently verified labels and representative defects; not a general accuracy guarantee.",
    }
