"""Bounded command/function execution with explicit executor, position and rotation.

Commands act only on the independent world. No shell, native game, or host input
is involved. Missing command forms remain Unsupported, including in function
files. Native permissions, startup ticks and complete result/error parity are
outside this profile.
"""

import json
import math
import operator
from dataclasses import dataclass, replace

from .diagnostics import Unsupported
from .selectors import in_range, literal, number, pairs, position, selector, split_balanced

ROOTS = {
    "say",
    "setblock",
    "fill",
    "give",
    "summon",
    "tp",
    "teleport",
    "kill",
    "tag",
    "event",
    "gamemode",
    "scoreboard",
    "execute",
    "function",
    "testfor",
    "testforblock",
}
COMPARE = {"=": operator.eq, "<": operator.lt, ">": operator.gt, "<=": operator.le, ">=": operator.ge}
SCORE_OP = {
    "+=": operator.add,
    "-=": operator.sub,
    "*=": operator.mul,
    "/=": operator.floordiv,
    "%=": operator.mod,
    "<": min,
    ">": max,
}


@dataclass(frozen=True)
class Context:
    executor: str | None
    origin: tuple
    rotation: tuple


def block_spec(identifier, remaining):
    identifier = literal(identifier)
    identifier = identifier if ":" in identifier else "minecraft:" + identifier
    states = {}
    remaining = list(remaining)
    if remaining and remaining[0].startswith("["):
        text = remaining.pop(0)
        if not text.endswith("]"):
            raise ValueError("Block state list must close with ]")
        for key, value in pairs(text[1:-1]):
            if key in states:
                raise ValueError("Duplicate block state")
            states[key] = json.loads(value)
            if type(states[key]) not in (str, int, bool):
                raise ValueError("Block states must be strings, integers or booleans")
    return identifier, states, remaining


def score_holders(world, target, context, objective=None):
    if target.startswith("@"):
        return selector(world, target, context.executor, context.origin)
    if target == "*":
        if objective is None:
            return sorted({k for obj in world.scoreboard.objectives.values() for k in obj["scores"]})
        return list(world.scoreboard.api("get", objective)["scores"])
    return [literal(target)]


def single_score(world, holder, objective, context):
    holders = score_holders(world, holder, context, objective)
    if len(holders) != 1:
        raise ValueError("Score condition requires exactly one score holder")
    return world.scoreboard.api("score", literal(objective), holders[0])


def parse_execute(tail):
    """Parse the whole chain before selection can short-circuit its evaluation."""
    clauses = []
    i = 0
    while i < len(tail):
        op = tail[i]
        if op == "run":
            leaf = tail[i + 1 :]
            if not leaf or leaf[0] not in ROOTS:
                raise Unsupported("Execute run command is not modeled")
            if leaf[0] == "execute":
                # Bound recursion separately when executing; parsing is also bounded.
                if sum(t == "execute" for t in leaf) > 16:
                    raise Unsupported("Execute nesting limit")
                parse_execute(leaf[1:])
            return clauses, leaf
        if op in {"as", "at", "in", "align"}:
            count = 2
        elif op in {"positioned", "rotated"}:
            count = 3 if tail[i + 1 : i + 2] == ["as"] else 4 if op == "positioned" else 3
        elif op == "facing":
            if tail[i + 1 : i + 2] == ["entity"]:
                raise Unsupported("Facing entity/eye anchors require a native height contract")
            count = 4
        elif op in {"if", "unless"}:
            kind = tail[i + 1 : i + 2]
            if kind == ["entity"]:
                count = 3
            elif kind == ["block"]:
                count = 6
                if i + count < len(tail) and tail[i + count].startswith("["):
                    count += 1
            elif kind == ["score"]:
                count = 6 if tail[i + 4 : i + 5] == ["matches"] else 7
            else:
                raise Unsupported("Execute condition not modeled: " + str(kind))
        else:
            raise Unsupported("Execute clause not modeled: " + op)
        if i + count > len(tail):
            raise ValueError("Incomplete execute clause: " + op)
        if op == "in" and tail[i + 1] not in {"overworld", "minecraft:overworld"}:
            raise Unsupported("Only the simulator overworld is modeled")
        if op == "align" and (
            not tail[i + 1] or set(tail[i + 1]) - set("xyz") or len(set(tail[i + 1])) != len(tail[i + 1])
        ):
            raise ValueError("Align axes must be distinct x/y/z")
        clauses.append(tail[i : i + count])
        i += count
    if not clauses or clauses[-1][0] not in {"if", "unless"}:
        raise ValueError("Execute must end in run or a condition")
    return clauses, None


def condition(world, clause, ctx):
    kind = clause[1]
    if kind == "entity":
        result = bool(selector(world, clause[2], ctx.executor, ctx.origin))
    elif kind == "block":
        pos = tuple(math.floor(v) for v in position(clause[2:5], ctx.origin, ctx.rotation))
        identifier, states, extra = block_spec(clause[5], clause[6:])
        if extra:
            raise ValueError("Unexpected block condition arguments")
        world.catalog.block(identifier, states)
        block = world.blocks.get(pos, {"identifier": "minecraft:air", "states": {}})
        result = block["identifier"] == identifier and all(
            block["states"].get(k) == v for k, v in states.items()
        )
    else:
        left = single_score(world, clause[2], clause[3], ctx)
        if clause[4] == "matches":
            result = in_range(left, clause[5])
        else:
            if clause[4] not in COMPARE:
                raise ValueError("Invalid score comparison")
            right = single_score(world, clause[5], clause[6], ctx)
            result = left is not None and right is not None and COMPARE[clause[4]](left, right)
    return bool(result) != (clause[0] == "unless")


def chain(world, tail, context, budget, depth):
    clauses, leaf = parse_execute(tail)
    for clause in clauses:
        target = None
        if clause[0] in {"as", "at"}:
            target = clause[1]
        elif clause[0] in {"positioned", "rotated"} and clause[1] == "as":
            target = clause[2]
        elif clause[0] in {"if", "unless"} and clause[1] == "entity":
            target = clause[2]
        if target is not None:
            selector(world, target, context.executor, context.origin, validate_only=True)
    contexts = [context]
    for clause in clauses:
        op = clause[0]
        next_contexts = []
        for ctx in contexts:
            if op in {"if", "unless"}:
                if condition(world, clause, ctx):
                    next_contexts.append(ctx)
            elif op == "as":
                next_contexts.extend(
                    replace(ctx, executor=key) for key in selector(world, clause[1], ctx.executor, ctx.origin)
                )
            elif op == "at" or op in {"positioned", "rotated"} and clause[1] == "as":
                target = clause[1] if op == "at" else clause[2]
                for key in selector(world, target, ctx.executor, ctx.origin):
                    entity = world.entity_data(key)
                    new_origin = tuple(entity["position"]) if op in {"at", "positioned"} else ctx.origin
                    new_rotation = (
                        (entity.get("yaw", 0), entity.get("pitch", 0))
                        if op in {"at", "rotated"}
                        else ctx.rotation
                    )
                    next_contexts.append(replace(ctx, origin=new_origin, rotation=new_rotation))
            elif op == "positioned":
                next_contexts.append(
                    replace(ctx, origin=tuple(position(clause[1:4], ctx.origin, ctx.rotation)))
                )
            elif op == "rotated":
                rotation = tuple(
                    ctx.rotation[i] + number(v[1:] or "0") if v.startswith("~") else number(v)
                    for i, v in enumerate(clause[1:3])
                )
                next_contexts.append(replace(ctx, rotation=rotation))
            elif op == "facing":
                destination = position(clause[1:4], ctx.origin, ctx.rotation)
                x, y, z = [destination[i] - ctx.origin[i] for i in range(3)]
                next_contexts.append(
                    replace(
                        ctx,
                        rotation=(
                            math.degrees(math.atan2(-x, z)),
                            math.degrees(math.atan2(-y, math.hypot(x, z))),
                        ),
                    )
                )
            elif op == "align":
                if not clause[1] or set(clause[1]) - set("xyz") or len(set(clause[1])) != len(clause[1]):
                    raise ValueError("Align axes must be distinct x/y/z")
                next_contexts.append(
                    replace(
                        ctx,
                        origin=tuple(
                            math.floor(v) if axis in clause[1] else v
                            for axis, v in zip("xyz", ctx.origin, strict=True)
                        ),
                    )
                )
            elif op == "in":
                if clause[1] not in {"overworld", "minecraft:overworld"}:
                    raise Unsupported("Only the simulator overworld is available")
                next_contexts.append(ctx)
        if len(next_contexts) > 128:
            raise Unsupported("Execute branch budget exceeded")
        contexts = next_contexts
    return sum(run_tokens(world, leaf, ctx, budget, depth + 1) for ctx in contexts) if leaf else len(contexts)


def scoreboard_command(world, tail, ctx):
    board = world.scoreboard
    if tail[:2] == ["objectives", "add"] and len(tail) in {4, 5} and tail[3] == "dummy":
        board.api("create", literal(tail[2]), literal(tail[4]) if len(tail) == 5 else literal(tail[2]))
        return 1
    if tail[:2] == ["objectives", "remove"] and len(tail) == 3:
        return int(board.api("remove", literal(tail[2])))
    if tail[:2] == ["objectives", "list"] and len(tail) == 2:
        return len(board.objectives)
    if tail[:1] != ["players"] or len(tail) < 3:
        raise Unsupported("Scoreboard command form")
    op = tail[1]
    if op in {"set", "add", "remove"} and len(tail) == 5:
        objective = literal(tail[3])
        targets = score_holders(world, tail[2], ctx, objective)
        value = int(tail[4]) * (-1 if op == "remove" else 1)
        for target in targets:
            board.api("set" if op == "set" else "add", objective, target, value)
        return len(targets)
    if op == "reset" and len(tail) in {3, 4}:
        objectives = [literal(tail[3])] if len(tail) == 4 else list(board.objectives)
        count = 0
        for objective in objectives:
            for target in score_holders(world, tail[2], ctx, objective):
                count += board.api("removeParticipant", objective, target)
        return count
    if op == "test" and len(tail) in {5, 6}:
        minimum = tail[4] if tail[4] != "*" else str(-(2**31))
        maximum = tail[5] if len(tail) == 6 and tail[5] != "*" else str(2**31 - 1)
        return sum(
            in_range(board.api("score", literal(tail[3]), key), minimum + ".." + maximum)
            for key in score_holders(world, tail[2], ctx, literal(tail[3]))
        )
    if op == "operation" and len(tail) == 7:
        target_objective, operation, source_objective = literal(tail[3]), tail[4], literal(tail[6])
        if operation not in {"=", "+=", "-=", "*=", "/=", "%=", "<", ">", "><"}:
            raise ValueError("Unknown scoreboard operation")
        targets = score_holders(world, tail[2], ctx, target_objective)
        sources = score_holders(world, tail[5], ctx, source_objective)
        count = 0
        for target in targets:
            for source in sources:
                left = board.api("score", target_objective, target)
                right = board.api("score", source_objective, source)
                if right is None:
                    continue
                left = left if left is not None else 0
                if operation in {"/=", "%="} and right == 0:
                    continue  # Published contract: division by zero leaves the score unchanged.
                value = right if operation in {"=", "><"} else SCORE_OP[operation](left, right)
                board.api("set", target_objective, target, value)
                if operation == "><":
                    board.api("set", source_objective, source, left)
                count += 1
        return count
    raise Unsupported("Scoreboard command form")


def run_function(world, name, ctx, budget, depth):
    name = literal(name)
    if name not in world.catalog.functions:
        raise Unsupported("Function not supplied: " + name)
    count = 0
    for line, command in world.catalog.functions[name]:
        try:
            count += run_tokens(world, split_balanced(command), ctx, budget, depth + 1)
        except Unsupported:
            raise
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            # Native functions continue after ordinary command failures. Preserve
            # the error in evidence instead of silently discarding it.
            world.catalog.findings.error("function-command", f"{name}:{line}", str(exc))
    return count


def run_tokens(world, args, ctx, budget, depth):
    if depth > 32:
        raise Unsupported("Command/function recursion depth limit")
    budget[0] += 1
    if budget[0] > 10000:
        raise Unsupported("10,000-command execution budget exceeded")
    if not args:
        raise ValueError("Empty command")
    name, tail = args[0], args[1:]

    def select(target):
        return selector(world, target, ctx.executor, ctx.origin)

    def coordinate(values):
        return position(values, ctx.origin, ctx.rotation)

    success = 0
    if name == "execute":
        success = chain(world, tail, ctx, budget, depth)
    elif name == "function" and len(tail) == 1:
        success = run_function(world, tail[0], ctx, budget, depth)
    elif name == "say":
        world.log("chat", text=" ".join(map(literal, tail)))
        success = 1
    elif name in {"setblock", "testforblock"} and len(tail) >= 4:
        pos = tuple(math.floor(v) for v in coordinate(tail[:3]))
        identifier, states, extra = block_spec(tail[3], tail[4:])
        if name == "testforblock":
            if extra:
                raise Unsupported("testforblock extra arguments")
            success = int(condition(world, ["if", "block", *tail], ctx))
        else:
            mode = extra[0] if extra else "replace"
            if len(extra) > 1 or mode not in {"replace", "keep"}:
                raise Unsupported("setblock mode/arguments")
            if mode != "keep" or pos not in world.blocks:
                world.put(pos, identifier, states)
                success = 1
    elif name == "fill" and len(tail) >= 7:
        a, b = coordinate(tail[:3]), coordinate(tail[3:6])
        lows = [math.floor(min(a[i], b[i])) for i in range(3)]
        highs = [math.floor(max(a[i], b[i])) for i in range(3)]
        volume = math.prod(highs[i] - lows[i] + 1 for i in range(3))
        if volume > 1024 or lows[1] < 0:
            raise ValueError("Fill exceeds bounded world/1024-block limit")
        identifier, states, extra = block_spec(tail[6], tail[7:])
        mode = extra[0] if extra else "replace"
        if len(extra) > 1 or mode not in {"replace", "keep", "outline", "hollow"}:
            raise Unsupported("Fill mode/filter not modeled")
        world.catalog.block(identifier, states)
        updates = {}
        for x in range(lows[0], highs[0] + 1):
            for y in range(lows[1], highs[1] + 1):
                for z in range(lows[2], highs[2] + 1):
                    pos = (x, y, z)
                    if mode == "keep" and pos in world.blocks:
                        continue
                    interior = all(lows[i] < pos[i] < highs[i] for i in range(3))
                    if mode == "outline" and interior:
                        continue
                    updates[pos] = "minecraft:air" if mode == "hollow" and interior else identifier
        added = sum(pos not in world.blocks and ident != "minecraft:air" for pos, ident in updates.items())
        removed = sum(pos in world.blocks and ident == "minecraft:air" for pos, ident in updates.items())
        if len(world.blocks) + added - removed > 2048:
            raise ValueError("Fill capacity limit")
        for pos, ident in updates.items():
            world.put(pos, ident, states if ident == identifier else {})
        success = len(updates)
    elif name == "give" and 2 <= len(tail) <= 3:
        targets = select(tail[0])
        if any(target != "player" for target in targets):
            raise ValueError("give requires players")
        for _ in targets:
            world.give(literal(tail[1]), int(tail[2]) if len(tail) == 3 else 1)
        success = len(targets)
    elif name == "summon" and len(tail) in {1, 4}:
        world.spawn(literal(tail[0]), coordinate(tail[1:4]) if len(tail) == 4 else ctx.origin)
        success = 1
    elif name in {"tp", "teleport"} and len(tail) in {3, 4}:
        targets = (
            [ctx.executor] if len(tail) == 3 and ctx.executor else select(tail[0]) if len(tail) == 4 else []
        )
        pos = coordinate(tail[-3:])
        for target in targets:
            world.entity_api("teleport", target, pos)
        success = len(targets)
    elif name == "kill" and len(tail) <= 1:
        targets = select(tail[0]) if tail else [ctx.executor] if ctx.executor else []
        for target in targets:
            world.entity_api("kill", target)
        success = len(targets)
    elif name == "testfor" and len(tail) == 1:
        success = len(select(tail[0]))
    elif name == "tag" and len(tail) == 3:
        if tail[1] not in {"add", "remove"}:
            raise Unsupported("Tag operation")
        for target in select(tail[0]):
            success += bool(
                world.entity_api("addTag" if tail[1] == "add" else "removeTag", target, literal(tail[2]))
            )
    elif name == "event" and len(tail) == 3 and tail[0] == "entity":
        targets = select(tail[1])
        for target in targets:
            world.entity_event(target, literal(tail[2]))
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
        targets = select(tail[1]) if len(tail) == 2 else [ctx.executor] if ctx.executor else []
        if any(target != "player" for target in targets):
            raise ValueError("Gamemode requires players")
        for target in targets:
            world.entity_api("mode", target, mode)
        success = len(targets)
    elif name == "scoreboard":
        success = scoreboard_command(world, tail, ctx)
    else:
        raise Unsupported("Command or syntax not implemented: " + " ".join(args))
    world.log("command", command=" ".join(args), successCount=success)
    return success


def execute(world, command, executor="player", depth=0, origin=None, rotation=None, budget=None):
    if not isinstance(command, str) or len(command) > 4096:
        raise ValueError("Command length/type limit")
    entity = world.entity_data(executor) if executor else {}
    context = Context(
        executor,
        tuple(origin if origin is not None else entity.get("position", [0, 0, 0])),
        tuple(rotation if rotation is not None else (entity.get("yaw", 0), entity.get("pitch", 0))),
    )
    return {
        "successCount": run_tokens(
            world,
            split_balanced(command.removeprefix("/")),
            context,
            budget if budget is not None else [0],
            depth,
        )
    }
