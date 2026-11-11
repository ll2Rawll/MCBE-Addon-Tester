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
    try:
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


if __name__ == "__main__":
    main()
