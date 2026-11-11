"""Resolve content from supplied packs. Vanilla assets are never silently invented."""

import hashlib
import io
import itertools
from copy import deepcopy
from pathlib import PurePosixPath

from PIL import Image

from ..packs import inventory, jsonc, read_files
from .diagnostics import Findings, Unsupported
from .molang import evaluate

BLOCK_COMPONENTS = {
    "minecraft:geometry",
    "minecraft:material_instances",
    "minecraft:collision_box",
    "minecraft:selection_box",
    "minecraft:display_name",
    "minecraft:destructible_by_mining",
    "minecraft:custom_components",
    "minecraft:tick",
}
ITEM_COMPONENTS = {
    "minecraft:display_name",
    "minecraft:icon",
    "minecraft:block_placer",
    "minecraft:max_stack_size",
    "minecraft:durability",
    "minecraft:tags",
    "minecraft:custom_components",
}
ENTITY_COMPONENTS = {
    "minecraft:health",
    "minecraft:collision_box",
    "minecraft:physics",
    "minecraft:type_family",
    "minecraft:movement",
    "minecraft:movement.basic",
    "minecraft:nameable",
    "minecraft:inventory",
}
METADATA = {"format_version", "description"}


class Catalog:
    def __init__(self, files):
        if len(files) > 20000 or sum(len(v) for v in files.values()) > 256 * 1024 * 1024:
            raise ValueError("Pack size limit exceeded")
        self.files = files
        self.packs = inventory(files)
        self.findings = Findings()
        self.blocks, self.items, self.entities, self.clients, self.recipes = {}, {}, {}, {}, {}
        self.geometries, self.animations, self.controllers = {}, {}, {}
        self.terrain, self.icons, self.strings = {}, {}, {}
        self.scripts = []
        self.origins = {}
        self._textures = {}
        self.occurrences = []
        digest = hashlib.sha256()
        for name in sorted(files):
            digest.update(name.encode() + b"\0" + hashlib.sha256(files[name]).digest())
        self.hash = digest.hexdigest()
        self._index()

    @classmethod
    def open(cls, source):
        return cls(read_files(source))

    def feature(self, name, path, status="modeled"):
        if len(self.occurrences) < 20000:
            self.occurrences.append({"feature": name, "location": path, "status": status})
        if status == "unsupported":
            self.findings.unsupported(name, path)

    def _index(self):
        identifiers = set()
        for pack in self.packs:
            prefix = pack["prefix"]
            for dep in pack["manifest"].get("dependencies", []):
                if dep.get("uuid") and dep["uuid"] not in {p["uuid"] for p in self.packs}:
                    self.findings.unsupported(
                        "external-pack", prefix, f"Dependency not supplied: {dep['uuid']}"
                    )
                if dep.get("module_name") and dep["module_name"] not in {
                    "@minecraft/server",
                    "@minecraft/server-ui",
                }:
                    self.findings.unsupported("script-module", prefix, dep["module_name"])
            for module in pack["manifest"].get("modules", []):
                if module["type"] == "script":
                    entry = prefix + module.get("entry", "")
                    if entry not in self.files:
                        self.findings.error("script-entry", entry, "Entry file is missing")
                    else:
                        self.scripts.append(entry)
            for name, raw in sorted(self.files.items()):
                if not name.startswith(prefix):
                    continue
                local = name[len(prefix) :]
                if local == "texts/en_US.lang":
                    self.strings.update(
                        line.split("=", 1)
                        for line in raw.decode("utf-8-sig").splitlines()
                        if "=" in line and not line.startswith("#")
                    )
                if not name.endswith(".json") or local in {"manifest.json", "texts/languages.json"}:
                    continue
                try:
                    data = jsonc(raw)
                    if not isinstance(data, dict):
                        raise ValueError("Expected a JSON object")
                    if local in ("textures/terrain_texture.json", "textures/item_texture.json"):
                        target = self.terrain if "terrain" in local else self.icons
                        for key, entry in data.get("texture_data", {}).items():
                            target[key] = (prefix, entry.get("textures"))
                        continue
                    handled = False
                    for key, target in (
                        ("minecraft:block", self.blocks),
                        ("minecraft:item", self.items),
                        ("minecraft:entity", self.entities),
                        ("minecraft:client_entity", self.clients),
                    ):
                        if key not in data:
                            continue
                        handled = True
                        definition = data[key]
                        identifier = definition.get("description", {}).get("identifier")
                        if not isinstance(identifier, str) or ":" not in identifier:
                            raise ValueError("Missing namespaced content identifier")
                        if (key, identifier) in identifiers:
                            self.findings.error("duplicate-identifier", name, identifier)
                        identifiers.add((key, identifier))
                        target[identifier] = definition
                        self.origins[(key, identifier)] = name
                        self.feature(key, name)
                        if key != "minecraft:client_entity":
                            supported = {
                                "minecraft:block": BLOCK_COMPONENTS,
                                "minecraft:item": ITEM_COMPONENTS,
                                "minecraft:entity": ENTITY_COMPONENTS,
                            }[key]
                            sections = [definition.get("components", {})]
                            sections += list(definition.get("component_groups", {}).values())
                            sections += [p.get("components", {}) for p in definition.get("permutations", [])]
                            for components in sections:
                                for component in components:
                                    custom = not component.startswith("minecraft:")
                                    status = "modeled" if component in supported or custom else "unsupported"
                                    self.feature(component, name, status)
                            allowed = METADATA | {"components", "component_groups", "events", "permutations"}
                            for unknown in set(definition) - allowed:
                                self.feature(f"{key}.{unknown}", name, "unsupported")
                            desc = definition.get("description", {})
                            if desc.get("traits"):
                                self.feature("block-placement-traits", name, "unsupported")
                            if desc.get("scripts") or desc.get("animations"):
                                self.feature("behavior-animation-controllers", name, "unsupported")
                        else:
                            desc = definition.get("description", {})
                            for prop in set(desc) - {
                                "identifier",
                                "textures",
                                "geometry",
                                "materials",
                                "animations",
                                "scripts",
                                "render_controllers",
                            }:
                                self.feature(f"client-entity.{prop}", name, "unsupported")
                            if desc.get("render_controllers"):
                                self.findings.approximate(
                                    "render-controllers",
                                    name,
                                    "Preview uses default geometry/texture; controller selection is not reproduced",
                                )
                    for key in data:
                        if key.startswith("minecraft:recipe_"):
                            handled = True
                            recipe = data[key]
                            identifier = recipe["description"]["identifier"]
                            self.recipes[identifier] = {**recipe, "kind": key}
                            self.feature(
                                key,
                                name,
                                "modeled" if key.endswith(("shaped", "shapeless")) else "unsupported",
                            )
                    for key, target in (
                        ("minecraft:geometry", self.geometries),
                        ("animations", self.animations),
                        ("animation_controllers", self.controllers),
                    ):
                        if key not in data:
                            continue
                        handled = True
                        if key == "minecraft:geometry":
                            for geometry in data[key]:
                                target[geometry["description"]["identifier"]] = geometry
                        else:
                            target.update(data[key])
                        self.feature(key, name)
                    if not handled:
                        self.feature(f"file:{local}", name, "unsupported")
                except (ValueError, KeyError, TypeError, AttributeError) as exc:
                    self.findings.error("pack-definition", name, str(exc))
        self.findings.approximate(
            "rendering",
            "profile",
            "Independent software renderer; no RenderDragon, PBR, lighting parity, or GPU validation",
        )
        self.findings.approximate(
            "movement", "profile", "Bounded flat-world AABB model; not calibrated native movement physics"
        )
        self.findings.approximate(
            "menus",
            "profile",
            "Simulator inventory/pause/forms layout; native Bedrock UI and custom JSON UI are not reproduced",
        )
        if self.scripts:
            self.findings.approximate(
                "script-scheduling",
                "profile",
                "QuickJS and bounded deterministic timers/jobs; native privileges, lifecycle ordering and per-version API differences are not fully reproduced",
            )

    def states(self, identifier):
        definitions = self.blocks[identifier].get("description", {}).get("states", {})
        result = {}
        for name, values in definitions.items():
            if isinstance(values, dict):
                bounds = values.get("values", values)
                if not isinstance(bounds, dict) or "min" not in bounds or "max" not in bounds:
                    raise Unsupported(f"Unknown state range for {name}")
                low, high = bounds["min"], bounds["max"]
                if type(low) is not int or type(high) is not int or high < low or high - low > 1024:
                    raise Unsupported(f"Invalid/oversized state range for {name}")
                values = list(range(low, high + 1))
            if not isinstance(values, list) or not values or len(values) > 1024:
                raise Unsupported(f"Invalid state values for {name}")
            result[name] = values
        return result

    def permutations(self, identifier, limit=256):
        states = self.states(identifier)
        count = 1
        for values in states.values():
            count *= len(values)
        if count > limit:
            self.findings.unsupported(
                "permutation-enumeration",
                identifier,
                f"{count} combinations exceed {limit}; only bounded prefix explored",
            )
        return [
            dict(zip(states, values, strict=True))
            for values in itertools.islice(itertools.product(*states.values()), limit)
        ]

    def block(self, identifier, states=None):
        if identifier == "minecraft:air":
            return {"minecraft:collision_box": False}, {}
        if identifier not in self.blocks:
            raise Unsupported(f"Block not supplied: {identifier}")
        allowed = self.states(identifier)
        chosen = {key: values[0] for key, values in allowed.items()}
        for key, value in (states or {}).items():
            if key not in allowed or value not in allowed[key] or type(value) is not type(allowed[key][0]):
                raise ValueError(f"Invalid block state {key}={value!r}")
            chosen[key] = value
        definition = self.blocks[identifier]
        components = deepcopy(definition.get("components", {}))
        for permutation in definition.get("permutations", []):
            if evaluate(permutation["condition"], {"states": chosen}):
                components.update(deepcopy(permutation.get("components", {})))
        return components, chosen

    def texture(self, name, kind="block", prefix=None):
        cache_key = (name, kind, prefix)
        if cache_key in self._textures:
            return self._textures[cache_key].copy()
        tables = self.terrain if kind == "block" else self.icons
        if name in tables:
            prefix, path = tables[name]
            if isinstance(path, list):
                if len(path) != 1:
                    self.findings.unsupported("texture-variants", str(name))
                path = path[0]
            if isinstance(path, dict):
                path = path.get("path")
        else:
            path = name
        paths = []
        if isinstance(path, str):
            for root in (
                [prefix]
                if prefix is not None
                else [p["prefix"] for p in self.packs if p["kind"] == "resource"]
            ):
                for ext in ("", ".png", ".tga"):
                    paths.append(root + path + ext)
        for candidate in paths:
            if candidate not in self.files:
                continue
            with Image.open(io.BytesIO(self.files[candidate])) as img:
                if img.width > 2048 or img.height > 2048:
                    raise Unsupported("Texture exceeds 2048x2048 simulator limit")
                image = img.convert("RGBA")
            self._textures[cache_key] = image
            return image.copy()
        self.findings.unsupported(
            "texture",
            str(name),
            "Asset not supplied; magenta checker is a placeholder, not evidence of native appearance",
        )
        image = Image.new("RGBA", (16, 16))
        image.putdata(
            [
                (220, 30, 180, 255) if (x // 4 + y // 4) % 2 else (30, 20, 35, 255)
                for y in range(16)
                for x in range(16)
            ]
        )
        return image

    def display_name(self, identifier):
        definition = self.items.get(identifier, self.blocks.get(identifier, {}))
        value = definition.get("components", {}).get("minecraft:display_name", identifier)
        value = value.get("value", identifier) if isinstance(value, dict) else value
        return self.strings.get(value, value)

    def summary(self):
        return {
            "content_sha256": self.hash,
            "packs": [{k: p[k] for k in ("name", "kind", "version")} for p in self.packs],
            "blocks": sorted(self.blocks),
            "items": sorted(self.items),
            "entities": sorted(self.entities),
            "recipes": sorted(self.recipes),
            "geometries": sorted(self.geometries),
            "animations": sorted(self.animations),
            "script_entries": self.scripts,
            "features": self.occurrences,
            "findings": self.findings.rows(),
        }

    def script_source(self, name, importer=None):
        if importer:
            if not name.startswith("."):
                raise Unsupported(f"External JavaScript module: {name}")
            parts = list(PurePosixPath(importer).parent.parts)
            for part in name.split("/"):
                if part == "..":
                    if not parts:
                        raise Unsupported("Script import escapes pack")
                    parts.pop()
                elif part not in (".", ""):
                    parts.append(part)
            name = "/".join(parts)
        if name not in self.files:
            raise Unsupported(f"Script module missing: {name}")
        if len(self.files[name]) > 256 * 1024:
            raise Unsupported("Script source exceeds 256 KiB limit")
        return name, self.files[name].decode("utf-8-sig")
