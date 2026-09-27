"""A manually authorized, timed synthetic session on a disposable runner.

The operator makes real ChatGPT calls during the advertised windows. No remote tool changes grants.
The report checks local edit/recovery receipts; screenshots of the actual ChatGPT reads are separate evidence.
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from keyhole import configure
from keyhole.client_install import asset, install
from keyhole.setup import DEMO_TEXT
from keyhole.state import StateStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or platform.system() not in ("Linux", "Windows")
    ):
        raise SystemExit(
            "Use the protected manual workflow on a disposable Linux or Windows runner only."
        )
    windows = platform.system() == "Windows"
    if windows:
        import ctypes

        if ctypes.windll.shell32.IsUserAnAdmin():
            raise SystemExit("Windows acceptance must run as the disposable standard user.")
    key = os.environ.pop("KEYHOLE_ACCEPTANCE_KEY", "")
    tunnel = os.environ.pop("KEYHOLE_ACCEPTANCE_TUNNEL", "")
    configure.validate_key(key)
    configure.validate_tunnel_id(tunnel)
    command = shutil.which("keyhole")
    if not command:
        raise SystemExit("The reviewed wheel must be installed before acceptance starts.")
    root = Path(
        tempfile.mkdtemp(prefix="keyhole-acceptance-", dir=Path.cwd() if windows else None)
    ).resolve()
    store = StateStore(root / "private")
    workspace = "WindowsDemo" if windows else "LinuxDemo"
    report = {
        "os": platform.platform(),
        "python": platform.python_version(),
        "full_chatgpt_acceptance": False,
        "fixture_only": True,
        "candidate_commit": os.environ["GITHUB_SHA"],
        "wheel_sha256": hashlib.sha256(args.wheel.read_bytes()).hexdigest(),
        "python_machine": platform.machine(),
        "standard_windows_user": True if windows else None,
    }

    def cli(*words):
        result = subprocess.run(
            [command, "--state-dir", str(store.path), *words],
            capture_output=True,
            encoding="utf-8",
            timeout=120,
        )
        try:
            value = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError("CLI output was not JSON; private details withheld.") from None
        if result.returncode or not value.get("ok"):
            raise RuntimeError("The acceptance CLI command failed; private details withheld.")
        return value

    def window(label, seconds):
        print(
            json.dumps(
                {"phase": label, "seconds": seconds, "started_at": datetime.now(UTC).isoformat()}
            ),
            flush=True,
        )
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            time.sleep(min(10, max(0, end - time.monotonic())))

    configured = False
    try:
        release = asset()
        client = install(store, release)
        report["tunnel_client"] = release["version"]
        configure.complete_setup(
            store,
            expected={},
            tunnel_id=tunnel,
            key=key,
            client=client,
            version=release["version"],
            source="managed",
        )
        configured = True
        key = ""
        demo = root / workspace
        demo.mkdir(mode=0o700)
        note = demo / "notes.md"
        note.write_bytes(DEMO_TEXT.encode("utf-8"))
        expected = hashlib.sha256(note.read_bytes()).hexdigest()
        opened = cli("open", str(demo), "--name", workspace, "--access", "ro")
        report["ready"] = opened["runtime"].get("ready") is True
        window(
            f"read-only: select the private {workspace} app, read notes.md, attempt a refused write",
            240,
        )
        cli("access", workspace, "rw")
        window(
            "read-write: read current hash, edit the checklist, restore the returned change id", 240
        )
        history = cli("history", "--limit", "100")
        # Keep only synthetic receipt counts; the raw journal, ids and paths are never uploaded.
        changes = history.get("changes", [])
        report["retained_change_count"] = len(changes)
        edited_hashes = {
            change["after"]["sha256"]
            for item in changes
            if item["workspace"] == workspace
            and item["operation"] in ("write", "patch")
            and item["status"] == "restored"
            for change in item["changes"]
            if change["path"] == "notes.md"
            and change["before"].get("sha256") == expected
            and change["after"].get("sha256") not in (None, expected)
        }
        report["edit_receipt_verified"] = bool(edited_hashes)
        report["restore_receipt_verified"] = any(
            change["before"].get("sha256") in edited_hashes
            and change["after"].get("sha256") == expected
            for item in changes
            if item["workspace"] == workspace
            and item["operation"] == "restore"
            and item["status"] == "committed"
            for change in item["changes"]
            if change["path"] == "notes.md"
        )
        report["original_bytes_restored"] = (
            hashlib.sha256(note.read_bytes()).hexdigest() == expected
        )
        report["original_sha256"] = expected
        closed = cli("close", workspace)
        report["shutdown_confirmed"] = closed.get("shutdown_confirmed") is True
        window(
            "closed: require a NEW ChatGPT tool call to fail; earlier text is still in the chat",
            240,
        )
        report["manual_evidence_required"] = [
            "ChatGPT read",
            "read-only write refusal",
            "new read after close refused",
        ]
        if (
            not report["ready"]
            or not report["edit_receipt_verified"]
            or not report["restore_receipt_verified"]
            or not report["original_bytes_restored"]
            or not report["shutdown_confirmed"]
        ):
            raise RuntimeError("No completed edit/restore/close round trip was observed.")
    except BaseException:
        report["session_failed"] = True
        raise
    finally:
        if configured:
            try:
                report["cleanup_shutdown_confirmed"] = (
                    cli("close", "--all").get("shutdown_confirmed") is True
                )
            except Exception:
                report["cleanup_shutdown_confirmed"] = False
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        # Credentials are discarded only after confirmed shutdown; the runner is disposable either way.
        if not configured or report.get("cleanup_shutdown_confirmed"):
            shutil.rmtree(root)


if __name__ == "__main__":
    main()
