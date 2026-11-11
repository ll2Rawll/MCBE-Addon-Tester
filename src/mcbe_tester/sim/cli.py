"""Cross-platform simulator CLI; runtime commands work offline without Minecraft."""

import argparse
import json
import sys
from pathlib import Path

from . import PROFILE, VERSION
from .runner import Simulation


def main():
    parser = argparse.ArgumentParser(
        description="Independent Bedrock add-on simulator. No Minecraft or AI API key required."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities")
    sub.add_parser("mcp", help="Run the simulator MCP tools over stdio")
    contract = sub.add_parser(
        "conformance", help="Check explicit published contracts, not native compatibility"
    )
    contract.add_argument("--suite", help="Optional JSON suite with independently supplied expectations")
    contract.add_argument("--out", help="Write a new JSON evidence file")
    contract.add_argument(
        "--allow-unsupported",
        action="store_true",
        help="Exit zero for declared coverage gaps; never ignore mismatches/errors",
    )
    for name in ("inspect", "explore", "render", "test"):
        command = sub.add_parser(name)
        command.add_argument("addon")
        command.add_argument("--seed", type=int, default=0)
        command.add_argument("--target-version", default="unspecified")
        if name in {"test", "render"}:
            command.add_argument("--scenario", required=name == "test")
            command.add_argument("--out", required=True)
        if name == "render":
            command.add_argument("--camera", choices=["overview", "player"], default="overview")
    args = parser.parse_args()
    if args.command == "capabilities":
        print(
            json.dumps(
                {
                    "version": VERSION,
                    "profile": PROFILE,
                    "requires_minecraft": False,
                    "requires_ai_api_key": False,
                    "native_compatibility": "unverified",
                },
                indent=2,
            )
        )
        return
    if args.command == "mcp":
        from .mcp import main as serve

        serve()
        return
    sim = None
    try:
        if args.command == "conformance":
            from .conformance import run_suite

            suite = None
            if args.suite:
                if Path(args.suite).stat().st_size > 2 * 1024 * 1024:
                    raise ValueError("Conformance suite exceeds 2 MiB")
                suite = json.loads(Path(args.suite).read_text(encoding="utf-8"))
            result = run_suite(suite)
            encoded = json.dumps(result, indent=2, allow_nan=False)
            if args.out:
                destination = Path(args.out)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("x", encoding="utf-8") as handle:
                    handle.write(encoded + "\n")
            print(encoded)
            sys.exit(
                1
                if result["overall"] == "failed"
                else 2
                if result["overall"] == "incomplete" and not args.allow_unsupported
                else 0
            )
        sim = Simulation.open(args.addon, args.seed, args.target_version)
        if args.command == "inspect":
            result = sim.catalog.summary()
        elif args.command == "explore":
            result = sim.explore()
        else:
            if args.scenario:
                spec = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
                if spec.get("content_sha256") and spec["content_sha256"] != sim.catalog.hash:
                    raise ValueError("Replay add-on hash mismatch")
                replay_target = (
                    spec.get("target_version", "unspecified")
                    if args.target_version == "unspecified"
                    else args.target_version
                )
                replay_seed = spec.get("seed", args.seed)
                if replay_seed != args.seed or replay_target != args.target_version:
                    sim.close()
                    sim = Simulation.open(args.addon, replay_seed, replay_target)
                sim.scenario(spec)
            else:
                for i, identifier in enumerate(sim.catalog.blocks):
                    if i >= 16:
                        break
                    sim.action(
                        {
                            "type": "set_block",
                            "identifier": identifier,
                            "position": [(i % 4) * 2, 0, (i // 4) * 2],
                        }
                    )
                for i, identifier in enumerate(sim.catalog.entities):
                    if i >= 8:
                        break
                    sim.action({"type": "spawn", "identifier": identifier, "position": [i * 2, 0, -2]})
            if args.command == "render":
                metadata, data = sim.screenshot(camera=args.camera)
                destination = Path(args.out)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                result = {"image": str(destination), **metadata}
            else:
                sim.screenshot()
                result = sim.export(args.out)
        print(json.dumps(result, indent=2, allow_nan=False))
        if args.command == "test":
            sys.exit({"simulation_passed": 0, "incomplete": 2, "failed": 1}[result["overall"]])
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(1)
    finally:
        if sim is not None:
            sim.close()


if __name__ == "__main__":
    main()
