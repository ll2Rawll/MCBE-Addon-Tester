def classify(tool, exit_code, payload, expected_tests=None, bds_version=None):
    if tool == "mct":
        if (
            payload.get("command") != "validate"
            or not payload.get("projects")
            or type(payload.get("errors")) is not int
        ):
            raise ValueError("Unrecognized MCT validation report")
        errors = payload["errors"]
        if errors:
            status = "failed"
        elif exit_code == 0:
            status = "passed"
        else:
            status = "blocked"
        return {
            "status": status,
            "errors": errors,
            "warnings": payload.get("warnings"),
            "reason": "MCT validation only; inspect rule applicability and warnings",
        }
    verdicts = payload["verdicts"]
    if (
        not isinstance(verdicts, list)
        or not verdicts
        or len(verdicts) != expected_tests
        or payload.get("bdsVersion") != bds_version
        or payload.get("infraError")
    ):
        return {"status": "blocked", "reason": "Missing tests, wrong build, or BDS infrastructure failure"}
    outcomes = [v["outcome"] for v in verdicts]
    if any(o not in {"pass", "fail", "absent", "unobservable"} for o in outcomes):
        raise ValueError("Unknown BDS verdict")
    if payload.get("scriptErrors") or any(o != "pass" for o in outcomes):
        return {
            "status": "failed",
            "reason": "GameTest failures or uncaught script errors",
            "tests": len(verdicts),
        }
    return {
        "status": "passed" if exit_code == 0 else "blocked",
        "tests": len(verdicts),
        "reason": "Results apply only to these registered tests on this BDS build",
    }
