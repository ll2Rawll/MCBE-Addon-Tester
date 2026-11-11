"""Continuous axis-aligned sweeps and ray/selection intersections for the bounded model."""

import math


def block_box(components, key="minecraft:collision_box"):
    box = components.get(key, True)
    if box is False:
        return None
    if box is True:
        return (0, 0, 0), (1, 1, 1)
    origin, size = box.get("origin", [-8, 0, -8]), box.get("size", [16, 16, 16])
    if len(origin) != 3 or len(size) != 3:
        raise ValueError("Block box needs three-coordinate origin and size")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in [*origin, *size]):
        raise ValueError("Block box must be finite")
    if any(
        size[i] < 0 or origin[i] < (-8, 0, -8)[i] or origin[i] + size[i] > (8, 16, 8)[i] for i in range(3)
    ):
        raise ValueError("Block box extends outside its cell")
    if 0 in size:
        return None
    low = (origin[0] / 16 + 0.5, origin[1] / 16, origin[2] / 16 + 0.5)
    return low, tuple(low[i] + size[i] / 16 for i in range(3))


def swept_move(world, obj, width, height, collide):
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in (width, height)):
        raise ValueError("Entity collision dimensions must be positive and finite")
    position, velocity = list(obj["position"]), list(obj["velocity"])
    grounded = False
    shapes = []
    if collide:
        for pos, block in world.blocks.items():
            shape = block_box(block["components"])
            if shape:
                shapes.append(tuple(tuple(pos[i] + bound[i] for i in range(3)) for bound in shape))
    for axis in (1, 0, 2):
        delta = velocity[axis]
        if not math.isfinite(delta):
            raise ValueError("Nonfinite velocity")
        low = (position[0] - width / 2, position[1], position[2] - width / 2)
        high = (position[0] + width / 2, position[1] + height, position[2] + width / 2)
        allowed = max(delta, -position[1]) if collide and axis == 1 and delta < 0 else delta
        for a, b in shapes:
            if any(low[i] >= b[i] - 1e-9 or high[i] <= a[i] + 1e-9 for i in range(3) if i != axis):
                continue
            if delta > 0 and high[axis] <= a[axis] + 1e-9:
                allowed = min(allowed, max(0, a[axis] - high[axis]))
            elif delta < 0 and low[axis] >= b[axis] - 1e-9:
                allowed = max(allowed, min(0, b[axis] - low[axis]))
        position[axis] += allowed
        if allowed != delta:
            velocity[axis] = 0
            grounded |= axis == 1 and delta < 0
    if any(abs(v) > 128 for v in position):
        from .diagnostics import Unsupported

        raise Unsupported("Movement left bounded simulation world")
    obj.update(position=position, velocity=velocity, on_ground=grounded)


def ray_box(origin, direction, low, high, reach):
    enter, leave, normal = 0.0, reach, None
    for axis in range(3):
        if abs(direction[axis]) < 1e-12:
            if not low[axis] <= origin[axis] <= high[axis]:
                return None
            continue
        first = (low[axis] - origin[axis]) / direction[axis]
        last = (high[axis] - origin[axis]) / direction[axis]
        sign = -1 if first < last else 1
        first, last = min(first, last), max(first, last)
        if first >= enter:
            enter = first
            normal = tuple(sign if i == axis else 0 for i in range(3))
        leave = min(leave, last)
        if enter > leave:
            return None
    return (enter, normal) if leave >= 0 else None


def select(world, reach):
    yaw, pitch = map(math.radians, (world.player["yaw"], world.player["pitch"]))
    direction = (-math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch))
    origin = list(world.player["position"])
    origin[1] += 1.62
    closest, target, adjacent = reach + 1, None, None
    for pos, block in world.blocks.items():
        shape = block_box(block["components"], "minecraft:selection_box")
        if not shape:
            continue
        low, high = (tuple(pos[i] + bound[i] for i in range(3)) for bound in shape)
        hit = ray_box(origin, direction, low, high, reach)
        if hit and hit[0] < closest:
            closest, normal = hit
            target = pos
            adjacent = tuple(pos[i] + normal[i] for i in range(3)) if normal else None
    if direction[1] < 0:
        floor_distance = -origin[1] / direction[1]
        if 0 <= floor_distance <= reach and floor_distance < closest:
            x, _, z = [origin[i] + direction[i] * floor_distance for i in range(3)]
            target, adjacent = (math.floor(x), -1, math.floor(z)), (math.floor(x), 0, math.floor(z))
    return target, adjacent
