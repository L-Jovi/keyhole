"""Exercise the evaluation entrypoint locally or in a network-disabled container."""

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TEXT = (
    "Keyhole evaluation demo.\n"
    "This synthetic file exists only inside the evaluation environment.\n"
    "The Demo workspace is read-only. No files on your Mac are connected.\n"
    "For real use, follow https://github.com/L-Jovi/keyhole/blob/main/docs/setup.md\n"
)


async def check(params, mode):
    async with Client(params, mode=mode, read_timeout_seconds=20) as client:
        tools = (await client.list_tools()).tools
        actual = [
            tool.model_dump(mode="json", exclude_none=True)
            for tool in sorted(tools, key=lambda tool: tool.name)
        ]
        assert actual == json.loads((ROOT / "tests/tool_snapshot.json").read_text())
        assert not (await client.list_resources()).resources
        assert not (await client.list_resource_templates()).resource_templates
        assert not (await client.list_prompts()).prompts
        workspaces = await client.call_tool("list_workspaces", {})
        assert not workspaces.is_error
        assert workspaces.structured_content["workspaces"] == [
            {
                "name": "Demo",
                "status": "available",
                "access": "ro",
                "recovery": "on",
                "exclusions": [],
            }
        ]
        read = await client.call_tool("read_file", {"workspace": "Demo", "path": "WELCOME.txt"})
        assert not read.is_error
        assert (
            read.structured_content["sha256"] == hashlib.sha256(EXPECTED_TEXT.encode()).hexdigest()
        )
        denied = await client.call_tool(
            "write_file",
            {
                "workspace": "Demo",
                "path": "unexpected.txt",
                "text": "Must not be written.",
                "expected_sha256": "absent",
                "request_id": "glama-smoke-write",
            },
        )
        assert denied.is_error and denied.structured_content["error"]["code"] == "read_only"
        listing = await client.call_tool("list_directory", {"workspace": "Demo"})
        assert not listing.is_error
        assert [entry["name"] for entry in listing.structured_content["entries"]] == ["WELCOME.txt"]
        for path in ("../private-state/grants.json", "/etc/passwd"):
            denied = await client.call_tool("read_file", {"workspace": "Demo", "path": path})
            assert denied.is_error and denied.structured_content["error"]["code"] == "invalid_path"
        print(f"{mode}: 12 unchanged tools; read, read-only and path boundaries passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", help="Test this Docker image instead of the local bootstrap.")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="keyhole-smoke-") as temporary:
        if args.image:
            params = StdioServerParameters(
                command="docker",
                args=[
                    "run",
                    "--rm",
                    "-i",
                    "--init",
                    "--network=none",
                    "--read-only",
                    "--tmpfs=/tmp:rw,nosuid,nodev,size=64m",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--memory=512m",
                    "--pids-limit=64",
                    args.image,
                ],
            )
        else:
            params = StdioServerParameters(
                command=sys.executable,
                args=["-I", str(ROOT / "contrib/glama/bootstrap.py")],
                env={"TMPDIR": str(Path(temporary).resolve()), "PYTHONDONTWRITEBYTECODE": "1"},
            )
        for mode in ("auto", "legacy"):
            asyncio.run(check(params, mode))
        assert not list(Path(temporary).iterdir()), "The local demo left temporary state behind."


if __name__ == "__main__":
    main()
