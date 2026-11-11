import io
import json
import unittest
from pathlib import Path

from PIL import Image

from mcbe_tester.packs import read_files
from mcbe_tester.sim.catalog import Catalog
from mcbe_tester.sim.diagnostics import Unsupported
from mcbe_tester.sim.runner import Simulation, digest
from mcbe_tester.sim.ui_expression import evaluate

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples/json-ui-addon"
BINDINGS = {"#heading": "UI workshop", "#show_notice": True, "#status": "Ready"}


class JsonUITests(unittest.TestCase):
    def sim(self, data=None, extra=None):
        files = read_files(FIXTURE)
        if data is not None:
            files["ui/workshop.json"] = json.dumps({"namespace": "workshop", **data}).encode()
        if extra:
            files.update(extra)
        sim = Simulation(Catalog(files))
        self.addCleanup(sim.close)
        return sim

    def open(self, sim, **values):
        result = sim.action({"type": "ui_open", "screen": "workshop.screen", "bindings": BINDINGS, **values})
        self.assertEqual(result["status"], "simulated", result)
        return sim.world.json_ui

    def click(self, sim, name):
        region = next(r for r in sim.world.ui_regions() if r["control"].endswith("/" + name))
        a, b, c, d = region["rect"]
        result = sim.action({"type": "click", "x": (a + c) / 2, "y": (b + d) / 2})
        self.assertEqual(result["status"], "simulated", result)
        return result

    def test_templates_scoped_defaults_and_percentage_anchors(self):
        s = self.sim(
            {
                "base": {
                    "type": "button",
                    "size": ["50% - 10px", 20],
                    "$caption|default": "Default",
                    "controls": [{"label": {"type": "label", "text": "$caption"}}],
                },
                "screen": {
                    "type": "screen",
                    "controls": [
                        {
                            "left@workshop.base": {
                                "$caption": "Left",
                                "anchor_from": "top_left",
                                "anchor_to": "top_left",
                                "offset": [10, 30],
                            }
                        },
                        {
                            "right@workshop.base": {
                                "anchor_from": "bottom_right",
                                "anchor_to": "bottom_right",
                                "offset": [-10, -20],
                            }
                        },
                    ],
                },
            }
        )
        ui = self.open(s, width=400, height=300)
        self.assertEqual(ui._by_id["workshop.screen/left"]["rect"], [10, 30, 200, 50])
        self.assertEqual(ui._by_id["workshop.screen/right"]["rect"], [200, 260, 390, 280])
        self.assertEqual(ui._by_id["workshop.screen/left/label"]["text"], "Left")
        self.assertEqual(ui._by_id["workshop.screen/right/label"]["text"], "Default")

    def test_cross_control_bindings_text_input_and_mapped_button(self):
        s = self.sim()
        ui = self.open(s)
        self.click(s, "name")
        self.assertEqual(s.action({"type": "text", "text": "Copper"})["status"], "simulated")
        self.assertEqual(ui._by_id["workshop.screen/card/stack/name/value"]["text"], "Copper")
        self.assertFalse(ui._by_id["workshop.screen/card/stack/name/placeholder"]["visible"])
        self.click(s, "enabled")
        self.assertTrue(ui._by_id["workshop.screen/card/stack/enabled/on"]["visible"])
        self.assertFalse(ui._by_id["workshop.screen/card/stack/enabled/off"]["visible"])
        self.click(s, "apply")
        self.assertEqual(
            ui.current["events"],
            [{"control": "workshop.screen/card/stack/apply", "button": "button.workshop_apply", "tick": 0}],
        )
        # A mapped ID does not invent a native gameplay callback.
        self.assertEqual(s.world.dynamic, {})

    def test_binding_payload_is_literal_and_input_failure_rolls_back(self):
        s = self.sim()
        ui = self.open(s)
        self.click(s, "name")
        literal = "(not #secret)"
        s.action({"type": "text", "text": literal})
        self.assertEqual(ui._by_id["workshop.screen/card/stack/name/value"]["text"], literal)
        before = ui.snapshot()
        self.assertEqual(s.action({"type": "text", "text": "x" * 25})["status"], "failed")
        self.assertEqual(before, ui.snapshot())
        s.action({"type": "ui_bind", "bindings": {"#heading": "(#unavailable)"}})
        self.assertEqual(ui._by_id["workshop.screen/heading"]["text"], "(#unavailable)")
        before = ui.snapshot()
        self.assertEqual(
            s.action({"type": "ui_bind", "bindings": {"#show_notice": "not a boolean"}})["status"], "failed"
        )
        self.assertEqual(before, ui.snapshot())

    def test_hidden_disabled_controls_do_not_receive_pointer_or_text(self):
        s = self.sim()
        ui = self.open(s)
        editor = s.catalog.ui.controls["workshop.screen"]["definition"]["controls"][2]["card"]["controls"][0][
            "stack"
        ]["controls"][0]["name"]
        editor["bindings"] = [{"binding_name": "#edit_enabled", "binding_name_override": "#enabled"}]
        s.action({"type": "ui_bind", "bindings": {"#edit_enabled": True}})
        self.click(s, "name")
        s.action({"type": "ui_bind", "bindings": {"#edit_enabled": False}})
        self.assertFalse(any(r["control"].endswith("/name") for r in ui.regions()))
        self.assertEqual(s.action({"type": "text", "text": "cannot type"})["status"], "failed")
        self.assertEqual(ui._by_id["workshop.screen/card/stack/name/value"]["text"], "")

    def test_clipping_and_topmost_hit_order_use_same_layout(self):
        button = {
            "type": "button",
            "size": [80, 80],
            "button_mappings": [{"from_button_id": "button.menu_select", "to_button_id": "button.hit"}],
        }
        s = self.sim(
            {
                "screen": {
                    "type": "screen",
                    "controls": [
                        {
                            "clip": {
                                "type": "panel",
                                "size": [100, 100],
                                "clips_children": True,
                                "controls": [{"under": button}, {"over": {**button, "offset": [40, 0]}}],
                            }
                        }
                    ],
                }
            }
        )
        ui = self.open(s, width=400, height=300)
        self.assertEqual(ui.regions()[0]["control"], "workshop.screen/clip/over")
        self.assertEqual(ui.regions()[0]["rect"], [0.5, 110 / 300, 0.625, 190 / 300])
        s.action({"type": "click", "x": 0.54, "y": 0.5})
        self.assertEqual(ui.current["events"][0]["control"], "workshop.screen/clip/over")
        before = len(ui.current["events"])
        s.action({"type": "click", "x": 0.7, "y": 0.5})
        self.assertEqual(len(ui.current["events"]), before)

    def test_keyboard_navigation_and_hover_pressed_visual_states(self):
        s = self.sim()
        ui = self.open(s)
        for key, expected in [("tab", "name"), ("down", "enabled"), ("down", "apply")]:
            self.assertEqual(s.action({"type": "key", "key": key})["status"], "simulated")
            self.assertTrue(ui.current["focus"].endswith("/" + expected))
        s.action({"type": "key", "key": "enter"})
        self.assertEqual(len(ui.current["events"]), 1)
        result = s.action({"type": "ui_pointer", "x": 0.5, "y": 0.5})
        self.assertEqual(result["status"], "simulated")
        base = "workshop.screen/card/stack/apply/"
        self.assertTrue(ui._by_id[base + "hover"]["visible"])
        s.action({"type": "ui_pointer", "x": 0.5, "y": 0.5, "pressed": True})
        self.assertTrue(ui._by_id[base + "pressed"]["visible"])
        s.action({"type": "ui_pointer", "x": 0.5, "y": 0.5, "pressed": True})
        self.assertEqual(len(ui.current["events"]), 2)
        s.action({"type": "ui_pointer", "x": 0.5, "y": 0.5, "pressed": False})
        self.assertTrue(ui._by_id[base + "hover"]["visible"])
        s.action({"type": "key", "key": "escape"})
        self.assertIsNone(ui.current)
        self.assertEqual(s.world.player["menu"], "game")

    def test_ui_variables_resolve_local_control_bindings(self):
        s = self.sim(
            {
                "screen": {
                    "type": "screen",
                    "controls": [
                        {"switch": {"type": "toggle", "$show": "(not #toggle_state)", "visible": "$show"}}
                    ],
                }
            }
        )
        ui = self.open(s)
        self.click(s, "switch")
        self.assertFalse(ui._by_id["workshop.screen/switch"]["visible"])
        self.assertEqual(ui.regions(), [])

    def test_stack_ignored_child_and_transparency_propagation(self):
        s = self.sim(
            {
                "screen": {
                    "type": "stack_panel",
                    "size": [100, 100],
                    "alpha": 0.5,
                    "controls": [
                        {"a": {"type": "panel", "size": [100, 10]}},
                        {"ignored": {"type": "panel", "size": [100, 50], "ignored": True}},
                        {"b": {"type": "panel", "size": [100, 20], "alpha": 0.5}},
                    ],
                }
            }
        )
        ui = self.open(s, width=400, height=300)
        self.assertEqual(ui._by_id["workshop.screen/a"]["alpha"], 1)
        self.assertEqual(ui._by_id["workshop.screen/b"]["alpha"], 0.5)
        self.assertEqual(ui._by_id["workshop.screen/b"]["rect"], [150, 110, 250, 130])
        s.catalog.ui.controls["workshop.screen"]["definition"]["propagate_alpha"] = True
        ui.rebuild()
        self.assertEqual(ui._by_id["workshop.screen/b"]["alpha"], 0.25)

    def test_missing_templates_bindings_and_unvisited_fields_block_report(self):
        for data in [
            {"screen@vanilla.missing": {}},
            {"screen": {"type": "label", "text": "#missing_native_binding"}},
            {"screen": {"type": "screen"}, "unused": {"type": "panel", "factory": "hardcoded_factory"}},
            {
                "screen": {"type": "screen"},
                "unused": {"type": "panel", "controls": [{"missing@vanilla.base": {}}]},
            },
        ]:
            with self.subTest(data=data):
                s = self.sim(data)
                s.action({"type": "ui_open", "screen": "workshop.screen"})
                s.assert_state("still alive", "/tick", "eq", 0)
                self.assertEqual(s.report()["overall"], "incomplete", s.report())

    def test_invalid_shapes_and_cycles_return_failures_without_losing_screen(self):
        cases = [
            {"type": "panel", "min_size": []},
            {"type": "panel", "size": [1]},
            {"type": "panel", "$a": "$b", "$b": "$a", "visible": "$a"},
            {
                "type": "label",
                "bindings": [
                    {"binding_type": "view", "source_property_name": "#text", "target_property_name": "#text"}
                ],
            },
        ]
        for data in cases:
            with self.subTest(data=data):
                s = self.sim()
                ui = self.open(s)
                before = ui.snapshot()
                s.catalog.ui.controls["workshop.bad"] = {
                    "definition": data,
                    "namespace": "workshop",
                    "parent": "",
                }
                r = s.action({"type": "ui_open", "screen": "workshop.bad"})
                self.assertEqual(r["status"], "failed", r)
                self.assertEqual(ui.snapshot(), before)
        s = self.sim({"a@workshop.b": {}, "b@workshop.a": {}, "screen@workshop.a": {}})
        self.assertEqual(s.action({"type": "ui_open", "screen": "workshop.screen"})["status"], "failed")

    def test_bad_mapping_does_not_toggle_or_focus(self):
        s = self.sim(
            {
                "screen": {
                    "type": "screen",
                    "controls": [
                        {
                            "switch": {
                                "type": "toggle",
                                "button_mappings": [
                                    {"from_button_id": "button.menu_select", "to_button_id": "$missing"}
                                ],
                            }
                        }
                    ],
                }
            }
        )
        ui = self.open(s)
        before = ui.snapshot()
        self.assertEqual(s.action({"type": "click", "x": 0.5, "y": 0.5})["status"], "blocked")
        self.assertEqual(before, ui.snapshot())

    def test_render_is_read_only_and_responsive_coordinates_scale(self):
        s = self.sim()
        ui = self.open(s)
        before = digest(s.world.snapshot())
        _, first = s.screenshot()
        self.assertEqual(digest(s.world.snapshot()), before)
        self.assertEqual(s.screenshot()[1], first)
        s.action({"type": "ui_pointer", "x": 0.5, "y": 0.5})
        _, second = s.screenshot()
        self.assertNotEqual(first, second)
        image = Image.open(io.BytesIO(second))
        # Original white texture tinted to hover color; tests rendering and layout together.
        self.assertEqual(image.getpixel((100, 195)), (76, 242, 217))
        s.action(
            {
                "type": "ui_open",
                "screen": "workshop.screen",
                "width": 800,
                "height": 600,
                "bindings": BINDINGS,
            }
        )
        region = next(r for r in ui.regions() if r["control"].endswith("/apply"))
        self.assertEqual(region["rect"], [0.12, 279 / 600, 0.88, 319 / 600])
        self.click(s, "apply")
        self.assertEqual(len(ui.current["events"]), 1)

    def test_texture_uv_crop_tint_alpha_and_clipping(self):
        strip = Image.new("RGBA", (2, 1))
        strip.putdata([(255, 0, 0, 255), (0, 255, 0, 255)])
        output = io.BytesIO()
        strip.save(output, format="PNG")
        s = self.sim(
            {
                "screen": {
                    "type": "screen",
                    "controls": [
                        {"base": {"type": "image", "texture": "textures/ui/white", "color": [0, 0, 1]}},
                        {
                            "tile": {
                                "type": "image",
                                "texture": "textures/ui/strip",
                                "size": [40, 40],
                                "uv": [1, 0],
                                "uv_size": [1, 1],
                                "alpha": 0.5,
                            }
                        },
                    ],
                }
            },
            {"textures/ui/strip.png": output.getvalue()},
        )
        self.open(s)
        image = Image.open(io.BytesIO(s.screenshot()[1]))
        self.assertEqual(image.getpixel((320, 200)), (0, 128, 127))
        self.assertEqual(image.getpixel((280, 200)), (0, 0, 255))

    def test_fixture_scenario_replays_without_a_game(self):
        spec = json.loads((ROOT / "examples/json-ui-scenario.json").read_text())
        a, b = self.sim(), self.sim()
        for sim in (a, b):
            result = sim.scenario(spec)
            self.assertEqual(result["report"]["overall"], "simulation_passed", result)
            self.assertIsNone(result["report"]["accuracy_percent"])
        self.assertEqual(digest(a.world.snapshot()), digest(b.world.snapshot()))
        self.assertEqual(a.frames, b.frames)

    def test_malformed_pack_fields_stay_inside_error_reports(self):
        for field, value in [
            ("controls", [None]),
            ("controls", [{}]),
            ("controls", [{"bad": None}]),
            ("bindings", ["wrong"]),
            ("size", {}),
            ("min_size", [1]),
            ("offset", ["9" * 400 + "%", 0]),
            ("color", [True, 0, 0]),
        ]:
            with self.subTest(field=field, value=value):
                s = self.sim({"screen": {"type": "screen", field: value}})
                result = s.action({"type": "ui_open", "screen": "workshop.screen"})
                self.assertIn(result["status"], {"failed", "blocked"}, result)
                self.assertIsNone(s.world.json_ui.current)
                self.assertEqual(s.world.player["menu"], "game")

    def test_corrupt_texture_is_a_finding_instead_of_a_host_exception(self):
        s = self.sim(extra={"textures/ui/white.png": b"not an image"})
        self.open(s)
        s.screenshot()
        s.assert_state("screen open", "/player/menu", "eq", "json_ui")
        self.assertEqual(s.report()["overall"], "incomplete")
        self.assertIn("cannot be decoded", json.dumps(s.report()))

    def test_declared_text_control_receives_edit_value(self):
        s = self.sim(
            {
                "screen": {
                    "type": "edit_box",
                    "text_control": "value",
                    "controls": [{"value": {"type": "label"}}],
                }
            }
        )
        ui = self.open(s)
        s.action({"type": "click", "x": 0.5, "y": 0.5})
        self.assertEqual(s.action({"type": "text", "text": "Shared value"})["status"], "simulated")
        self.assertEqual(ui._by_id["workshop.screen/value"]["text"], "Shared value")

    def test_keep_ratio_outside_a_clip_does_not_draw_or_raise(self):
        strip = Image.new("RGBA", (4, 1), (255, 0, 0, 255))
        output = io.BytesIO()
        strip.save(output, format="PNG")
        s = self.sim(
            {
                "screen": {
                    "type": "screen",
                    "controls": [
                        {"base": {"type": "image", "texture": "textures/ui/white", "color": [0, 0, 1]}},
                        {
                            "clip": {
                                "type": "panel",
                                "size": [80, 10],
                                "offset": [0, -40],
                                "clips_children": True,
                                "controls": [
                                    {
                                        "image": {
                                            "type": "image",
                                            "size": [80, 100],
                                            "texture": "textures/ui/strip",
                                            "anchor_from": "top_left",
                                            "anchor_to": "top_left",
                                            "keep_ratio": True,
                                        }
                                    }
                                ],
                            }
                        },
                    ],
                }
            },
            {"textures/ui/strip.png": output.getvalue()},
        )
        self.open(s)
        image = Image.open(io.BytesIO(s.screenshot()[1]))
        self.assertEqual(image.getpixel((320, 160)), (0, 0, 255))
        self.assertFalse(
            any(row["severity"] in {"error", "unsupported"} for row in s.catalog.findings.rows())
        )

    def test_forward_binding_defaults_and_pointer_drag_do_not_duplicate_presses(self):
        s = self.sim(
            {
                "screen": {
                    "type": "screen",
                    "controls": [
                        {
                            "first": {
                                "type": "label",
                                "text": "Forward",
                                "bindings": [
                                    {
                                        "binding_type": "view",
                                        "source_control_name": "later",
                                        "source_property_name": "#visible",
                                        "target_property_name": "#visible",
                                    }
                                ],
                            }
                        },
                        {
                            "later": {
                                "type": "button",
                                "size": [100, 100],
                                "button_mappings": [
                                    {"from_button_id": "button.menu_select", "to_button_id": "button.test"}
                                ],
                            }
                        },
                    ],
                }
            }
        )
        ui = self.open(s)
        self.assertTrue(ui._by_id["workshop.screen/first"]["visible"])
        for x in (0.5, 0, 0.5):
            self.assertEqual(
                s.action({"type": "ui_pointer", "x": x, "y": 0.5, "pressed": True})["status"], "simulated"
            )
        self.assertEqual(len(ui.current["events"]), 1)


class UIExpressionTests(unittest.TestCase):
    def test_typed_arithmetic_short_circuit_strings_and_bindings(self):
        values = {"$a": 3, "#name": "Minecraft Bedrock"}
        for expr, expected in [
            ("(2 + 3 * 4)", 14),
            ("(false and #missing)", False),
            ("(true or #missing)", True),
            ("((#name - 'Bedrock') != #name)", True),
            ("('hello ' + 'world')", "hello world"),
            ("(not ($a >= 4))", True),
        ]:
            with self.subTest(expr=expr):
                self.assertEqual(evaluate(expr, values.__getitem__), expected)

    def test_malformed_expressions_and_unsupported_coercion_are_rejected(self):
        for expr in [
            "(",
            "(1 2)",
            "(1 +)",
            "(1 / 0)",
            "('value' + 1)",
            "(false and unknown())",
            "(" * 60 + "1" + ")" * 60,
        ]:
            with self.subTest(expr=expr), self.assertRaises((ValueError, Unsupported)):
                evaluate(expr, {}.__getitem__)
        self.assertEqual(evaluate("literal text", {}.__getitem__), "literal text")


if __name__ == "__main__":
    unittest.main()
