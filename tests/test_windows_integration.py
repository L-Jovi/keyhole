"""Native Windows integration; synthetic directories and fake transport only."""

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

if sys.platform == "win32":
    from keyhole import file_ops as fileio
    from keyhole.bridge import Bridge
    from keyhole.configure import open_private_directory, save_runtime
    from keyhole.errors import KeyholeError
    from keyhole.filesystem import SafeFS
    from keyhole.management import Manager
    from keyhole.readers import parse_document
    from keyhole.runtime import NativeRuntime
    from keyhole.state import StateStore, boot_id


class OfflineRuntime:
    def __init__(self):
        self.running = False

    def preflight(self):
        return {}

    def checks(self):
        return {"ok": True}

    def status(self):
        return {"ready": self.running, "healthy": self.running, "process_running": self.running}

    def stop(self):
        self.running = False
        return self.status()

    def connect(self, generation):
        self.running = True
        return self.status()


@unittest.skipUnless(sys.platform == "win32", "requires native Windows")
class WindowsIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="keyhole-integration-", dir=Path.cwd()))
        self.addCleanup(shutil.rmtree, self.root)
        self.state = self.root / "private 状态"
        os.close(open_private_directory(self.state))
        self.shared = self.root / "Shared 笔记"
        self.shared.mkdir()
        self.note = self.shared / "Resume 笔记.md"
        self.note.write_bytes(b"# Synthetic notes\r\n- prepare demo\r\n")
        self.store = StateStore(self.state)
        self.manager = Manager(self.store, OfflineRuntime())
        self.manager.open(self.shared, name="Demo")
        self.sequence = 0

    def bridge(self):
        return Bridge(self.store, self.store.read()["generation"])

    def mutate(self, operation, **args):
        self.sequence += 1
        return self.bridge().mutate(operation, "Demo", f"native-{self.sequence}", **args)

    def test_native_boot_directory_enumeration_and_case_alias(self):
        self.assertEqual(boot_id(), boot_id())
        grant = self.store.read()["workspaces"][0]
        fs = SafeFS(grant)
        entries, omitted = fs.entries(".")
        self.assertEqual([x["name"] for x in entries], [self.note.name])
        self.assertEqual(omitted, 0)
        data, metadata = fs.read("resume 笔记.md")
        self.assertEqual(data, self.note.read_bytes())
        self.assertEqual(metadata["sha256"], hashlib.sha256(data).hexdigest())

    def test_boot_identity_in_a_minimal_mcp_environment(self):
        from mcp.client.stdio import get_default_environment

        env = get_default_environment()
        env.pop("PSMODULEPATH", None)
        result = subprocess.run(
            [sys.executable, "-c", "from keyhole.state import boot_id; print(boot_id())"],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), boot_id())

    def test_official_client_json_uses_utf8_instead_of_the_windows_ansi_codepage(self):
        payload = {"path": "C:/Synthetic/示例 笔记", "ready": False}
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        actual_run = subprocess.run

        def synthetic_client(_command, **kwargs):
            return actual_run(
                [
                    sys.executable,
                    "-c",
                    f"import sys; sys.stdout.buffer.write(bytes.fromhex('{encoded.hex()}'))",
                ],
                **kwargs,
            )

        runtime = NativeRuntime(self.store, client=sys.executable)
        with patch("keyhole.runtime.subprocess.run", side_effect=synthetic_client):
            self.assertEqual(runtime.invoke("list"), payload)

    def test_config_key_and_history_are_private(self):
        save_runtime(self.state, "tunnel_" + "a" * 32, "sk-fake-test-never-a-real-key", "0.0.14")
        NativeRuntime(self.store).check_key()
        with self.store.directory() as parent:
            for name in ("runtime.key", "runtime.json", "grants.json", "management.lock"):
                fd = fileio.open(name, fileio.FILE_FLAGS, dir_fd=parent)
                try:
                    self.assertTrue(fileio.private(fd, 0o600))
                finally:
                    os.close(fd)

    def test_edit_restore_close_and_acl_preservation(self):
        from keyhole import writes
        from keyhole.windows_security import user_sid

        compare = writes.same

        def checked_images(a, b):
            result = compare(a, b)
            if a["kind"] == b["kind"] == "file" and a["sha256"] == b["sha256"] and not result:
                self.fail(
                    json.dumps(
                        {
                            "actual": writes.public_image(a),
                            "expected": writes.public_image(b),
                            "acl_equal": a.get("windows_acl") == b.get("windows_acl"),
                            "actual_acl": a["windows_acl"].replace(user_sid(), "OWNER"),
                            "expected_acl": b["windows_acl"].replace(user_sid(), "OWNER"),
                        }
                    )
                )
            return result

        comparison = patch.object(writes, "same", side_effect=checked_images)
        comparison.start()
        self.addCleanup(comparison.stop)
        original = self.note.read_bytes()
        expected = hashlib.sha256(original).hexdigest()
        with self.assertRaises(KeyholeError) as denied:
            self.mutate("write", path=self.note.name, expected_sha256=expected, text="new")
        self.assertEqual(denied.exception.code, "read_only")
        self.manager.access("Demo", "rw")
        changed = self.mutate(
            "write", path=self.note.name, expected_sha256=expected, text="# Updated\n"
        )
        self.assertEqual(self.note.read_bytes(), b"# Updated\r\n")
        self.assertNotIn("windows_acl", str(changed))
        self.assertNotIn("S-1-5-21", str(changed))
        self.mutate("restore", change_id=changed["change_id"])
        self.assertEqual(self.note.read_bytes(), original)
        before = self.bridge()
        self.manager.select([], True, enable=False)
        with self.assertRaises(KeyholeError):
            before.read_file("Demo", self.note.name)

    def test_create_copy_move_delete_directory_and_restore(self):
        self.manager.access("Demo", "rw")
        created = self.mutate("write", path="new.md", expected_sha256="absent", text="synthetic\n")
        digest = hashlib.sha256(b"synthetic\n").hexdigest()
        self.mutate("copy", path="new.md", destination="copy.md", expected_sha256=digest)
        self.mutate("move", path="copy.md", destination="moved.md", expected_sha256=digest)
        deleted = self.mutate("delete", path="moved.md", expected_sha256=digest)
        self.assertFalse((self.shared / "moved.md").exists())
        self.mutate("restore", change_id=deleted["change_id"])
        self.assertEqual((self.shared / "moved.md").read_bytes(), b"synthetic\n")
        self.mutate("restore", change_id=created["change_id"])
        self.assertFalse((self.shared / "new.md").exists())
        directory = self.mutate("mkdir", path="New directory")
        self.assertTrue((self.shared / "New directory").is_dir())
        self.mutate("restore", change_id=directory["change_id"])
        self.assertFalse((self.shared / "New directory").exists())

    def test_management_lock_excludes_a_separate_process(self):
        command = [
            sys.executable,
            "-c",
            "from pathlib import Path; from keyhole.state import StateStore; import sys; s=StateStore(Path(sys.argv[1]));\nwith s.lock(timeout=0.1): print('acquired')",
            str(self.state),
        ]
        with self.store.lock():
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Another local authorization change", result.stderr)
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)

    def test_cross_directory_copy_and_move_are_private_and_restore_original_acl(self):
        from keyhole import windows_security as security

        self.manager.access("Demo", "rw")
        original = self.note.read_bytes()
        digest = hashlib.sha256(original).hexdigest()
        with (
            fileio.win.walk(self.shared) as parent,
            fileio.win.child(parent, self.note.name) as opened,
        ):
            original_acl = security.file_dacl_signature(security.snapshot(opened.value))
        self.mutate("mkdir", path="Review")
        copied = self.mutate(
            "copy", path=self.note.name, destination="Review/copy.md", expected_sha256=digest
        )
        moved = self.mutate(
            "move", path=self.note.name, destination="Review/moved.md", expected_sha256=digest
        )
        with fileio.win.walk(self.shared / "Review") as parent:
            for name in ("copy.md", "moved.md"):
                with fileio.win.child(parent, name) as opened:
                    self.assertTrue(security.is_private(security.snapshot(opened.value)))
        self.assertFalse(self.note.exists())
        self.mutate("restore", change_id=moved["change_id"])
        self.assertEqual(self.note.read_bytes(), original)
        with (
            fileio.win.walk(self.shared) as parent,
            fileio.win.child(parent, self.note.name) as opened,
        ):
            self.assertEqual(
                security.file_dacl_signature(security.snapshot(opened.value)), original_acl
            )
        self.mutate("restore", change_id=copied["change_id"])
        self.assertFalse((self.shared / "Review/copy.md").exists())

    def test_parser_uses_an_inherited_snapshot_handle(self):
        from PIL import Image

        image = io.BytesIO()
        Image.new("RGB", (12, 8), "white").save(image, format="PNG")
        result = parse_document(
            image.getvalue(), "image", {}, "synthetic-generation", state_dir=str(self.state)
        )
        self.assertEqual(result["format"], "image")

    def test_native_process_cleanup_is_limited_to_the_exact_state(self):
        from keyhole.windows_process import owned_processes, terminate_owned

        generation = self.store.read()["generation"]
        other = self.root / "other state"
        os.close(open_private_directory(other))
        second = StateStore(other)
        other_manager = Manager(second, OfflineRuntime())
        other_manager.open(self.shared, name="Other")
        commands = [
            [
                sys.executable,
                "-m",
                "keyhole.server",
                "--state-dir",
                str(self.state),
                "--generation",
                generation,
            ],
            [
                sys.executable,
                "-m",
                "keyhole.server",
                "--state-dir",
                str(other),
                "--generation",
                second.read()["generation"],
            ],
        ]
        processes = [
            subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )
            for command in commands
        ]
        try:
            deadline = time.monotonic() + 15
            observed = {}
            while time.monotonic() < deadline:
                observed = owned_processes(self.state)
                if processes[0].pid in observed:
                    break
            # A Windows virtualenv can have both its redirector and base Python
            # process for one server. Both belong to that state, not another one.
            self.assertIn(processes[0].pid, observed)
            other_processes = owned_processes(other)
            self.assertIn(processes[1].pid, other_processes)
            self.assertTrue(set(observed).isdisjoint(other_processes))
            terminate_owned(self.state)
            processes[0].wait(timeout=5)
            self.assertIsNone(processes[1].poll())
            self.assertEqual(owned_processes(self.state), {})
        finally:
            terminate_owned(self.state)
            terminate_owned(other)
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)

    def test_streams_and_readonly_acl_are_refused_before_journaling(self):
        from keyhole import windows_security as security

        self.manager.access("Demo", "rw")
        original = self.note.read_bytes()
        Path(str(self.note) + ":metadata").write_bytes(b"synthetic alternate stream")
        with self.assertRaises(KeyholeError) as denied:
            self.mutate(
                "write",
                path=self.note.name,
                expected_sha256=hashlib.sha256(original).hexdigest(),
                text="replacement",
            )
        self.assertEqual(denied.exception.code, "file_read_only")
        self.assertEqual(self.note.read_bytes(), original)
        Path(str(self.note) + ":metadata").unlink()
        with fileio.win.walk(self.shared) as parent:
            descriptor = security.descriptor_from_sddl(
                f"O:{security.user_sid()}D:P(A;;FR;;;{security.user_sid()})"
            )
            with fileio.win.child(parent, "readonly.md", write=True, create=True) as file:
                security.restore_dacl(file.value, descriptor)
        with self.assertRaises(KeyholeError) as denied:
            self.mutate(
                "write",
                path="readonly.md",
                expected_sha256=hashlib.sha256(b"").hexdigest(),
                text="replacement",
            )
        self.assertEqual(denied.exception.code, "file_read_only")
        # The owner can explicitly restore its fixture ACL for local cleanup.
        subprocess.run(
            ["icacls", str(self.shared / "readonly.md"), "/reset"], check=True, capture_output=True
        )

    def test_repeated_and_partial_setup_preserve_private_key_grants_and_history(self):
        from keyhole import setup
        from keyhole.diagnostics import redact_status

        args = SimpleNamespace(
            state_dir=self.state,
            tunnel_client=None,
            no_browser=True,
            rotate_key=False,
            accept_client_version=False,
            update_client=False,
        )
        key = "sk-synthetic-windows-key-never-a-real-credential"
        tunnel = "tunnel_" + "a" * 32
        grants = (self.state / "grants.json").read_bytes()
        history = self.state / "changes.sqlite3"
        history.write_bytes(b"synthetic retained history")
        with (
            patch(
                "keyhole.setup.select_client", return_value=(sys.executable, "0.0.14", "external")
            ),
            patch.object(NativeRuntime, "checks", return_value={"ok": True}),
            patch.object(NativeRuntime, "status", return_value={"ready": False}),
            patch("webbrowser.open") as browser,
            patch("getpass.getpass", return_value=key) as secret,
            contextlib.redirect_stderr(io.StringIO()) as output,
        ):
            with patch("builtins.input", side_effect=[tunnel, "n"]):
                result = setup.run(args)
            self.assertTrue(result["configured"])
            self.assertFalse(result["chatgpt_read_verified"])
            secret.assert_called_once()
            secret.reset_mock()
            before = (self.state / "runtime.key").read_bytes()
            for partial in (False, True):
                if partial:
                    (self.state / "runtime.json").unlink()
                with patch("builtins.input", side_effect=[tunnel, "n"] if partial else ["n"]):
                    setup.run(args)
                secret.assert_not_called()
                self.assertEqual((self.state / "runtime.key").read_bytes(), before)
            browser.assert_not_called()
            self.assertNotIn(key, output.getvalue())
            redacted = str(redact_status(self.manager.status()))
            self.assertNotIn(str(self.state), redacted)
            self.assertNotIn(tunnel, redacted)
            self.assertNotIn(key, redacted)
        self.assertEqual((self.state / "grants.json").read_bytes(), grants)
        self.assertEqual(history.read_bytes(), b"synthetic retained history")

    def test_new_demo_stays_readonly_and_never_overwrites_or_resumes_saved_folders(self):
        from keyhole import setup

        store = StateStore(self.root / "demo state")
        os.close(open_private_directory(store.path))
        runtime = OfflineRuntime()
        manager = Manager(store, runtime)
        manager.open(self.shared, name="Saved", access="rw")
        manager.select(["Saved"], False, enable=False)
        demo = self.root / "Demo 笔记"
        setup.create_demo(store, runtime, demo)
        grants = {g["name"]: g for g in store.read()["workspaces"]}
        self.assertFalse(grants["Saved"]["enabled"])
        self.assertEqual(grants["Saved"]["access"], "rw")
        self.assertEqual(grants["demo"]["access"], "ro")
        self.assertTrue(grants["demo"]["enabled"])
        self.assertEqual((demo / "notes.md").read_bytes(), setup.DEMO_TEXT.encode())
        with self.assertRaises(KeyholeError) as collision:
            setup.create_demo(store, runtime, demo)
        self.assertEqual(collision.exception.code, "demo_exists")
        self.assertEqual((demo / "notes.md").read_bytes(), setup.DEMO_TEXT.encode())


if __name__ == "__main__":
    unittest.main()
