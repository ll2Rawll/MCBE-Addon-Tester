"""Audit implemented field contracts, including branches not reached by a scenario.

This is deliberately a model-coverage audit, not a complete Bedrock JSON schema.
"""

from .diagnostics import Unsupported
from .molang import MATH, compile_expression
from .render_controllers import SUPPORTED as RENDER_FIELDS

COMPONENT_FIELDS = {
    "minecraft:health": {"value", "max"},
    "minecraft:physics": {"has_gravity", "has_collision"},
    "minecraft:inventory": {"inventory_size"},
    "minecraft:type_family": {"family"},
    "minecraft:variant": {"value"},
    "minecraft:skin_id": {"value"},
    "minecraft:is_baby": set(),
    "minecraft:display_name": {"value"},
    "minecraft:max_stack_size": {"value"},
    "minecraft:tags": {"tags"},
    "minecraft:durability": {"max_durability"},
    "minecraft:block_placer": {"block"},
    "minecraft:tick": {"interval_range", "looping"},
    "minecraft:geometry": {"identifier", "bone_visibility"},
    "minecraft:icon": {"texture", "textures"},
    "minecraft:destructible_by_mining": set(),
}
QUERIES = {
    "query.life_time",
    "query.anim_time",
    "query.health",
    "query.is_alive",
    "query.is_on_ground",
    "query.ground_speed",
    "query.modified_move_speed",
    "query.is_moving",
    "query.variant",
    "query.skin_id",
    "query.is_baby",
    "query.is_sneaking",
    "query.delta_time",
    "query.any_animation_finished",
    "query.all_animations_finished",
    "query.state_time",
}
QUERY_FUNCTIONS = {"query.block_state", "query.property", "query.has_property"}
ANIMATION_FIELDS = {"bones", "animation_length", "loop", "override_previous_animation", "blend_weight"}
STATE_FIELDS = {"animations", "transitions", "on_entry", "on_exit", "blend_transition", "variables"}


def audit(catalog):
    def fields(value, allowed, location):
        if not isinstance(value, dict):
            catalog.findings.error("field-type", location, "Expected an object")
            return
        for key in set(value) - allowed:
            catalog.feature(f"field:{key}", location + "/" + key, "unsupported")

    def expression(value, location):
        if not isinstance(value, str):
            return
        try:
            nodes, _ = compile_expression(value)
            pending = list(nodes)
            while pending:
                node = pending.pop()
                if not isinstance(node, tuple) or not node:
                    continue
                kind = node[0]
                if kind == "name" and node[1].startswith("query.") and node[1] not in QUERIES:
                    catalog.feature("molang:" + node[1], location, "unsupported")
                if kind == "call" and node[1] not in QUERY_FUNCTIONS | {"loop"} | {
                    "math." + key for key in MATH
                }:
                    catalog.feature("molang:" + node[1], location, "unsupported")
                for child in node[1:]:
                    if isinstance(child, tuple):
                        if child and isinstance(child[0], str):
                            pending.append(child)
                        else:
                            pending.extend(child)
        except (Unsupported, IndexError, RecursionError) as exc:
            catalog.findings.unsupported("molang-syntax", location, str(exc))

    def animations(entries, location):
        for entry in entries:
            if isinstance(entry, dict):
                for name, value in entry.items():
                    expression(value, location + "/" + name)

    for kind, definitions in (
        ("block", catalog.blocks),
        ("item", catalog.items),
        ("entity", catalog.entities),
    ):
        for identifier, definition in definitions.items():
            sections = [("components", definition.get("components", {}))]
            sections += [
                (f"component_groups/{name}", value)
                for name, value in definition.get("component_groups", {}).items()
            ]
            for i, permutation in enumerate(definition.get("permutations", [])):
                expression(permutation.get("condition"), f"{identifier}/permutations/{i}/condition")
                sections.append((f"permutations/{i}/components", permutation.get("components", {})))
            for section, components in sections:
                for name, value in components.items():
                    location = f"{identifier}/{section}/{name}"
                    if name in COMPONENT_FIELDS and isinstance(value, dict):
                        fields(value, COMPONENT_FIELDS[name], location)
                    if name in {"minecraft:collision_box", "minecraft:selection_box"} and isinstance(
                        value, dict
                    ):
                        fields(
                            value, {"width", "height"} if kind == "entity" else {"origin", "size"}, location
                        )
                    if name == "minecraft:geometry" and isinstance(value, dict):
                        for bone, condition in value.get("bone_visibility", {}).items():
                            expression(condition, location + "/bone_visibility/" + bone)
                    if name == "minecraft:material_instances":
                        for face, material in value.items():
                            fields(material, {"texture", "render_method"}, location + "/" + face)
                            if material.get("render_method", "opaque") not in {"opaque", "alpha_test"}:
                                catalog.feature(
                                    "material-render-method", location + "/" + face, "unsupported"
                                )
    for identifier, animation in catalog.animations.items():
        fields(animation, ANIMATION_FIELDS, identifier)
        expression(animation.get("blend_weight"), identifier + "/blend_weight")
        if animation.get("loop", False) not in (True, False, "hold_on_last_frame"):
            catalog.feature("animation-expression-loop", identifier, "unsupported")
        for bone, channels in animation.get("bones", {}).items():
            fields(channels, {"rotation", "position", "scale"}, f"{identifier}/{bone}")
            pending = [(channels, f"{identifier}/{bone}")]
            while pending:
                value, location = pending.pop()
                if isinstance(value, dict):
                    if "lerp_mode" in value:
                        if value["lerp_mode"] != "linear":
                            catalog.feature("animation-interpolation", location, "unsupported")
                        fields(value, {"pre", "post", "lerp_mode"}, location)
                    pending.extend((v, location + "/" + str(k)) for k, v in value.items() if k != "lerp_mode")
                elif isinstance(value, list):
                    pending.extend((v, location + f"/{i}") for i, v in enumerate(value))
                else:
                    expression(value, location)
    for identifier, controller in catalog.controllers.items():
        fields(controller, {"states", "initial_state"}, identifier)
        states = controller.get("states", {})
        for name, state in states.items():
            location = identifier + "/states/" + name
            fields(state, STATE_FIELDS, location)
            animations(state.get("animations", []), location)
            for key in ("on_entry", "on_exit"):
                for value in state.get(key, []):
                    expression(value, location + "/" + key)
            for key, value in state.get("variables", {}).items():
                if isinstance(value, dict):
                    catalog.feature("controller-variable-remap", location + "/" + key, "unsupported")
                else:
                    expression(value, location + "/" + key)
            for transition in state.get("transitions", []):
                for target, condition in transition.items():
                    if target not in states:
                        catalog.findings.error(
                            "animation-state-reference", location, f"Missing state {target}"
                        )
                    expression(condition, location + "/transitions/" + target)
    for identifier, controller in catalog.render_controllers.items():
        fields(controller, RENDER_FIELDS, identifier)
        pending = [(controller, identifier)]
        while pending:
            value, location = pending.pop()
            if isinstance(value, dict):
                pending.extend((v, location + "/" + str(k)) for k, v in value.items())
            elif isinstance(value, list):
                pending.extend((v, location + f"/{i}") for i, v in enumerate(value))
            else:
                expression(value, location)
    for identifier, client in catalog.clients.items():
        desc = client.get("description", {})
        scripts = desc.get("scripts", {})
        fields(scripts, {"animate", "initialize", "pre_animation"}, identifier + "/scripts")
        animations(scripts.get("animate", []), identifier + "/scripts/animate")
        for key in ("initialize", "pre_animation"):
            for value in scripts.get(key, []):
                expression(value, identifier + "/scripts/" + key)
        for entry in desc.get("render_controllers", []):
            entries = entry if isinstance(entry, dict) else {entry: 1}
            for name, condition in entries.items():
                if name not in catalog.render_controllers:
                    catalog.findings.unsupported("render-controller-reference", identifier, name)
                expression(condition, identifier + "/render_controllers/" + name)


def coverage(catalog):
    return {
        "basis": "detected content fields, not behavioral or native compatibility coverage",
        "modeled_occurrences": sum(v["status"] == "modeled" for v in catalog.occurrences),
        "unsupported_occurrences": sum(v["status"] == "unsupported" for v in catalog.occurrences),
        "native_reference_cases": 0,
        "native_accuracy_percent": None,
        "detection_complete": False,
    }
