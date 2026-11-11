import argparse
import json
import sys
from pathlib import Path

from .lab import Lab
from .util import write_json


def parser():
    p = argparse.ArgumentParser(
        description="MCBE Addon Tester: real-client controls and evidence-based checks"
    )
    p.add_argument("--root", default=".mcbe-lab", help="Private local artifacts directory")
    subs = p.add_subparsers(dest="command", required=True)
    for name in ("doctor", "logs", "report", "launch", "open-world", "stop", "versions"):
        subs.add_parser(name)
    s = subs.add_parser("start")
    s.add_argument("plan")
    s.add_argument("addon")
    s.add_argument("--log-dir", action="append")
    s = subs.add_parser("bind")
    s.add_argument("--hwnd", type=int)
    s = subs.add_parser("capture")
    s.add_argument("--name", default="screen")
    s = subs.add_parser("action")
    s.add_argument("name", choices=["focus", "press", "click", "look", "scroll", "type_text", "wait"])
    s.add_argument("--args", default="{}", help="JSON action arguments")
    s = subs.add_parser("prepare-world")
    s.add_argument("template")
    s.add_argument(
        "--api-version",
        help="Optional observer @minecraft/server version; omission gives an uninstrumented world",
    )
    s = subs.add_parser("run")
    s.add_argument("scenario")
    s = subs.add_parser("record")
    s.add_argument("requirement")
    s.add_argument("status", choices=["passed", "failed", "blocked", "inconclusive"])
    s.add_argument("--observed", required=True)
    s.add_argument("--evidence", nargs="*", default=[])
    s.add_argument("--method", choices=["ai-review", "human-review"], default="human-review")
    s = subs.add_parser("baseline")
    s.add_argument("screenshot_id")
    s.add_argument("destination")
    s.add_argument("--reason", required=True)
    s.add_argument("--reviewer", choices=["human", "ai"], default="human")
    s = subs.add_parser("compare")
    s.add_argument("requirement")
    s.add_argument("actual_id")
    s.add_argument("baseline")
    s.add_argument("--tolerance", type=int, default=12)
    s.add_argument("--max-changed-fraction", type=float, default=0.01)
    s = subs.add_parser("external")
    s.add_argument("tool", choices=["mct", "bds"])
    s.add_argument("input")
    s.add_argument("--tag")
    s.add_argument("--expected-tests", type=int)
    s.add_argument("--bds-version")
    s.add_argument("--timeout", type=int, default=300)
    s = subs.add_parser("benchmark")
    s.add_argument("cases")
    s.add_argument("--out", default=".mcbe-lab/benchmark.json")
    return p


def main():
    args = parser().parse_args()
    lab = Lab(args.root)
    if args.command == "stop":
        (lab.root / "STOP").touch()
        print(json.dumps({"status": "stop_requested"}))
        return
    try:
        with lab.exclusive():
            match args.command:
                case "versions":
                    from .versions import latest_builds

                    result = latest_builds()
                case "doctor":
                    result = lab.doctor()
                case "start":
                    result = lab.start(args.plan, args.addon, args.log_dir)
                case "bind":
                    result = lab.bind(args.hwnd)
                case "capture":
                    result = lab.capture(args.name)
                case "action":
                    result = lab.action(args.name, **json.loads(args.args))
                case "logs":
                    result = lab.read_logs()
                case "report":
                    result = lab.report()
                case "launch":
                    result = lab.launch_game()
                case "open-world":
                    result = lab.open_world()
                case "prepare-world":
                    result = lab.prepare_world(args.template, args.api_version)
                case "run":
                    result = lab.scenario(args.scenario)
                case "record":
                    result = lab.record(
                        args.requirement, args.status, args.observed, args.evidence, args.method
                    )
                case "baseline":
                    result = lab.approve_baseline(
                        args.screenshot_id, args.destination, args.reason, args.reviewer
                    )
                case "compare":
                    result = lab.compare_image(
                        args.requirement,
                        args.actual_id,
                        args.baseline,
                        args.tolerance,
                        args.max_changed_fraction,
                    )
                case "external":
                    result = lab.external(
                        args.tool, args.input, args.tag, args.expected_tests, args.bds_version, args.timeout
                    )
                case "benchmark":
                    from .benchmark import evaluate

                    result = evaluate(Path(args.cases))
                    write_json(args.out, result)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            if result.get("overall") in {"failed", "incomplete"} or result.get("status") in {
                "failed",
                "blocked",
            }:
                sys.exit(1)
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
