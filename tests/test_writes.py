import base64
import hashlib
import json
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_bridge import FixtureCase

from keyhole import writes
from keyhole.writes import Entry, Journal


def sha(data):
    return hashlib.sha256(data).hexdigest()


class WriteTests(FixtureCase):
    def setUp(self):
        super().setUp()
        self.manager.access("Alpha", "rw")
        self.seq = 0

    def edit(self, operation, path=None, workspace="Alpha", **kwargs):
        self.seq += 1
        if path is not None:
            kwargs["path"] = path
        return self.bridge().mutate(operation, workspace, "test-request-" + str(self.seq), **kwargs)

    def test_text_create_patch_versions_replay_and_preservation(self):
        p = self.a / "Ünïcode code.py"
        p.write_bytes(b"\xef\xbb\xbf# keep\r\ndef add(a, b):\r\n    return a - b\r\n")
        p.chmod(0o755)
        h = sha(p.read_bytes())
        args = dict(
            operation="patch",
            workspace="Alpha",
            request_id="stable-request",
            path=p.name,
            expected_sha256=h,
            edits=[{"old": "return a - b", "new": "return a + b", "count": 1}],
        )
        first = self.bridge().mutate(**args)
        expected = b"\xef\xbb\xbf# keep\r\ndef add(a, b):\r\n    return a + b\r\n"
        self.assertEqual(p.read_bytes(), expected)
        self.assertEqual(p.stat().st_mode & 0o777, 0o755)
        self.assertTrue(first["recoverable"])
        self.assertTrue(self.bridge().mutate(**args)["replayed"])
        self.refused(
            "request_id_reused",
            lambda: self.bridge().mutate(**{**args, "edits": [{"old": "a + b", "new": "0"}]}),
        )
        self.refused(
            "version_conflict", lambda: self.edit("write", p.name, text="bad", expected_sha256=h)
        )
        self.refused(
            "patch_mismatch",
            lambda: self.edit(
                "patch",
                p.name,
                edits=[{"old": "missing", "new": "x"}],
                expected_sha256=sha(expected),
            ),
        )
        result = self.edit("write", "new.md", text="# Done 🌱\n", expected_sha256="absent")
        self.assertEqual((self.a / "new.md").read_text(), "# Done 🌱\n")
        self.edit("restore", change_id=result["change_id"])
        self.assertFalse((self.a / "new.md").exists())
        self.edit("restore", change_id=first["change_id"])
        self.assertEqual(sha(p.read_bytes()), h)

    def test_full_text_preserves_crlf_bom_and_checks_encoding(self):
        p = self.a / "example.txt"
        p.write_bytes(b"\xef\xbb\xbfvalue=1\r\nkeep=yes\r\n")
        self.edit("write", p.name, text="value=2\nkeep=yes\n", expected_sha256=sha(p.read_bytes()))
        self.assertEqual(p.read_bytes(), b"\xef\xbb\xbfvalue=2\r\nkeep=yes\r\n")
        p.write_bytes("value=3".encode("utf-16"))
        self.refused(
            "unsupported_encoding",
            lambda: self.edit("write", p.name, text="x", expected_sha256=sha(p.read_bytes())),
        )

    def test_supported_write_formats_are_text_only_but_file_operations_are_type_neutral(self):
        binaries = {
            "photo.png": b"\x89PNG\r\n\x1a\n" + os.urandom(64),
            "report.docx": b"PK\x03\x04" + os.urandom(64),
            "sheet.xlsx": b"PK\x03\x04" + os.urandom(64),
            "paper.pdf": b"%PDF-1.7\n" + os.urandom(64),
            "bundle.zip": b"PK\x03\x04" + os.urandom(64),
        }
        for name, data in binaries.items():
            (self.a / name).write_bytes(data)
            h = sha(data)
            # Creating, replacing or patching document, image and archive formats is refused.
            self.refused(
                "not_text", lambda n=name, h=h: self.edit("write", n, text="x", expected_sha256=h)
            )
            self.refused(
                "not_text",
                lambda n=name, h=h: self.edit(
                    "patch", n, edits=[{"old": "a", "new": "b"}], expected_sha256=h
                ),
            )
            self.refused(
                "not_text",
                lambda n=name: self.edit("write", "new-" + n, text="x", expected_sha256="absent"),
            )
            # Copy, move, delete and restore treat every regular file as opaque bytes.
            copied = self.edit("copy", name, destination="copy-" + name, expected_sha256=h)
            self.assertEqual((self.a / ("copy-" + name)).read_bytes(), data)
            moved = self.edit(
                "move", "copy-" + name, destination="moved-" + name, expected_sha256=h
            )
            deleted = self.edit("delete", "moved-" + name, expected_sha256=h)
            self.assertFalse((self.a / ("moved-" + name)).exists())
            self.edit("restore", change_id=deleted["change_id"])
            self.assertEqual((self.a / ("moved-" + name)).read_bytes(), data)
            self.edit("restore", change_id=moved["change_id"])
            self.edit("restore", change_id=copied["change_id"])
            self.assertFalse((self.a / ("copy-" + name)).exists())
        # Any other extension is treated as UTF-8 text.
        for name in ("script.sh", "config.toml", "notes", "data.custom", "index.html"):
            self.edit("write", name, text="line\n", expected_sha256="absent")
            self.assertEqual((self.a / name).read_text(), "line\n")
        self.refused(
            "not_text",
            lambda: self.edit("write", "nul-content.txt", text="a\x00b", expected_sha256="absent"),
        )
        self.refused(
            "input_too_large",
            lambda: self.edit(
                "write", "big.txt", text="x" * (1024 * 1024 + 1), expected_sha256="absent"
            ),
        )

    def test_directory_copy_move_delete_restore_and_conflicts(self):
        original = (self.a / "proof.md").read_bytes()
        h = sha(original)
        folder = self.edit("mkdir", "review")
        copied = self.edit("copy", "proof.md", destination="review/copy.md", expected_sha256=h)
        moved = self.edit(
            "move", "review/copy.md", destination="review/renamed.md", expected_sha256=h
        )
        self.assertFalse((self.a / "review/copy.md").exists())
        self.assertEqual((self.a / "review/renamed.md").read_bytes(), original)
        self.refused(
            "destination_exists",
            lambda: self.edit(
                "copy", "proof.md", destination="review/renamed.md", expected_sha256=h
            ),
        )
        deleted = self.edit("delete", "review/renamed.md", expected_sha256=h)
        self.edit("restore", change_id=deleted["change_id"])
        self.edit("restore", change_id=moved["change_id"])
        self.edit("restore", change_id=copied["change_id"])
        self.edit("restore", change_id=folder["change_id"])
        self.assertFalse((self.a / "review").exists())

    def test_recovery_refuses_later_changes_and_wrong_workspace(self):
        h = sha((self.a / "proof.md").read_bytes())
        changed = self.edit("write", "proof.md", text="new", expected_sha256=h)
        (self.a / "proof.md").write_text("external")
        self.refused(
            "restore_conflict", lambda: self.edit("restore", change_id=changed["change_id"])
        )
        self.manager.open(self.b, access="rw")
        self.refused(
            "change_unavailable",
            lambda: self.bridge().mutate(
                "restore", "Beta", "wrong-workspace", change_id=changed["change_id"]
            ),
        )
        self.assertEqual((self.a / "proof.md").read_text(), "external")

    def test_recovery_off_keeps_metadata_only_but_still_recovers_interrupted_operations(self):
        self.manager.open(self.b, access="rw", recovery="off")
        self.assertEqual(self.bridge().list_workspaces()["workspaces"][1]["recovery"], "off")
        original = (self.b / "other.txt").read_bytes()
        changed = self.edit(
            "write", "other.txt", workspace="Beta", text="replaced\n", expected_sha256=sha(original)
        )
        self.assertFalse(changed["recoverable"])
        with self.store.lock(), Journal(self.store) as journal:
            stored = journal.get(changed["change_id"])
        self.assertNotIn("data", stored["before"][0])
        self.assertEqual(stored["before"][0]["sha256"], sha(original))
        self.refused(
            "recovery_disabled",
            lambda: self.edit("restore", workspace="Beta", change_id=changed["change_id"]),
        )
        self.assertEqual((self.b / "other.txt").read_text(), "replaced\n")
        listed = self.bridge().list_changes("Beta")["changes"][0]
        self.assertFalse(listed["recoverable"])
        # An interrupted operation keeps its in-flight snapshot so the path can be repaired.
        h = sha((self.b / "other.txt").read_bytes())
        real = Entry.apply

        def interrupted(entry, before, after, stage):
            if entry.name == "other.txt":
                raise OSError("injected interruption")
            return real(entry, before, after, stage)

        with patch.object(Entry, "apply", interrupted):
            self.refused(
                "operation_incomplete",
                lambda: self.edit(
                    "move",
                    "other.txt",
                    workspace="Beta",
                    destination="renamed.txt",
                    expected_sha256=h,
                ),
            )
        pending = self.bridge().list_changes("Beta")["changes"][0]
        self.assertEqual(pending["status"], "prepared")
        self.edit("restore", workspace="Beta", change_id=pending["change_id"])
        self.assertEqual((self.b / "other.txt").read_text(), "replaced\n")
        self.assertFalse((self.b / "renamed.txt").exists())
        # Switching recovery back on applies to later changes only.
        self.manager.open(self.b, recovery="on")
        again = self.edit(
            "write",
            "other.txt",
            workspace="Beta",
            text="third\n",
            expected_sha256=sha(b"replaced\n"),
        )
        self.assertTrue(again["recoverable"])

    def test_history_evicts_oldest_completed_records_but_never_interrupted_ones(self):
        with patch.object(writes, "MAX_RECORDS", 3):
            first = self.edit("mkdir", "one")
            self.edit("mkdir", "two")
            self.edit("mkdir", "three")
            fourth = self.edit("mkdir", "four")
            self.assertEqual(fourth["evicted_records"], 1)
            history = self.bridge().list_changes("Alpha", limit=10)
            self.assertNotIn(first["change_id"], [c["change_id"] for c in history["changes"]])
            self.assertEqual(history["usage"]["records"], 3)
            self.refused(
                "change_unavailable", lambda: self.edit("restore", change_id=first["change_id"])
            )

            def stopped(entry, before, after, stage):
                writes.fileio.mkdir(entry.name, after["mode"], dir_fd=entry.fd)
                raise KeyboardInterrupt()

            for name in ("p1", "p2", "p3"):
                with patch.object(Entry, "apply", stopped), self.assertRaises(KeyboardInterrupt):
                    self.edit("mkdir", name)
            pending = [
                c
                for c in self.bridge().list_changes("Alpha", limit=10)["changes"]
                if c["status"] == "prepared"
            ]
            self.assertEqual(len(pending), 3)
            self.refused("history_full", lambda: self.edit("mkdir", "five"))
        with self.store.lock(), Journal(self.store) as journal:
            self.assertEqual(journal.purge("2000-01-01")["purged_records"], 0)

    def test_permissions_legacy_state_revoke_and_reboot(self):
        self.manager.open(self.b)
        self.refused(
            "read_only",
            lambda: self.bridge().mutate(
                "write",
                "Beta",
                "readonly-request",
                path="new.md",
                text="x",
                expected_sha256="absent",
            ),
        )
        state = self.store.read()
        state["workspaces"][0].pop("access")
        self.store.write(state)
        self.refused("read_only", lambda: self.edit("mkdir", "new"))
        self.manager.access("Alpha", "rw")
        self.assertEqual(
            self.manager.open(self.a)["configuration"]["workspaces"][0]["access"], "rw"
        )
        old = self.bridge()
        self.manager.access("Alpha", "ro")
        self.refused(
            "authorization_changed",
            lambda: old.mutate("mkdir", "Alpha", "old-generation", path="bad"),
        )
        self.manager.select(["Alpha"], False, enable=False)
        self.manager.access("Alpha", "rw")
        self.assertFalse(self.store.read()["workspaces"][0]["enabled"])
        self.manager.select(["Alpha"], False, enable=True)
        self.assertEqual(self.bridge().list_workspaces()["workspaces"][0]["access"], "rw")

    def test_links_traversal_protected_controls_and_readonly_mode(self):
        p = self.a / "proof.md"
        h = sha(p.read_bytes())
        (self.a / "link.md").symlink_to(p)
        self.refused(
            "unsafe_file", lambda: self.edit("write", "link.md", text="bad", expected_sha256=h)
        )
        os.link(p, self.a / "hard.md")
        self.refused("unsafe_file", lambda: self.edit("delete", "hard.md", expected_sha256=h))
        (self.a / "hard.md").unlink()
        self.refused("invalid_path", lambda: self.edit("mkdir", "../outside"))
        self.refused(
            "excluded", lambda: self.edit("write", ".env", text="bad", expected_sha256="absent")
        )
        for hook in (".zshrc", "sub/.BASHRC", ".bash_profile", "a/b/.profile"):
            self.refused(
                "protected_control_path",
                lambda hook=hook: self.edit("write", hook, text="bad", expected_sha256="absent"),
            )
        p.chmod(0o444)
        self.refused(
            "file_read_only", lambda: self.edit("write", "proof.md", text="bad", expected_sha256=h)
        )

    def test_bridge_code_environment_and_state_are_protected_by_identity(self):
        project = self.b / "bridge-code"
        project.mkdir()
        (project / "server.py").write_text("print('untouched')\n")
        self.manager.open(self.b, access="rw")
        spellings = [
            Path("/System/Volumes/Data" + str(project)),
            Path(str(project).swapcase()),
            project,
        ]
        for spelled in spellings:
            if not spelled.exists():
                continue
            with patch.object(writes, "PACKAGE_DIR", spelled):
                self.refused(
                    "protected_control_path",
                    lambda: self.edit(
                        "write",
                        "bridge-code/server.py",
                        workspace="Beta",
                        text="evil",
                        expected_sha256=sha(b"print('untouched')\n"),
                    ),
                )
                self.refused(
                    "protected_control_path",
                    lambda: self.edit(
                        "write",
                        "bridge-code/new.py",
                        workspace="Beta",
                        text="evil",
                        expected_sha256="absent",
                    ),
                )
                self.refused(
                    "protected_control_path",
                    lambda: self.edit("mkdir", "bridge-code/sub", workspace="Beta"),
                )
                self.edit(
                    "write",
                    "elsewhere.txt",
                    workspace="Beta",
                    text="fine\n",
                    expected_sha256="absent",
                )
                (self.b / "elsewhere.txt").unlink()
        self.assertEqual((project / "server.py").read_text(), "print('untouched')\n")

    def test_interrupted_move_is_explicit_and_recoverable(self):
        original = (self.a / "proof.md").read_bytes()
        h = sha(original)
        real = Entry.apply

        def interrupted(entry, before, after, stage):
            if entry.name == "proof.md":
                raise OSError("injected second step interruption")
            return real(entry, before, after, stage)

        with patch.object(Entry, "apply", interrupted):
            self.refused(
                "operation_incomplete",
                lambda: self.edit("move", "proof.md", destination="moved.md", expected_sha256=h),
            )
        pending = self.bridge().list_changes("Alpha")["changes"][0]
        self.assertEqual(pending["status"], "prepared")
        self.refused(
            "operation_incomplete",
            lambda: self.edit("write", "moved.md", text="bad", expected_sha256=h),
        )
        self.edit("restore", change_id=pending["change_id"])
        self.assertFalse((self.a / "moved.md").exists())
        self.assertEqual((self.a / "proof.md").read_bytes(), original)

    def test_interrupted_exclusive_creation_cleans_only_its_stage(self):
        def stopped(entry, before, after, stage):
            fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=entry.fd)
            os.write(fd, base64.b64decode(after["data"]))
            os.close(fd)
            os.link(stage, entry.name, src_dir_fd=entry.fd, dst_dir_fd=entry.fd)
            raise KeyboardInterrupt()

        with patch.object(Entry, "apply", stopped), self.assertRaises(KeyboardInterrupt):
            self.edit("write", "created.md", text="new", expected_sha256="absent")
        pending = self.bridge().list_changes("Alpha")["changes"][0]
        self.assertEqual((self.a / "created.md").stat().st_nlink, 2)
        self.edit("restore", change_id=pending["change_id"])
        self.assertFalse((self.a / "created.md").exists())
        self.assertEqual(list(self.a.glob(".keyhole-stage-*")), [])

    def test_revoke_waits_for_commit_then_blocks_old_writer(self):
        entered, release = threading.Event(), threading.Event()
        errors = []
        real = Entry.apply
        old = self.bridge()

        def paused(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            return real(*args)

        def write():
            try:
                old.mutate(
                    "write",
                    "Alpha",
                    "concurrent-write",
                    path="new.md",
                    text="ok",
                    expected_sha256="absent",
                )
            except BaseException as exc:
                errors.append(exc)

        def revoke():
            try:
                self.manager.select([], True, enable=False)
            except BaseException as exc:
                errors.append(exc)

        with patch.object(Entry, "apply", paused):
            writer = threading.Thread(target=write)
            writer.start()
            self.assertTrue(entered.wait(2))
            closer = threading.Thread(target=revoke)
            closer.start()
            time.sleep(0.1)
            self.assertTrue(closer.is_alive())
            release.set()
            writer.join(3)
            closer.join(3)
        self.assertEqual(errors, [])
        self.refused(
            "authorization_changed",
            lambda: old.mutate("mkdir", "Alpha", "after-revoke", path="bad"),
        )
        self.assertFalse(self.runtime.running)

    def test_history_is_bounded_private_and_pending_not_purged(self):
        self.edit("mkdir", "one")
        with self.store.lock(), Journal(self.store) as journal:
            result = journal.history()
            self.assertEqual(len(result["changes"]), 1)
            self.assertNotIn("data", json.dumps(result))
            self.assertEqual(journal.purge("2000-01-01")["purged_records"], 0)
        self.assertEqual((self.state / "changes.sqlite3").stat().st_mode & 0o777, 0o600)

    def test_interrupted_mkdir_without_durable_identity_refuses_restore(self):
        def stopped(entry, before, after, stage):
            writes.fileio.mkdir(entry.name, after["mode"], dir_fd=entry.fd)
            raise KeyboardInterrupt()

        with patch.object(Entry, "apply", stopped), self.assertRaises(KeyboardInterrupt):
            self.edit("mkdir", "unfinished")
        pending = self.bridge().list_changes("Alpha")["changes"][0]
        self.assertEqual(pending["status"], "prepared")
        self.refused(
            "restore_conflict", lambda: self.edit("restore", change_id=pending["change_id"])
        )
        self.assertTrue((self.a / "unfinished").is_dir())


if __name__ == "__main__":
    unittest.main()
