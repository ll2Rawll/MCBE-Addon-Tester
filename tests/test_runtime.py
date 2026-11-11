import json
import tempfile
import time
import unittest
from pathlib import Path

from mcbe_tester.sim.runner import Simulation
from tests.test_simulator import simulation


class RuntimeTests(unittest.TestCase):
    def sim(self, source, mutate=None, ready=True):
        # Most API tests need a loaded world; run their module continuation after
        # the actual v2 worldLoad event instead of granting test-only privileges.
        if ready:
            source = (
                "import {world as lifecycleWorld} from '@minecraft/server'; await new Promise(resolve=>lifecycleWorld.afterEvents.worldLoad.subscribe(resolve));\n"
                + source
            )
        result = simulation(source, mutate)
        self.addCleanup(result.close)
        return result

    def test_classes_destructuring_optional_chaining_generators_and_async(self):
        s = self.sim("""
            import {world, system} from '@minecraft/server';
            class Counter { #value=1; add(n){ this.#value+=n; } get value(){return this.#value;} }
            const counter = new Counter();
            const {x,...rest} = {x:2,y:3};
            counter.add(x + rest?.y);
            await Promise.resolve();
            world.setDynamicProperty('answer', counter.value);
            system.runJob((function*(){for(let i=0;i<3;i++){world.setDynamicProperty('job', i);yield;}})());
        """)
        self.assertFalse(s.script.broken, s.report())
        self.assertEqual(s.world.dynamic["answer"], 6)
        s.action({"type": "tick", "ticks": 1})
        self.assertEqual(s.world.dynamic["job"], 2)

    def test_esm_live_bindings_and_dynamic_import(self):
        def mutate(files):
            files["behavior_pack/scripts/state.js"] = b"export let count=0; export function add(){count++;}"

        s = self.sim(
            """
            import {world} from '@minecraft/server';
            import {count,add} from './state.js';
            add(); add();
            const ns=await import('./state.js');
            world.setDynamicProperty('count',count+ns.count);
        """,
            mutate,
        )
        self.assertFalse(s.script.broken, s.report())
        self.assertEqual(s.world.dynamic["count"], 4)

    def test_uncaught_sync_and_async_errors_fail_reports(self):
        for source in [
            "throw Error('failure-sentinel');",
            "Promise.reject(Error('failure-sentinel'));",
            "async function f(){throw Error('failure-sentinel')}; f();",
            "import {system} from '@minecraft/server'; system.run(async()=>{throw Error('failure-sentinel')});",
        ]:
            with self.subTest(source=source):
                s = self.sim(source)
                s.action({"type": "tick", "ticks": 1})
                self.assertTrue(s.script.broken)
                self.assertTrue(any("failure-sentinel" in x["message"] for x in s.report()["findings"]))
                self.assertEqual(s.report()["overall"], "failed")

    def test_handled_rejections_do_not_fail(self):
        s = self.sim("""
            import {world} from '@minecraft/server';
            try {await Promise.reject(Error('expected'));}catch{world.setDynamicProperty('caught',true);}
            Promise.reject(Error('handled')).catch(()=>{});
        """)
        self.assertFalse(s.script.broken, s.report())
        self.assertTrue(s.world.dynamic["caught"])

    def test_script_has_no_node_or_python_host_globals(self):
        s = self.sim("""
            import {world} from '@minecraft/server';
            world.setDynamicProperty('globals', JSON.stringify([typeof process,typeof globalThis.require,typeof fetch,typeof __host_rpc,typeof __sim_dispatch]));
        """)
        self.assertEqual(json.loads(s.world.dynamic["globals"]), ["undefined"] * 5)

    def test_heap_limit_and_runaway_job_terminate(self):
        started = time.monotonic()
        s = self.sim("const values=[]; while(true) values.push(new Uint8Array(1024*1024));")
        self.assertTrue(s.script.broken)
        self.assertLess(time.monotonic() - started, 8)
        self.assertIsNone(s.script.process)

    def test_entity_queries_scoreboards_and_commands_share_state(self):
        s = self.sim("""
            import {world} from '@minecraft/server';
            const dim=world.getDimension('overworld');
            const first=dim.spawnEntity('workshop:robot',{x:1,y:0,z:0});
            first.addTag('chosen');
            dim.spawnEntity('workshop:robot',{x:8,y:0,z:0});
            const selected=dim.getEntities({type:'workshop:robot',tags:['chosen'],location:{x:0,y:0,z:0},maxDistance:3});
            world.setDynamicProperty('selected',selected.length);
            world.scoreboard.addObjective('points','Points').setScore(first,3);
            dim.runCommand('scoreboard players add @e[tag=chosen] points 4');
            dim.runCommand('execute as @e[tag=chosen] run tp @s ~1 ~ ~');
        """)
        self.assertFalse(s.script.broken, s.report())
        self.assertEqual(s.world.dynamic["selected"], 1)
        self.assertEqual(s.world.scoreboard.objectives["points"]["scores"]["e1"], 7)
        # execute as changes the executor, not the original command position.
        self.assertEqual(s.world.entities["e1"]["position"], [1, 0, 0])

    def test_damage_events_and_effect_expiration(self):
        s = self.sim("""
            import {world} from '@minecraft/server';
            world.afterEvents.entityHurt.subscribe(e=>world.setDynamicProperty('hurt',e.damage));
            world.afterEvents.entityDie.subscribe(e=>world.setDynamicProperty('dead',e.deadEntity.id));
            const e=world.getDimension('overworld').spawnEntity('workshop:robot',{x:0,y:0,z:0});
            e.addEffect('speed',2,{amplifier:1}); e.applyDamage(5); e.applyDamage(7);
        """)
        self.assertFalse(s.script.broken, s.report())
        self.assertEqual(s.world.dynamic["hurt"], 7)
        self.assertEqual(s.world.dynamic["dead"], "e1")
        self.assertEqual(s.world.entities["e1"]["health"], 0)
        s.action({"type": "tick", "ticks": 2})
        self.assertEqual(s.world.entities["e1"]["effects"], [])

    def test_inventory_stack_identity_and_clone(self):
        s = self.sim("""
            import {world,ItemStack} from '@minecraft/server';
            const container=world.getAllPlayers()[0].getComponent('inventory').container;
            const original=new ItemStack('workshop:shard',60);
            original.nameTag='Named'; original.setLore(['line']);
            container.addItem(original);
            const second=original.clone(); second.amount=8;
            container.addItem(second);
            second.nameTag='Different';second.amount=1;container.addItem(second);
            const copy=container.getItem(0);copy.amount=1;
            world.setDynamicProperty('untouched',container.getItem(0).amount);
        """)
        self.assertFalse(s.script.broken, s.report())
        slots = s.world.inventory.slots["player"]
        self.assertEqual([slot["amount"] for slot in slots[:3]], [64, 4, 1])
        self.assertEqual(s.world.dynamic["untouched"], 64)
        self.assertEqual(s.world.player["inventory"]["workshop:shard"], 69)

    def test_async_action_form_callback_from_click_and_replay(self):
        s = self.sim("""
            import {system,world} from '@minecraft/server';
            import {ActionFormData} from '@minecraft/server-ui';
            system.run(async()=>{
              const response=await new ActionFormData().title('Choose').body('Pick a mode').button('One').button('Two').show(world.getAllPlayers()[0]);
              if(!response.canceled)world.setDynamicProperty('chosen',response.selection);
            });
        """)
        s.action({"type": "tick", "ticks": 1})
        self.assertEqual(s.world.player["menu"], "form")
        region = next(r for r in s.world.ui_regions() if r["id"] == "form:1")
        x0, y0, x1, y1 = region["rect"]
        s.action({"type": "click", "x": (x0 + x1) / 2, "y": (y0 + y1) / 2})
        self.assertFalse(s.script.broken, s.report())
        self.assertEqual(s.world.dynamic["chosen"], 1)
        s.assert_state("choice", "/dynamic_properties/chosen", "eq", 1)
        with tempfile.TemporaryDirectory() as folder:
            s.export(folder)
            spec = json.loads((Path(folder) / "replay.json").read_text())
            other = Simulation.open(Path(folder) / "input.zip", spec["seed"])
            self.addCleanup(other.close)
            self.assertEqual(other.scenario(spec)["report"]["overall"], "simulation_passed")

    def test_modal_form_types_and_cancel(self):
        s = self.sim("""
            import {system,world} from '@minecraft/server';
            import {ModalFormData} from '@minecraft/server-ui';
            system.run(()=>new ModalFormData().title('Settings').toggle('Enabled').slider('Count',0,10,{valueStep:2}).dropdown('Type',['A','B']).textField('Name','name').show(world.getAllPlayers()[0]).then(r=>world.setDynamicProperty('response',JSON.stringify(r))));
        """)
        s.action({"type": "tick", "ticks": 1})
        with self.assertRaises(ValueError):
            s.world.forms.respond(values=[True, 3, 0, "x"])
        self.assertIsNotNone(s.world.forms.current)
        s.action({"type": "form_response", "values": [True, 4, 1, "name"]})
        self.assertEqual(json.loads(s.world.dynamic["response"])["formValues"], [True, 4, 1, "name"])

    def test_custom_block_tick_and_item_hooks(self):
        def mutate(files):
            data = json.loads(files["behavior_pack/blocks/lamp.json"])
            data["minecraft:block"]["components"]["minecraft:tick"] = {"interval_range": [2, 2]}
            files["behavior_pack/blocks/lamp.json"] = json.dumps(data).encode()

        s = self.sim(
            """
            import {system,world} from '@minecraft/server';
            system.beforeEvents.startup.subscribe(e=>e.blockComponentRegistry.registerCustomComponent('workshop:toggle',{
              onTick(){world.setDynamicProperty('ticks',(world.getDynamicProperty('ticks')||0)+1);}
            }));
        """,
            mutate,
            ready=False,
        )
        s.action({"type": "set_block", "identifier": "workshop:lamp", "position": [0, 0, 0]})
        s.action({"type": "tick", "ticks": 6})
        self.assertEqual(s.world.dynamic["ticks"], 3)


if __name__ == "__main__":
    unittest.main()
