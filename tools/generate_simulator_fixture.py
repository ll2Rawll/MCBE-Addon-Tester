"""Regenerate original, self-contained simulator fixtures; no Minecraft assets used."""

import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / "examples/simulator-addon"


def save(path, data):
    destination = ROOT / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


for name, kind, uid in [
    ("behavior_pack", "data", "2a8ad08f-8350-426f-93a5-83bcf091d22c"),
    ("resource_pack", "resources", "b33593cb-9e69-4e26-9280-9b80fdafc972"),
]:
    modules = [
        {
            "type": kind,
            "uuid": "58ea315e-a619-4c2a-b055-cb7a47a0f630"
            if kind == "data"
            else "b7528a6b-bc75-4e25-a6d1-93dc552f4d08",
            "version": [1, 0, 0],
        }
    ]
    manifest = {
        "format_version": 2,
        "header": {
            "name": "Simulator workshop " + kind,
            "description": "Original independent-simulator fixtures; native compatibility unverified",
            "uuid": uid,
            "version": [1, 0, 0],
            "min_engine_version": [1, 21, 90],
        },
        "modules": modules,
    }
    if kind == "data":
        modules.append(
            {
                "type": "script",
                "language": "javascript",
                "uuid": "abdc18d1-f9e1-49ab-b080-4a005ab84673",
                "version": [1, 0, 0],
                "entry": "scripts/main.js",
            }
        )
        manifest["dependencies"] = [
            {"module_name": "@minecraft/server", "version": "2.0.0"},
            {"uuid": "b33593cb-9e69-4e26-9280-9b80fdafc972", "version": [1, 0, 0]},
        ]
    save(name + "/manifest.json", manifest)

save(
    "behavior_pack/blocks/lamp.json",
    {
        "format_version": "1.21.90",
        "minecraft:block": {
            "description": {
                "identifier": "workshop:lamp",
                "states": {"workshop:lit": [False, True]},
                "menu_category": {"category": "construction"},
            },
            "components": {
                "minecraft:geometry": "minecraft:geometry.full_block",
                "minecraft:material_instances": {
                    "*": {"texture": "workshop_lamp_off", "render_method": "opaque"}
                },
                "workshop:toggle": {},
            },
            "permutations": [
                {
                    "condition": "q.block_state('workshop:lit')",
                    "components": {
                        "minecraft:material_instances": {
                            "*": {"texture": "workshop_lamp_on", "render_method": "opaque"}
                        }
                    },
                }
            ],
        },
    },
)
save(
    "behavior_pack/items/shard.json",
    {
        "format_version": "1.21.90",
        "minecraft:item": {
            "description": {"identifier": "workshop:shard", "menu_category": {"category": "items"}},
            "components": {
                "minecraft:display_name": {"value": "Workshop shard"},
                "minecraft:icon": "workshop_shard",
            },
        },
    },
)
save(
    "behavior_pack/recipes/lamp.json",
    {
        "format_version": "1.21.90",
        "minecraft:recipe_shapeless": {
            "description": {"identifier": "workshop:lamp_recipe"},
            "tags": ["crafting_table"],
            "ingredients": [{"item": "workshop:shard"}, {"item": "workshop:shard"}],
            "result": {"item": "workshop:lamp", "count": 1},
        },
    },
)
save(
    "behavior_pack/entities/robot.json",
    {
        "format_version": "1.21.90",
        "minecraft:entity": {
            "description": {
                "identifier": "workshop:robot",
                "is_spawnable": True,
                "is_summonable": True,
                "properties": {"workshop:active": {"type": "bool", "default": False, "client_sync": True}},
            },
            "components": {
                "minecraft:health": {"value": 12, "max": 12},
                "minecraft:collision_box": {"width": 0.7, "height": 1.5},
                "minecraft:physics": {"has_gravity": True, "has_collision": True},
            },
            "events": {"workshop:activate": {"set_property": {"workshop:active": True}}},
        },
    },
)
save(
    "resource_pack/entity/robot.json",
    {
        "format_version": "1.10.0",
        "minecraft:client_entity": {
            "description": {
                "identifier": "workshop:robot",
                "materials": {"default": "entity_alphatest"},
                "textures": {"default": "textures/entity/robot"},
                "geometry": {"default": "geometry.workshop.robot"},
                "animations": {
                    "idle": "animation.workshop.idle",
                    "active": "animation.workshop.active",
                    "controller": "controller.animation.workshop.robot",
                },
                "scripts": {"animate": ["controller"]},
            }
        },
    },
)
save(
    "resource_pack/models/entity/robot.geo.json",
    {
        "format_version": "1.12.0",
        "minecraft:geometry": [
            {
                "description": {
                    "identifier": "geometry.workshop.robot",
                    "texture_width": 64,
                    "texture_height": 64,
                    "visible_bounds_width": 3,
                    "visible_bounds_height": 3,
                    "visible_bounds_offset": [0, 1, 0],
                },
                "bones": [
                    {
                        "name": "body",
                        "pivot": [0, 10, 0],
                        "cubes": [{"origin": [-6, 3, -4], "size": [12, 14, 8], "uv": [0, 0]}],
                    },
                    {
                        "name": "head",
                        "parent": "body",
                        "pivot": [0, 17, 0],
                        "cubes": [{"origin": [-5, 17, -4], "size": [10, 8, 8], "uv": [0, 24]}],
                    },
                    {
                        "name": "antenna",
                        "parent": "head",
                        "pivot": [0, 25, 0],
                        "cubes": [{"origin": [-1, 25, -1], "size": [2, 6, 2], "uv": [40, 0]}],
                    },
                    {
                        "name": "left_foot",
                        "cubes": [{"origin": [-6, 0, -5], "size": [5, 3, 10], "uv": [32, 16]}],
                    },
                    {
                        "name": "right_foot",
                        "cubes": [{"origin": [1, 0, -5], "size": [5, 3, 10], "uv": [32, 16]}],
                    },
                ],
            }
        ],
    },
)
save(
    "resource_pack/animations/robot.json",
    {
        "format_version": "1.8.0",
        "animations": {
            "animation.workshop.idle": {
                "loop": True,
                "animation_length": 2,
                "bones": {"antenna": {"rotation": [0, 0, "math.sin(q.anim_time * 180) * 12"]}},
            },
            "animation.workshop.active": {
                "loop": True,
                "animation_length": 2,
                "bones": {
                    "body": {"rotation": [0, "q.anim_time * 180", 0]},
                    "antenna": {"rotation": [0, 0, "math.sin(q.anim_time * 360) * 35"]},
                },
            },
        },
    },
)
save(
    "resource_pack/animation_controllers/robot.json",
    {
        "format_version": "1.10.0",
        "animation_controllers": {
            "controller.animation.workshop.robot": {
                "initial_state": "idle",
                "states": {
                    "idle": {
                        "animations": ["idle"],
                        "transitions": [{"active": "q.property('workshop:active')"}],
                    },
                    "active": {
                        "animations": ["active"],
                        "transitions": [{"idle": "!q.property('workshop:active')"}],
                    },
                },
            },
        },
    },
)
save(
    "resource_pack/textures/terrain_texture.json",
    {
        "resource_pack_name": "workshop",
        "texture_name": "atlas.terrain",
        "texture_data": {
            "workshop_lamp_off": {"textures": ["textures/blocks/lamp_off"]},
            "workshop_lamp_on": {"textures": ["textures/blocks/lamp_on"]},
        },
    },
)
save(
    "resource_pack/textures/item_texture.json",
    {
        "resource_pack_name": "workshop",
        "texture_name": "atlas.items",
        "texture_data": {"workshop_shard": {"textures": ["textures/items/shard"]}},
    },
)

for name, base, bright in [
    ("lamp_off", (26, 82, 94), (43, 118, 124)),
    ("lamp_on", (193, 134, 23), (255, 227, 104)),
]:
    img = Image.new("RGBA", (16, 16))
    img.putdata(
        [
            (
                *(
                    (10, 23, 34)
                    if x in (0, 15) or y in (0, 15)
                    else bright
                    if (x + y) % 5 == 0 or (5 <= x <= 10 and 5 <= y <= 10)
                    else base
                ),
                255,
            )
            for y in range(16)
            for x in range(16)
        ]
    )
    path = ROOT / f"resource_pack/textures/blocks/{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
img = Image.new("RGBA", (64, 64))
img.putdata(
    [
        (*((43, 162, 171) if (x // 4 + y // 4) % 2 else (22, 82, 104)), 255)
        for y in range(64)
        for x in range(64)
    ]
)
(ROOT / "resource_pack/textures/entity").mkdir(exist_ok=True)
img.save(ROOT / "resource_pack/textures/entity/robot.png")
img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
for y in range(2, 14):
    for x in range(3, 13):
        if abs(x - 8) + abs(y - 8) < 7:
            img.putpixel((x, y), (75, 218, 215, 255))
(ROOT / "resource_pack/textures/items").mkdir(exist_ok=True)
img.save(ROOT / "resource_pack/textures/items/shard.png")
icon = Image.new("RGBA", (256, 256))
icon.putdata(
    [
        (55, 188, 191, 255)
        if 48 < x < 208 and 48 < y < 208 and (x // 24 + y // 24) % 2
        else (16, 38, 55, 255)
        for y in range(256)
        for x in range(256)
    ]
)
for folder in ("behavior_pack", "resource_pack"):
    icon.save(ROOT / folder / "pack_icon.png")
