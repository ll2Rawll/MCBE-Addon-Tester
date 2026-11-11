import io
import json
import unittest

import numpy as np
from PIL import Image

from mcbe_tester.sim.animations import channel, pose
from mcbe_tester.sim.diagnostics import Unsupported
from mcbe_tester.sim.molang import evaluate
from mcbe_tester.sim.render import Renderer
from mcbe_tester.sim.render_controllers import layers
from tests.test_simulator import simulation


class MolangContractTests(unittest.TestCase):
    def test_documented_numeric_and_control_flow_contracts(self):
        cases = [
            ("v.x=2; v.x+3;", 0),
            ("V.X=2; RETURN v.x+3;", 5),
            ("1 / 0", 0),
            ("math.sqrt(-1)", 0),
            ("math.mod(-5,2)", -1),
            ("math.round(-1.5)", -2),
            ("math.asin(1)", 90),
            ("math.acos(-1)", 180),
            ("math.atan2(1,0)", 90),
            ("math.hermite_blend(0.5)", 0.5),
            ("math.min_angle(math.lerprotate(350,10,0.5))", 0),
            ("v.missing ?? 7", 7),
            ("v.x=0; return v.x ?? 7;", 0),
            ("1 ? 0 ? 2 : 3 : 4", 3),
            ("0 ? q.unimplemented", 0),
            ("v.x=0; loop(4, {v.x=v.x+2;}); return v.x;", 8),
            ("v.x=0; loop(5, {v.x=v.x+1; (v.x==3)?{break;};}); return v.x;", 3),
            ("v.x=0; v.y=0; loop(3,{v.x=v.x+1; (v.x==2)?{continue;}; v.y=v.y+1;}); return v.y;", 2),
            ("v.x=0; loop(99999,{v.x=v.x+1;}); return v.x;", 1024),
            ("loop(5, {return 9;}); return 1;", 9),
            ("v.point.x=5; v.other=v.point; return v.other.x;", 5),
            ("'HELLO' == 'hello'", False),
            ("array.skins[-1.9]", "first"),
            ("array.skins[4.9]", "second"),
        ]
        for expression, expected in cases:
            with self.subTest(expression=expression):
                result = evaluate(expression, {"array.skins": ["first", "second", "third"]})
                self.assertEqual(result, expected)

    def test_errors_remain_observable_and_unknown_features_block(self):
        errors = []
        self.assertEqual(evaluate("1 / 0", {"_molang_error": errors.append}), 0)
        self.assertEqual(len(errors), 1)
        for expression in (
            "q.unknown ?? 4",
            "'a' + 'b'",
            "loop(1024,{loop(1024,{v.x=1;});});",
            "return 1; @",
        ):
            with self.subTest(expression=expression), self.assertRaises(Unsupported):
                evaluate(expression)

    def test_temp_scope_and_short_circuit_side_effects(self):
        variables = {}
        self.assertEqual(evaluate("t.x=6; return t.x;", variables=variables), 6)
        self.assertEqual(evaluate("t.x ?? 9", variables=variables), 9)
        self.assertEqual(evaluate("v.x=0; 0 && (v.x=1); return v.x;", variables=variables), 0)


class ScriptPrivilegeTests(unittest.TestCase):
    def sim(self, source):
        sim = simulation(source)
        self.addCleanup(sim.close)
        return sim

    def test_early_access_fails_but_worldload_is_available(self):
        s = self.sim("""
            import {world} from '@minecraft/server';
            let blocked=false;
            try { world.getAllPlayers(); } catch(e) { blocked=e.message.includes('early execution'); }
            world.afterEvents.worldLoad.subscribe(()=>world.setDynamicProperty('blocked',blocked));
        """)
        self.assertFalse(s.script.broken, s.report())
        self.assertTrue(s.world.dynamic["blocked"])
        other = self.sim("import {world} from '@minecraft/server';world.setDynamicProperty('bad',1);")
        self.assertTrue(other.script.broken)
        self.assertNotIn("bad", other.world.dynamic)

    def test_startup_cannot_write_and_registry_expires(self):
        s = self.sim("""
            import {world,system} from '@minecraft/server';
            let registry, blocked=false;
            system.beforeEvents.startup.subscribe(e=>{
              registry=e.blockComponentRegistry;
              try{world.setDynamicProperty('bad',true);}catch{blocked=true;}
            });
            world.afterEvents.worldLoad.subscribe(()=>{
              world.setDynamicProperty('startupBlocked',blocked);
              try{registry.registerCustomComponent('workshop:late',{});}
              catch{world.setDynamicProperty('registrationBlocked',true);}
            });
        """)
        self.assertFalse(s.script.broken, s.report())
        self.assertTrue(s.world.dynamic["startupBlocked"])
        self.assertTrue(s.world.dynamic["registrationBlocked"])

    def test_before_event_reads_messages_and_deferred_writes(self):
        s = self.sim("""
            import {world,system} from '@minecraft/server';
            let blocked=false;
            world.beforeEvents.playerBreakBlock.subscribe(e=>{
              world.sendMessage(e.player.getGameMode());
              try{e.player.setGameMode('survival');}catch(err){blocked=err.message.includes('restricted execution');}
              system.run(()=>world.setDynamicProperty('deferred',true));
            });
            world.afterEvents.playerBreakBlock.subscribe(()=>world.setDynamicProperty('blocked',blocked));
        """)
        s.world.put([0, 0, 0], "workshop:lamp")
        s.action({"type": "break", "position": [0, 0, 0]})
        self.assertFalse(s.script.broken, s.report())
        self.assertEqual(s.world.player["mode"], "creative")
        self.assertTrue(s.world.dynamic["blocked"])
        s.action({"type": "tick"})
        self.assertTrue(s.world.dynamic["deferred"])

    def test_async_before_continuation_runs_after_restricted_phase(self):
        s = self.sim("""
            import {world} from '@minecraft/server';
            world.beforeEvents.playerBreakBlock.subscribe(async e=>{
              await Promise.resolve();
              world.setDynamicProperty('continued',true);
              e.cancel=true;
            });
        """)
        s.world.put([0, 0, 0], "workshop:lamp")
        s.action({"type": "break", "position": [0, 0, 0]})
        self.assertFalse(s.script.broken, s.report())
        self.assertTrue(s.world.dynamic["continued"])
        self.assertNotIn((0, 0, 0), s.world.blocks)  # Cancellation after await is too late.

    def test_wait_ticks_can_schedule_early_and_rejects_invalid_delays(self):
        s = self.sim("""
            import {world,system} from '@minecraft/server';
            system.waitTicks(3).then(()=>world.setDynamicProperty('resolved',system.currentTick));
            let invalid=0;
            for(const n of [0,-1,0.5,NaN,Infinity])try{system.waitTicks(n);}catch{invalid++;}
            world.afterEvents.worldLoad.subscribe(()=>world.setDynamicProperty('invalid',invalid));
        """)
        self.assertEqual(s.world.dynamic["invalid"], 5)
        s.world.advance(2)
        self.assertNotIn("resolved", s.world.dynamic)
        s.world.advance()
        self.assertEqual(s.world.dynamic["resolved"], 3)

    def test_builtin_import_must_be_declared_by_owning_pack(self):
        def mutate(files):
            path = "behavior_pack/manifest.json"
            data = json.loads(files[path])
            data["dependencies"] = [
                d for d in data["dependencies"] if d.get("module_name") != "@minecraft/server-ui"
            ]
            files[path] = json.dumps(data).encode()

        s = simulation("import {ActionFormData} from '@minecraft/server-ui'; new ActionFormData();", mutate)
        self.addCleanup(s.close)
        self.assertTrue(s.script.broken)
        self.assertIn("not declared", json.dumps(s.report()))

    def test_script_events_reject_early_calls_and_deliver_in_default_phase(self):
        s = self.sim("""
            import {world,system} from '@minecraft/server';
            let blocked=false;
            try{system.sendScriptEvent('workshop:ping','early');}catch{blocked=true;}
            system.afterEvents.scriptEventReceive.subscribe(e=>{
              world.setDynamicProperty('payload',e.message);
              world.setDynamicProperty('blocked',blocked);
            });
            world.afterEvents.worldLoad.subscribe(()=>system.sendScriptEvent('workshop:ping','ready'));
        """)
        self.assertFalse(s.script.broken, s.report())
        self.assertTrue(s.world.dynamic["blocked"])
        self.assertEqual(s.world.dynamic["payload"], "ready")

    def test_restricted_item_reads_clone_and_setters(self):
        s = self.sim("""
            import {world,system,ItemStack} from '@minecraft/server';
            let failures=0,readable=false;
            world.afterEvents.worldLoad.subscribe(()=>{
              const item=new ItemStack('workshop:shard');item.nameTag='Original';
              world.getAllPlayers()[0].getComponent('inventory').container.setItem(0,item);
            });
            world.beforeEvents.itemUse.subscribe(e=>{
              const copy=e.itemStack.clone();readable=copy.nameTag==='Original';
              for(const f of [()=>copy.amount=2,()=>copy.nameTag='Changed',()=>copy.setLore(['bad'])])try{f();}catch{failures++;}
              system.run(()=>{world.setDynamicProperty('failures',failures);world.setDynamicProperty('readable',readable);});
            });
        """)
        s.action({"type": "use"})
        s.world.advance()
        self.assertFalse(s.script.broken, s.report())
        self.assertEqual(s.world.dynamic["failures"], 3)
        self.assertTrue(s.world.dynamic["readable"])

    def test_item_limits_and_clones_do_not_alias_metadata(self):
        def mutate(files):
            files["behavior_pack/items/unique.json"] = json.dumps(
                {
                    "format_version": "1.21.90",
                    "minecraft:item": {
                        "description": {"identifier": "workshop:unique"},
                        "components": {"minecraft:max_stack_size": 1},
                    },
                }
            ).encode()

        s = simulation(
            """
            import {world,ItemStack} from '@minecraft/server';
            world.afterEvents.worldLoad.subscribe(()=>{
              const item=new ItemStack('workshop:shard',200);
              world.setDynamicProperty('clamped',item.amount);
              let errors=0;
              for(const n of [0,-1,256,1.5])try{new ItemStack('workshop:shard',n);}catch{errors++;}
              for(const f of [()=>item.nameTag='x'.repeat(256),()=>item.setLore(['x'.repeat(51)]),()=>item.setLore(Array(21).fill('x'))])try{f();}catch{errors++;}
              try{item.setDynamicProperty('x',1);}catch{errors++;}
              world.setDynamicProperty('errors',errors);
              const unique=new ItemStack('workshop:unique');
              unique.setDynamicProperty('x',1);const cloned=unique.clone();cloned.setDynamicProperty('x',2);
              world.setDynamicProperty('original',unique.getDynamicProperty('x'));
              world.setDynamicProperty('copy',cloned.getDynamicProperty('x'));
              item.nameTag='';world.setDynamicProperty('cleared',item.nameTag===undefined);
            });
        """,
            mutate,
        )
        self.addCleanup(s.close)
        self.assertFalse(s.script.broken, s.report())
        self.assertEqual(
            s.world.dynamic, {"clamped": 64, "errors": 8, "original": 1, "copy": 2, "cleared": True}
        )


class RenderControllerTests(unittest.TestCase):
    def sim(self, mutate=None):
        sim = simulation("", mutate)
        self.addCleanup(sim.close)
        sim.world.spawn("workshop:robot", [0, 0, 0])
        return sim

    def test_texture_array_and_geometry_selection_change_pixels(self):
        def mutate(files):
            key = "resource_pack/entity/robot.json"
            data = json.loads(files[key])
            desc = data["minecraft:client_entity"]["description"]
            desc["textures"]["alternate"] = "textures/entity/alternate"
            files[key] = json.dumps(data).encode()
            buffer = io.BytesIO()
            Image.new("RGBA", (64, 64), (255, 20, 20, 255)).save(buffer, format="PNG")
            files["resource_pack/textures/entity/alternate.png"] = buffer.getvalue()
            data = json.loads(files["resource_pack/render_controllers/robot.json"])
            controller = data["render_controllers"]["controller.render.workshop.robot"]
            controller["arrays"] = {"textures": {"Array.skins": ["Texture.default", "Texture.alternate"]}}
            controller["textures"] = ["Array.skins[q.property('workshop:active')]"]
            files["resource_pack/render_controllers/robot.json"] = json.dumps(data).encode()

        s = self.sim(mutate)
        entity = s.world.entities["e1"]
        self.assertEqual(layers(s.world, entity)[0]["texture"], "textures/entity/robot")
        before = s.screenshot()[1]
        s.world.set_property("e1", "workshop:active", True)
        self.assertEqual(layers(s.world, entity)[0]["texture"], "textures/entity/alternate")
        after = s.screenshot()[1]
        self.assertNotEqual(before, after)
        self.assertEqual(after, s.screenshot()[1])

    def test_ordered_visibility_and_conditional_controller(self):
        s = self.sim()
        controller = s.catalog.render_controllers["controller.render.workshop.robot"]
        controller["part_visibility"] = [{"*": False}, {"head": True}]
        layer = layers(s.world, s.world.entities["e1"])[0]
        self.assertTrue(layer["visibility"]["head"])
        self.assertFalse(layer["visibility"]["body"])
        desc = s.catalog.clients["workshop:robot"]["description"]
        desc["render_controllers"] = [{"controller.render.workshop.robot": "q.property('workshop:active')"}]
        self.assertEqual(layers(s.world, s.world.entities["e1"]), [])
        s.world.set_property("e1", "workshop:active", True)
        self.assertEqual(len(layers(s.world, s.world.entities["e1"])), 1)

    def test_missing_controller_shader_and_array_cycles_block(self):
        for mutation in ("missing", "shader", "cycle"):
            with self.subTest(mutation=mutation):
                s = self.sim()
                controller = s.catalog.render_controllers["controller.render.workshop.robot"]
                if mutation == "missing":
                    s.catalog.render_controllers.clear()
                elif mutation == "shader":
                    controller["materials"] = [{"*": "'unknown_shader'"}]
                else:
                    controller["arrays"] = {"textures": {"Array.a": ["Array.b"], "Array.b": ["Array.a"]}}
                s.screenshot()
                s.assert_state("loaded", "/player/menu", "eq", "game")
                self.assertEqual(s.report()["overall"], "incomplete")

    def test_near_plane_clips_instead_of_dropping_crossing_triangle(self):
        s = self.sim()
        renderer = Renderer(s.world, camera="player")
        # One vertex behind the near plane; the visible polygon must remain.
        origin, forward, right, up = renderer.origin, renderer.forward, renderer.right, renderer.up
        vertices = np.array(
            [
                origin + forward * 0.01 + up * 0.2,
                origin + forward * 1 - right * 0.5 - up * 0.2,
                origin + forward * 1 + right * 0.5 - up * 0.2,
            ]
        )
        texture = np.full((2, 2, 4), 255, dtype=np.uint8)
        renderer.triangle(vertices, np.array([[0, 0], [0, 2], [2, 2]], float), texture, 1)
        self.assertGreater(np.count_nonzero(np.isfinite(renderer.depth)), 100)
        self.assertGreaterEqual(renderer.depth[np.isfinite(renderer.depth)].min(), 0.05 - 1e-8)


class AnimationContractTests(unittest.TestCase):
    def sim(self):
        s = simulation("")
        self.addCleanup(s.close)
        s.catalog.animations["animation.workshop.idle"] = {
            "loop": True,
            "animation_length": 2,
            "bones": {"body": {"rotation": [0, 0, 0]}},
        }
        s.catalog.animations["animation.workshop.active"] = {
            "animation_length": 1,
            "bones": {"body": {"rotation": {"0": [0, 0, 0], "1": [0, 90, 0]}}},
        }
        s.world.spawn("workshop:robot", [0, 0, 0])
        return s

    def test_animation_starts_when_entered_not_when_entity_was_spawned(self):
        s = self.sim()
        s.world.advance(40)
        s.world.set_property("e1", "workshop:active", True)
        s.world.advance()
        self.assertEqual(pose(s.world, s.world.entities["e1"])["body"]["rotation"][1], 0)
        s.world.advance(10)
        self.assertAlmostEqual(pose(s.world, s.world.entities["e1"])["body"]["rotation"][1], 45)

    def test_reentering_state_restarts_clip_and_entry_exit_run_once(self):
        s = self.sim()
        states = s.catalog.controllers["controller.animation.workshop.robot"]["states"]
        states["active"]["on_entry"] = ["v.entries=(v.entries ?? 0)+1;"]
        states["active"]["on_exit"] = ["v.exits=(v.exits ?? 0)+1;"]
        s.world.set_property("e1", "workshop:active", True)
        s.world.advance(3)
        s.world.set_property("e1", "workshop:active", False)
        s.world.advance()
        s.world.set_property("e1", "workshop:active", True)
        s.world.advance()
        entity = s.world.entities["e1"]
        self.assertEqual(entity["variables"]["entries"], 2)
        self.assertEqual(entity["variables"]["exits"], 1)
        self.assertEqual(pose(s.world, entity)["body"]["rotation"][1], 0)
        before = dict(entity["variables"])
        s.screenshot()
        s.screenshot()
        self.assertEqual(before, entity["variables"])

    def test_rotation_crossfade_uses_outgoing_duration(self):
        s = self.sim()
        s.catalog.animations["animation.workshop.active"]["bones"]["body"]["rotation"] = [0, 90, 0]
        s.catalog.controllers["controller.animation.workshop.robot"]["states"]["idle"]["blend_transition"] = (
            0.2
        )
        s.world.set_property("e1", "workshop:active", True)
        s.world.advance()
        entity = s.world.entities["e1"]
        self.assertEqual(pose(s.world, entity)["body"]["rotation"][1], 0)
        s.world.advance(2)
        self.assertEqual(pose(s.world, entity)["body"]["rotation"][1], 45)
        s.world.advance(2)
        self.assertEqual(pose(s.world, entity)["body"]["rotation"][1], 90)

    def test_finished_query_transitions_after_clip_duration(self):
        s = self.sim()
        states = s.catalog.controllers["controller.animation.workshop.robot"]["states"]
        states["active"]["transitions"] = [{"finished": "q.all_animations_finished"}]
        states["finished"] = {"on_entry": ["v.done=1;"]}
        s.world.set_property("e1", "workshop:active", True)
        s.world.advance(20)
        self.assertEqual(
            s.world.entities["e1"]["controllers"]["controller.animation.workshop.robot"], "active"
        )
        s.world.advance()
        self.assertEqual(s.world.entities["e1"]["variables"]["done"], 1)

    def test_discontinuous_keyframe_and_uniform_vector_contract(self):
        value = {"0.5": {"pre": [1], "post": [2]}, "1": [1]}
        np.testing.assert_allclose(channel(value, 0.25, {}, {}, [1, 1, 1]), [1, 1, 1])
        np.testing.assert_allclose(channel(value, 0.5, {}, {}, [1, 1, 1]), [2, 2, 2])
        np.testing.assert_allclose(channel(value, 0.75, {}, {}, [1, 1, 1]), [1.5, 1.5, 1.5])
        self.assertEqual(value["0.5"]["post"], [2])

    def test_finished_clip_stops_unless_hold_or_loop_is_enabled(self):
        s = self.sim()
        s.world.set_property("e1", "workshop:active", True)
        s.world.advance(22)
        entity = s.world.entities["e1"]
        self.assertNotIn("body", pose(s.world, entity))
        s.catalog.animations["animation.workshop.active"]["loop"] = "hold_on_last_frame"
        self.assertEqual(pose(s.world, entity)["body"]["rotation"][1], 90)
        s.catalog.animations["animation.workshop.active"]["loop"] = True
        self.assertAlmostEqual(pose(s.world, entity)["body"]["rotation"][1], 4.5)


class FeatureAuditTests(unittest.TestCase):
    def changed_pack(self, path, change):
        def mutate(files):
            data = json.loads(files[path])
            change(data)
            files[path] = json.dumps(data).encode()

        s = simulation("", mutate)
        self.addCleanup(s.close)
        s.assert_state("loaded", "/player/menu", "eq", "game")
        return s

    def test_unsupported_nested_component_field_blocks_even_without_spawning(self):
        s = self.changed_pack(
            "behavior_pack/entities/robot.json",
            lambda d: d["minecraft:entity"]["components"].update(
                {"minecraft:inventory": {"inventory_size": 4, "restrict_to_owner": True}}
            ),
        )
        self.assertEqual(s.report()["overall"], "incomplete")
        self.assertIn("restrict_to_owner", json.dumps(s.report()))
        self.assertGreater(s.report()["coverage"]["unsupported_occurrences"], 0)

    def test_unknown_query_in_unreached_state_is_not_hidden(self):
        s = self.changed_pack(
            "resource_pack/animation_controllers/robot.json",
            lambda d: d["animation_controllers"]["controller.animation.workshop.robot"]["states"].update(
                unreachable={"animations": [{"idle": "q.is_swimming"}]}
            ),
        )
        self.assertEqual(s.report()["overall"], "incomplete")
        self.assertIn("query.is_swimming", json.dumps(s.report()))

    def test_unknown_interpolation_and_api_major_block(self):
        s = self.changed_pack(
            "resource_pack/animations/robot.json",
            lambda d: d["animations"]["animation.workshop.active"]["bones"]["body"].update(
                rotation={"0": {"post": [0, 0, 0], "lerp_mode": "catmullrom"}}
            ),
        )
        self.assertEqual(s.report()["overall"], "incomplete")
        s = self.changed_pack(
            "behavior_pack/manifest.json",
            lambda d: next(
                v for v in d["dependencies"] if v.get("module_name") == "@minecraft/server"
            ).update(version="1.19.0"),
        )
        self.assertEqual(s.report()["overall"], "incomplete")
        self.assertIn("script-api-version", json.dumps(s.report()))


if __name__ == "__main__":
    unittest.main()
