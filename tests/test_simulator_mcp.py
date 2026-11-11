import json
import sys
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def payload(result):
    return result.structuredContent or json.loads(result.content[0].text)


class SimulatorMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_protocol_load_actions_assertions_and_image(self):
        params = StdioServerParameters(command=sys.executable, args=["-m", "mcbe_tester.mcp_server"])
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as client:
            await client.initialize()
            names = {t.name for t in (await client.list_tools()).tools}
            self.assertTrue({"load_addon", "simulate_actions", "render_view", "simulation_report"} <= names)
            self.assertNotIn("launch_minecraft", names)
            caps = await client.call_tool("simulator_capabilities", {})
            self.assertFalse(payload(caps)["requires_minecraft"])
            source = Path(__file__).resolve().parents[1] / "examples/simulator-addon"
            loaded = await client.call_tool("load_addon", {"path": str(source)})
            self.assertFalse(loaded.isError)
            key = payload(loaded)["session_id"]
            action = await client.call_tool(
                "simulate_actions",
                {
                    "session_id": key,
                    "actions": [
                        {"type": "set_block", "identifier": "workshop:lamp", "position": [0, 0, 0]},
                        {"type": "interact", "position": [0, 0, 0]},
                    ],
                },
            )
            self.assertFalse(action.isError)
            asserted = await client.call_tool(
                "assert_state",
                {
                    "session_id": key,
                    "requirement": "toggle",
                    "path": "/blocks/0,0,0/states/workshop:lit",
                    "operator": "eq",
                    "expected": True,
                },
            )
            self.assertEqual(payload(asserted)["status"], "simulation_passed")
            rendered = await client.call_tool("render_view", {"session_id": key})
            self.assertFalse(rendered.isError)
            images = [item for item in rendered.content if item.type == "image"]
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0].mimeType, "image/png")
            report = await client.call_tool("simulation_report", {"session_id": key})
            self.assertIsNone(payload(report)["accuracy_percent"])
            self.assertEqual(payload(report)["native_compatibility"], "unverified")
            await client.call_tool("close_session", {"session_id": key})

    async def test_inline_pack_needs_no_local_addon_path(self):
        from mcbe_tester.sim.mcp import close_session, load_addon

        manifest = {
            "format_version": 2,
            "header": {
                "name": "Inline",
                "uuid": "b33593cb-9e69-4e26-9280-9b80fdafc972",
                "version": [1, 0, 0],
            },
            "modules": [{"type": "data"}],
        }
        result = load_addon(files={"manifest.json": json.dumps(manifest)})
        self.assertIn("session_id", result)
        close_session(result["session_id"])
