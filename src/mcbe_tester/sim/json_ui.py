"""Independent JSON UI definition, binding, layout and virtual-input model.

Native screen factories/game bindings must be supplied explicitly; missing
values/templates and unsupported fields remain blockers. Layout uses an explicit
logical viewport and original/Pillow text metrics, never native screenshots.
"""

import math
import re
from copy import deepcopy

from .diagnostics import Unsupported
from .ui_expression import evaluate

TYPES = {"screen", "panel", "input_panel", "stack_panel", "label", "image", "button", "toggle", "edit_box"}
FIELDS = {
    "type",
    "controls",
    "size",
    "offset",
    "anchor_from",
    "anchor_to",
    "min_size",
    "max_size",
    "layer",
    "visible",
    "enabled",
    "ignored",
    "alpha",
    "propagate_alpha",
    "color",
    "text",
    "texture",
    "uv",
    "uv_size",
    "keep_ratio",
    "bilinear",
    "grayscale",
    "shadow",
    "font_scale_factor",
    "font_size",
    "text_alignment",
    "localize",
    "bindings",
    "button_mappings",
    "clips_children",
    "allow_clipping",
    "orientation",
    "default_control",
    "hover_control",
    "pressed_control",
    "locked_control",
    "focus_enabled",
    "focus_change_up",
    "focus_change_down",
    "focus_change_left",
    "focus_change_right",
    "toggle_default_state",
    "max_length",
    "text_box_name",
    "text_control",
    "place_holder_control",
}
ANCHORS = {
    "top_left": (0, 0),
    "top_middle": (0.5, 0),
    "top_right": (1, 0),
    "left_middle": (0, 0.5),
    "center": (0.5, 0.5),
    "right_middle": (1, 0.5),
    "bottom_left": (0, 1),
    "bottom_middle": (0.5, 1),
    "bottom_right": (1, 1),
}
BIND_FIELDS = {
    "binding_type",
    "binding_name",
    "binding_name_override",
    "source_control_name",
    "source_property_name",
    "target_property_name",
    "binding_condition",
}
MAPPING_FIELDS = {"from_button_id", "to_button_id", "mapping_type", "input_mode_condition"}


def merge(base, changes):
    result = deepcopy(base)
    for key, value in changes.items():
        result[key] = (
            merge(result[key], value)
            if isinstance(result.get(key), dict) and isinstance(value, dict)
            else deepcopy(value)
        )
    return result


def finite(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(label + " must be a finite number")
    return value


def flag(value):
    if type(value) not in (bool, int, float):
        raise ValueError("UI boolean properties need boolean/numeric values")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("UI boolean properties must be finite")
    return bool(value)


def pair(value, label):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(label + " needs two values")
    return value


def length(value, parent):
    if type(value) in (int, float):
        return finite(value, "UI dimension")
    if isinstance(value, str):
        if value == "default":
            return parent
        if re.fullmatch(r"-?\d+(?:\.\d+)?px", value):
            return finite(float(value[:-2]), "UI dimension")
        match = re.fullmatch(r"(-?\d+(?:\.\d+)?)%(?:\s*([+-])\s*(\d+(?:\.\d+)?)(?:px)?)?", value)
        if match:
            percentage, sign, offset = match.groups()
            return parent * float(percentage) / 100 + (
                float(offset) * (1 if sign == "+" else -1) if offset else 0
            )
    raise Unsupported("UI size expression not modeled: " + str(value))


class Definitions:
    def __init__(self, catalog):
        self.catalog = catalog
        self.controls = {}
        self.variables = {}
        self.audited = 0
        self.references = []

    def load(self):
        catalog = self.catalog
        for variables in catalog.ui_globals:
            for key, value in variables.items():
                if not key.startswith("$"):
                    catalog.findings.unsupported("ui-global-field", key)
                self.variables[key] = deepcopy(value)
        for path in catalog.ui_defs:
            if path not in catalog.ui_documents:
                catalog.findings.error("ui-definition-reference", path, "UI file not supplied")
                continue
            data = catalog.ui_documents[path]
            namespace = data.get("namespace")
            if not isinstance(namespace, str) or not namespace or "." in namespace:
                catalog.findings.error("ui-namespace", path, "UI namespace must be a nonempty simple name")
                continue
            for name, definition in data.items():
                if name == "namespace":
                    continue
                if not isinstance(definition, dict):
                    catalog.findings.error("ui-definition", path + "/" + name, "Expected a control object")
                    continue
                short, _, parent = name.partition("@")
                key = namespace + "." + short
                if key in self.controls:
                    catalog.findings.unsupported(
                        "ui-pack-stacking", key, "Duplicate definitions need a pack-priority model"
                    )
                self.controls[key] = {
                    "definition": definition,
                    "parent": parent,
                    "namespace": namespace,
                    "source": path,
                }
                self.audit(definition, key)
        if len(self.controls) > 512:
            raise ValueError("512 UI definitions limit")
        for path in set(catalog.ui_documents) - set(catalog.ui_defs):
            catalog.findings.unsupported(
                "ui-native-template-loading",
                path,
                "File is not listed in supplied _ui_defs.json; native base screen loading is not modeled",
            )
        for key, entry in self.controls.items():
            if entry["parent"] and not entry["parent"].startswith("$"):
                name = (
                    entry["parent"] if "." in entry["parent"] else entry["namespace"] + "." + entry["parent"]
                )
                if name not in self.controls:
                    catalog.findings.unsupported("ui-template-reference", key, name)
                else:
                    try:
                        self.resolve(key)
                    except Unsupported as exc:
                        catalog.findings.unsupported("ui-template-reference", key, str(exc))
                    except ValueError as exc:
                        catalog.findings.error("ui-inheritance", key, str(exc))
        for location, template in self.references:
            if template not in self.controls:
                catalog.findings.unsupported("ui-template-reference", location, template)

    def audit(self, definition, location, depth=0):
        self.audited += 1
        if self.audited > 4096:
            self.catalog.findings.unsupported(
                "ui-audit-budget", "pack", "UI audit exceeds 4096 control definitions"
            )
            return
        if depth > 32:
            self.catalog.findings.unsupported("ui-depth", location)
            return
        if not isinstance(definition, dict):
            raise ValueError("UI control definition must be an object")
        for key in ("controls", "bindings", "button_mappings"):
            if key in definition and (not isinstance(definition[key], list) or len(definition[key]) > 512):
                raise ValueError("UI " + key + " must be an array with at most 512 entries")
        for field in definition:
            if field not in FIELDS and not field.startswith("$"):
                self.catalog.feature("ui-field:" + field, location, "unsupported")
        if (
            "type" in definition
            and definition["type"] not in TYPES
            and not str(definition["type"]).startswith("$")
        ):
            self.catalog.feature("ui-control-type", location, "unsupported")
        for binding in definition.get("bindings", []):
            if not isinstance(binding, dict):
                raise ValueError("UI bindings must be objects")
            for field in set(binding) - BIND_FIELDS:
                self.catalog.feature("ui-binding:" + field, location, "unsupported")
            if binding.get("binding_type", "global") not in {"global", "view"}:
                self.catalog.feature("ui-binding-type", location, "unsupported")
            if binding.get("binding_condition", "always") != "always":
                self.catalog.feature("ui-binding-condition", location, "unsupported")
        for mapping in definition.get("button_mappings", []):
            if not isinstance(mapping, dict):
                raise ValueError("UI input mappings must be objects")
            for field in set(mapping) - MAPPING_FIELDS:
                self.catalog.feature("ui-input:" + field, location, "unsupported")
            if mapping.get("mapping_type", "pressed") != "pressed":
                self.catalog.feature("ui-input-mapping", location, "unsupported")
            if mapping.get("input_mode_condition", "not_gamepad") not in {"not_gamepad", "gamepad"}:
                self.catalog.feature("ui-input-mode", location, "unsupported")
        for item in definition.get("controls", []):
            if not isinstance(item, dict) or len(item) != 1:
                raise ValueError("UI controls must contain single named objects")
            name, child = next(iter(item.items()))
            template = name.partition("@")[2]
            if template and not template.startswith("$"):
                self.references.append(
                    (
                        location + "/" + name,
                        template if "." in template else location.split(".")[0] + "." + template,
                    )
                )
            self.audit(child, location + "/" + name, depth + 1)

    def resolve(self, identifier, override=None, chain=()):
        if identifier in chain or len(chain) > 24:
            raise ValueError("Cyclic/deep UI inheritance")
        if identifier not in self.controls:
            raise Unsupported("UI template not supplied: " + identifier)
        entry = self.controls[identifier]
        value = deepcopy(entry["definition"])
        if entry["parent"]:
            parent = entry["parent"]
            if parent.startswith("$"):
                candidates = {**self.variables, **value, **(override or {})}
                parent = candidates.get(parent, candidates.get(parent + "|default"))
                if not isinstance(parent, str) or parent.startswith("$"):
                    raise Unsupported("Dynamic UI inheritance could not be resolved")
            if "." not in parent:
                parent = entry["namespace"] + "." + parent
            value = merge(self.resolve(parent, chain=(*chain, identifier))[0], value)
        return merge(value, override or {}), entry["namespace"]


class JsonUI:
    def __init__(self, world):
        self.world = world
        self.current = None
        self.nodes = []
        self._by_id = {}
        self._cache = {}

    def open(self, screen, variables=None, bindings=None, width=640, height=400):
        if self.world.forms.current:
            raise ValueError("Close the scripted form before opening a JSON UI fixture")
        if (
            type(width) is not int
            or type(height) is not int
            or not 160 <= width <= 1920
            or not 120 <= height <= 1080
        ):
            raise ValueError("UI logical viewport must be 160–1920 by 120–1080")
        variables, bindings = deepcopy(variables or {}), deepcopy(bindings or {})
        if (
            not isinstance(variables, dict)
            or not isinstance(bindings, dict)
            or len(variables) + len(bindings) > 256
        ):
            raise ValueError("UI variable/binding input limit")
        if any(not key.startswith("$") for key in variables) or any(
            not key.startswith("#") for key in bindings
        ):
            raise ValueError("UI variables start with $; binding values start with #")
        previous = self.current
        self.current = {
            "screen": screen,
            "variables": variables,
            "bindings": bindings,
            "viewport": [width, height],
            "state": {},
            "focus": None,
            "hover": None,
            "pressed": None,
            "pointer_down": False,
            "events": [],
        }
        try:
            self.rebuild()
        except Exception:
            self.current = previous
            self.nodes = []
            if previous:
                self.rebuild()
            raise
        self.world.player["menu"] = "json_ui"
        self.world.log("ui-opened", screen=screen, viewport=[width, height])
        return self.snapshot()

    def close(self):
        if self.current is None:
            return
        self.world.log("ui-closed", screen=self.current["screen"])
        self.current = None
        self.nodes = []
        self._by_id = {}
        self.world.player["menu"] = "game"

    def variable(self, name, variables, chain=(), binding=None):
        if name in chain or len(chain) > 32:
            raise ValueError("Cyclic UI variable reference")
        if name not in variables:
            raise Unsupported("UI variable not supplied: " + name)
        return self.value(
            variables[name],
            variables,
            binding if binding is not None else lambda key: self.current["bindings"][key],
            (*chain, name),
        )

    def value(self, value, variables, binding, chain=()):
        if isinstance(value, list):
            return [self.value(v, variables, binding, chain) for v in value]

        def lookup(name):
            if name.startswith("$"):
                return self.variable(name, variables, chain, binding)
            try:
                return binding(name)
            except KeyError as exc:
                raise Unsupported("UI binding not supplied: " + name) from exc

        return evaluate(value, lookup)

    def rebuild(self):
        if self.current is None:
            return
        self.nodes = []
        self._by_id = {}
        self._cache = {}
        registry = self.world.catalog.ui
        root, namespace = registry.resolve(self.current["screen"])
        environment = {**registry.variables, **self.current["variables"]}

        def add(name, definition, parent, variables, namespace, depth=0):
            if depth > 32 or len(self.nodes) >= 512:
                raise Unsupported("UI control expansion budget")
            if not isinstance(name, str) or not isinstance(definition, dict):
                raise ValueError("UI controls need a string name and an object definition")
            short, _, template = name.partition("@")
            if template:
                if template.startswith("$"):
                    candidates = {**variables, **{k: v for k, v in definition.items() if k.startswith("$")}}
                    template = self.variable(template, candidates)
                if not isinstance(template, str):
                    raise ValueError("UI template must resolve to a name")
                if "." not in template:
                    template = namespace + "." + template
                definition, namespace = registry.resolve(template, definition)
            variables = dict(variables)
            for key, value in definition.items():
                if key.startswith("$"):
                    if key.endswith("|default"):
                        variables.setdefault(key.removesuffix("|default"), value)
                    else:
                        variables[key] = value
            identifier = parent["id"] + "/" + short if parent else short
            if identifier in self._by_id:
                raise ValueError("Duplicate UI control name: " + identifier)
            kind = self.value(
                definition.get("type", "panel"), variables, lambda key: self.current["bindings"][key]
            )
            if kind not in TYPES:
                raise Unsupported("UI control type not modeled: " + str(kind))
            node = {
                "id": identifier,
                "name": short,
                "type": kind,
                "parent": parent["id"] if parent else None,
                "raw": definition,
                "variables": variables,
                "children": [],
                "assignments": {},
            }
            for row in definition.get("bindings", []):
                if not isinstance(row, dict):
                    raise ValueError("UI bindings must be objects")
                if (
                    set(row) - BIND_FIELDS
                    or row.get("binding_type", "global") not in {"global", "view"}
                    or row.get("binding_condition", "always") != "always"
                ):
                    raise Unsupported("UI binding type/condition/fields not modeled")
                target = (
                    row.get("target_property_name")
                    if row.get("binding_type") == "view"
                    else row.get("binding_name_override", row.get("binding_name"))
                )
                if not isinstance(target, str) or not target.startswith("#"):
                    raise ValueError("UI binding target must begin with #")
                node["assignments"][target.removeprefix("#")] = row
            self.nodes.append(node)
            self._by_id[identifier] = node
            if parent:
                parent["children"].append(identifier)
            for child in definition.get("controls", []):
                if not isinstance(child, dict) or len(child) != 1:
                    raise ValueError("UI children must be single named objects")
                child_name, child_def = next(iter(child.items()))
                add(child_name, child_def, node, variables, namespace, depth + 1)

        add(self.current["screen"], root, None, environment, namespace)
        root_node = self.nodes[0]
        width, height = self.current["viewport"]
        self.layout(root_node, [0, 0, width, height], [0, 0, width, height], True, True, 1, 0)

    def find_control(self, name, relative):
        if name in self._by_id:
            return self._by_id[name]
        matches = [node for node in self.nodes if node["name"] == name]
        ancestor = relative
        while ancestor:
            local = [
                node for node in matches if node["parent"] == ancestor["id"] or node["id"] == ancestor["id"]
            ]
            if len(local) == 1:
                return local[0]
            ancestor = self._by_id.get(ancestor["parent"])
        if len(matches) != 1:
            raise Unsupported("UI source control is missing or ambiguous: " + name)
        return matches[0]

    def prop(self, node, key, default=None, chain=()):
        if key == "text_box_text":
            editor = node
            while editor and editor["type"] != "edit_box":
                editor = self._by_id.get(editor["parent"])
            if editor:
                return self.prop(editor, "text", "", chain)
        cache_key = (node["id"], key)
        if cache_key in self._cache:
            return self._cache[cache_key]
        if cache_key in chain or len(chain) > 64:
            raise ValueError("Cyclic/deep UI binding dependency")
        next_chain = (*chain, cache_key)
        state = self.current["state"].get(node["id"], {})
        parent = self._by_id.get(node["parent"])
        if (
            key == "text"
            and parent
            and parent["type"] == "edit_box"
            and self.prop(parent, "text_control", "") == node["name"]
        ):
            result = self.prop(parent, "text", "", next_chain)
        elif key in state:
            result = state[key]
        elif key in node["assignments"]:
            row = node["assignments"][key]
            if row.get("binding_type", "global") == "global":
                source = row["binding_name"]
                if source not in self.current["bindings"]:
                    raise Unsupported("Native/global UI binding not supplied: " + source)
                result = self.current["bindings"][source]
            else:
                source = (
                    self.find_control(row["source_control_name"], node)
                    if row.get("source_control_name")
                    else node
                )
                result = self.value(
                    row["source_property_name"],
                    node["variables"],
                    lambda name: self.prop(source, name.removeprefix("#"), chain=next_chain),
                    (),
                )
        elif key not in node["raw"] and "#" + key in self.current["bindings"]:
            # Binding payloads are data, even if a user types an expression-looking string.
            result = self.current["bindings"]["#" + key]
        else:
            if key in node["raw"]:
                value = node["raw"][key]
            elif key == "toggle_state" and node["type"] == "toggle":
                value = node["raw"].get("toggle_default_state", False)
            elif default is not None:
                value = default
            elif key in {"visible", "enabled", "alpha", "text", "ignored"}:
                value = {"visible": True, "enabled": True, "alpha": 1, "text": "", "ignored": False}[key]
            else:
                raise Unsupported("UI control property not supplied: " + node["id"] + "/#" + key)
            result = self.value(
                value,
                node["variables"],
                lambda name: self.prop(node, name.removeprefix("#"), chain=next_chain),
                (),
            )
        self._cache[cache_key] = result
        return result

    def layout(
        self,
        node,
        parent_rect,
        parent_clip,
        parent_visible,
        parent_enabled,
        parent_alpha,
        parent_layer,
        stack_offset=None,
    ):
        def p(key, default=None):
            return self.prop(node, key, default)

        a, b, c, d = parent_rect
        dimensions = p("size", ["100%", "100%"])
        offset = p("offset", [0, 0])
        if (
            not isinstance(dimensions, list)
            or len(dimensions) != 2
            or not isinstance(offset, list)
            or len(offset) != 2
        ):
            raise ValueError("UI size/offset need two values")
        width, height = length(dimensions[0], c - a), length(dimensions[1], d - b)
        minima = pair(p("min_size", [0, 0]), "UI minimum size")
        maxima = pair(p("max_size", [4096, 4096]), "UI maximum size")
        if any(
            not 0 <= finite(lo, "min size") <= finite(hi, "max size") <= 4096
            for lo, hi in zip(minima, maxima, strict=True)
        ):
            raise ValueError("UI size constraints require 0 <= min <= max <= 4096")
        width = min(max(width, finite(minima[0], "min size")), finite(maxima[0], "max size"))
        height = min(max(height, finite(minima[1], "min size")), finite(maxima[1], "max size"))
        if not 0 <= width <= 4096 or not 0 <= height <= 4096:
            raise ValueError("UI element size outside 0–4096")
        anchor_from, anchor_to = p("anchor_from", "center"), p("anchor_to", "center")
        if anchor_from not in ANCHORS or anchor_to not in ANCHORS:
            raise Unsupported("UI anchor not modeled")
        x = a + (c - a) * ANCHORS[anchor_from][0] - width * ANCHORS[anchor_to][0] + length(offset[0], c - a)
        y = b + (d - b) * ANCHORS[anchor_from][1] - height * ANCHORS[anchor_to][1] + length(offset[1], d - b)
        if stack_offset:
            axis, amount = stack_offset
            if axis == 0:
                x = a + amount + length(offset[0], c - a)
            else:
                y = b + amount + length(offset[1], d - b)
        rect = [x, y, x + width, y + height]
        if any(abs(finite(value, "UI position")) > 16384 for value in rect):
            raise Unsupported("UI layout exceeds the 16384-unit coordinate budget")
        visible = flag(p("visible", True)) and not flag(p("ignored", False)) and parent_visible
        enabled = flag(p("enabled", True)) and parent_enabled
        alpha = finite(p("alpha", 1), "alpha")
        if not 0 <= alpha <= 1:
            raise ValueError("UI alpha must be in [0,1]")
        alpha *= parent_alpha
        layer = parent_layer + finite(p("layer", 0), "layer")
        clip = parent_clip if flag(p("allow_clipping", True)) else [0, 0, *self.current["viewport"]]
        node.update(rect=rect, clip=clip, visible=visible, enabled=enabled, alpha=alpha, layer=layer)
        node["text"] = p("text", "") if node["type"] in {"label", "edit_box"} else ""
        if not isinstance(node["text"], str) or len(node["text"]) > 4096:
            raise ValueError("UI text must resolve to a string of at most 4096 characters")
        if flag(p("localize", False)):
            node["text"] = self.world.catalog.strings.get(node["text"], node["text"])
        node["color"] = p("color", [1, 1, 1])
        if (
            not isinstance(node["color"], list)
            or len(node["color"]) != 3
            or any(not 0 <= finite(v, "color") <= 1 for v in node["color"])
        ):
            raise ValueError("UI color requires three values in [0,1]")
        child_clip = (
            [max(clip[0], x), max(clip[1], y), min(clip[2], x + width), min(clip[3], y + height)]
            if flag(p("clips_children", False))
            else clip
        )
        orientation = p("orientation", "vertical")
        if node["type"] == "stack_panel" and orientation not in {"vertical", "horizontal"}:
            raise Unsupported("UI stack orientation")
        axis = 0 if orientation == "horizontal" else 1
        cursor = 0
        states = {
            p(key, "") for key in ("default_control", "hover_control", "pressed_control", "locked_control")
        } - {""}
        active = (
            p("locked_control", "")
            if not enabled
            else p("pressed_control", "")
            if self.current["pressed"] == node["id"]
            else p("hover_control", "")
            if self.current["hover"] == node["id"]
            else p("default_control", "")
        )
        active = active or p("default_control", "")
        for child_id in node["children"]:
            child = self._by_id[child_id]
            shown = visible and (child["name"] not in states or child["name"] == active)
            if node["type"] == "edit_box" and child["name"] == p("place_holder_control", ""):
                shown = shown and not node["text"]
            self.layout(
                child,
                rect,
                child_clip,
                shown,
                enabled,
                alpha if flag(p("propagate_alpha", False)) else 1,
                layer,
                (axis, cursor) if node["type"] == "stack_panel" else None,
            )
            if node["type"] == "stack_panel" and not flag(self.prop(child, "ignored", False)):
                cursor += child["rect"][axis + 2] - child["rect"][axis]

    def update_bindings(self, bindings):
        if (
            self.current is None
            or not isinstance(bindings, dict)
            or any(not key.startswith("#") for key in bindings)
        ):
            raise ValueError("Open a UI fixture and supply #binding values")
        if len(set(self.current["bindings"]) | set(bindings)) > 256:
            raise ValueError("UI binding limit")
        before = deepcopy(self.current["bindings"])
        self.current["bindings"].update(deepcopy(bindings))
        try:
            self.rebuild()
        except Exception:
            self.current["bindings"] = before
            self.rebuild()
            raise
        return self.snapshot()

    def regions(self):
        if self.current is None:
            return []
        width, height = self.current["viewport"]
        result = []
        # A later control is drawn on top at the same layer and must receive the hit.
        for node in reversed(sorted(self.nodes, key=lambda node: node["layer"])):
            if (
                node["type"] not in {"button", "toggle", "edit_box"}
                or not node["visible"]
                or not node["enabled"]
            ):
                continue
            rect = [
                max(node["rect"][0], node["clip"][0]),
                max(node["rect"][1], node["clip"][1]),
                min(node["rect"][2], node["clip"][2]),
                min(node["rect"][3], node["clip"][3]),
            ]
            if rect[0] >= rect[2] or rect[1] >= rect[3]:
                continue
            result.append(
                {
                    "id": "ui:" + node["id"],
                    "control": node["id"],
                    "kind": node["type"],
                    "label": node["text"],
                    "rect": [rect[0] / width, rect[1] / height, rect[2] / width, rect[3] / height],
                }
            )
        return result

    def input(self, node, button="button.menu_select"):
        if not node["visible"] or not node["enabled"]:
            return {"events": []}
        emitted = []
        for mapping in node["raw"].get("button_mappings", []):
            if set(mapping) - MAPPING_FIELDS or mapping.get("mapping_type", "pressed") != "pressed":
                raise Unsupported("UI input mapping not modeled")
            mode = mapping.get("input_mode_condition", "not_gamepad")
            if mode not in {"not_gamepad", "gamepad"}:
                raise Unsupported("UI input mode condition not modeled")
            if mode == "gamepad":
                continue
            source = self.value(
                mapping["from_button_id"],
                node["variables"],
                lambda key: self.prop(node, key.removeprefix("#")),
            )
            if source == button:
                target = self.value(
                    mapping["to_button_id"],
                    node["variables"],
                    lambda key: self.prop(node, key.removeprefix("#")),
                )
                if not isinstance(target, str) or not target or len(target) > 256:
                    raise ValueError("UI mapped button must be a short nonempty string")
                emitted.append({"control": node["id"], "button": target, "tick": self.world.tick})
        if len(self.current["events"]) + len(emitted) > 256 or len(self.world.events) + len(emitted) > 10000:
            raise Unsupported("UI event budget")
        before = deepcopy(self.current)
        try:
            if flag(self.prop(node, "focus_enabled", True)):
                self.current["focus"] = node["id"]
            if node["type"] == "toggle" and button == "button.menu_select":
                value = not flag(self.prop(node, "toggle_state"))
                self.current["state"].setdefault(node["id"], {})["toggle_state"] = value
            self.current["events"].extend(emitted)
            self.rebuild()
        except Exception:
            self.current = before
            self.rebuild()
            raise
        # These events are inspectable evidence; no native menu handler is invented.
        for event in emitted:
            self.world.log("ui-button", **event)
        return {"events": emitted, "focus": self.current["focus"]}

    def pointer(self, x, y, pressed=None):
        if (
            self.current is None
            or not 0 <= finite(x, "pointer x") <= 1
            or not 0 <= finite(y, "pointer y") <= 1
        ):
            raise ValueError("UI pointer needs normalized x/y and an open screen")
        target = next(
            (
                region["control"]
                for region in self.regions()
                if region["rect"][0] <= x <= region["rect"][2] and region["rect"][1] <= y <= region["rect"][3]
            ),
            None,
        )
        if pressed is not None and type(pressed) is not bool:
            raise ValueError("Pointer pressed must be boolean")
        before = deepcopy(self.current)
        try:
            self.current["hover"] = target
            if pressed is not None:
                was_down = self.current["pointer_down"]
                self.current["pointer_down"] = pressed
                self.current["pressed"] = target if pressed else None
                if pressed and target and not was_down:
                    return self.input(self._by_id[target])
            self.rebuild()
        except Exception:
            self.current = before
            self.rebuild()
            raise
        return {"hover": target, "pressed": self.current["pressed"]}

    def click(self, region):
        return self.input(self._by_id[region["control"]])

    def text(self, value):
        if self.current is None or self.current["focus"] not in self._by_id:
            raise ValueError("Focus a UI edit_box before typing")
        node = self._by_id[self.current["focus"]]
        if (
            node["type"] != "edit_box"
            or not node["visible"]
            or not node["enabled"]
            or not isinstance(value, str)
        ):
            raise ValueError("UI text input requires an edit_box and a string")
        maximum = self.prop(node, "max_length", 256)
        if type(maximum) is not int or not 0 <= maximum <= 4096 or len(value) > maximum:
            raise ValueError("UI text length limit")
        before = deepcopy(self.current["state"])
        self.current["state"].setdefault(node["id"], {})["text"] = value
        try:
            self.rebuild()
        except Exception:
            self.current["state"] = before
            self.rebuild()
            raise
        return {"control": node["id"], "text": value}

    def key(self, key):
        if key == "escape":
            self.close()
            return {"closed": True}
        clickable = {r["control"] for r in self.regions()}
        available = [
            node["id"]
            for node in self.nodes
            if node["id"] in clickable and flag(self.prop(node, "focus_enabled", True))
        ]
        if key == "tab":
            index = available.index(self.current["focus"]) + 1 if self.current["focus"] in available else 0
            self.current["focus"] = available[index % len(available)] if available else None
        elif key in {"up", "down", "left", "right"} and self.current["focus"]:
            node = self._by_id[self.current["focus"]]
            target = self.prop(node, "focus_change_" + key, "")
            if target:
                selected = self.find_control(target, node)
                if selected["id"] in available:
                    self.current["focus"] = selected["id"]
        elif key in {"enter", "space"} and self.current["focus"] in available:
            return self.input(self._by_id[self.current["focus"]])
        else:
            raise Unsupported("JSON UI key not modeled: " + key)
        return {"focus": self.current["focus"]}

    def snapshot(self):
        if self.current is None:
            return None
        return {
            **deepcopy(self.current),
            "nodes": [
                {
                    k: deepcopy(node[k])
                    for k in (
                        "id",
                        "name",
                        "type",
                        "parent",
                        "rect",
                        "clip",
                        "visible",
                        "enabled",
                        "alpha",
                        "layer",
                        "text",
                        "color",
                    )
                }
                for node in self.nodes
            ],
        }
