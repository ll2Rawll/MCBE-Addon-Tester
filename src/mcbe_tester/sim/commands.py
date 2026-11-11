"""Bounded command interpreter. Commands mutate the simulator, never the OS."""

import math
import re
import shlex

from .diagnostics import Unsupported


def selector(world, text, executor="player"):
    if text == "@s":
        return [executor] if executor else []
    match = re.fullmatch(r"@([aep])(?:\[(.*)\])?", text)
    if not match:
        if text.startswith("@"):
            raise Unsupported("Selector not implemented: " + text)
        if text in {"player", world.player["name"]}:
            return ["player"]
        if text in world.entities:
            return [text]
        return []
    kind, raw = match.groups()
    options = {}
    if executor:
        options["location"] = world.entity_data(executor)["position"]
    for entry in raw.split(",") if raw else []:
        key, value = entry.split("=", 1)
        if key == "type":
            if value.startswith("!"):
                options.setdefault("excludeTypes", []).append(value[1:])
            else:
                options["type"] = value
        elif key == "tag":
            options.setdefault("excludeTags" if value.startswith("!") else "tags", []).append(
                value.lstrip("!")
            )
        elif key in {"r", "rm"}:
            options["maxDistance" if key == "r" else "minDistance"] = float(value)
        elif key == "c":
            options["closest" if int(value) >= 0 else "farthest"] = abs(int(value))
        elif key == "name":
            options["name"] = value
        else:
            raise Unsupported("Selector option: " + key)
    if kind == "p":
        options["closest"] = 1
    return world.query_entities(options, players_only=kind in {"a", "p"})


def position(tokens, origin):
    if len(tokens) != 3:
        raise ValueError("Three coordinates required")
    result = []
    for i, value in enumerate(tokens):
        if value.startswith("^"):
            raise Unsupported("Local command coordinates are not modeled")
        result.append(origin[i] + float(value[1:] or 0) if value.startswith("~") else float(value))
    if any(not math.isfinite(v) or abs(v) > 128 for v in result):
        raise ValueError("Command position outside simulation bounds")
    return result


def execute(world, command, executor="player", depth=0, origin=None):
    if not isinstance(command, str) or len(command) > 4096 or depth > 8:
        raise ValueError("Command length/recursion limit")
    args = shlex.split(command.lstrip("/"))
    if not args:
        raise ValueError("Empty command")
    name, tail = args[0], args[1:]
    if origin is None:
        origin = world.entity_data(executor)["position"] if executor else [0, 0, 0]
    success = 0
    if name == "say":
        world.log("chat", text=" ".join(tail))
        success = 1
    elif name == "setblock" and len(tail) in {4, 5}:
        pos = [math.floor(v) for v in position(tail[:3], origin)]
        identifier = tail[3] if ":" in tail[3] else "minecraft:" + tail[3]
        mode = tail[4] if len(tail) == 5 else "replace"
        if mode not in {"replace", "keep"}:
            raise Unsupported("setblock mode: " + mode)
        if mode != "keep" or tuple(pos) not in world.blocks:
            world.put(pos, identifier)
            success = 1
    elif name == "fill" and len(tail) == 7:
        a, b = position(tail[:3], origin), position(tail[3:6], origin)
        lows, highs = (
            [math.floor(min(a[i], b[i])) for i in range(3)],
            [math.floor(max(a[i], b[i])) for i in range(3)],
        )
        volume = math.prod(highs[i] - lows[i] + 1 for i in range(3))
        if volume > 1024 or lows[1] < 0:
            raise ValueError("Fill exceeds bounded world/1024-block limit")
        identifier = tail[6] if ":" in tail[6] else "minecraft:" + tail[6]
        world.catalog.block(identifier)
        if identifier != "minecraft:air" and len(world.blocks) + volume > 2048:
            raise ValueError("Fill capacity limit")
        for x in range(lows[0], highs[0] + 1):
            for y in range(lows[1], highs[1] + 1):
                for z in range(lows[2], highs[2] + 1):
                    world.put([x, y, z], identifier)
        success = volume
    elif name == "give" and 2 <= len(tail) <= 3:
        targets = selector(world, tail[0], executor)
        if any(target != "player" for target in targets):
            raise ValueError("give requires players")
        for _ in targets:
            world.give(tail[1], int(tail[2]) if len(tail) == 3 else 1)
        success = len(targets)
    elif name == "summon" and len(tail) in {1, 4}:
        world.spawn(tail[0], position(tail[1:4], origin) if len(tail) == 4 else origin)
        success = 1
    elif name in {"tp", "teleport"} and len(tail) in {3, 4}:
        targets = [executor] if len(tail) == 3 else selector(world, tail[0], executor)
        pos = position(tail[-3:], origin)
        for target in targets:
            world.entity_api("teleport", target, pos)
        success = len(targets)
    elif name == "kill" and len(tail) <= 1:
        targets = selector(world, tail[0], executor) if tail else [executor]
        for target in targets:
            world.entity_api("kill", target)
        success = len(targets)
    elif name == "tag" and len(tail) == 3:
        if tail[1] not in {"add", "remove"}:
            raise ValueError("Unknown tag operation")
        targets = selector(world, tail[0], executor)
        for target in targets:
            success += bool(world.entity_api("addTag" if tail[1] == "add" else "removeTag", target, tail[2]))
    elif name == "event" and len(tail) == 3 and tail[0] == "entity":
        targets = selector(world, tail[1], executor)
        for target in targets:
            world.entity_event(target, tail[2])
        success = len(targets)
    elif name == "gamemode" and 1 <= len(tail) <= 2:
        modes = {
            "0": "survival",
            "1": "creative",
            "2": "adventure",
            "s": "survival",
            "c": "creative",
            "a": "adventure",
        }
        mode = modes.get(tail[0], tail[0])
        targets = selector(world, tail[1], executor) if len(tail) == 2 else [executor]
        for target in targets:
            world.entity_api("mode", target, mode)
        success = len(targets)
    elif name == "scoreboard" and len(tail) >= 3:
        if tail[:2] == ["objectives", "add"] and len(tail) in {4, 5} and tail[3] == "dummy":
            world.scoreboard.api("create", tail[2], tail[4] if len(tail) == 5 else tail[2])
            success = 1
        elif tail[:2] == ["objectives", "remove"] and len(tail) == 3:
            success = int(world.scoreboard.api("remove", tail[2]))
        elif tail[0] == "players" and len(tail) == 5 and tail[1] in {"set", "add", "remove"}:
            participants = selector(world, tail[2], executor) if tail[2].startswith("@") else [tail[2]]
            value = int(tail[4]) * (-1 if tail[1] == "remove" else 1)
            for target in participants:
                world.scoreboard.api("set" if tail[1] == "set" else "add", tail[3], target, value)
            success = len(participants)
        else:
            raise Unsupported("Scoreboard command form")
    elif name == "execute" and len(tail) >= 4 and tail[0] == "as" and tail[2] == "run":
        for target in selector(world, tail[1], executor):
            success += execute(world, shlex.join(tail[3:]), target, depth + 1, origin=origin)["successCount"]
    else:
        raise Unsupported(f"Command or syntax not implemented: {command}")
    world.log("command", command=command, successCount=success)
    return {"successCount": success}
