import io
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from mcbe_tester.packs import read_files
from mcbe_tester.sim.catalog import Catalog
from mcbe_tester.sim.diagnostics import Unsupported
from mcbe_tester.sim.molang import evaluate
from mcbe_tester.sim.render import channel
from mcbe_tester.sim.runner import Simulation, digest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples/simulator-addon"
SCENARIO = ROOT / "examples/simulator-scenario.json"


def simulation(script=None, mutate=None):
    files = read_files(FIXTURE)
    if script is not None:
        files["behavior_pack/scripts/main.js"] = script.encode()
    if mutate:
        mutate(files)
    return Simulation(Catalog(files), seed=17)


class MolangTests(unittest.TestCase):
    def test_precedence_trigonometry_and_short_circuit(self):
        self.assertEqual(evaluate("2 + 3 * 4 == 14 && !false"), True)
        self.assertAlmostEqual(evaluate("math.sin(90)"), 1)
        self.assertEqual(evaluate("true ? 4 : q.unimplemented()"), 4)
        self.assertFalse(evaluate("false && q.unimplemented()"))

    def test_variables_and_block_states(self):
        variables = {}
        self.assertEqual(evaluate("v.x = 4; return v.x * 2;", variables=variables), 8)
        self.assertEqual(variables, {"x": 4})
        self.assertTrue(evaluate("q.block_state('workshop:lit')", {"states": {"workshop:lit": True}}))

    def test_unknown_query_and_python_syntax_block(self):
        for expression in ("q.is_swimming", "__import__('os')", "[1,2]", "1 / 0"):
            with self.subTest(expression=expression), self.assertRaises(Unsupported):
                evaluate(expression)

    def test_linear_keyframes_pre_post(self):
        values = {"0": [0, 0, 0], "1": {"pre": [10, 20, 30], "post": [20, 40, 60]}}
        np.testing.assert_allclose(channel(values, 0.5, {}, {}, [0, 0, 0]), [5, 10, 15])


class SimulationTests(unittest.TestCase):
    def test_complete_spec_example_and_deterministic_replay(self):
        spec = json.loads(SCENARIO.read_text())
        first, second = simulation(), simulation()
        report = first.scenario(spec)["report"]
        second.scenario(spec)
        self.assertEqual(report["overall"], "simulation_passed")
        self.assertEqual(len(report["assertions"]), 10)
        self.assertIsNone(report["accuracy_percent"])
        self.assertEqual(report["native_compatibility"], "unverified")
        self.assertEqual(digest(first.world.snapshot()), digest(second.world.snapshot()))
        self.assertEqual(first.screenshot()[1], second.screenshot()[1])

    def test_no_assertions_or_untested_requirement_never_passes(self):
        sim = simulation()
        sim.explore()
        self.assertEqual(sim.report()["overall"], "incomplete")
        sim.requirements["animation"] = "Expected design was not checked"
        sim.assert_state("menu", "/player/menu", "eq", "game")
        self.assertEqual(sim.report()["untested_requirements"], ["animation"])
        self.assertEqual(sim.report()["overall"], "incomplete")

    def test_wrong_expectation_fails(self):
        sim = simulation()
        sim.assert_state("menu", "/player/menu", "eq", "inventory")
        self.assertEqual(sim.report()["overall"], "failed")

    def test_unknown_component_blocks_even_if_assertion_passes(self):
        def mutate(files):
            key = "behavior_pack/blocks/lamp.json"
            data = json.loads(files[key])
            data["minecraft:block"]["components"]["minecraft:redstone_conductivity"] = {}
            files[key] = json.dumps(data).encode()

        sim = simulation(mutate=mutate)
        sim.assert_state("menu", "/player/menu", "eq", "game")
        self.assertEqual(sim.report()["overall"], "incomplete")
        self.assertTrue(
            any(x["feature"] == "minecraft:redstone_conductivity" for x in sim.report()["findings"])
        )

    def test_script_callback_toggles_permutation_and_changes_pixels(self):
        sim = simulation()
        sim.action({"type": "set_block", "identifier": "workshop:lamp", "position": [0, 0, 0]})
        before = sim.screenshot()[1]
        result = sim.action({"type": "interact", "position": [0, 0, 0]})
        self.assertEqual(result["status"], "simulated")
        self.assertTrue(sim.world.blocks[(0, 0, 0)]["states"]["workshop:lit"])
        after = sim.screenshot()[1]
        self.assertNotEqual(before, after)
        a, b = np.array(Image.open(io.BytesIO(before))), np.array(Image.open(io.BytesIO(after)))
        self.assertGreater(np.count_nonzero(a[50:300] != b[50:300]), 500)

    def test_menu_controls_share_screenshot_hit_regions(self):
        sim = simulation()
        sim.action({"type": "key", "key": "e"})
        initial = sim.world.player["position"].copy()
        sim.action({"type": "move", "keys": ["w"], "ticks": 5})
        self.assertEqual(initial, sim.world.player["position"])
        metadata, _ = sim.screenshot()
        region = next(r for r in metadata["ui_regions"] if r["id"] == "workshop:shard")
        a, b, c, d = region["rect"]
        sim.action({"type": "click", "x": (a + c) / 2, "y": (b + d) / 2})
        self.assertEqual(sim.world.player["inventory"]["workshop:shard"], 1)
        sim.action({"type": "key", "key": "escape"})
        self.assertEqual(sim.world.player["menu"], "game")

    def test_collision_and_jump(self):
        sim = simulation()
        sim.action({"type": "set_block", "identifier": "workshop:lamp", "position": [0, 0, 0]})
        sim.action({"type": "move", "keys": ["w"], "ticks": 30})
        self.assertGreaterEqual(sim.world.player["position"][2], 1.3)
        self.assertLess(sim.world.player["position"][2], 1.4)
        sim.action({"type": "key", "key": "space", "ticks": 2})
        self.assertGreater(sim.world.player["position"][1], 0.4)

    def test_invalid_block_state_fails_without_replacing_block(self):
        sim = simulation()
        sim.action({"type": "set_block", "identifier": "workshop:lamp", "position": [0, 0, 0]})
        result = sim.action({"type": "set_state", "position": [0, 0, 0], "states": {"workshop:lit": 1}})
        self.assertEqual(result["status"], "failed")
        self.assertIs(sim.world.blocks[(0, 0, 0)]["states"]["workshop:lit"], False)

    def test_recipe_consumes_exact_inputs_and_rejects_missing_items(self):
        sim = simulation()
        grid = ["workshop:shard", "workshop:shard"] + [None] * 7
        self.assertEqual(
            sim.action({"type": "craft", "recipe": "workshop:lamp_recipe", "grid": grid})["status"], "failed"
        )
        self.assertEqual(sim.world.player["inventory"], {})
        sim.action({"type": "give", "identifier": "workshop:shard", "count": 3})
        sim.action({"type": "craft", "recipe": "workshop:lamp_recipe", "grid": grid})
        self.assertEqual(sim.world.player["inventory"], {"workshop:shard": 1, "workshop:lamp": 1})

    def test_missing_texture_is_unresolved_not_native_appearance(self):
        sim = simulation(mutate=lambda files: files.pop("resource_pack/textures/blocks/lamp_off.png"))
        sim.action({"type": "set_block", "identifier": "workshop:lamp", "position": [0, 0, 0]})
        sim.screenshot()
        sim.assert_state("menu", "/player/menu", "eq", "game")
        self.assertEqual(sim.report()["overall"], "incomplete")
        self.assertTrue(any(x["feature"] == "texture" for x in sim.report()["findings"]))

    def test_animation_changes_model_pixels_and_render_is_repeatable(self):
        sim = simulation()
        sim.action({"type": "spawn", "identifier": "workshop:robot", "position": [0, 0, 0]})
        sim.action({"type": "entity_event", "entity_id": "e1", "event": "workshop:activate"})
        first = sim.screenshot()[1]
        sim.action({"type": "tick", "ticks": 5})
        second = sim.screenshot()[1]
        a, b = np.array(Image.open(io.BytesIO(first))), np.array(Image.open(io.BytesIO(second)))
        self.assertGreater(np.count_nonzero(a[40:300] != b[40:300]), 100)
        self.assertEqual(second, sim.screenshot()[1])
        self.assertEqual(
            sim.world.entities["e1"]["controllers"]["controller.animation.workshop.robot"], "active"
        )

    def test_export_replay_preserves_input_hash_and_state(self):
        sim = simulation()
        sim.scenario(json.loads(SCENARIO.read_text()))
        with tempfile.TemporaryDirectory() as root:
            sim.export(root)
            replay = json.loads((Path(root) / "replay.json").read_text())
            other = Simulation.open(Path(root) / "input.zip", seed=replay["seed"])
            self.assertEqual(other.catalog.hash, replay["content_sha256"])
            other.scenario(replay)
            self.assertEqual(digest(sim.world.snapshot()), digest(other.world.snapshot()))
            with self.assertRaises(ValueError):
                sim.export(root)

    def test_visual_regression_is_not_fidelity(self):
        sim = simulation()
        metadata, data = sim.screenshot()
        result = sim.compare_image("reference", metadata["id"], data)
        self.assertEqual(result["changed_fraction"], 0)
        self.assertFalse(result["native_graphics_verified"])
        self.assertIsNone(sim.report()["accuracy_percent"])
        other = simulation()
        other.scenario({"steps": sim.operations})
        self.assertEqual(other.assertions, sim.assertions)

    def test_inline_upload_rejects_path_escape(self):
        with self.assertRaises(ValueError):
            Simulation.from_files({"../secret": "data"})


class ScriptBoundaryTests(unittest.TestCase):
    def test_scheduled_callbacks_and_clear_run(self):
        sim = simulation("""
            import {system, world} from '@minecraft/server';
            let n = 0;
            const handle = system.runInterval(() => {
              n += 1;
              world.setDynamicProperty('n', n);
              if (n === 3) system.clearRun(handle);
            }, 2);
        """)
        sim.action({"type": "tick", "ticks": 20})
        self.assertEqual(sim.world.dynamic["n"], 3)
        self.assertFalse(sim.script.broken)

    def test_infinite_loop_is_bounded(self):
        sim = simulation("while (true) {}")
        self.assertTrue(sim.script.broken)
        self.assertIn("budget", sim.report()["findings"][-1]["message"])
        self.assertEqual(sim.report()["overall"], "incomplete")

    def test_host_escape_and_unsupported_apis_are_blocked(self):
        for source in [
            "console.log.constructor('return process')()",
            "import fs from 'node:fs';",
            "eval('1')",
            "import {world} from '@minecraft/server'; world.getDimension('overworld').runCommand('say hi');",
            "async function f() {}",
            "const a = []; a * 999999999999;",
        ]:
            with self.subTest(source=source):
                sim = simulation(source)
                self.assertTrue(sim.script.broken)
                self.assertEqual(sim.report()["overall"], "incomplete")

    def test_before_event_cancellation_prevents_block_break(self):
        sim = simulation("""
            import {world} from '@minecraft/server';
            world.beforeEvents.playerBreakBlock.subscribe(e => { e.cancel = true; });
        """)
        sim.action({"type": "set_block", "identifier": "workshop:lamp", "position": [0, 0, 0]})
        result = sim.action({"type": "break", "position": [0, 0, 0]})
        self.assertTrue(result["result"]["cancelled"])
        self.assertIn((0, 0, 0), sim.world.blocks)

    def test_relative_module_exports(self):
        def mutate(files):
            files["behavior_pack/scripts/helpers.js"] = b"export function answer() { return 42; }"

        sim = simulation(
            "import {world} from '@minecraft/server'; import {answer} from './helpers.js'; world.setDynamicProperty('answer', answer());",
            mutate,
        )
        self.assertFalse(sim.script.broken)
        self.assertEqual(sim.world.dynamic["answer"], 42)


if __name__ == "__main__":
    unittest.main()
