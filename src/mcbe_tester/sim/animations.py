"""Tick-driven animation instances and controller state transitions."""

from copy import deepcopy

import numpy as np

from .diagnostics import Unsupported
from .molang import evaluate


def channel(value, time, context, variables, default):
    if isinstance(value, dict):
        frames = sorted((float(t), v) for t, v in value.items())
        if not frames:
            return np.array(default, dtype=float)

        def endpoint(entry, side):
            if isinstance(entry, dict):
                if entry.get("lerp_mode", "linear") != "linear":
                    raise Unsupported("Only linear animation interpolation is implemented")
                entry = entry.get(side, entry.get("post", entry.get("pre", default)))
            return channel(entry, time, context, variables, default)

        if time < frames[0][0]:
            return endpoint(frames[0][1], "pre")
        if time >= frames[-1][0]:
            return endpoint(frames[-1][1], "post")
        for i in range(len(frames) - 1):
            before, after = frames[i : i + 2]
            if before[0] <= time < after[0]:
                alpha = (time - before[0]) / (after[0] - before[0])
                return endpoint(before[1], "post") * (1 - alpha) + endpoint(after[1], "pre") * alpha
    if not isinstance(value, list):
        value = [value] * 3
    elif len(value) == 1:
        value = value * 3
    if len(value) != 3:
        raise ValueError("Animation channel needs one or three components")
    return np.array([float(evaluate(v, context, variables)) for v in value])


def length(animation):
    if "animation_length" in animation:
        result = animation["animation_length"]
    else:
        result = max(
            [
                float(t)
                for bone in animation.get("bones", {}).values()
                for value in bone.values()
                if isinstance(value, dict)
                for t in value
            ]
            + [0]
        )
    if type(result) not in (int, float) or not 0 <= result <= 10000:
        raise ValueError("Animation duration outside supported range")
    return result


def active_animations(world, entity):
    if entity.get("_animation_tick") == world.tick:
        return entity.get("_animations", [])
    desc = world.catalog.clients.get(entity["identifier"], {}).get("description", {})
    context, variables = world.context(entity), entity["variables"]
    scripts = desc.get("scripts", {})
    for key in set(scripts) - {"animate", "pre_animation", "initialize"}:
        world.catalog.findings.unsupported("client-script", entity["identifier"], key)
    if not entity.get("_animation_initialized"):
        for expression in scripts.get("initialize", []):
            evaluate(expression, context, variables)
        entity["_animation_initialized"] = True
    for expression in scripts.get("pre_animation", []):
        evaluate(expression, context, variables)
    aliases = desc.get("animations", {})
    instances = entity.setdefault("_animation_instances", {})
    controllers = entity.setdefault("_controller_runtime", {})
    touched, controller_touched = set(), set()
    resolved_count = 0

    def resolve_entries(entries, path, parent_weight=1, depth=0):
        out = []
        for index, entry in enumerate(entries):
            entries_map = entry if isinstance(entry, dict) else {entry: 1}
            for key, expression in entries_map.items():
                weight = float(evaluate(expression, context, variables)) * parent_weight
                if weight:
                    out.extend(resolve(key, f"{path}/{index}:{key}", weight, depth))
        return out

    def effects(state, key):
        for expression in state.get(key, []):
            if not isinstance(expression, str) or expression.startswith(("/", "@")):
                raise Unsupported("Controller entry/exit supports Molang only")
            evaluate(expression, context, variables)

    def resolve(name, path, weight, depth):
        nonlocal resolved_count
        resolved_count += 1
        if depth > 8 or resolved_count > 128:
            raise Unsupported("Animation expansion limit")
        name = aliases.get(name, name)
        if name in world.catalog.animations:
            touched.add(path)
            start = instances.setdefault(path, world.tick)
            return [{"name": name, "weight": weight, "time": (world.tick - start) / 20}]
        if name not in world.catalog.controllers:
            raise Unsupported(f"Animation/controller not supplied: {name}")
        controller_touched.add(path)
        definition = world.catalog.controllers[name]
        states = definition["states"]
        if path not in controllers:
            initial = definition.get("initial_state", "default")
            if initial not in states:
                raise ValueError("Controller initial state does not exist")
            controllers[path] = {"state": initial, "entered": world.tick, "transition_tick": -1}
            effects(states[initial], "on_entry")
        runtime = controllers[path]
        current = runtime["state"]
        state = states[current]
        for key in set(state) - {
            "animations",
            "transitions",
            "on_entry",
            "on_exit",
            "blend_transition",
            "variables",
        }:
            world.catalog.findings.unsupported("controller-state-feature", name, key)
        for key, expression in state.get("variables", {}).items():
            if isinstance(expression, dict):
                raise Unsupported("Controller variable remap curves are not modeled")
            variables[key.removeprefix("variable.")] = evaluate(expression, context, variables)
        clips = resolve_entries(
            state.get("animations", []), f"{path}/{current}@{runtime['entered']}", weight, depth + 1
        )
        finished = [clip["time"] >= length(world.catalog.animations[clip["name"]]) for clip in clips]
        transition_context = {
            **context,
            "query.any_animation_finished": any(finished),
            "query.all_animations_finished": all(finished),
            "query.state_time": (world.tick - runtime["entered"]) / 20,
        }
        if runtime["transition_tick"] != world.tick:
            for transition in state.get("transitions", []):
                if not isinstance(transition, dict) or len(transition) != 1:
                    raise ValueError("Controller transitions require one target and condition")
                target, condition = next(iter(transition.items()))
                if not evaluate(condition, transition_context, variables):
                    continue
                if target not in states:
                    raise ValueError(f"Missing animation state {target}")
                old_blend = runtime.get("blend")
                if old_blend and (world.tick - old_blend["start"]) / 20 < old_blend["duration"]:
                    raise Unsupported("Interrupted controller cross-fades are not modeled")
                duration = state.get("blend_transition", 0)
                if type(duration) not in (int, float) or not 0 <= duration <= 10000:
                    raise ValueError("Invalid blend duration")
                effects(state, "on_exit")
                runtime.update(state=target, entered=world.tick, transition_tick=world.tick)
                if duration:
                    runtime["blend"] = {"start": world.tick, "duration": duration, "clips": deepcopy(clips)}
                else:
                    runtime.pop("blend", None)
                effects(states[target], "on_entry")
                for key, expression in states[target].get("variables", {}).items():
                    if isinstance(expression, dict):
                        raise Unsupported("Controller variable remap curves are not modeled")
                    variables[key.removeprefix("variable.")] = evaluate(expression, context, variables)
                clips = resolve_entries(
                    states[target].get("animations", []), f"{path}/{target}@{world.tick}", weight, depth + 1
                )
                break
        entity["controllers"][name] = runtime["state"]
        if "blend" in runtime:
            blend = runtime["blend"]
            elapsed = (world.tick - blend["start"]) / 20
            alpha = min(1, elapsed / blend["duration"])
            if alpha >= 1:
                runtime.pop("blend")
            else:
                # Rotation/position channels compose linearly. Scale composition
                # needs a separate native contract before cross-fades can verify it.
                if any(
                    "scale" in bone
                    for clip in [*clips, *blend["clips"]]
                    for bone in world.catalog.animations[clip["name"]].get("bones", {}).values()
                ):
                    raise Unsupported("Controller cross-fades involving scale channels are not modeled")
                clips = [{**clip, "weight": clip["weight"] * alpha} for clip in clips] + [
                    {**clip, "weight": clip["weight"] * (1 - alpha), "time": clip["time"] + elapsed}
                    for clip in blend["clips"]
                ]
        return clips

    result = resolve_entries(scripts.get("animate", []), "root")
    entity["_animation_instances"] = {key: value for key, value in instances.items() if key in touched}
    entity["_controller_runtime"] = {
        key: value for key, value in controllers.items() if key in controller_touched
    }
    entity["_animation_tick"], entity["_animations"] = world.tick, result
    return result


def pose(world, entity):
    out = {}
    for clip in active_animations(world, entity):
        name, weight, time = clip["name"], clip["weight"], clip["time"]
        animation = world.catalog.animations[name]
        for key in set(animation) - {
            "bones",
            "animation_length",
            "loop",
            "override_previous_animation",
            "blend_weight",
        }:
            world.catalog.findings.unsupported("animation-feature", name, key)
        duration = length(animation)
        loop = animation.get("loop", False)
        if loop is False and duration > 0 and time >= duration:
            continue
        if loop is True and duration > 0:
            time %= duration
        elif loop not in (True, False, "hold_on_last_frame"):
            raise Unsupported("Expression animation loops are not modeled")
        elif duration:
            time = min(time, duration)
        context = {**world.context(entity), "query.anim_time": time}
        variables = deepcopy(entity["variables"])
        weight *= float(evaluate(animation.get("blend_weight", 1), context, variables))
        for bone, channels in animation.get("bones", {}).items():
            if bone not in out or animation.get("override_previous_animation"):
                out[bone] = {"rotation": np.zeros(3), "position": np.zeros(3), "scale": np.ones(3)}
            target = out[bone]
            for key, default in (("rotation", [0, 0, 0]), ("position", [0, 0, 0]), ("scale", [1, 1, 1])):
                if key in channels:
                    value = channel(channels[key], time, context, variables, default)
                    if key == "scale":
                        target[key] *= 1 + (value - 1) * weight
                    else:
                        target[key] += value * weight
    return out
