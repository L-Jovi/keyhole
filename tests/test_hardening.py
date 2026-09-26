"""Regression cases for revocation, physical aliases, and bounded recovery."""

import json
import os
import shlex
import unicodedata
from pathlib import Path
from unittest.mock import patch

from test_bridge import FakeRuntime, FixtureCase

from keyhole import writes
from keyhole.errors import KeyholeError
from keyhole.management import Manager
from keyhole.runtime import NativeRuntime, owned_processes
from keyhole.state import StateStore
from keyhole.writes import Entry, Journal


class BoundaryTests(FixtureCase):
    def test_state_ancestors_and_descendants_are_refused_under_alias_spellings(self):
        spellings = [
            self.state,
            Path(str(self.state).swapcase()),
            Path("/System/Volumes/Data" + str(self.state)),
        ]
        child = self.state / "child"
        child.mkdir()
        for spelling in spellings:
            if not spelling.exists():
                continue
            with self.subTest(spelling=spelling):
                manager = Manager(StateStore(spelling), FakeRuntime())
                for root in (self.base, self.state, child):
                    self.refused(
                        "protected_root", lambda root=root, manager=manager: manager.open(root)
                    )
                self.assertTrue(manager.open(self.b)["runtime"]["ready"])

    def test_failed_ro_restart_still_revokes_old_writer(self):
        self.manager.access("Alpha", "rw")
        old = self.bridge()
        self.runtime.fail_start = True
        with patch.object(
            self.runtime,
            "preflight",
            side_effect=KeyholeError("client_version_changed", "synthetic upgrade"),
        ):
            self.refused("injected_failure", lambda: self.manager.access("Alpha", "ro"))
        state = self.store.read()
        self.assertEqual(state["workspaces"][0]["access"], "ro")
        self.assertFalse(any(w["enabled"] for w in state["workspaces"]))
        self.refused(
            "authorization_changed",
            lambda: old.mutate("mkdir", "Alpha", "stale-writer", path="bad"),
        )
        self.assertFalse((self.a / "bad").exists())

    def test_ro_can_be_saved_when_the_root_is_missing(self):
        self.manager.access("Alpha", "rw")
        self.a.rename(self.base / "moved")
        self.manager.access("Alpha", "ro")
        self.assertEqual(self.store.read()["workspaces"][0]["access"], "ro")

    def test_native_preflight_failure_happens_after_ro_revocation(self):
        self.manager.access("Alpha", "rw")
        old = self.bridge()
        runtime = NativeRuntime(self.store)
        manager = Manager(self.store, runtime)
        with (
            patch.object(runtime, "stop", return_value={"ready": False}),
            patch.object(
                runtime,
                "preflight",
                side_effect=KeyholeError("client_version_changed", "synthetic upgrade"),
            ),
        ):
            self.refused("client_version_changed", lambda: manager.access("Alpha", "ro"))
        self.assertEqual(self.store.read()["workspaces"][0]["access"], "ro")
        self.refused("authorization_changed", lambda: old.list_workspaces())

    def test_process_cleanup_requires_the_exact_module_and_state_option(self):
        listing = "\n".join(
            [
                f"111 {os.getuid()} python -m keyhole.server --state-dir /tmp/a --generation ga",
                f"112 {os.getuid()} python -m keyhole.server --state-dir /tmp/b --generation gb",
                f"113 {os.getuid()} python -I -m keyhole.parsers --state-dir /tmp/a --generation ga",
                f"114 {os.getuid()} python -I -m keyhole.parsers --state-dir /tmp/b --generation gb",
                f"115 {os.getuid()} echo keyhole.parsers --state-dir /tmp/a",
                f"116 {os.getuid()} python -m keyhole.parsers --state-dir /tmp/b --nonce /tmp/a",
            ]
        )
        with patch("keyhole.runtime.subprocess.check_output", return_value=listing):
            self.assertEqual(set(owned_processes(Path("/tmp/a"))), {111, 113})

    def test_next_command_keeps_custom_paths_and_quotes_them(self):
        store = StateStore(self.base / "state with spaces")
        self.assertEqual(
            shlex.split(store.command("setup", client="/client with spaces")),
            [
                "keyhole",
                "--state-dir",
                str(store.path),
                "--tunnel-client",
                "/client with spaces",
                "setup",
            ],
        )


class RecoveryBoundaryTests(FixtureCase):
    def setUp(self):
        super().setUp()
        self.manager.access("Alpha", "rw")

    def mutate(self, operation, request_id, **args):
        return self.bridge().mutate(operation, "Alpha", request_id, **args)

    def interrupt_write(self, path, request_id):
        with patch.object(Entry, "apply", side_effect=OSError("synthetic interruption")):
            self.refused(
                "operation_incomplete",
                lambda: self.mutate(
                    "write", request_id, path=path, text="hello", expected_sha256="absent"
                ),
            )
        return self.bridge().list_changes("Alpha")["changes"][0]["change_id"]

    def test_pending_case_and_unicode_aliases_do_not_escape_the_guard(self):
        name = "R\u00e9sum\u00e9.txt"
        self.interrupt_write(name, "interrupted-original")
        for path in (name, name.lower(), unicodedata.normalize("NFD", name)):
            with self.subTest(path=path):
                self.refused(
                    "operation_incomplete",
                    lambda path=path: self.mutate(
                        "write", "attempt-alias", path=path, text="other", expected_sha256="absent"
                    ),
                )
        self.assertFalse((self.a / name).exists())

    def test_full_pending_history_can_recover_without_purging_pending_records(self):
        with patch.object(writes, "MAX_RECORDS", 1):
            original = self.interrupt_write("new.txt", "interrupted-original")
            restored = self.mutate("restore", "recover-original", change_id=original)
            self.assertEqual(restored["status"], "committed")
            with Journal(self.store) as journal:
                self.assertLessEqual(journal.usage()["records"], 1)
                self.assertEqual(
                    journal.pending(writes.scope_for(self.store.read()["workspaces"][0])), []
                )
            self.assertFalse((self.a / "new.txt").exists())

    def test_restore_at_capacity_never_resurrects_an_evicted_original(self):
        with patch.object(writes, "MAX_RECORDS", 1):
            original = self.mutate(
                "write", "create-original", path="new.txt", text="hello", expected_sha256="absent"
            )
            restored = self.mutate("restore", "recover-original", change_id=original["change_id"])
            with Journal(self.store) as journal:
                self.assertEqual(journal.usage()["records"], 1)
                self.assertIsNone(journal.get(original["change_id"]))
                self.assertEqual(journal.get(restored["change_id"])["status"], "committed")
            self.assertFalse((self.a / "new.txt").exists())

    def test_interrupted_restore_retries_in_place_at_capacity(self):
        with patch.object(writes, "MAX_RECORDS", 1):
            original = self.mutate(
                "write", "create-original", path="new.txt", text="hello", expected_sha256="absent"
            )
            args = dict(change_id=original["change_id"])
            with patch.object(Entry, "apply", side_effect=OSError("synthetic interruption")):
                self.refused(
                    "operation_incomplete",
                    lambda: self.mutate("restore", "recover-original", **args),
                )
            with Journal(self.store) as journal:
                self.assertEqual(journal.usage()["records"], 2)
            restored = self.mutate("restore", "recover-original", **args)
            self.assertEqual(restored["status"], "committed")
            self.assertTrue(self.mutate("restore", "recover-original", **args)["replayed"])
            self.assertFalse((self.a / "new.txt").exists())
            with Journal(self.store) as journal:
                self.assertEqual(journal.usage()["records"], 1)

    def test_byte_capacity_recovery_and_utf8_accounting(self):
        original = self.interrupt_write("Résumé.txt", "interrupted-original")
        with Journal(self.store) as journal:
            cap = journal.usage()["bytes"]
            text = json.dumps(journal.get(original), ensure_ascii=False)
            self.assertEqual(cap, len(text.encode()))
            self.assertGreater(cap, len(text))
        with patch.object(writes, "MAX_BYTES", cap):
            restored = self.mutate("restore", "recover-original", change_id=original)
            self.assertEqual(restored["status"], "committed")
            with Journal(self.store) as journal:
                self.assertLessEqual(journal.usage()["bytes"], cap)
        self.assertFalse((self.a / "Résumé.txt").exists())

    def test_partially_applied_restore_retries_without_overwriting_external_edits(self):
        source = self.a / "proof.md"
        content = source.read_bytes()
        original = self.mutate(
            "move",
            "move-original",
            path="proof.md",
            destination="moved.md",
            expected_sha256=self.bridge().read_file("Alpha", "proof.md")["sha256"],
        )
        apply = Entry.apply
        calls = 0

        def interrupt_after_one(entry, *args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic interruption after first path")
            return apply(entry, *args)

        args = dict(change_id=original["change_id"])
        with patch.object(Entry, "apply", interrupt_after_one):
            self.refused(
                "operation_incomplete", lambda: self.mutate("restore", "undo-move", **args)
            )
        source.write_text("an external edit\n")
        self.refused("restore_conflict", lambda: self.mutate("restore", "undo-move", **args))
        self.assertEqual(source.read_text(), "an external edit\n")
        source.unlink()
        restored = self.mutate("restore", "undo-move", **args)
        replay = self.mutate("restore", "undo-move", **args)
        self.assertEqual(replay["changes"], restored["changes"])
        self.assertEqual(replay["evicted_records"], restored["evicted_records"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(source.read_bytes(), content)
        self.assertFalse((self.a / "moved.md").exists())

    def test_failed_reservation_keeps_previous_history(self):
        first = self.mutate("mkdir", "first-directory", path="one")
        with patch.object(writes, "MAX_BYTES", 1):
            self.refused(
                "history_full", lambda: self.mutate("mkdir", "second-directory", path="two")
            )
        with Journal(self.store) as journal:
            self.assertIsNotNone(journal.get(first["change_id"]))
        self.assertFalse((self.a / "two").exists())
