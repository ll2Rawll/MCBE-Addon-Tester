"""Deterministic virtual world and semantic controls. Never sends operating-system input."""

import math
import random
from collections import Counter
from copy import deepcopy

from .diagnostics import Unsupported
from .molang import evaluate


def vector(value, integer=False):
    if isinstance(value, dict):
        value = [value[k] for k in ("x", "y", "z")]
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("Expected a three-coordinate position")
    if any(not isinstance(x, (int, float)) or not math.isfinite(x) or abs(x) > 128 for x in value):
        raise ValueError("Coordinates must be finite and inside [-128,128]")
    if integer and any(int(x) != x for x in value):
        raise ValueError("Block coordinates must be integers")
    return tuple(int(x) if integer else float(x) for x in value)


def collision_box(components):
    box = components.get("minecraft:collision_box", True)
    if box is False:
        return None
    if box is True:
        return (0, 0, 0), (1, 1, 1)
    origin = box.get("origin", [-8, 0, -8])
    size = box.get("size", [16, 16, 16])
    low = (origin[0] / 16 + 0.5, origin[1] / 16, origin[2] / 16 + 0.5)
    return low, tuple(low[i] + size[i] / 16 for i in range(3))


class World:
    def __init__(self, catalog, seed=0):
        self.catalog = catalog
        self.seed = int(seed)
        self.random = random.Random(self.seed)
        self.tick = 0
        self.blocks = {}
        self.entities = {}
        self.next_entity = 1
        self.player = {
            "position": [0.5, 0.0, 4.5],
            "velocity": [0.0, 0.0, 0.0],
            "yaw": 180.0,
            "pitch": 12.0,
            "on_ground": True,
            "health": 20,
            "inventory": {},
            "hotbar": [],
            "selected": 0,
            "menu": "game",
            "mode": "creative",
        }
        self.events = []
        self.trace = []
        self.script = None
        self.dynamic = {}

    def log(self, kind, **data):
        if len(self.events) >= 10000:
            raise Unsupported("Event budget exhausted")
        self.events.append({"tick": self.tick, "kind": kind, **deepcopy(data)})

    def dispatch(self, event, data):
        self.log(event, **{k: v for k, v in data.items() if not k.startswith("_")})
        if self.script:
            return self.script.event(event, data)
        return data

    def put(self, position, identifier, states=None):
        position = vector(position, integer=True)
        if position[1] < 0:
            raise ValueError("The simulator floor occupies y < 0")
        if identifier == "minecraft:air":
            self.blocks.pop(position, None)
            return
        if len(self.blocks) >= 2048 and position not in self.blocks:
            raise Unsupported("2048-block simulation limit")
        components, chosen = self.catalog.block(identifier, states)
        self.blocks[position] = {"identifier": identifier, "states": chosen, "components": components}

    def spawn(self, identifier, position):
        if identifier not in self.catalog.entities:
            raise Unsupported(f"Entity not supplied: {identifier}")
        if len(self.entities) >= 128:
            raise Unsupported("128-entity simulation limit")
        definition = self.catalog.entities[identifier]
        properties = {
            k: v.get("default", 0) for k, v in definition.get("description", {}).get("properties", {}).items()
        }
        components = deepcopy(definition.get("components", {}))
        entity_id = f"e{self.next_entity}"
        self.next_entity += 1
        entity = {
            "id": entity_id,
            "identifier": identifier,
            "position": list(vector(position)),
            "velocity": [0.0, 0.0, 0.0],
            "components": components,
            "groups": [],
            "health": components.get("minecraft:health", {}).get("value", 20),
            "properties": properties,
            "age": 0,
            "variables": {},
            "controllers": {},
        }
        self.entities[entity_id] = entity
        if "minecraft:entity_spawned" in definition.get("events", {}):
            self.entity_event(entity_id, "minecraft:entity_spawned")
        self.dispatch("entitySpawn", {"entity_id": entity_id})
        return entity_id

    def entity_event(self, entity_id, event):
        entity = self.entities[entity_id]
        definition = self.catalog.entities[entity["identifier"]]
        events = definition.get("events", {})
        if event not in events:
            raise Unsupported(f"Entity event not supplied: {event}")
        count = 0

        def apply(node, depth=0):
            nonlocal count
            count += 1
            if count > 100 or depth > 16:
                raise Unsupported("Entity event budget exhausted")
            if "filters" in node:
                raise Unsupported("Entity event filters are not implemented")
            for key in node:
                if key not in {"add", "remove", "sequence", "randomize", "weight", "set_property", "trigger"}:
                    raise Unsupported(f"Entity event response not modeled: {key}")
            for step in node.get("sequence", []):
                apply(step, depth + 1)
            choices = node.get("randomize", [])
            if choices:
                weights = [choice.get("weight", 1) for choice in choices]
                if (
                    any(not isinstance(w, (float, int)) or not math.isfinite(w) or w < 0 for w in weights)
                    or sum(weights) <= 0
                ):
                    raise ValueError("Invalid event weights")
                apply(self.random.choices(choices, weights=weights)[0], depth + 1)
            for group in node.get("remove", {}).get("component_groups", []):
                if group in entity["groups"]:
                    entity["groups"].remove(group)
            for group in node.get("add", {}).get("component_groups", []):
                if group not in definition.get("component_groups", {}):
                    raise ValueError(f"Missing component group: {group}")
                if group not in entity["groups"]:
                    entity["groups"].append(group)
            for key, value in node.get("set_property", {}).items():
                spec = definition.get("description", {}).get("properties", {}).get(key)
                if spec is None:
                    raise ValueError(f"Unknown entity property: {key}")
                evaluated = value if spec.get("type") == "enum" else evaluate(value, self.context(entity))
                if spec.get("type") == "enum" and evaluated not in spec.get("values", []):
                    raise ValueError(f"Invalid property value: {key}")
                if "range" in spec and not spec["range"][0] <= evaluated <= spec["range"][1]:
                    raise ValueError(f"Property outside range: {key}")
                entity["properties"][key] = evaluated
            if "trigger" in node:
                target = node["trigger"]
                target = target.get("event") if isinstance(target, dict) else target
                if target not in events:
                    raise ValueError(f"Missing triggered event: {target}")
                apply(events[target], depth + 1)

        apply(events[event])
        entity["components"] = deepcopy(definition.get("components", {}))
        for group in entity["groups"]:
            entity["components"].update(deepcopy(definition["component_groups"][group]))
        self.log("entity-event", entity_id=entity_id, event=event)

    def context(self, entity=None):
        entity = entity or {}
        return {
            "query.life_time": entity.get("age", self.tick) / 20,
            "query.anim_time": entity.get("age", self.tick) / 20,
            "query.health": entity.get("health", self.player["health"]),
            "query.is_alive": entity.get("health", 20) > 0,
            "query.is_on_ground": entity.get("position", self.player["position"])[1] <= 0,
            "query.ground_speed": math.hypot(*[entity.get("velocity", [0, 0, 0])[i] for i in (0, 2)]) * 20,
            "query.modified_move_speed": math.hypot(*[entity.get("velocity", [0, 0, 0])[i] for i in (0, 2)]),
            "query.is_moving": any(entity.get("velocity", [0, 0, 0])),
            "properties": entity.get("properties", {}),
        }

    def intersects(self, position, width=0.6, height=1.8):
        low = (position[0] - width / 2, position[1], position[2] - width / 2)
        high = (position[0] + width / 2, position[1] + height, position[2] + width / 2)
        if low[1] < -1e-8:
            return True
        for pos, block in self.blocks.items():
            shape = collision_box(block["components"])
            if shape and all(
                low[i] < pos[i] + shape[1][i] - 1e-8 and high[i] > pos[i] + shape[0][i] + 1e-8
                for i in range(3)
            ):
                return True
        return False

    def move(self, obj, width=0.6, height=1.8, collide=True):
        on_ground = False
        # Small substeps avoid tunneling; this remains an approximation, not native physics.
        for axis in (1, 0, 2):
            delta = obj["velocity"][axis]
            steps = max(1, math.ceil(abs(delta) / 0.05))
            if steps > 100:
                raise Unsupported("Movement budget exceeded")
            for _ in range(steps):
                position = obj["position"].copy()
                position[axis] += delta / steps
                if collide and self.intersects(position, width, height):
                    if axis == 1 and delta < 0:
                        on_ground = True
                    obj["velocity"][axis] = 0
                    break
                if any(abs(c) > 128 for c in position):
                    raise Unsupported("Movement left bounded simulation world")
                obj["position"] = position
        obj["on_ground"] = on_ground

    def advance(self, ticks=1, keys=None):
        if type(ticks) is not int or not 1 <= ticks <= 600:
            raise ValueError("Use 1–600 ticks per action")
        keys = set(keys or [])
        if keys - {"w", "a", "s", "d", "space", "shift", "ctrl"}:
            raise ValueError(f"Unsupported held keys: {sorted(keys)}")
        for _ in range(ticks):
            if self.tick >= 20000:
                raise Unsupported("20,000-tick session limit")
            self.tick += 1
            if self.player["menu"] == "game":
                forward = int("w" in keys) - int("s" in keys)
                side = int("d" in keys) - int("a" in keys)
                length = max(1, math.hypot(forward, side))
                speed = 0.215 * (1.3 if "ctrl" in keys else 0.3 if "shift" in keys else 1)
                yaw = math.radians(self.player["yaw"])
                self.player["velocity"][0] = (
                    (-math.sin(yaw) * forward - math.cos(yaw) * side) / length * speed
                )
                self.player["velocity"][2] = (math.cos(yaw) * forward - math.sin(yaw) * side) / length * speed
                if "space" in keys and self.player["on_ground"]:
                    self.player["velocity"][1] = 0.42
                self.player["velocity"][1] = (self.player["velocity"][1] - 0.08) * 0.98
                self.move(self.player)
            for entity in list(self.entities.values()):
                entity["age"] += 1
                physics = entity["components"].get("minecraft:physics", {})
                if physics.get("has_gravity", True):
                    entity["velocity"][1] = (entity["velocity"][1] - 0.08) * 0.98
                box = entity["components"].get("minecraft:collision_box", {})
                self.move(
                    entity, box.get("width", 0.6), box.get("height", 1.8), physics.get("has_collision", True)
                )
            if self.script:
                self.script.advance()
            from .render import active_animations

            for entity in self.entities.values():
                try:
                    active_animations(self, entity)
                except (ValueError, KeyError, TypeError) as exc:
                    self.catalog.findings.unsupported("animation-controller", entity["identifier"], str(exc))

    def ray(self, reach=5):
        yaw, pitch = map(math.radians, (self.player["yaw"], self.player["pitch"]))
        direction = (-math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch))
        origin = [*self.player["position"]]
        origin[1] += 1.62
        last = None
        for step in range(1, int(reach * 100) + 1):
            point = [origin[i] + direction[i] * step / 100 for i in range(3)]
            cell = tuple(math.floor(v) for v in point)
            if cell in self.blocks or cell[1] < 0:
                return cell, last
            last = cell
        return None, None

    def give(self, identifier, count=1):
        if identifier not in self.catalog.items and identifier not in self.catalog.blocks:
            raise Unsupported(f"Item not supplied: {identifier}")
        if type(count) is not int or not 1 <= count <= 2304:
            raise ValueError("Item count must be 1–2304")
        inv = self.player["inventory"]
        if inv.get(identifier, 0) + count > 2304:
            raise Unsupported("Per-item inventory limit exceeded")
        inv[identifier] = inv.get(identifier, 0) + count
        if identifier not in self.player["hotbar"] and len(self.player["hotbar"]) < 9:
            self.player["hotbar"].append(identifier)

    def craft(self, recipe_id, grid):
        recipe = self.catalog.recipes[recipe_id]
        if len(grid) != 9 or any(x is not None and not isinstance(x, str) for x in grid):
            raise ValueError("Crafting grid needs nine item identifiers or nulls")
        if recipe["kind"] == "minecraft:recipe_shapeless":
            ingredients = recipe["ingredients"]
            needed = Counter()
            for value in ingredients:
                if "tag" in value or set(value) - {"item", "count", "data"} or value.get("data", 0) != 0:
                    raise Unsupported("Recipe ingredient tags/data are not modeled")
                needed[value["item"]] += value.get("count", 1)
            if Counter(x for x in grid if x) != needed:
                raise ValueError("Crafting grid does not match shapeless ingredients")
        elif recipe["kind"] == "minecraft:recipe_shaped":
            pattern = recipe["pattern"]
            height, width = len(pattern), max(map(len, pattern))
            if not 1 <= height <= 3 or not 1 <= width <= 3:
                raise ValueError("Invalid shaped recipe dimensions")
            matches = False
            for mirror in (False, True):
                for ox in range(4 - width):
                    for oy in range(4 - height):
                        expected = [None] * 9
                        for y, row in enumerate(pattern):
                            for x, char in enumerate(row[::-1] if mirror else row):
                                if char != " ":
                                    value = recipe["key"][char]
                                    if "tag" in value or value.get("data", 0) != 0:
                                        raise Unsupported("Recipe ingredient tags/data are not modeled")
                                    expected[(oy + y) * 3 + ox + x] = value["item"]
                        matches |= expected == grid
            if not matches:
                raise ValueError("Crafting grid does not match shaped recipe")
            needed = Counter(x for x in grid if x)
        else:
            raise Unsupported(f"Recipe type: {recipe['kind']}")
        if any(self.player["inventory"].get(k, 0) < n for k, n in needed.items()):
            raise ValueError("Inventory lacks recipe ingredients")
        result = recipe["result"]
        if isinstance(result, list):
            raise Unsupported("Multiple crafting results are not modeled")
        # Validate output before consuming input.
        if result["item"] not in self.catalog.items and result["item"] not in self.catalog.blocks:
            raise Unsupported(f"Recipe output not supplied: {result['item']}")
        for k, n in needed.items():
            self.player["inventory"][k] -= n
        self.give(result["item"], result.get("count", 1))
        self.log("crafted", recipe=recipe_id, result=result)
        return result

    def action(self, action):
        if len(self.trace) >= 2000:
            raise Unsupported("2000-action trace limit")
        self.trace.append(deepcopy(action))
        op = action.get("type")
        if op == "set_block":
            self.put(action["position"], action["identifier"], action.get("states"))
        elif op == "set_state":
            position = vector(action["position"], True)
            block = self.blocks[position]
            self.put(position, block["identifier"], {**block["states"], **action["states"]})
        elif op == "spawn":
            return {"entity_id": self.spawn(action["identifier"], action.get("position", [0, 0, 0]))}
        elif op == "entity_event":
            self.entity_event(action["entity_id"], action["event"])
        elif op == "teleport":
            self.player["position"] = list(vector(action["position"]))
            self.player["velocity"] = [0, 0, 0]
        elif op == "look":
            for key in ("yaw", "pitch"):
                value = action.get(key, self.player[key])
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("Look angles must be finite")
                self.player[key] = value % 360 if key == "yaw" else max(-89, min(89, value))
        elif op == "give":
            self.give(action["identifier"], action.get("count", 1))
        elif op == "craft":
            return self.craft(action["recipe"], action["grid"])
        elif op == "tick":
            self.advance(action.get("ticks", 1))
        elif op == "key":
            key = action["key"].lower()
            if key == "e":
                self.player["menu"] = "game" if self.player["menu"] == "inventory" else "inventory"
            elif key == "escape":
                self.player["menu"] = "pause" if self.player["menu"] == "game" else "game"
            elif key in "123456789" and len(key) == 1:
                self.player["selected"] = int(key) - 1
            else:
                self.advance(action.get("ticks", 1), [key])
        elif op == "move":
            self.advance(action.get("ticks", 1), action.get("keys", []))
        elif op in ("interact", "break", "place"):
            position = vector(action["position"], True)
            if op == "break":
                block = self.blocks[position]
                if block["components"].get("minecraft:destructible_by_mining") is False:
                    raise ValueError("Block cannot be mined")
                event = self.dispatch(
                    "before:playerBreakBlock", {"position": list(position), "cancel": False}
                )
                if event.get("cancel"):
                    return {"cancelled": True}
                self.put(position, "minecraft:air")
                self.dispatch(
                    "playerBreakBlock", {"position": list(position), "identifier": block["identifier"]}
                )
            elif op == "place":
                if position in self.blocks:
                    raise ValueError("Placement cell is occupied")
                identifier = action["identifier"]
                item = self.catalog.items.get(identifier, {}).get("components", {})
                block_id = item.get("minecraft:block_placer", {}).get("block", identifier)
                self.put(position, block_id, action.get("states"))
                self.dispatch("playerPlaceBlock", {"position": list(position), "identifier": block_id})
            else:
                if position not in self.blocks:
                    raise ValueError("No block at interaction position")
                self.dispatch("playerInteractWithBlock", {"position": list(position)})
        elif op == "use":
            hotbar = self.player["hotbar"]
            if self.player["selected"] >= len(hotbar):
                raise ValueError("Selected slot is empty")
            item = hotbar[self.player["selected"]]
            event = self.dispatch("before:itemUse", {"identifier": item, "cancel": False})
            if not event.get("cancel"):
                self.dispatch("itemUse", {"identifier": item})
        elif op == "click":
            return self.click(action)
        else:
            raise Unsupported(f"Unknown simulator action: {op}")
        return {"tick": self.tick, "menu": self.player["menu"]}

    def ui_regions(self):
        if self.player["menu"] == "pause":
            return [{"id": "resume", "rect": [0.3, 0.4, 0.7, 0.52], "label": "Resume simulation"}]
        if self.player["menu"] == "inventory":
            result = [{"id": "close", "rect": [0.82, 0.12, 0.9, 0.2], "label": "X"}]
            for i, identifier in enumerate(sorted(set(self.catalog.items) | set(self.catalog.blocks))[:36]):
                x, y = 0.12 + (i % 9) * 0.084, 0.28 + (i // 9) * 0.13
                result.append(
                    {
                        "id": identifier,
                        "rect": [x, y, x + 0.074, y + 0.11],
                        "label": self.catalog.display_name(identifier),
                    }
                )
            return result
        return []

    def click(self, action):
        if self.player["menu"] != "game":
            x, y = action.get("x"), action.get("y")
            if (
                not isinstance(x, (int, float))
                or not isinstance(y, (int, float))
                or not (0 <= x <= 1 and 0 <= y <= 1)
            ):
                raise ValueError("Menu click needs normalized x/y")
            for region in self.ui_regions():
                a, b, c, d = region["rect"]
                if a <= x <= c and b <= y <= d:
                    if region["id"] in {"resume", "close"}:
                        self.player["menu"] = "game"
                    else:
                        self.give(region["id"])
                    return {"clicked": region["id"]}
            return {"clicked": None}
        hit, adjacent = self.ray()
        if not hit:
            return {"hit": None}
        if action.get("button", "left") == "left":
            if hit[1] < 0:
                raise Unsupported("Simulator floor cannot be broken")
            return self.action({"type": "break", "position": list(hit)})
        if action.get("button") != "right":
            raise ValueError("Mouse button must be left or right")
        hotbar = self.player["hotbar"]
        if self.player["selected"] < len(hotbar) and adjacent:
            return self.action(
                {"type": "place", "position": list(adjacent), "identifier": hotbar[self.player["selected"]]}
            )
        return self.action({"type": "interact", "position": list(hit)})

    def snapshot(self):
        return {
            "tick": self.tick,
            "seed": self.seed,
            "player": deepcopy(self.player),
            "blocks": {
                ",".join(map(str, pos)): deepcopy(value) for pos, value in sorted(self.blocks.items())
            },
            "entities": deepcopy(self.entities),
            "dynamic_properties": deepcopy(self.dynamic),
            "ui_regions": self.ui_regions(),
            "events": deepcopy(self.events[-100:]),
        }
