import json
import unittest

from mcbe_tester.sim.commands import execute
from mcbe_tester.sim.diagnostics import Unsupported
from mcbe_tester.sim.selectors import in_range, position, selector, split_balanced
from tests.test_simulator import simulation


class CommandTests(unittest.TestCase):
    def sim(self, functions=None, tick=None):
        def mutate(files):
            for name, source in (functions or {}).items():
                files["behavior_pack/functions/" + name + ".mcfunction"] = source.encode()
            if tick is not None:
                files["behavior_pack/functions/tick.json"] = json.dumps({"values": tick}).encode()

        s = simulation("", mutate)
        self.addCleanup(s.close)
        return s

    def command(self, s, command):
        result = s.action({"type": "command", "command": command})
        self.assertEqual(result["status"], "simulated", result)
        return result["result"]

    def test_tokenizer_preserves_quoted_names_and_nested_score_filters(self):
        self.assertEqual(
            split_balanced('execute as @e[name="Copper Bot",scores={a=2..4,b=!1}] run say "Hello world"'),
            ["execute", "as", '@e[name="Copper Bot",scores={a=2..4,b=!1}]', "run", "say", '"Hello world"'],
        )
        for text in ['say "unfinished', "@e[scores={a=1]", "@e]"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                split_balanced(text)

    def test_as_preserves_position_while_at_changes_it_and_not_executor(self):
        s = self.sim()
        a = s.world.spawn("workshop:robot", [9, 0, 8])
        self.command(s, "execute as @e[type=workshop:robot] run tp @s ~2 ~ ~")
        self.assertEqual(s.world.entities[a]["position"], [2.5, 0, 4.5])
        self.command(s, "execute at @e[type=workshop:robot] run tp @s ~1 ~ ~")
        self.assertEqual(s.world.player["position"], [3.5, 0, 4.5])
        self.assertEqual(s.world.entities[a]["position"], [2.5, 0, 4.5])

    def test_selectors_use_execute_origin_even_if_executor_did_not_move(self):
        s = self.sim()
        a = s.world.spawn("workshop:robot", [20, 0, 20])
        b = s.world.spawn("workshop:robot", [1, 0, 1])
        self.command(s, "execute positioned 20 0 20 as @e[type=workshop:robot,r=1] run tag @s add nearby")
        self.assertIn("nearby", s.world.entities[a]["tags"])
        self.assertNotIn("nearby", s.world.entities[b]["tags"])

    def test_local_basis_and_rotation_are_context_not_entity_mutations(self):
        s = self.sim()
        self.command(s, "execute positioned 0 3 0 rotated 90 0 run tp @s ^1 ^2 ^3")
        for actual, expected in zip(s.world.player["position"], [-3, 5, 1], strict=True):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(s.world.player["yaw"], 180)
        self.command(s, "execute positioned 0 0 0 facing 10 0 0 run tp @s ^ ^ ^2")
        self.assertAlmostEqual(s.world.player["position"][0], 2)
        self.assertAlmostEqual(s.world.player["position"][2], 0)
        with self.assertRaises(ValueError):
            position(["^1", "~", "^2"], [0, 0, 0])

    def test_block_states_predicates_and_score_ranges(self):
        s = self.sim()
        self.command(s, 'setblock 1 0 1 workshop:lamp ["workshop:lit"=true]')
        self.command(
            s,
            'execute if block 1 0 1 workshop:lamp ["workshop:lit"=true] unless entity @e[type=workshop:robot] run tag @s add checked',
        )
        self.assertIn("checked", s.world.player["tags"])
        self.command(s, "scoreboard objectives add points dummy")
        self.command(s, 'scoreboard players set "Score holder" points 7')
        self.command(s, 'execute if score "Score holder" points matches 5..9 run tag @s add score')
        self.assertIn("score", s.world.player["tags"])
        self.assertEqual(self.command(s, "execute if score Missing points matches !1")["successCount"], 0)
        self.assertEqual(
            self.command(s, 'testforblock 1 0 1 workshop:lamp ["workshop:lit"=false]')["successCount"], 0
        )

    def test_score_arithmetic_uses_floor_division_and_preserves_zero_divisor(self):
        s = self.sim()
        self.command(s, "scoreboard objectives add value dummy")
        for key, value in [("a", -7), ("b", 3), ("zero", 0)]:
            self.command(s, f"scoreboard players set {key} value {value}")
        self.command(s, "scoreboard players operation a value /= b value")
        self.assertEqual(s.world.scoreboard.api("score", "value", "a"), -3)
        self.command(s, "scoreboard players set a value -7")
        self.command(s, "scoreboard players operation a value %= b value")
        self.assertEqual(s.world.scoreboard.api("score", "value", "a"), 2)
        self.command(s, "scoreboard players operation a value /= zero value")
        self.assertEqual(s.world.scoreboard.api("score", "value", "a"), 2)
        self.command(s, "scoreboard players operation a value >< b value")
        self.assertEqual(s.world.scoreboard.api("score", "value", "a"), 3)
        self.assertEqual(s.world.scoreboard.api("score", "value", "b"), 2)

    def test_nested_score_filters_negation_and_named_entity(self):
        s = self.sim()
        a = s.world.spawn("workshop:robot", [2, 0, 0])
        b = s.world.spawn("workshop:robot", [3, 0, 0])
        s.world.entity_api("nameTag", a, "Copper Bot")
        self.command(s, "scoreboard objectives add points dummy")
        self.command(s, "scoreboard objectives add deaths dummy")
        for objective, value in [("points", 7), ("deaths", 0)]:
            s.world.scoreboard.api("set", objective, a, value)
        self.assertEqual(
            selector(s.world, '@e[type=workshop:robot,name="Copper Bot",scores={points=5..9,deaths=!1}]'), [a]
        )
        self.assertNotIn(b, selector(s.world, "@e[scores={points=!0}]"))
        self.assertEqual(selector(s.world, "@e[m=!survival]"), ["player"])
        self.assertFalse(in_range(None, "!1"))

    def test_counts_random_selection_and_selector_volume(self):
        s = self.sim()
        a = s.world.spawn("workshop:robot", [2, 0, 0])
        b = s.world.spawn("workshop:robot", [8, 0, 0])
        self.assertEqual(selector(s.world, "@e[type=workshop:robot,x=0,y=0,z=0,c=-1]"), [b])
        self.assertEqual(selector(s.world, "@e[type=workshop:robot,x=0,y=0,z=0,dx=3]"), [a])
        self.assertEqual(selector(s.world, "@r"), ["player"])
        self.assertIn(selector(s.world, "@r[type=workshop:robot]")[0], [a, b])
        for query in ["@p[type=workshop:robot]", "@e[type=one,type=two]", "@e[c=0]"]:
            with self.subTest(query=query), self.assertRaises(ValueError):
                selector(s.world, query)

    def test_function_inherits_context_and_tick_files_run_in_declared_order(self):
        s = self.sim(
            {
                "init": "# comment\nscoreboard objectives add timer dummy\nscoreboard players set Clock timer 0",
                "tick/add": "scoreboard players add Clock timer 1",
                "tick/mark": "execute if score Clock timer matches 3 run tag @a add third",
                "move": "tp @s ~2 ~ ~",
            },
            ["tick/add", "tick/mark"],
        )
        self.command(s, "function init")
        key = s.world.spawn("workshop:robot", [10, 0, 10])
        self.command(s, "execute as @e[type=workshop:robot] at @s run function move")
        self.assertEqual(s.world.entities[key]["position"], [12, 0, 10])
        s.world.advance(4)
        self.assertEqual(s.world.scoreboard.api("score", "timer", "Clock"), 4)
        self.assertIn("third", s.world.player["tags"])
        s.assert_state("ticks", "/scoreboard/timer/scores/Clock", "eq", 4)
        self.assertEqual(s.report()["overall"], "simulation_passed", s.report())

    def test_functions_continue_after_ordinary_errors_and_retain_evidence(self):
        s = self.sim({"failure": "scoreboard players add A absent 1\ntag @s add continued"})
        self.command(s, "function failure")
        self.assertIn("continued", s.world.player["tags"])
        self.assertEqual(s.report()["overall"], "failed")
        self.assertIn("function-command", json.dumps(s.report()))

    def test_recursion_and_unexecuted_unsupported_functions_do_not_pass(self):
        s = self.sim({"loop": "function loop", "unvisited": "playsound not.modeled"})
        result = s.action({"type": "command", "command": "function loop"})
        self.assertEqual(result["status"], "blocked")
        self.assertIn("playsound", json.dumps(s.report()))
        with self.assertRaises(Unsupported):
            execute(s.world, "execute as @e[type=not:present] run playsound something")
        for command in (
            "execute as @e[type=not:present] in nether run say x",
            "execute as @e[type=not:present] at @e[unknown=1] run say x",
        ):
            with self.subTest(command=command), self.assertRaises(Unsupported):
                execute(s.world, command)
        for query in ("@e[type=not:present,dx=bad]", "@e[tag=x,]", "@e[m=wrong]"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                selector(s.world, query)

    def test_fill_hollow_leaves_air_and_outline_preserves_interior(self):
        s = self.sim()
        self.command(s, 'fill 0 0 0 2 2 2 workshop:lamp ["workshop:lit"=true] hollow')
        self.assertEqual(len(s.world.blocks), 26)
        self.assertNotIn((1, 1, 1), s.world.blocks)
        self.command(s, 'setblock 1 1 1 workshop:lamp ["workshop:lit"=false]')
        self.command(s, 'fill 0 0 0 2 2 2 workshop:lamp ["workshop:lit"=true] outline')
        self.assertFalse(s.world.blocks[(1, 1, 1)]["states"]["workshop:lit"])


if __name__ == "__main__":
    unittest.main()
