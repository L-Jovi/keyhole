"""Native Windows integration; synthetic directories and fake transport only."""

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

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
            self.assertEqual(set(observed), {processes[0].pid})
            self.assertEqual(set(owned_processes(other)), {processes[1].pid})
            terminate_owned(self.state)
            processes[0].wait(timeout=5)
            self.assertIsNone(processes[1].poll())
            self.assertEqual(owned_processes(self.state), {})
        finally:
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


if __name__ == "__main__":
    unittest.main()
