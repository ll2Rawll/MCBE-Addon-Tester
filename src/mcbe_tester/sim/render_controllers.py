"""Resolve entity render-controller resources without silently choosing default assets."""

import math
import re
from copy import deepcopy

from .diagnostics import Unsupported
from .molang import canonical, evaluate

SUPPORTED = {"arrays", "geometry", "materials", "textures", "part_visibility", "color"}


def matches(pattern, name):
    if not isinstance(pattern, str) or any(c in pattern for c in "?[]"):
        raise Unsupported("Only literal names and * bone wildcards are modeled")
    return re.fullmatch(re.escape(pattern).replace(r"\*", ".*"), name) is not None


def layers(world, entity):
    catalog = world.catalog
    desc = catalog.clients.get(entity["identifier"], {}).get("description", {})
    selectors = desc.get("render_controllers", [])
    if not selectors:
        raise Unsupported("No explicit client render controller supplied")
    if len(selectors) > 16:
        raise Unsupported("16 render-controller layers per entity limit")
    base = world.context(entity)
    for kind, section in (("geometry", "geometry"), ("texture", "textures"), ("material", "materials")):
        base.update({f"{kind}.{key.lower()}": value for key, value in desc.get(section, {}).items()})
    variables = deepcopy(entity.get("variables", {}))
    result = []
    for selector in selectors:
        if isinstance(selector, str):
            identifier = selector
        elif isinstance(selector, dict) and len(selector) == 1:
            identifier, condition = next(iter(selector.items()))
            if not evaluate(condition, base, variables):
                continue
        else:
            raise ValueError("Render controller selector must be a name or single condition")
        if identifier not in catalog.render_controllers:
            raise Unsupported(f"Render controller not supplied: {identifier}")
        definition = catalog.render_controllers[identifier]
        unknown = set(definition) - SUPPORTED
        if unknown:
            raise Unsupported(f"Render-controller fields not modeled: {sorted(unknown)}")
        context = dict(base)
        arrays = {}
        for section, values in definition.get("arrays", {}).items():
            if section not in {"textures", "geometries", "materials"}:
                raise Unsupported(f"Render array kind: {section}")
            for key, value in values.items():
                key = canonical(key)
                if not key.startswith("array.") or key in arrays or not isinstance(value, list):
                    raise ValueError("Invalid/duplicate resource array")
                arrays[key] = value

        def resolve_array(name, visiting=(), context=context, arrays=arrays):
            if name in context:
                return context[name]
            if name in visiting or len(visiting) >= 16 or name not in arrays:
                raise ValueError("Cyclic/missing resource array: " + name)
            output = []
            for item in arrays[name]:
                if isinstance(item, str) and canonical(item) in arrays:
                    output.extend(resolve_array(canonical(item), (*visiting, name)))
                else:
                    value = evaluate(item, context, variables)
                    if not isinstance(value, str):
                        raise ValueError("Resource array entry must resolve to an asset")
                    output.append(value)
                if len(output) > 1024:
                    raise Unsupported("Resource array expansion limit")
            if not output:
                raise ValueError("Resource array cannot be empty")
            context[name] = output
            return output

        for key in arrays:
            resolve_array(key)
        geometry = evaluate(definition["geometry"], context, variables)
        if not isinstance(geometry, str) or geometry not in catalog.geometries:
            raise Unsupported(f"Selected geometry not supplied: {geometry}")
        texture_entries = definition.get("textures", [])
        if isinstance(texture_entries, str):
            texture_entries = [texture_entries]
        if len(texture_entries) != 1:
            raise Unsupported("Render materials with multiple texture inputs are not modeled")
        texture = evaluate(texture_entries[0], context, variables)
        if not isinstance(texture, str):
            raise ValueError("Selected texture must be an asset reference")
        bones = [b["name"] for b in catalog.geometries[geometry].get("bones", [])]
        material_map = dict.fromkeys(bones)
        for entry in definition.get("materials", []):
            if not isinstance(entry, dict):
                raise ValueError("Material entries must map bone names to material references")
            for pattern, expression in entry.items():
                material = evaluate(expression, context, variables)
                if material not in {"entity", "entity_alphatest"}:
                    raise Unsupported(f"Material shader not modeled: {material}")
                for bone in bones:
                    if matches(pattern, bone):
                        material_map[bone] = material
        if any(value is None for value in material_map.values()):
            raise Unsupported("Render controller leaves bones without a modeled material")
        visibility = dict.fromkeys(bones, True)
        for entry in definition.get("part_visibility", []):
            for pattern, expression in entry.items():
                visible = bool(evaluate(expression, context, variables))
                for bone in bones:
                    if matches(pattern, bone):
                        visibility[bone] = visible
        tint = [float(evaluate(definition.get("color", {}).get(k, 1), context, variables)) for k in "rgba"]
        if any(not math.isfinite(v) or not 0 <= v <= 1 for v in tint):
            raise ValueError("Render color must be finite RGBA in [0,1]")
        if tint[3] not in {0, 1}:
            raise Unsupported("Translucent render-controller tint is not modeled")
        if tint[3] == 0:
            continue
        result.append(
            {
                "controller": identifier,
                "geometry": geometry,
                "texture": texture,
                "materials": material_map,
                "visibility": visibility,
                "tint": tint,
            }
        )
    return result
