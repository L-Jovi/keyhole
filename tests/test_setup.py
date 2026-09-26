"""Setup, key handling and first-run diagnostics use synthetic values only."""

import json
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from keyhole import configure
from keyhole.errors import KeyholeError
from keyhole.management import Manager
from keyhole.runtime import NativeRuntime
from keyhole.state import StateStore

KEY = "sk-test-not-a-real-key-0123456789"  # gitleaks:allow
TUNNEL = "tunnel_0123456789abcdef0123456789abcdef"  # not a real tunnel


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="keyhole-setup-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.state = self.root / "state"

    def refused(self, code, callback):
        with self.assertRaises(KeyholeError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_private_files_and_reference_only_config(self):
        result = configure.save_runtime(self.state, TUNNEL, KEY, "0.0.14")
        self.assertEqual(result["tunnel_client_version"], "0.0.14")
        for path, mode in (
            (self.state, 0o700),
            (self.state / "runtime.key", 0o600),
            (self.state / "runtime.json", 0o600),
        ):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
        raw = (self.state / "runtime.json").read_text()
        self.assertNotIn(KEY, raw)
        config = json.loads(raw)
        self.assertEqual(config["runtime_api_key_ref"], "file:" + str(self.state / "runtime.key"))
        self.assertEqual(config["tunnel_client_version"], "0.0.14")

    def test_existing_configuration_is_not_replaced(self):
        configure.save_runtime(self.state, TUNNEL, KEY, "0.0.14")
        self.refused(
            "already_configured",
            lambda: configure.save_runtime(self.state, TUNNEL, KEY + "-new", "0.0.14"),
        )
        self.assertEqual((self.state / "runtime.key").read_text().strip(), KEY)

    def test_symlink_directory_is_rejected_with_a_named_cause(self):
        target = self.root / "target"
        target.mkdir(mode=0o700)
        self.state.symlink_to(target)
        self.refused(
            "symlink_in_path", lambda: configure.save_runtime(self.state, TUNNEL, KEY, "0.0.14")
        )
        self.assertEqual(list(target.iterdir()), [])

    def test_symlink_secret_is_not_overwritten(self):
        self.state.mkdir(mode=0o700)
        target = self.root / "untouched"
        target.write_text("leave unchanged")
        (self.state / "runtime.key").symlink_to(target)
        self.refused(
            "already_configured", lambda: configure.save_runtime(self.state, TUNNEL, KEY, "0.0.14")
        )
        self.assertEqual(target.read_text(), "leave unchanged")

    def test_admin_key_and_bad_tunnel_id_are_rejected_before_any_write(self):
        self.refused(
            "invalid_key",
            lambda: configure.save_runtime(
                self.state, TUNNEL, "sk-admin-not-a-real-key-0123456789", "0.0.14"
            ),
        )
        self.refused(
            "invalid_tunnel_id",
            lambda: configure.save_runtime(self.state, "not-a-tunnel", KEY, "0.0.14"),
        )
        self.assertFalse(self.state.exists())

    def test_accept_version_changes_only_the_version_and_rotate_replaces_only_the_key(self):
        configure.save_runtime(self.state, TUNNEL, KEY, "0.0.14")
        store = StateStore(self.state)
        key_inode = (self.state / "runtime.key").stat().st_ino
        result = configure.accept_client_version(store, "0.0.15")
        self.assertEqual(result, {"previous_version": "0.0.14", "tunnel_client_version": "0.0.15"})
        config = store.read("runtime.json")
        self.assertEqual(config["tunnel_client_version"], "0.0.15")
        self.assertEqual(config["tunnel_id"], TUNNEL)
        self.assertEqual((self.state / "runtime.key").stat().st_ino, key_inode)
        self.assertEqual(stat.S_IMODE((self.state / "runtime.json").stat().st_mode), 0o600)
        configure.rotate_key(store, KEY + "-rotated")
        self.assertEqual((self.state / "runtime.key").read_text().strip(), KEY + "-rotated")
        self.assertEqual(stat.S_IMODE((self.state / "runtime.key").stat().st_mode), 0o600)
        self.assertEqual(store.read("runtime.json"), config)
        self.refused("invalid_key", lambda: configure.rotate_key(store, "nope"))

    def test_unconfigured_state_is_reported_not_crashed(self):
        store = StateStore(self.state)
        self.refused("not_configured", lambda: store.read("runtime.json"))
        self.refused("not_configured", store.read)
        status = Manager(store, NativeRuntime(store, str(self.root / "missing-client"))).status()
        self.assertFalse(status["configured"])
        self.assertEqual(status["next_step"], "keyhole setup")
        self.assertEqual(status["checks"]["client_error"]["code"], "native_client_missing")
        self.assertEqual(status["checks"]["config_error"]["code"], "not_configured")
        self.state.mkdir(mode=0o700)
        self.refused("not_configured", lambda: store.read("runtime.json"))
        self.refused("offline", store.read)

    def test_tunnel_id_must_match_tunnel_client_format(self):
        for bad in ("not-a-tunnel", "tunnel_TOO_SHORT", "tunnel_" + "x" * 31, "tunnel_" + "X" * 32):
            self.refused(
                "invalid_tunnel_id",
                lambda bad=bad: configure.save_runtime(self.state, bad, KEY, "0.0.14"),
            )
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
