"""Run the real stdio server against a disposable, read-only evaluation fixture."""

import tempfile
from pathlib import Path

from keyhole.server import main as serve
from keyhole.state import StateStore, empty_state

DEMO_TEXT = (
    "Keyhole evaluation demo.\n"
    "This synthetic file exists only inside the evaluation environment.\n"
    "The Demo workspace is read-only. No files on your Mac are connected.\n"
    "For real use, follow https://github.com/L-Jovi/keyhole/blob/main/docs/setup.md\n"
)


def main():
    # Resolve macOS's symlinked temporary-directory prefix when testing outside Docker.
    with tempfile.TemporaryDirectory(prefix="keyhole-demo-") as temporary:
        base = Path(temporary).resolve()
        workspace = base / "Demo"
        workspace.mkdir(mode=0o700)
        (workspace / "WELCOME.txt").write_text(DEMO_TEXT, encoding="utf-8")
        state_dir = base / "private-state"
        state_dir.mkdir(mode=0o700)
        identity = workspace.stat()
        state = empty_state()
        state["workspaces"] = [
            {
                "name": "Demo",
                "path": str(workspace),
                "device": identity.st_dev,
                "inode": identity.st_ino,
                "enabled": True,
                "access": "ro",
                "recovery": "on",
                "exclusions": [],
            }
        ]
        StateStore(state_dir).write(state)
        serve(["--state-dir", str(state_dir), "--generation", state["generation"]])


if __name__ == "__main__":
    main()
