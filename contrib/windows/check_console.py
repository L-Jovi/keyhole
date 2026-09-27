"""Exercise the installed wizard in a real Windows pseudoterminal with fake account values."""

import argparse
import json
import os
import re
import select
import subprocess
import time
from pathlib import Path

from winpty import PtyProcess

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
FAKE_KEY = "sk-synthetic-console-never-a-real-credential"
FAKE_TUNNEL = "tunnel_0123456789abcdef0123456789abcdef"


def interact(command, prompts, expected_exit):
    process = PtyProcess.spawn(command, dimensions=(30, 160))
    received = ""
    index = 0
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select.select([process], [], [], 0.2)
            if readable:
                try:
                    received += process.read(8192)
                except EOFError:
                    break
                clean = ANSI.sub("", received)
                if index < len(prompts) and prompts[index][0] in clean:
                    process.write(prompts[index][1] + "\r")
                    index += 1
            elif not process.isalive():
                break
        assert not process.isalive(), "Wizard did not finish before the console deadline."
        assert index == len(prompts), "The wizard did not show the expected console prompts."
        assert process.exitstatus == expected_exit, "The wizard returned an unexpected exit code."
        assert FAKE_KEY not in received, "The hidden-input prompt echoed the synthetic key."
        return ANSI.sub("", received)
    finally:
        process.close(force=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Use only with synthetic state on the disposable CI runner.")
    root = Path.cwd()
    cancelled = root / "cancelled setup 状态"
    interact(
        [str(args.cli), "--state-dir", str(cancelled), "setup", "--no-browser"],
        [("Download and install this client?", "n")],
        1,
    )
    assert not cancelled.exists()
    # Download only the pinned public official bundle; no runtime is connected.
    installed = subprocess.run(
        [
            str(args.python),
            "-c",
            "from pathlib import Path; from keyhole.client_install import install,asset; from keyhole.state import StateStore; import sys; print(install(StateStore(Path(sys.argv[1])),asset()))",
            str(root / "client installation"),
        ],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    client = installed.stdout.strip()
    state = root / "wizard setup 状态"
    command = [
        str(args.cli),
        "--state-dir",
        str(state),
        "--tunnel-client",
        client,
        "setup",
        "--no-browser",
    ]
    first = interact(
        command,
        [
            ("Tunnel id (tunnel_...):", FAKE_TUNNEL),
            ("Runtime API key (hidden):", FAKE_KEY),
            ("Create a new sample folder", "n"),
        ],
        0,
    )
    assert "Local configuration saved" in first
    key = (state / "runtime.key").read_bytes()
    assert key == (FAKE_KEY + "\n").encode("ascii")
    second = interact(command, [("Create a new sample folder", "n")], 0)
    assert "Runtime API key (hidden):" not in second
    assert "Tunnel id (tunnel_...):" not in second
    assert (state / "runtime.key").read_bytes() == key
    assert not (state / "grants.json").exists()
    print(
        json.dumps(
            {
                "real_windows_console": True,
                "cancel_preserved_empty_state": True,
                "hidden_input_not_echoed": True,
                "repeat_setup_preserved_key": True,
                "no_grants_or_runtime_started": True,
            }
        )
    )


if __name__ == "__main__":
    main()
