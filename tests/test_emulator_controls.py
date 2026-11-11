import io
import json
import unittest
from copy import deepcopy

from PIL import Image

from tests.test_simulator import simulation


class EmulatorControlsTests(unittest.TestCase):
    def sim(self, script="", mutate=None):
        sim = simulation(script, mutate)
        self.addCleanup(sim.close)
        return sim

    def click(self, sim, identifier):
        region = next(r for r in sim.world.ui_regions() if r["id"] == identifier)
        a, b, c, d = region["rect"]
        result = sim.action({"type": "click", "x": (a + c) / 2, "y": (b + d) / 2})
        self.assertEqual(result["status"], "simulated", result)

    def test_form_visual_controls_edit_and_resolve_async(self):
        s = self.sim("""
            import {world,system} from '@minecraft/server';
            import {ModalFormData} from '@minecraft/server-ui';
            system.run(async()=>{
              const r=await new ModalFormData().title('Settings').toggle('Enabled')
                .slider('Count',0,10,{valueStep:2}).dropdown('Color',['Blue','Red'])
                .textField('Name','Type name').show(world.getAllPlayers()[0]);
              world.setDynamicProperty('response',JSON.stringify(r));
            });
        """)
        s.action({"type": "tick"})
        first = s.screenshot()[1]
        for i in range(4):
            self.click(s, f"form:input:{i}")
        s.action({"type": "text", "text": "Robot"})
        second = s.screenshot()[1]
        self.assertNotEqual(first, second)
        self.assertEqual(Image.open(io.BytesIO(second)).size, (640, 400))
        s.action({"type": "key", "key": "e"})
        self.assertEqual(s.world.player["menu"], "form")
        self.click(s, "form:submit")
        self.assertFalse(s.script.broken, s.report())
        self.assertEqual(json.loads(s.world.dynamic["response"])["formValues"], [True, 4, 1, "Robot"])

    def test_paginated_form_preserves_original_button_index(self):
        s = self.sim("""
            import {world,system} from '@minecraft/server';
            import {ActionFormData} from '@minecraft/server-ui';
            system.run(async()=>{const f=new ActionFormData().title('Pages').header('Header');
              for(let i=0;i<15;i++)f.button('Choice '+i);
              const r=await f.show(world.getAllPlayers()[0]);world.setDynamicProperty('choice',r.selection);});
        """)
        s.action({"type": "tick"})
        self.click(s, "form:next")
        self.click(s, "form:next")
        self.click(s, "form:14")
        self.assertEqual(s.world.dynamic["choice"], 14)

    def test_busy_and_user_closed_forms_are_resolved_promises(self):
        s = self.sim("""
            import {world,system} from '@minecraft/server';
            import {MessageFormData} from '@minecraft/server-ui';
            system.run(()=>{
              new MessageFormData().button1('Yes').button2('No').show(world.getAllPlayers()[0])
                .then(r=>world.setDynamicProperty('first',r.cancelationReason));
              new MessageFormData().show(world.getAllPlayers()[0])
                .then(r=>world.setDynamicProperty('second',r.cancelationReason));
            });
        """)
        s.action({"type": "tick"})
        self.assertEqual(s.world.dynamic["second"], "UserBusy")
        s.action({"type": "key", "key": "escape"})
        self.assertEqual(s.world.dynamic["first"], "UserClosed")

    def test_inventory_failure_and_invalid_set_are_atomic(self):
        s = self.sim()
        s.world.give("workshop:shard", 2304)
        before = deepcopy(s.world.player)
        with self.assertRaises(ValueError):
            s.world.give("workshop:lamp")
        self.assertEqual(s.world.player, before)
        with self.assertRaises(ValueError):
            s.world.inventory.api("set", "player", 0, {"typeId": "workshop:shard", "amount": 65})
        self.assertEqual(s.world.player, before)

    def test_crafting_output_overflow_rolls_back_inputs(self):
        s = self.sim()
        s.world.give("workshop:shard", 2304)
        before = deepcopy(s.world.player)
        recipe = next(iter(s.catalog.recipes))
        with self.assertRaises(ValueError):
            s.world.craft(recipe, ["workshop:shard", "workshop:shard"] + [None] * 7)
        self.assertEqual(s.world.player, before)

    def test_invalid_tick_does_not_replace_block(self):
        s = self.sim()
        s.world.put([0, 0, 0], "workshop:lamp")
        before = deepcopy(s.world.blocks)
        s.catalog.blocks["workshop:lamp"]["components"]["minecraft:tick"] = {"interval_range": [5, 1]}
        with self.assertRaises(ValueError):
            s.world.put([0, 0, 0], "workshop:lamp", {"workshop:lit": True})
        self.assertEqual(s.world.blocks, before)

    def test_tick_replacement_clears_schedule(self):
        s = self.sim()
        components = s.catalog.blocks["workshop:lamp"]["components"]
        components["minecraft:tick"] = {"interval_range": [2, 2]}
        s.world.put([0, 0, 0], "workshop:lamp")
        self.assertEqual(s.world.block_ticks[(0, 0, 0)], 2)
        del components["minecraft:tick"]
        s.world.put([0, 0, 0], "workshop:lamp")
        self.assertEqual(s.world.block_ticks, {})

    def test_thin_collision_sweep_cannot_tunnel(self):
        s = self.sim()
        s.catalog.blocks["workshop:lamp"]["components"]["minecraft:collision_box"] = {
            "origin": [-8, 0, -8],
            "size": [16, 16, 0.01],
        }
        s.world.put([0, 0, 0], "workshop:lamp")
        p = s.world.player
        p.update(position=[0.5, 0, 3], velocity=[0, 0, -12])
        s.world.move(p)
        self.assertAlmostEqual(p["position"][2], 0.3 + 0.01 / 16)
        self.assertEqual(p["velocity"][2], 0)

    def test_ray_uses_selection_shape_instead_of_collision(self):
        s = self.sim()
        c = s.catalog.blocks["workshop:lamp"]["components"]
        c["minecraft:collision_box"] = False
        c["minecraft:selection_box"] = {"origin": [-8, 0, -8], "size": [16, 4, 16]}
        s.world.put([0, 1, 0], "workshop:lamp")
        s.world.player.update(position=[0.5, 0, 4], yaw=180, pitch=0)
        self.assertEqual(s.world.ray(), (None, None))
        c["minecraft:selection_box"] = True
        s.world.put([0, 1, 0], "workshop:lamp")
        self.assertEqual(s.world.ray(), ((0, 1, 0), (0, 1, 1)))
        c["minecraft:selection_box"] = False
        s.world.put([0, 1, 0], "workshop:lamp")
        self.assertEqual(s.world.ray(), (None, None))

    def test_cancellation_prevents_place_interact_and_chat_delivery(self):
        s = self.sim("""
            import {world} from '@minecraft/server';
            world.beforeEvents.playerPlaceBlock.subscribe(e=>e.cancel=true);
            world.beforeEvents.playerInteractWithBlock.subscribe(e=>e.cancel=true);
            world.beforeEvents.chatSend.subscribe(e=>e.cancel=true);
            world.afterEvents.playerInteractWithBlock.subscribe(()=>world.setDynamicProperty('unexpected',true));
            world.afterEvents.chatSend.subscribe(()=>world.setDynamicProperty('unexpected',true));
        """)
        s.action({"type": "place", "identifier": "workshop:lamp", "position": [0, 0, 0]})
        self.assertEqual(s.world.blocks, {})
        s.world.put([0, 0, 0], "workshop:lamp")
        s.action({"type": "interact", "position": [0, 0, 0]})
        s.action({"type": "chat", "message": "Hello"})
        self.assertNotIn("unexpected", s.world.dynamic)

    def test_unmodeled_events_and_dynamic_compilation_cannot_silently_pass(self):
        for script in (
            "world.afterEvents.playerDimensionChange.subscribe(()=>{});",
            "eval('1+1');",
            "Function('return 3')();",
        ):
            with self.subTest(script=script):
                s = self.sim("import {world} from '@minecraft/server';try{" + script + "}catch{}")
                s.assert_state("menu", "/player/menu", "eq", "game")
                self.assertEqual(s.report()["overall"], "incomplete")

    def test_removed_entity_invalidates_cached_container(self):
        s = self.sim()
        s.catalog.entities["workshop:robot"]["components"]["minecraft:inventory"] = {"inventory_size": 3}
        key = s.world.spawn("workshop:robot", [0, 0, 0])
        s.world.inventory.api("set", key, 0, {"typeId": "workshop:shard", "amount": 2})
        s.world.entity_api("remove", key)
        with self.assertRaises(ValueError):
            s.world.inventory.api("get", key, 0)

    def test_execute_as_preserves_command_position(self):
        s = self.sim()
        s.world.spawn("workshop:robot", [10, 0, 10])
        result = s.action(
            {"type": "command", "command": "execute as @e[type=workshop:robot] run tp @s ~2 ~ ~"}
        )
        self.assertEqual(result["status"], "simulated", result)
        self.assertEqual(s.world.entities["e1"]["position"], [2.5, 0, 4.5])
        result = s.action({"type": "command", "command": "tp @initiator 0 0 0"})
        self.assertEqual(result["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
