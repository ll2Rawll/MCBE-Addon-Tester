import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.helpers import addon, plan


class MCPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_handshake_and_tools(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = addon(root / "addon")
            spec = plan(root / "plan.json")
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "mcbe_tester.legacy_mcp_server"],
                env={**os.environ, "MCBE_LAB_ROOT": str(root / "lab")},
            )
            async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                self.assertTrue(
                    {"screenshot", "press_keys", "compare_visual", "assert_state", "write_report"} <= names
                )
                started = await session.call_tool(
                    "start_run", {"plan_path": str(spec), "addon_path": str(source)}
                )
                self.assertFalse(started.isError)
                report = await session.call_tool("write_report", {})
                self.assertFalse(report.isError)
                payload = report.structuredContent or json.loads(report.content[0].text)
                self.assertEqual(payload["overall"], "incomplete")
