"""CPU preview of supplied JSON UI controls using explicit logical coordinates.

Only supplied textures are used. Font metrics, antialiasing and input timing are
independent approximations, not RenderDragon/Bedrock output.
"""

import math

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from .diagnostics import Unsupported
from .json_ui import finite, flag, pair


def draw_ui(world, image):
    ui = world.json_ui
    width, height = ui.current["viewport"]
    sx, sy = image.width / width, image.height / height
    for node in sorted(ui.nodes, key=lambda value: value["layer"]):
        if not node["visible"] or node["alpha"] == 0:
            continue
        if node["type"] not in {"image", "label", "edit_box"}:
            continue
        try:
            draw_control(ui, node, image, sx, sy)
        except (ValueError, KeyError, TypeError, ArithmeticError, RecursionError) as exc:
            world.catalog.findings.unsupported("ui-render", node["id"], str(exc))
    return image


def draw_control(ui, node, canvas, sx, sy):
    def prop(key, default=None):
        return ui.prop(node, key, default)

    x, y, right, bottom = node["rect"]
    if right <= x or bottom <= y:
        return
    clip = node["clip"]
    # Bound allocations to the visible output, even for enormous/offscreen layouts.
    bounds = [
        max(0, math.floor(max(x, clip[0]) * sx)),
        max(0, math.floor(max(y, clip[1]) * sy)),
        min(canvas.width, math.ceil(min(right, clip[2]) * sx)),
        min(canvas.height, math.ceil(min(bottom, clip[3]) * sy)),
    ]
    a, b, c, d = bounds
    if a >= c or b >= d:
        return
    if node["type"] == "image":
        name = prop("texture", "")
        if not isinstance(name, str):
            raise ValueError("UI texture must resolve to a path")
        if not name:
            return
        source = ui.world.catalog.texture(name, "ui")
        uv = pair(prop("uv", [0, 0]), "UI uv")
        size = pair(prop("uv_size", [source.width, source.height]), "UI uv_size")
        u, v = (finite(value, "UI uv") for value in uv)
        uw, vh = (finite(value, "UI uv_size") for value in size)
        if u < 0 or v < 0 or uw <= 0 or vh <= 0 or u + uw > source.width or v + vh > source.height:
            raise Unsupported("UI texture wrapping/out-of-range UV is not modeled")
        if flag(prop("keep_ratio", False)):
            scale = min((right - x) / uw, (bottom - y) / vh)
            x += (right - x - uw * scale) / 2
            y += (bottom - y - vh * scale) / 2
            right, bottom = x + uw * scale, y + vh * scale
            a, b, c, d = (
                max(a, math.ceil(x * sx)),
                max(b, math.ceil(y * sy)),
                min(c, math.ceil(right * sx)),
                min(d, math.ceil(bottom * sy)),
            )
            if a >= c or b >= d:
                return
        dx, dy = uw / ((right - x) * sx), vh / ((bottom - y) * sy)
        tile = source.transform(
            (c - a, d - b),
            Image.Transform.AFFINE,
            (dx, 0, u + (a - x * sx) * dx, 0, dy, v + (b - y * sy) * dy),
            resample=Image.Resampling.BILINEAR if flag(prop("bilinear", False)) else Image.Resampling.NEAREST,
        )
        if flag(prop("grayscale", False)):
            grey = ImageOps.grayscale(tile).convert("RGBA")
            grey.putalpha(tile.getchannel("A"))
            tile = grey
        tint = tuple(round(value * 255) for value in node["color"]) + (round(node["alpha"] * 255),)
        tile = ImageChops.multiply(tile, Image.new("RGBA", tile.size, tint))
        # keep_ratio must not show neighboring texels outside the fitted rectangle.
        mask = Image.new("L", tile.size)
        ImageDraw.Draw(mask).rectangle(
            [
                max(0, math.ceil(x * sx - a)),
                max(0, math.ceil(y * sy - b)),
                min(c - a - 1, math.ceil(right * sx - a) - 1),
                min(d - b - 1, math.ceil(bottom * sy - b) - 1),
            ],
            fill=255,
        )
        tile.putalpha(ImageChops.multiply(tile.getchannel("A"), mask))
    else:
        if node["type"] == "edit_box" and prop("text_control", ""):
            return
        size = prop("font_size", "normal")
        if size not in {"small", "normal", "large"}:
            raise Unsupported("UI font_size not modeled: " + str(size))
        scale = finite(prop("font_scale_factor", 1), "UI font scale")
        if not 0 < scale <= 8:
            raise ValueError("UI font scale must be in (0,8]")
        font = ImageFont.load_default(
            size=max(1, round({"small": 8, "normal": 10, "large": 12}[size] * scale * sy))
        )
        tile = Image.new("RGBA", (c - a, d - b))
        draw = ImageDraw.Draw(tile)
        text = node["text"]
        alignment = prop("text_alignment", "center")
        if alignment not in {"left", "center", "right"}:
            raise Unsupported("UI text alignment not modeled")
        left, top, tr, tb = draw.multiline_textbbox((0, 0), text, font=font, align=alignment)
        fraction = {"left": 0, "center": 0.5, "right": 1}[alignment]
        tx = x * sx - a + ((right - x) * sx - (tr - left)) * fraction - left
        ty = y * sy - b + ((bottom - y) * sy - (tb - top)) / 2 - top
        rgba = tuple(round(value * 255) for value in node["color"]) + (round(node["alpha"] * 255),)
        if flag(prop("shadow", False)):
            draw.multiline_text((tx + sx, ty + sy), text, font=font, fill=(0, 0, 0, rgba[3]), align=alignment)
        draw.multiline_text((tx, ty), text, font=font, fill=rgba, align=alignment)
    canvas.alpha_composite(tile, (a, b))
