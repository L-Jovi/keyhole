#!/usr/bin/env python3
"""A stand-in for `tunnel-client runtimes ...` that keeps a registry under $TMPDIR.

Control files (all optional, all under $TMPDIR):
  fake-tunnel-version   text printed by --version (default "0.0.14+fake")
  fake-tunnel-fail      if present, `runtimes connect` exits 1 with a message
  fake-tunnel-registry.json  alias registry maintained by this script
  fake-tunnel-log.jsonl      every invocation's argv, one JSON list per line
"""

import json
import os
import sys
from pathlib import Path

TMP = Path(os.environ.get("TMPDIR", "/tmp"))
REGISTRY = TMP / "fake-tunnel-registry.json"


def load() -> dict:
    return json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}


def save(registry: dict) -> None:
    REGISTRY.write_text(json.dumps(registry))


def status(alias: str, entry: dict) -> dict:
    running = entry.get("running", False)
    return {
        "alias": alias,
        "runtime_state": "running" if running else "stopped",
        "process_running": running,
        "healthy": running,
        "ready": running,
        "stale": False,
        "tunnel_id": entry.get("tunnel_id"),
        "profile_name": entry.get("profile"),
        "control_plane_poll_health": {"state": "ok" if running else "unknown"},
        "tmux": {"session": f"tc-{alias}" if running else None},
    }


def main() -> int:
    argv = sys.argv[1:]
    with (TMP / "fake-tunnel-log.jsonl").open("a") as log:
        log.write(json.dumps(argv) + "\n")
    if argv == ["--version"]:
        version_file = TMP / "fake-tunnel-version"
        print(version_file.read_text().strip() if version_file.exists() else "0.0.14+fake")
        return 0
    if not argv or argv[0] != "runtimes":
        print("unsupported", file=sys.stderr)
        return 2
    command, args = argv[1], [a for a in argv[2:] if a != "--json"]
    registry = load()
    if command == "list":
        print(json.dumps({"aliases": [{"alias": name} for name in registry]}))
        return 0
    if command == "status":
        alias = args[0]
        if alias not in registry:
            print(f"alias {alias} is not known; run create or connect first")
            return 1
        print(json.dumps(status(alias, registry[alias])))
        return 0
    if command == "stop":
        alias = args[0]
        if alias not in registry:
            print(f"alias {alias} is not known; run create or connect first")
            return 1
        registry[alias]["running"] = False
        save(registry)
        print(json.dumps(status(alias, registry[alias])))
        return 0
    if command == "connect":
        options = dict(zip(args[::2], args[1::2], strict=True))
        alias = options["--alias"]
        if (TMP / "fake-tunnel-fail").exists():
            print("connect refused by fake control plane", file=sys.stderr)
            return 1
        registry[alias] = {
            "running": True,
            "tunnel_id": options.get("--tunnel-id"),
            "profile": options.get("--profile"),
            "mcp_command": options.get("--mcp-command"),
        }
        save(registry)
        print(json.dumps(status(alias, registry[alias])))
        return 0
    print(f"unknown runtimes command {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
