"""NativeRuntime against a fake tunnel-client; no network, no real tunnel."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_setup import KEY, TUNNEL

from keyhole import configure
from keyhole.errors import KeyholeError
from keyhole.management import Manager
from keyhole.runtime import ALIAS, NativeRuntime, parse_version
from keyhole.state import StateStore

FAKE = str(Path(__file__).resolve().with_name("fake_tunnel_client.py"))


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="keyhole-runtime-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        env = patch.dict(os.environ, {"TMPDIR": str(self.tmp)})
        env.start()
        self.addCleanup(env.stop)
        # Never touch real server processes from a test run.
        sweep = patch("keyhole.runtime.owned_processes", return_value={})
        sweep.start()
        self.addCleanup(sweep.stop)
        self.state = self.tmp / "state"
        configure.save_runtime(self.state, TUNNEL, KEY, "0.0.14")
        self.store = StateStore(self.state)
        self.runtime = NativeRuntime(self.store, FAKE)

    def refused(self, code, callback):
        with self.assertRaises(KeyholeError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def invocations(self):
        log = self.tmp / "fake-tunnel-log.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_parse_version(self):
        self.assertEqual(
            parse_version("0.0.14+0f870e50a973fa820d4c409000059e181e8d242b (git sha: 0f870e)"),
            "0.0.14",
        )
        self.assertEqual(parse_version("v0.0.15\n"), "0.0.15")
        self.assertIsNone(parse_version("tunnel-client version unknown"))
        self.assertIsNone(parse_version(""))

    def test_fresh_registry_status_stop_and_first_connect(self):
        self.assertEqual(self.runtime.status()["runtime_state"], "not_created")
        stopped = self.runtime.stop()
        self.assertFalse(stopped["process_running"])
        self.assertNotIn(["runtimes", "stop", ALIAS, "--json"], self.invocations())
        info = self.runtime.preflight()
        self.assertEqual(info["client_version"], "0.0.14")
        self.assertTrue(info["client_version_tested"])
        result = self.runtime.connect("generation-1")
        self.assertTrue(result["ready"] and result["process_running"] and result["healthy"])
        connect = next(call for call in self.invocations() if call[:2] == ["runtimes", "connect"])
        options = dict(zip(connect[2:-1:2], connect[3:-1:2], strict=True))
        self.assertEqual(options["--tunnel-id"], TUNNEL)
        self.assertEqual(options["--runtime-api-key"], "file:" + str(self.state / "runtime.key"))
        self.assertIn("-m keyhole.server", options["--mcp-command"])
        self.assertIn("--generation generation-1", options["--mcp-command"])
        self.assertEqual(self.runtime.status()["runtime_state"], "running")
        self.assertFalse(self.runtime.stop()["process_running"])
        self.assertIn(["runtimes", "stop", ALIAS, "--json"], self.invocations())
        self.assertTrue(self.runtime.checks()["ok"])

    def test_version_policy_blocks_connect_but_never_stop(self):
        (self.tmp / "fake-tunnel-version").write_text("0.0.15+abc\n")
        self.refused("client_version_changed", self.runtime.preflight)
        with self.assertRaises(KeyholeError) as caught:
            self.runtime.preflight()
        self.assertIn(
            self.store.command("setup", "--accept-client-version", client=FAKE),
            caught.exception.message,
        )
        self.refused("client_version_changed", lambda: self.runtime.connect("generation-2"))
        checks = self.runtime.checks()
        self.assertFalse(checks["ok"])
        self.assertEqual(
            (checks["client_version"], checks["accepted_client_version"]), ("0.0.15", "0.0.14")
        )
        self.assertFalse(checks["client_version_tested"])
        self.runtime.stop()
        configure.accept_client_version(self.store, "0.0.15")
        self.assertEqual(self.runtime.preflight()["client_version"], "0.0.15")
        self.assertTrue(self.runtime.checks()["ok"])

    def test_connect_failure_is_actionable_and_missing_client_is_named(self):
        (self.tmp / "fake-tunnel-fail").touch()
        with self.assertRaises(KeyholeError) as caught:
            self.runtime.connect("generation-3")
        self.assertEqual(caught.exception.code, "native_runtime_failed")
        self.assertIn(f"runtimes status {ALIAS}", caught.exception.message)
        missing = NativeRuntime(self.store, str(self.tmp / "no-such-client"))
        self.refused("native_client_missing", missing.client_version)
        self.assertEqual(missing.checks()["client_error"]["code"], "native_client_missing")
        broken = NativeRuntime(self.store, "/bin/ls")
        self.refused("native_client_failed", broken.client_version)

    def test_manager_end_to_end_with_fake_client(self):
        shared = self.tmp / "shared"
        shared.mkdir()
        manager = Manager(self.store, self.runtime)
        opened = manager.open(shared, access="ro")
        self.assertTrue(opened["runtime"]["ready"])
        self.assertEqual(manager.status()["effective_open_workspaces"], ["shared"])
        closed = manager.select([], True, enable=False)
        self.assertTrue(closed["shutdown_confirmed"])
        self.assertEqual(self.runtime.status()["runtime_state"], "stopped")
        (self.tmp / "fake-tunnel-version").write_text("0.0.15+abc\n")
        self.refused(
            "client_version_changed", lambda: manager.select(["shared"], False, enable=True)
        )
        self.assertFalse(any(w["enabled"] for w in self.store.read()["workspaces"]))
        self.assertTrue(manager.select([], True, enable=False)["shutdown_confirmed"])


if __name__ == "__main__":
    unittest.main()
