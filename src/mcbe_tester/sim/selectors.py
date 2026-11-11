"""Bounded Bedrock command tokenization, coordinates and target selection."""

import json
import math
import re

from .diagnostics import Unsupported


def split_balanced(value, separator=None):
    parts, current, stack, quoted, escaped = [], [], [], False, False
    for char in value:
        if quoted:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in "[{":
            stack.append(char)
        elif char in "]}":
            if not stack or stack.pop() != {"]": "[", "}": "{"}[char]:
                raise ValueError("Unbalanced command brackets")
        if not stack and (char.isspace() if separator is None else char == separator):
            if current:
                parts.append("".join(current).strip())
                current = []
            elif separator is not None:
                raise ValueError("Empty command entry")
        else:
            current.append(char)
    if quoted or stack:
        raise ValueError("Unclosed command string/brackets")
    if current:
        parts.append("".join(current).strip())
    return parts


def literal(value):
    return json.loads(value) if value.startswith('"') else value


def pairs(value):
    if value.rstrip().endswith(","):
        raise ValueError("Empty trailing command entry")
    result = []
    for entry in split_balanced(value, ",") if value.strip() else []:
        if "=" not in entry:
            raise ValueError("Expected command key=value")
        name, content = entry.split("=", 1)
        result.append((literal(name.strip()), content.strip()))
    return result


def number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Command number must be finite")
    return result


def in_range(value, text):
    negate = text.startswith("!")
    text = text.removeprefix("!")
    if not re.fullmatch(r"-?\d+(?:\.\.-?\d*)?|\.\.-?\d+", text):
        raise ValueError("Invalid integer score range")
    if ".." in text:
        low, high = text.split("..")
        low, high = int(low) if low else -math.inf, int(high) if high else math.inf
        if low > high:
            raise ValueError("Inverted integer range")
    else:
        low = high = int(text)
    return False if value is None else (low <= value <= high) != negate


def position(tokens, origin, rotation=(0, 0)):
    if len(tokens) != 3:
        raise ValueError("Three coordinates required")
    if any(token.startswith("^") for token in tokens):
        if not all(token.startswith("^") for token in tokens):
            raise ValueError("Local coordinates cannot mix with absolute/relative coordinates")
        left, up, forward = [number(token[1:] or "0") for token in tokens]
        yaw, pitch = map(math.radians, rotation)
        basis = (
            (math.cos(yaw), 0, math.sin(yaw)),
            (-math.sin(yaw) * math.sin(pitch), math.cos(pitch), math.cos(yaw) * math.sin(pitch)),
            (-math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch)),
        )
        result = [
            origin[i] + sum(v * b[i] for v, b in zip((left, up, forward), basis, strict=True))
            for i in range(3)
        ]
    else:
        result = [
            origin[i] + number(token[1:] or "0") if token.startswith("~") else number(token)
            for i, token in enumerate(tokens)
        ]
    if any(not math.isfinite(v) or abs(v) > 128 for v in result):
        raise ValueError("Command position outside simulation bounds")
    return result


def selector(world, text, executor="player", origin=None, validate_only=False):
    text = literal(text)
    if not text.startswith("@"):
        if text in {"player", world.player["name"]}:
            return ["player"]
        return [text] if text in world.entities else []
    match = re.fullmatch(r"@([aeprs])(?:\[(.*)\])?", text)
    if not match:
        raise Unsupported("Selector not implemented: " + text)
    kind, raw = match.groups()
    options = pairs(raw or "")
    if kind in {"a", "p"} and any(key == "type" for key, _ in options):
        raise ValueError("type is unavailable with @a and @p")
    for name in ("type", "name"):
        if sum(key == name and not value.startswith("!") for key, value in options) > 1:
            raise ValueError(f"Only one non-negated {name} selector is allowed")
    allowed = {
        "type",
        "name",
        "tag",
        "family",
        "r",
        "rm",
        "x",
        "y",
        "z",
        "dx",
        "dy",
        "dz",
        "c",
        "scores",
        "m",
    }
    unknown = {key for key, _ in options} - allowed
    if unknown:
        raise Unsupported("Selector options: " + ", ".join(sorted(unknown)))
    scalar = {}
    repeatable = {"type", "tag", "name", "family"}
    for key, value in options:
        if key in scalar and key not in repeatable:
            raise ValueError("Duplicate selector option: " + key)
        scalar[key] = value
    origin = list(
        origin if origin is not None else world.entity_data(executor)["position"] if executor else (0, 0, 0)
    )
    for i, axis in enumerate("xyz"):
        if axis in scalar:
            origin[i] = number(scalar[axis])
    radius = number(scalar.get("r", "128000"))
    minimum = number(scalar.get("rm", "0"))
    if radius < 0 or minimum < 0:
        raise ValueError("Selector radius cannot be negative")
    scores = scalar.get("scores", "{}")
    if not scores.startswith("{") or not scores.endswith("}"):
        raise ValueError("Selector scores require an object")
    score_filters = pairs(scores[1:-1])
    for _, value in score_filters:
        in_range(None, value)  # Validate ranges even when no target exists.
    count = int(scalar["c"]) if "c" in scalar else 1 if kind in {"p", "r"} else None
    if count == 0 or count is not None and abs(count) > 128:
        raise ValueError("Selector count must be nonzero and at most 128")
    for axis in "xyz":
        if "d" + axis in scalar:
            number(scalar["d" + axis])
    if "m" in scalar and literal(scalar["m"].removeprefix("!")) not in {
        "0",
        "1",
        "2",
        "s",
        "c",
        "a",
        "survival",
        "creative",
        "adventure",
        "spectator",
    }:
        raise ValueError("Invalid selector game mode")
    if validate_only:
        return []
    players_only = kind in {"a", "p"} or kind == "r" and "type" not in scalar
    candidates = [("player", world.player), *world.entities.items()]
    if kind == "s":
        candidates = [(executor, world.entity_data(executor))] if executor else []
    result = []
    for key, entity in candidates:
        if players_only and key != "player" or kind != "a" and entity.get("health", 1) <= 0:
            continue
        distance = math.dist(origin, entity["position"])
        if not minimum <= distance <= radius:
            continue
        accepted = True
        for name, raw_value in options:
            if name not in repeatable | {"m"}:
                continue
            negate = raw_value.startswith("!")
            value = literal(raw_value.removeprefix("!"))
            if name == "type":
                value = value if ":" in value else "minecraft:" + value
                selected = entity["identifier"] == value
            elif name == "name":
                selected = entity.get("name", entity.get("nameTag", "")) == value
            elif name == "tag":
                selected = value in entity.get("tags", []) if value else not entity.get("tags", [])
            elif name == "family":
                selected = value in entity.get("components", {}).get("minecraft:type_family", {}).get(
                    "family", []
                )
            else:
                if key != "player":
                    accepted = False
                    continue
                modes = {
                    "0": "survival",
                    "1": "creative",
                    "2": "adventure",
                    "s": "survival",
                    "c": "creative",
                    "a": "adventure",
                }
                selected = key == "player" and entity["mode"] == modes.get(value, value)
            accepted &= selected != negate
        for objective, value in score_filters:
            score = world.scoreboard.objectives.get(objective, {}).get("scores", {}).get(key)
            accepted &= in_range(score, value)
        if any("d" + axis in scalar for axis in "xyz"):
            shape = entity.get("components", {}).get("minecraft:collision_box", {})
            width, height = shape.get("width", 0.6), shape.get("height", 1.8)
            low = [entity["position"][i] - (width / 2 if i != 1 else 0) for i in range(3)]
            high = [low[i] + (height if i == 1 else width) for i in range(3)]
            for i, axis in enumerate("xyz"):
                delta = number(scalar.get("d" + axis, "0"))
                a, b = origin[i] + min(0, delta), origin[i] + max(0, delta) + 1
                accepted &= low[i] < b and high[i] > a
        if accepted:
            result.append((key, distance))
    if kind == "r":
        world.random.shuffle(result)
    elif count is not None or kind == "p":
        result.sort(key=lambda value: (value[1], value[0]), reverse=count is not None and count < 0)
    if count is not None:
        result = result[: abs(count)]
    return [key for key, _ in result]
