"""Install a candidate as a standard CI user before passing the acceptance secret."""

import argparse
import ctypes
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--install-only", action="store_true")
    args = parser.parse_args()
    key = os.environ.pop("KEYHOLE_ACCEPTANCE_KEY")
    tunnel = os.environ.pop("KEYHOLE_ACCEPTANCE_TUNNEL")
    tools = Path.cwd() / "tools"
    bins = Path.cwd() / "bin"
    os.environ.update(UV_TOOL_DIR=str(tools), UV_TOOL_BIN_DIR=str(bins))
    subprocess.run(
        [
            str(args.uv),
            "tool",
            "install",
            "--no-build",
            "--python",
            sys.executable,
            str(args.wheel),
        ],
        check=True,
    )
    env = {
        **os.environ,
        "PATH": str(bins) + os.pathsep + os.environ["PATH"],
        "KEYHOLE_ACCEPTANCE_KEY": key,
        "KEYHOLE_ACCEPTANCE_TUNNEL": tunnel,
    }
    python = tools / "keyhole-mcp" / "Scripts/python.exe"
    if args.install_only:
        if ctypes.windll.shell32.IsUserAnAdmin():
            raise SystemExit("The installer check must run as a standard user.")
        command = bins / "keyhole.exe"
        subprocess.run([str(command), "--version"], check=True)
        missing = Path.cwd() / "unconfigured"
        result = subprocess.run(
            [str(command), "--state-dir", str(missing), "status", "--redact"],
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
        status = json.loads(result.stdout)
        assert not status["configured"] and status["open_workspace_count"] == 0
        assert not missing.exists()
        subprocess.run(
            [
                sys.executable,
                str(Path.cwd() / "console-test.py"),
                "--cli",
                str(command),
                "--python",
                str(python),
            ],
            check=True,
        )
        args.output.write_text(
            json.dumps(
                {
                    "install_only": True,
                    "full_chatgpt_acceptance": False,
                    "standard_windows_user": True,
                    "wheel_sha256": hashlib.sha256(args.wheel.read_bytes()).hexdigest(),
                    "unconfigured_status": status,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print("Candidate wheel installed and diagnosed as a standard user; no tunnel was started.")
        return
    result = subprocess.run(
        [str(python), str(args.session), "--wheel", str(args.wheel), "--output", str(args.output)],
        env=env,
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
