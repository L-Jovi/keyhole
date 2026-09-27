"""Onboarding uses private temporary state, fake keys and a fake official runtime."""

import contextlib
import io
import json
import os
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_runtime import FAKE
from test_setup import KEY, TUNNEL

from keyhole import cli, configure, setup
from keyhole.diagnostics import redact_status
from keyhole.errors import KeyholeError
from keyhole.management import Manager
from keyhole.runtime import NativeRuntime
from keyhole.state import StateStore


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="keyhole-onboarding-")).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        self.state = self.root / "private state"
        self.store = StateStore(self.state)
        for mocker in (
            patch.dict(os.environ, {"TMPDIR": str(self.root)}),
            patch("keyhole.runtime.owned_processes", return_value={}),
        ):
            mocker.start()
            self.addCleanup(mocker.stop)
        self.args = SimpleNamespace(
            state_dir=self.state,
            tunnel_client=FAKE,
            no_browser=True,
            rotate_key=False,
            accept_client_version=False,
            update_client=False,
        )

    def run_setup(self, inputs, key=KEY):
        with (
            patch("builtins.input", side_effect=inputs),
            patch("getpass.getpass", return_value=key),
            patch("webbrowser.open") as browser,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            result = setup.run(self.args)
        browser.assert_not_called()
        self.assertNotIn(key, stderr.getvalue())
        return result

    def test_first_setup_persists_client_then_reentry_does_not_prompt_for_secrets_or_change_grants(
        self,
    ):
        result = self.run_setup([TUNNEL, "n"])
        self.assertTrue(result["configured"])
        self.assertFalse(result["chatgpt_read_verified"])
        self.assertEqual(self.store.read("runtime.json")["tunnel_client_path"], FAKE)
        self.assertEqual(NativeRuntime(self.store).client, FAKE)
        before = (self.state / "runtime.key").read_bytes()
        config_before = (self.state / "runtime.json").stat().st_mtime_ns
        history = self.state / "changes.sqlite3"
        history.write_bytes(b"synthetic retained history")
        with (
            patch("builtins.input", return_value="n"),
            patch("getpass.getpass") as secret,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            setup.run(self.args)
        secret.assert_not_called()
        self.assertEqual((self.state / "runtime.key").read_bytes(), before)
        self.assertEqual((self.state / "runtime.json").stat().st_mtime_ns, config_before)
        self.assertEqual(history.read_bytes(), b"synthetic retained history")
        self.assertFalse((self.state / "grants.json").exists())

    def test_partial_key_only_and_config_only_can_resume(self):
        self.state.mkdir(mode=0o700)
        key_path = self.state / "runtime.key"
        key_path.write_text(KEY + "\n")
        key_path.chmod(0o600)
        with (
            patch("builtins.input", side_effect=[TUNNEL, "n"]),
            patch("getpass.getpass") as secret,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            setup.run(self.args)
        secret.assert_not_called()
        key_path.unlink()
        self.run_setup(["n"], key=KEY + "-new")
        self.assertEqual(key_path.read_text(), KEY + "-new\n")

    def test_cancellation_and_invalid_key_leave_existing_configuration_untouched(self):
        with self.assertRaises(KeyholeError):
            self.run_setup([TUNNEL], key="invalid")
        self.assertFalse(self.state.exists())
        self.run_setup([TUNNEL, "n"])
        before = (self.state / "runtime.json").read_bytes()
        (self.root / "fake-tunnel-version").write_text("0.0.15\n")
        with self.assertRaises(KeyholeError):
            self.run_setup([""])
        self.assertEqual((self.state / "runtime.json").read_bytes(), before)

    def test_saved_missing_path_never_falls_back_to_another_path_client(self):
        self.run_setup([TUNNEL, "n"])
        config = self.store.read("runtime.json")
        config["tunnel_client_path"] = str(self.root / "missing")
        self.store.write_json("runtime.json", config)
        with patch("shutil.which", return_value=FAKE) as lookup:
            runtime = NativeRuntime(self.store)
            self.assertEqual(runtime.client, config["tunnel_client_path"])
            with self.assertRaises(KeyholeError) as caught:
                runtime.client_version()
            self.assertEqual(caught.exception.code, "native_client_missing")
        lookup.assert_not_called()
        self.assertEqual(NativeRuntime(self.store, FAKE).client_version(), "0.0.14")

    def test_legacy_path_resolution_and_external_update_refusal(self):
        configure.save_runtime(self.state, TUNNEL, KEY, "0.0.14")
        with patch("shutil.which", return_value=FAKE):
            self.assertEqual(NativeRuntime(self.store).client, FAKE)
        self.args.update_client = True
        with self.assertRaises(KeyholeError) as caught:
            self.run_setup([])
        self.assertEqual(caught.exception.code, "client_not_managed")

    def test_missing_client_download_requires_consent_and_saves_managed_identity(self):
        self.args.tunnel_client = None
        with patch("shutil.which", return_value=None), patch("keyhole.setup.install") as download:
            with self.assertRaises(KeyholeError):
                self.run_setup(["n"])
            download.assert_not_called()
            download.return_value = FAKE
            result = self.run_setup(["y", TUNNEL, "n"])
        self.assertEqual(result["client_source"], "managed")
        self.assertEqual(self.store.read("runtime.json")["tunnel_client_path"], FAKE)

    def test_demo_uses_normal_ro_grant_checks_with_unicode_path_and_never_overwrites(self):
        self.run_setup([TUNNEL, "n"])
        demo = self.root / "Sample 笔记"
        result = setup.create_demo(self.store, NativeRuntime(self.store), demo)
        self.assertTrue(result["runtime"]["ready"])
        grant = self.store.read()["workspaces"][0]
        self.assertEqual((grant["name"], grant["access"]), ("demo", "ro"))
        original = (demo / "notes.md").read_bytes()
        with self.assertRaises(KeyholeError) as caught:
            setup.create_demo(self.store, NativeRuntime(self.store), demo)
        self.assertEqual(caught.exception.code, "demo_exists")
        self.assertEqual((demo / "notes.md").read_bytes(), original)

    def test_existing_demo_directory_is_never_reused_implicitly(self):
        self.run_setup([TUNNEL, "n"])
        demo = self.root / "my notes"
        demo.mkdir()
        (demo / "notes.md").write_text("Private existing material")
        with self.assertRaises(KeyholeError) as caught:
            setup.create_demo(self.store, NativeRuntime(self.store), demo)
        self.assertEqual(caught.exception.code, "demo_exists")
        self.assertEqual((demo / "notes.md").read_text(), "Private existing material")
        self.assertFalse((self.state / "grants.json").exists())

    def test_failed_demo_connection_keeps_files_and_prints_an_explicit_retry(self):
        self.run_setup([TUNNEL, "n"])
        runtime = NativeRuntime(self.store)
        demo = self.root / "Retry 笔记"
        with (
            patch.object(
                runtime,
                "connect",
                side_effect=KeyholeError("runtime_not_ready", "Connection failed."),
            ),
            self.assertRaises(KeyholeError) as caught,
        ):
            setup.create_demo(self.store, runtime, demo)
        self.assertEqual((demo / "notes.md").read_text(), setup.DEMO_TEXT)
        self.assertFalse(self.store.read()["workspaces"][0]["enabled"])
        self.assertIn(
            self.store.command("open", str(demo), "--name", "demo", "--access", "ro"),
            caught.exception.message,
        )

    def test_demo_does_not_resume_closed_workspaces_and_managed_clients_cannot_be_shared(self):
        self.run_setup([TUNNEL, "n"])
        manager = Manager(self.store, NativeRuntime(self.store))
        old = self.root / "saved folder"
        old.mkdir()
        manager.open(old, name="saved", access="rw")
        manager.select(["saved"], False, enable=False)
        setup.create_demo(self.store, NativeRuntime(self.store), self.root / "new demo")
        grants = {g["name"]: g for g in self.store.read()["workspaces"]}
        self.assertFalse(grants["saved"]["enabled"])
        self.assertEqual(grants["saved"]["access"], "rw")
        self.assertTrue(grants["demo"]["enabled"])
        clients = self.state / "clients" / "0.0.14" / "darwin-arm64"
        clients.mkdir(parents=True)
        with self.assertRaises(KeyholeError) as caught:
            manager.open(clients, name="clients")
        self.assertEqual(caught.exception.code, "protected_root")

    def test_cancelled_client_install_is_not_reported_as_complete_configuration(self):
        self.state.mkdir(mode=0o700)
        status = Manager(self.store, NativeRuntime(self.store, FAKE)).status()
        self.assertFalse(status["configured"])
        self.assertEqual(status["checks"]["config_error"]["code"], "not_configured")

    def test_configured_but_missing_client_keeps_diagnostics_available(self):
        self.run_setup([TUNNEL, "n"])
        data = self.store.read("runtime.json")
        data["tunnel_client_path"] = str(self.root / "missing")
        self.store.write_json("runtime.json", data)
        status = Manager(self.store, NativeRuntime(self.store)).status()
        self.assertEqual(status["checks"]["client_error"]["code"], "native_client_missing")
        self.assertEqual(status["runtime"]["error"]["code"], "native_client_failed")

    def test_malformed_config_gets_named_error_without_reset_or_secret_echo(self):
        self.run_setup([TUNNEL, "n"])
        config = self.state / "runtime.json"
        config.write_text("not JSON PRIVATE-MARKER")
        with self.assertRaises(KeyholeError) as caught:
            self.run_setup([])
        self.assertEqual(caught.exception.code, "runtime_config")
        status = NativeRuntime(self.store).checks()
        self.assertEqual(status["config_error"]["code"], "runtime_config")
        self.assertNotIn("PRIVATE-MARKER", json.dumps(status))
        self.assertEqual(config.read_text(), "not JSON PRIVATE-MARKER")

    def test_concurrent_config_change_does_not_overwrite_it(self):
        self.run_setup([TUNNEL, "n"])
        current = self.store.read("runtime.json")
        with self.assertRaises(KeyholeError) as caught:
            configure.complete_setup(
                self.store,
                expected={},
                tunnel_id=TUNNEL,
                key=KEY,
                client=FAKE,
                version="0.0.14",
                source="external",
            )
        self.assertEqual(caught.exception.code, "setup_changed")
        self.assertEqual(self.store.read("runtime.json"), current)

    def test_concurrent_secret_creation_is_not_silently_accepted(self):
        self.state.mkdir(mode=0o700)
        key = self.state / "runtime.key"
        key.write_text(KEY)
        key.chmod(0o600)
        with self.assertRaises(KeyholeError) as caught:
            configure.complete_setup(
                self.store,
                expected={},
                tunnel_id=TUNNEL,
                key=KEY + "-new",
                client=FAKE,
                version="0.0.14",
                source="external",
            )
        self.assertEqual(caught.exception.code, "setup_changed")
        self.assertEqual(key.read_text(), KEY)
        self.assertFalse((self.state / "runtime.json").exists())

    def test_rotate_key_does_not_require_a_working_client(self):
        self.run_setup([TUNNEL, "n"])
        self.args.rotate_key = True
        self.args.tunnel_client = str(self.root / "missing")
        self.run_setup([], key=KEY + "-rotated")
        self.assertEqual((self.state / "runtime.key").read_text().strip(), KEY + "-rotated")

    def test_redaction_is_a_whitelist_even_for_future_fields_and_error_text(self):
        private = "PRIVATE-MARKER"
        result = redact_status(
            {
                "configured": True,
                "state_dir": private,
                "next_step": private,
                "configuration": {"workspaces": [{"name": private, "path": private}]},
                "effective_open_workspaces": [private],
                "checks": {
                    "client_version": "0.0.14",
                    "accepted_client_version": private,
                    "tunnel_id": private,
                    "future": private,
                    "config_error": {"code": private, "message": private},
                },
                "runtime": {
                    "ready": private,
                    "control_plane_poll_health": private,
                    "error": {"code": "native_runtime_failed", "message": KEY},
                },
            }
        )
        self.assertNotIn(private, json.dumps(result))
        self.assertNotIn(KEY, json.dumps(result))
        self.assertEqual(result["open_workspace_count"], 1)
        self.assertEqual(result["checks"]["client_version"], "0.0.14")

    def test_cli_status_default_json_human_and_redacted_missing_client(self):
        for flags in ([], ["--human"], ["--redact"]):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                cli.main(
                    [
                        "--state-dir",
                        str(self.state),
                        "--tunnel-client",
                        str(self.root / "missing"),
                        "status",
                        *flags,
                    ]
                )
            text = output.getvalue()
            if flags == ["--human"]:
                self.assertIn("Next:", text)
            else:
                self.assertFalse(json.loads(text)["configured"])
            if flags == ["--redact"]:
                self.assertNotIn(str(self.root), text)

    def test_real_terminal_setup_hides_secret_and_emits_final_json(self):
        master, slave = pty.openpty()
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "keyhole.cli",
                "--state-dir",
                str(self.state),
                "--tunnel-client",
                FAKE,
                "setup",
                "--no-browser",
            ],
            stdin=slave,
            stdout=slave,
            stderr=slave,
        )
        os.close(slave)
        transcript = b""
        replies = [
            (b"Tunnel id (tunnel_...):", TUNNEL),
            (b"Runtime API key (hidden):", KEY),
            (b"[y/N]", "n"),
        ]
        deadline = time.monotonic() + 30
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        break
                    if not data:
                        break
                    transcript += data
                    if replies and replies[0][0] in transcript:
                        _, reply = replies.pop(0)
                        os.write(master, (reply + "\n").encode())
                elif process.poll() is not None:
                    break
            self.assertEqual(process.wait(timeout=2), 0, transcript.decode(errors="replace"))
            self.assertEqual(replies, [])
            self.assertNotIn(KEY.encode(), transcript)
            self.assertIn(b'"configured": true', transcript)
            self.assertIn(b'"chatgpt_read_verified": false', transcript)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)


if __name__ == "__main__":
    unittest.main()
