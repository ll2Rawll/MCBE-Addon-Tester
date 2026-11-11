"""Authoritative entity state used by controls, scripts, commands and assertions."""

import math
from copy import deepcopy

from .diagnostics import Unsupported


class EntitySystem:
    def entity_data(self, entity_id):
        if entity_id == "player":
            return self.player
        if entity_id not in self.entities:
            raise ValueError("Entity handle is invalid")
        return self.entities[entity_id]

    def query_entities(self, options, players_only=False):
        from .world import vector

        allowed = {
            "type",
            "excludeTypes",
            "tags",
            "excludeTags",
            "name",
            "location",
            "minDistance",
            "maxDistance",
            "closest",
            "farthest",
            "families",
            "excludeFamilies",
        }
        if not isinstance(options, dict) or set(options) - allowed:
            raise Unsupported("Entity query options: " + str(set(options) - allowed))
        origin = vector(options["location"]) if "location" in options else (0, 0, 0)
        candidates = (
            [("player", self.player)] if players_only else [("player", self.player), *self.entities.items()]
        )
        result = []
        for key, e in candidates:
            if e.get("health", 1) <= 0:
                continue
            families = e.get("components", {}).get("minecraft:type_family", {}).get("family", [])
            if options.get("type", e["identifier"]) != e["identifier"] or e["identifier"] in options.get(
                "excludeTypes", []
            ):
                continue
            if any(tag not in e.get("tags", []) for tag in options.get("tags", [])) or any(
                tag in e.get("tags", []) for tag in options.get("excludeTags", [])
            ):
                continue
            if any(family not in families for family in options.get("families", [])) or any(
                family in families for family in options.get("excludeFamilies", [])
            ):
                continue
            if "name" in options and options["name"] != e.get("name", e.get("nameTag", "")):
                continue
            distance = math.dist(origin, e["position"])
            if not options.get("minDistance", 0) <= distance <= options.get("maxDistance", math.inf):
                continue
            result.append((key, distance))
        if "closest" in options or "farthest" in options:
            count = options.get("closest", options.get("farthest"))
            if type(count) is not int or not 0 <= count <= 128:
                raise ValueError("Invalid entity query count")
            result.sort(key=lambda v: (v[1], v[0]), reverse="farthest" in options)
            result = result[:count]
        return [key for key, _ in result]

    def damage(self, entity_id, amount, cause="none", source=None):
        if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
            raise ValueError("Damage must be finite and nonnegative")
        e = self.entity_data(entity_id)
        if e.get("health", 0) <= 0:
            return False
        if entity_id == "player" and e.get("mode") in {"creative", "spectator"} and cause != "void":
            return False
        e["health"] = max(0, e["health"] - amount)
        self.dispatch(
            "entityHurt",
            {"hurtEntity": entity_id, "damage": amount, "cause": cause, "damagingEntity": source},
        )
        if e["health"] == 0:
            self.dispatch("entityDie", {"deadEntity": entity_id, "cause": cause, "damagingEntity": source})
        return True

    def set_property(self, entity_id, name, value):
        e = self.entity_data(entity_id)
        definition = (
            self.catalog.entities.get(e["identifier"], {})
            .get("description", {})
            .get("properties", {})
            .get(name)
        )
        if definition is None:
            raise ValueError("Unknown entity property")
        kind = definition["type"]
        if kind == "bool" and type(value) is not bool or kind == "int" and type(value) is not int:
            raise ValueError("Invalid entity property type")
        if kind == "float" and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError("Invalid entity property type")
        if kind == "enum" and value not in definition["values"]:
            raise ValueError("Invalid enum property")
        if "range" in definition and not definition["range"][0] <= value <= definition["range"][1]:
            raise ValueError("Entity property out of range")
        e.setdefault("properties", {})[name] = value

    def entity_api(self, op, entity_id, *args):
        from .world import vector

        if op == "valid":
            return entity_id == "player" or entity_id in self.entities
        e = self.entity_data(entity_id)
        if op == "read":
            return deepcopy(e)
        if op == "nameTag":
            if not isinstance(args[0], str) or len(args[0]) > 256:
                raise ValueError("Invalid nameTag")
            e["nameTag"] = args[0]
        elif op == "teleport":
            options = args[1] if len(args) > 1 else {}
            if set(options) - {"rotation", "checkForBlocks"}:
                raise Unsupported("Teleport option not modeled")
            position = vector(args[0])
            if options.get("checkForBlocks") and self.intersects(position):
                raise ValueError("Teleport destination is blocked")
            e["position"], e["velocity"] = list(position), [0, 0, 0]
            if "rotation" in options:
                self.entity_api("rotation", entity_id, options["rotation"])
        elif op == "rotation":
            rotation = args[0]
            if any(
                type(rotation.get(k)) not in (int, float) or not math.isfinite(rotation[k])
                for k in ("x", "y")
            ):
                raise ValueError("Invalid rotation")
            e.update(pitch=max(-90, min(90, rotation["x"])), yaw=rotation["y"] % 360)
        elif op == "impulse":
            impulse = vector(args[0])
            e["velocity"] = [e["velocity"][i] + impulse[i] for i in range(3)]
        elif op == "clearVelocity":
            e["velocity"] = [0, 0, 0]
        elif op == "damage":
            options = args[1] if len(args) > 1 else {}
            return self.damage(
                entity_id, args[0], options.get("cause", "none"), options.get("damagingEntity")
            )
        elif op == "kill":
            return self.damage(entity_id, e["health"], "void")
        elif op == "remove":
            if entity_id == "player":
                raise ValueError("Cannot remove the player")
            del self.entities[entity_id]
            self.dispatch("entityRemove", {"removedEntityId": entity_id, "typeId": e["identifier"]})
        elif op == "health":
            value = args[0]
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 <= value <= e.get("max_health", 20)
            ):
                raise ValueError("Health outside range")
            e["health"] = value
        elif op in {"addTag", "removeTag"}:
            tag = args[0]
            if not isinstance(tag, str) or len(tag) > 256:
                raise ValueError("Invalid entity tag")
            tags = e.setdefault("tags", [])
            if op == "addTag":
                if tag in tags:
                    return False
                if len(tags) >= 256:
                    raise ValueError("Entity tag limit")
                tags.append(tag)
                return True
            if tag in tags:
                tags.remove(tag)
                return True
            return False
        elif op == "property":
            self.set_property(entity_id, args[0], args[1])
        elif op == "resetProperty":
            value = self.catalog.entities[e["identifier"]]["description"]["properties"][args[0]]["default"]
            self.set_property(entity_id, args[0], value)
        elif op == "event":
            self.entity_event(entity_id, args[0])
        elif op == "effect":
            name, duration, options = args
            name = name if ":" in name else "minecraft:" + name
            supported = {
                "minecraft:speed",
                "minecraft:slowness",
                "minecraft:jump_boost",
                "minecraft:regeneration",
                "minecraft:poison",
            }
            if name not in supported:
                raise Unsupported("Effect behavior: " + name)
            amplifier = options.get("amplifier", 0)
            if (
                type(duration) is not int
                or not 1 <= duration <= 20000000
                or type(amplifier) is not int
                or not 0 <= amplifier <= 255
            ):
                raise ValueError("Invalid effect duration/amplifier")
            e["effects"] = [v for v in e.get("effects", []) if v["typeId"] != name]
            value = {"typeId": name, "duration": duration, "amplifier": amplifier}
            e["effects"].append(value)
            return deepcopy(value)
        elif op == "removeEffect":
            name = args[0] if ":" in args[0] else "minecraft:" + args[0]
            old = e.get("effects", [])
            e["effects"] = [v for v in old if v["typeId"] != name]
            return old != e["effects"]
        elif op == "selected":
            if entity_id != "player" or type(args[0]) is not int or not 0 <= args[0] <= 8:
                raise ValueError("Invalid selected slot")
            e["selected"] = args[0]
        elif op == "mode":
            if args[0] not in {"creative", "survival", "adventure", "spectator"}:
                raise ValueError("Unknown game mode")
            e["mode"] = args[0]
        else:
            raise Unsupported("Entity operation: " + op)
        return None

    def spawn_item(self, stack, position):
        from .world import vector

        stack = self.inventory.validate(stack)
        if len(self.entities) >= 128:
            raise ValueError("Entity limit")
        key = f"e{self.next_entity}"
        self.next_entity += 1
        self.entities[key] = {
            "id": key,
            "identifier": "minecraft:item",
            "position": list(vector(position)),
            "velocity": [0, 0, 0],
            "itemStack": stack,
            "health": 5,
            "max_health": 5,
            "components": {"minecraft:physics": {}},
            "properties": {},
            "groups": [],
            "age": 0,
            "variables": {},
            "controllers": {},
        }
        self.dispatch("entitySpawn", {"entity_id": key})
        return key
