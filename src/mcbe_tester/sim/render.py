"""Independent textured cuboid renderer and animation preview, with explicit fidelity limits."""

import io
import math

import numpy as np
from PIL import Image, ImageDraw

from .diagnostics import Unsupported
from .molang import evaluate

FACES = {
    "north": (0, 1, 2, 3),
    "south": (5, 4, 7, 6),
    "west": (4, 0, 3, 7),
    "east": (1, 5, 6, 2),
    "up": (3, 2, 6, 7),
    "down": (4, 5, 1, 0),
}
SHADE = {"north": 0.8, "south": 0.75, "west": 0.65, "east": 0.9, "up": 1.0, "down": 0.5}


def rotation(angles):
    x, y, z = np.radians(angles)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return rz @ ry @ rx


def channel(value, time, context, variables, default):
    if isinstance(value, dict):
        frames = sorted((float(t), v) for t, v in value.items())
        if not frames:
            return np.array(default, dtype=float)
        before, after = frames[0], frames[-1]
        for frame in frames:
            if frame[0] <= time:
                before = frame
            if frame[0] >= time:
                after = frame
                break

        def endpoint(value, side):
            if isinstance(value, dict):
                if value.get("lerp_mode", "linear") != "linear":
                    raise Unsupported("Only linear animation interpolation is implemented")
                value = value.get(side, value.get("post", value.get("pre", default)))
            return channel(value, time, context, variables, default)

        a, b = endpoint(before[1], "post"), endpoint(after[1], "pre")
        alpha = max(0, min(1, (time - before[0]) / (after[0] - before[0]))) if after[0] != before[0] else 0
        return a * (1 - alpha) + b * alpha
    if not isinstance(value, list):
        value = [value] * 3
    if len(value) != 3:
        raise ValueError("Animation channel needs three components")
    return np.array([float(evaluate(v, context, variables)) for v in value])


def active_animations(world, entity):
    """A controller is advanced once per simulation tick, not per screenshot."""
    if entity.get("_animation_tick") == world.tick:
        return entity.get("_animations", [])
    entity["_animation_tick"] = world.tick
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
    result = []

    def resolve(name, weight=1, depth=0):
        if depth > 8 or len(result) > 64:
            raise Unsupported("Animation expansion limit")
        name = aliases.get(name, name)
        if name in world.catalog.animations:
            result.append((name, weight))
            return
        if name not in world.catalog.controllers:
            raise Unsupported(f"Animation/controller not supplied: {name}")
        controller = world.catalog.controllers[name]
        current = entity["controllers"].setdefault(name, controller.get("initial_state", "default"))
        states = controller["states"]
        state = states[current]
        for key in set(state) - {"animations", "transitions"}:
            world.catalog.findings.unsupported("controller-state-feature", name, key)
        for transition in state.get("transitions", []):
            target, condition = next(iter(transition.items()))
            if evaluate(condition, context, variables):
                if target not in states:
                    raise ValueError(f"Missing animation state {target}")
                entity["controllers"][name] = current = target
                break
        for animation in states[current].get("animations", []):
            if isinstance(animation, dict):
                for key, condition in animation.items():
                    value = evaluate(condition, context, variables)
                    if value:
                        resolve(key, weight * value, depth + 1)
            else:
                resolve(animation, weight, depth + 1)

    for value in scripts.get("animate", []):
        if isinstance(value, dict):
            for name, condition in value.items():
                weight = evaluate(condition, context, variables)
                if weight:
                    resolve(name, weight)
        else:
            resolve(value)
    entity["_animations"] = result
    return result


def pose(world, entity):
    out = {}
    for name, weight in active_animations(world, entity):
        animation = world.catalog.animations[name]
        for key in set(animation) - {"bones", "animation_length", "loop", "override_previous_animation"}:
            world.catalog.findings.unsupported("animation-feature", name, key)
        if animation.get("override_previous_animation"):
            world.catalog.findings.unsupported("animation-override", name)
        length = animation.get("animation_length", 1)
        time = entity["age"] / 20
        if animation.get("loop") is True and length > 0:
            time %= length
        elif animation.get("loop") not in (None, False, "hold_on_last_frame"):
            raise Unsupported("Expression animation loops are not modeled")
        else:
            time = min(time, length)
        context = {**world.context(entity), "query.anim_time": time}
        for bone, channels in animation.get("bones", {}).items():
            target = out.setdefault(
                bone, {"rotation": np.zeros(3), "position": np.zeros(3), "scale": np.ones(3)}
            )
            for name, default in (("rotation", [0, 0, 0]), ("position", [0, 0, 0]), ("scale", [1, 1, 1])):
                if name in channels:
                    value = channel(channels[name], time, context, entity["variables"], default)
                    if name == "scale":
                        target[name] *= 1 + (value - 1) * weight
                    else:
                        target[name] += value * weight
    return out


def corners(origin, size, inflate=0):
    x, y, z = np.array(origin, float) - inflate
    a, b, c = np.array(size, float) + 2 * inflate
    return np.array(
        [
            [x, y, z],
            [x + a, y, z],
            [x + a, y + b, z],
            [x, y + b, z],
            [x, y, z + c],
            [x + a, y, z + c],
            [x + a, y + b, z + c],
            [x, y + b, z + c],
        ]
    )


def uv_faces(cube):
    uv = cube.get("uv", [0, 0])
    if isinstance(uv, dict):
        result = {}
        for name, face in uv.items():
            if name not in FACES:
                continue
            u, v = face["uv"]
            width, height = face.get("uv_size", cube["size"][:2])
            result[name] = ([u, v, width, height], face.get("material_instance", name))
        return result
    u, v = uv
    width, height, depth = cube["size"]
    return {
        "west": ([u, v + depth, depth, height], "west"),
        "north": ([u + depth, v + depth, width, height], "north"),
        "east": ([u + depth + width, v + depth, depth, height], "east"),
        "south": ([u + 2 * depth + width, v + depth, width, height], "south"),
        "up": ([u + depth, v, width, depth], "up"),
        "down": ([u + depth + width, v, width, depth], "down"),
    }


class Renderer:
    def __init__(self, world, width=640, height=400, camera="overview"):
        if not 160 <= width <= 960 or not 120 <= height <= 720:
            raise ValueError("Render dimensions must be 160–960 by 120–720")
        if camera not in {"overview", "player"}:
            raise ValueError("Camera must be overview or player")
        self.world, self.catalog = world, world.catalog
        self.width, self.height, self.camera = width, height, camera
        self.pixels = np.zeros((height, width, 4), dtype=np.uint8)
        self.pixels[:] = [24, 34, 48, 255]
        self.depth = np.full((height, width), np.inf)
        self.faces = 0
        if camera == "overview":
            locations = list(world.blocks) + [e["position"] for e in world.entities.values()]
            center = np.mean(locations, axis=0) + [0.5, 0.5, 0.5] if locations else np.array([0.5, 0.5, 0.5])
            span = max([math.dist(loc, center) for loc in locations] + [1])
            self.scale = min(width / (span * 3.4 + 5), height / (span * 2.3 + 4))
            self.origin = center + np.array([8, 7, 10])
            forward = center - self.origin
        else:
            self.origin = np.array(world.player["position"]) + [0, 1.62, 0]
            yaw, pitch = map(math.radians, (world.player["yaw"], world.player["pitch"]))
            forward = np.array(
                [-math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch)]
            )
        self.forward = forward / np.linalg.norm(forward)
        self.right = np.cross(self.forward, [0, 1, 0])
        self.right /= np.linalg.norm(self.right)
        self.up = np.cross(self.right, self.forward)

    def project(self, vertices):
        relative = vertices - self.origin
        x, y, z = relative @ self.right, relative @ self.up, relative @ self.forward
        if self.camera == "player":
            # Near-plane crossing is omitted and explicitly approximate in the profile.
            if np.any(z <= 0.05):
                return None
            scale = self.width / (2 * math.tan(math.radians(70 / 2))) / z
        else:
            scale = self.scale
        return np.column_stack([self.width / 2 + x * scale, self.height / 2 - y * scale, z])

    def triangle(self, vertices, uv, texture, shade):
        projected = self.project(vertices)
        if projected is None:
            return
        if not np.all(np.isfinite(projected)) or not np.all(np.isfinite(uv)):
            raise ValueError("Nonfinite geometry or UV")
        x0, y0 = np.floor(projected[:, :2].min(axis=0)).astype(int)
        x1, y1 = np.ceil(projected[:, :2].max(axis=0)).astype(int)
        x0, x1 = max(0, x0), min(self.width - 1, x1)
        y0, y1 = max(0, y0), min(self.height - 1, y1)
        if x1 < x0 or y1 < y0:
            return
        a, b, c = projected
        determinant = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(determinant) < 1e-8:
            return
        xx, yy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        wa = ((b[1] - c[1]) * (xx - c[0]) + (c[0] - b[0]) * (yy - c[1])) / determinant
        wb = ((c[1] - a[1]) * (xx - c[0]) + (a[0] - c[0]) * (yy - c[1])) / determinant
        wc = 1 - wa - wb
        if self.camera == "player":
            inverse = wa / a[2] + wb / b[2] + wc / c[2]
            safe = np.where(abs(inverse) > 1e-9, inverse, 1e-9)
            depth = 1 / safe
            weights = np.stack([wa / a[2], wb / b[2], wc / c[2]], axis=-1) / safe[..., None]
        else:
            depth = wa * a[2] + wb * b[2] + wc * c[2]
            weights = np.stack([wa, wb, wc], axis=-1)
        coords = weights @ np.array(uv)
        tx = np.clip(np.floor(coords[..., 0]).astype(int), 0, texture.shape[1] - 1)
        ty = np.clip(np.floor(coords[..., 1]).astype(int), 0, texture.shape[0] - 1)
        colors = texture[ty, tx].copy()
        colors[..., :3] = (colors[..., :3] * shade).astype(np.uint8)
        region = self.depth[y0 : y1 + 1, x0 : x1 + 1]
        mask = (wa >= -1e-8) & (wb >= -1e-8) & (wc >= -1e-8) & (depth < region) & (colors[..., 3] >= 128)
        region[mask] = depth[mask]
        self.pixels[y0 : y1 + 1, x0 : x1 + 1][mask] = colors[mask]

    def quad(self, points, uv_rect, texture, shade=1):
        self.faces += 1
        if self.faces > 12000:
            raise Unsupported("12,000-face rendering budget exhausted")
        u, v, w, h = uv_rect
        uvs = np.array([[u, v + h], [u + w, v + h], [u + w, v], [u, v]])
        pixels = np.asarray(texture)
        for indices in ((0, 1, 2), (0, 2, 3)):
            self.triangle(points[list(indices)], uvs[list(indices)], pixels, shade)

    def full_block(self, position, block):
        components = block["components"]
        materials = components.get("minecraft:material_instances", {})
        points = corners(position, [1, 1, 1])
        for name, indices in FACES.items():
            material = materials.get(name, materials.get("*", {}))
            texture = self.catalog.texture(material.get("texture", "<missing-material>"))
            if material.get("render_method", "opaque") not in {"opaque", "alpha_test"}:
                self.catalog.findings.unsupported(
                    "material-render-method", block["identifier"], material["render_method"]
                )
            self.quad(points[list(indices)], [0, 0, texture.width, texture.height], texture, SHADE[name])

    def geometry(self, geometry, position, texture_for, transforms=None, visibility=None, context=None):
        bones = {b["name"]: b for b in geometry.get("bones", [])}
        transforms, visibility, context = transforms or {}, visibility or {}, context or {}
        matrices = {}

        def matrix(name, chain=()):
            if name in matrices:
                return matrices[name]
            if name in chain or len(chain) > 32:
                raise ValueError("Cyclic/deep geometry bone hierarchy")
            bone = bones[name]
            transform = transforms.get(name, {})
            pivot = np.array(bone.get("pivot", [0, 0, 0]), dtype=float)
            angles = np.array(bone.get("rotation", [0, 0, 0])) + transform.get("rotation", np.zeros(3))
            r = rotation(angles) @ np.diag(transform.get("scale", [1, 1, 1]))
            m = np.eye(4)
            m[:3, :3] = r
            m[:3, 3] = pivot - r @ pivot + transform.get("position", np.zeros(3))
            if "parent" in bone:
                m = matrix(bone["parent"], (*chain, name)) @ m
            matrices[name] = m
            return m

        desc = geometry["description"]
        texture_width, texture_height = desc.get("texture_width", 64), desc.get("texture_height", 64)
        if texture_width <= 0 or texture_height <= 0:
            raise ValueError("Geometry texture dimensions must be positive")
        for name, bone in bones.items():
            if not evaluate(visibility.get(name, True), context):
                continue
            for key in set(bone) - {
                "name",
                "parent",
                "pivot",
                "rotation",
                "cubes",
                "locators",
                "mirror",
                "inflate",
            }:
                self.catalog.findings.unsupported("geometry-bone-feature", desc["identifier"], key)
            for cube in bone.get("cubes", []):
                points = corners(cube["origin"], cube["size"], cube.get("inflate", bone.get("inflate", 0)))
                if "rotation" in cube:
                    pivot = np.array(cube.get("pivot", [0, 0, 0]))
                    points = (points - pivot) @ rotation(cube["rotation"]).T + pivot
                m = matrix(name)
                points = (points @ m[:3, :3].T + m[:3, 3]) / 16 + np.array(position)
                for face, (uv, material) in uv_faces(cube).items():
                    texture = texture_for(material)
                    u, v, w, h = uv
                    uv = [
                        u * texture.width / texture_width,
                        v * texture.height / texture_height,
                        w * texture.width / texture_width,
                        h * texture.height / texture_height,
                    ]
                    if cube.get("mirror", bone.get("mirror", False)):
                        uv[0] += uv[2]
                        uv[2] *= -1
                    self.quad(points[list(FACES[face])], uv, texture, SHADE[face])

    def render(self):
        floor = Image.new("RGBA", (2, 2))
        floor.putdata([(54, 69, 78, 255), (61, 77, 85, 255), (61, 77, 85, 255), (54, 69, 78, 255)])
        for x in range(-5, 6):
            for z in range(-5, 6):
                self.quad(
                    np.array([[x, 0, z], [x + 1, 0, z], [x + 1, 0, z + 1], [x, 0, z + 1]]),
                    [0, 0, 2, 2],
                    floor,
                )
        for position, block in self.world.blocks.items():
            try:
                components = block["components"]
                geometry = components.get("minecraft:geometry", "minecraft:geometry.full_block")
                options = geometry if isinstance(geometry, dict) else {"identifier": geometry}
                identifier = options["identifier"]
                if identifier in {"minecraft:geometry.full_block", "minecraft:geometry.full_block_v1"}:
                    self.full_block(position, block)
                else:
                    if identifier not in self.catalog.geometries:
                        raise Unsupported(f"Geometry not supplied: {identifier}")
                    materials = components.get("minecraft:material_instances", {})

                    def texture_for(face, materials=materials):
                        return self.catalog.texture(
                            materials.get(face, materials.get("*", {})).get("texture", "<missing-material>")
                        )

                    offset = [position[0] + 0.5, position[1], position[2] + 0.5]
                    self.geometry(
                        self.catalog.geometries[identifier],
                        offset,
                        texture_for,
                        visibility=options.get("bone_visibility"),
                        context={"states": block["states"]},
                    )
                for option in set(options) - {"identifier", "bone_visibility"}:
                    self.catalog.findings.unsupported("geometry-option", block["identifier"], option)
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                self.catalog.findings.unsupported("block-render", block["identifier"], str(exc))
        for entity in self.world.entities.values():
            try:
                desc = self.catalog.clients[entity["identifier"]]["description"]
                geometries, textures = desc.get("geometry", {}), desc.get("textures", {})
                geometry_id = geometries.get("default", next(iter(geometries.values()), None))
                texture_name = textures.get("default", next(iter(textures.values()), None))
                if geometry_id not in self.catalog.geometries:
                    raise Unsupported(f"Client geometry missing: {geometry_id}")
                texture = self.catalog.texture(texture_name, "entity")
                self.geometry(
                    self.catalog.geometries[geometry_id],
                    entity["position"],
                    lambda _, texture=texture: texture,
                    pose(self.world, entity),
                )
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                self.catalog.findings.unsupported("entity-render", entity["identifier"], str(exc))
        image = Image.fromarray(self.pixels)
        draw = ImageDraw.Draw(image)
        draw.rectangle([0, 0, self.width, 31], fill=(13, 20, 30, 255))
        draw.text(
            (12, 9), f"BEDROCK ADDON TESTER  /  SIMULATED  /  tick {self.world.tick}", fill=(190, 226, 237)
        )
        if self.world.player["menu"] != "game":
            draw.rectangle(
                [self.width * 0.08, self.height * 0.1, self.width * 0.92, self.height * 0.88],
                fill=(28, 38, 50),
                outline=(115, 161, 172),
                width=2,
            )
            draw.text(
                (self.width * 0.12, self.height * 0.16),
                f"Simulator {self.world.player['menu']}",
                fill="white",
            )
            for region in self.world.ui_regions():
                a, b, c, d = region["rect"]
                rect = [int(a * self.width), int(b * self.height), int(c * self.width), int(d * self.height)]
                draw.rectangle(rect, fill=(52, 69, 81), outline=(130, 165, 173))
                draw.text((rect[0] + 4, rect[1] + 5), str(region["label"])[:10], fill="white")
        else:
            if self.camera == "player":
                x, y = self.width // 2, self.height // 2
                draw.line([x - 5, y, x + 5, y], fill="white")
                draw.line([x, y - 5, x, y + 5], fill="white")
            for i in range(9):
                x, y = self.width / 2 - 9 * 21 + i * 42, self.height - 48
                draw.rectangle(
                    [x, y, x + 39, y + 37],
                    fill=(30, 43, 56),
                    outline=(240, 214, 108) if i == self.world.player["selected"] else (85, 110, 124),
                    width=2,
                )
                if i < len(self.world.player["hotbar"]):
                    name = self.world.player["hotbar"][i]
                    draw.text((x + 3, y + 4), name.split(":")[-1][:5], fill="white")
        draw.rectangle([0, self.height - 12, self.width, self.height], fill=(13, 20, 30))
        draw.text(
            (7, self.height - 12),
            "Independent preview - native graphics compatibility unverified",
            fill=(180, 195, 207),
        )
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        return buffer.getvalue()


def render(world, width=640, height=400, camera="overview"):
    return Renderer(world, width, height, camera).render()
