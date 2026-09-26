"""The MCP tool surface is frozen: any change here needs a Refresh in every user's ChatGPT app."""

import asyncio
import json
import unittest
from pathlib import Path

from mcp import Client

from keyhole import __version__
from keyhole.server import create_server

SNAPSHOT = Path(__file__).resolve().with_name("tool_snapshot.json")
PLUGIN = Path(__file__).resolve().parents[1] / ".codex-plugin" / "plugin.json"


class ToolSurfaceTests(unittest.TestCase):
    def test_tool_definitions_match_the_snapshot(self):
        async def listing():
            async with Client(create_server(object())) as client:
                return (await client.list_tools()).tools

        tools = asyncio.run(listing())
        current = [
            t.model_dump(mode="json", exclude_none=True)
            for t in sorted(tools, key=lambda t: t.name)
        ]
        self.assertEqual(current, json.loads(SNAPSHOT.read_text()))

    def test_versions_agree(self):
        self.assertEqual(json.loads(PLUGIN.read_text())["version"], __version__)


if __name__ == "__main__":
    unittest.main()
