import asyncio
import hashlib
import os
import shutil
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from keyhole.bridge import Bridge
from keyhole.errors import KeyholeError
from keyhole.management import Manager
from keyhole.state import StateStore

SERVER_ARGS = ["-m", "keyhole.server"]
UNICODE_LINE = "Ünïcode evidence one"
TOOLS = {
    "list_workspaces",
    "list_directory",
    "search_files",
    "read_file",
    "write_file",
    "apply_text_patch",
    "create_directory",
    "copy_file",
    "move_file",
    "delete_file",
    "list_changes",
    "restore_change",
}


class FakeRuntime:
    def __init__(self):
        self.running = False
        self.starts = 0
        self.stops = 0
        self.fail_start = False

    def status(self):
        return {"process_running": self.running, "ready": self.running, "healthy": self.running}

    def checks(self):
        return {"ok": True}

    def preflight(self):
        return {}

    def stop(self):
        self.running = False
        self.stops += 1
        return self.status()

    def connect(self, generation):
        if self.fail_start:
            raise KeyholeError("injected_failure", "Synthetic native failure.")
        self.running = True
        self.starts += 1
        return self.status()


class FixtureCase(unittest.TestCase):
    def setUp(self):
        # Resolve the temp dir: the walk refuses symlinked components such as macOS /tmp.
        self.base = Path(tempfile.mkdtemp(prefix="keyhole-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.state = self.base / "private-state"
        self.state.mkdir(mode=0o700)
        self.a = self.base / "Alpha"
        self.b = self.base / "Beta"
        self.a.mkdir()
        self.b.mkdir()
        (self.a / "proof.md").write_text(UNICODE_LINE + "\nline two\n")
        (self.b / "other.txt").write_text("independent evidence\n")
        self.store = StateStore(self.state)
        self.runtime = FakeRuntime()
        self.manager = Manager(self.store, self.runtime)
        self.manager.open(self.a)

    def bridge(self):
        return Bridge(self.store, self.store.read()["generation"])

    def refused(self, code, callback):
        with self.assertRaises(KeyholeError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def server_params(self):
        generation = self.store.read()["generation"]
        args = SERVER_ARGS + ["--state-dir", str(self.state), "--generation", generation]
        return StdioServerParameters(command=sys.executable, args=args, env={})


class BridgeTests(FixtureCase):
    def test_new_changed_multiple_directories_and_idempotence(self):
        before = self.runtime.starts
        result = self.manager.open(self.a)
        self.assertFalse(result["changed"])
        self.assertEqual(before, self.runtime.starts)
        self.manager.open(self.b)
        bridge = self.bridge()
        self.assertEqual(
            [w["name"] for w in bridge.list_workspaces()["workspaces"]], ["Alpha", "Beta"]
        )
        first = bridge.read_file("Alpha", "proof.md")
        self.assertEqual(first["lines"][0]["text"], UNICODE_LINE)
        (self.a / "proof.md").write_text("updated evidence\n")
        next_read = bridge.read_file("Alpha", "proof.md")
        self.assertNotEqual(first["sha256"], next_read["sha256"])
        (self.a / "new.txt").write_text("arrived later")
        self.assertIn("new.txt", [x["name"] for x in bridge.list_directory("Alpha")["entries"]])
        self.refused(
            "content_changed",
            lambda: bridge.read_file("Alpha", "proof.md", expected_sha256=first["sha256"]),
        )
        self.assertEqual(
            bridge.read_file("Beta", "other.txt")["lines"][0]["text"], "independent evidence"
        )
        status = self.manager.status()
        self.assertTrue(status["configured"])
        self.assertEqual(status["effective_open_workspaces"], ["Alpha", "Beta"])

    def test_alias_overlap_replacement_and_fail_closed_start(self):
        child = self.a / "child"
        child.mkdir()
        self.refused("overlapping_roots", lambda: self.manager.open(child))
        self.refused("alias_conflict", lambda: self.manager.open(self.b, "Alpha"))
        old = self.base / "Old"
        self.a.rename(old)
        self.a.mkdir()
        self.refused("root_changed", lambda: self.bridge().read_file("Alpha", "proof.md"))
        self.refused("root_changed", lambda: self.manager.open(self.a))
        self.runtime.fail_start = True
        self.refused("injected_failure", lambda: self.manager.open(self.b))
        self.assertFalse(any(w["enabled"] for w in self.store.read()["workspaces"]))
        self.assertFalse(self.runtime.running)

    def test_reopening_a_custom_alias_is_idempotent(self):
        self.manager.open(self.b, "My Evidence")
        result = self.manager.open(self.b)
        self.assertFalse(result["changed"])
        self.assertEqual(result["configuration"]["workspaces"][1]["name"], "My Evidence")

    def test_failed_stop_revokes_all_and_does_not_report_live_grants(self):
        self.manager.open(self.b)
        old = self.bridge()
        with patch.object(
            self.runtime,
            "stop",
            side_effect=KeyholeError("stop_unconfirmed", "Synthetic surviving runtime."),
        ):
            self.refused(
                "stop_unconfirmed", lambda: self.manager.select(["Alpha"], False, enable=False)
            )
        self.assertTrue(self.runtime.running)
        self.assertFalse(any(w["enabled"] for w in self.store.read()["workspaces"]))
        self.assertEqual(self.manager.status()["effective_open_workspaces"], [])
        self.refused("authorization_changed", old.list_workspaces)
        self.manager.select(["Beta"], False, enable=True)
        self.assertEqual(
            [w["name"] for w in self.bridge().list_workspaces()["workspaces"]], ["Beta"]
        )

    def test_revoke_invalidates_old_instances_and_final_stop(self):
        self.manager.open(self.b)
        old = self.bridge()
        self.manager.select(["Alpha"], False, enable=False)
        self.refused("authorization_changed", old.list_workspaces)
        self.refused("workspace_unavailable", lambda: self.bridge().read_file("Alpha", "proof.md"))
        self.assertTrue(self.runtime.running)
        result = self.manager.select([], True, enable=False)
        self.assertTrue(result["shutdown_confirmed"])
        self.assertFalse(self.runtime.running)
        self.assertEqual(len(self.store.read()["workspaces"]), 2)
        self.manager.select(["Beta"], False, enable=True)
        self.assertEqual(
            [w["name"] for w in self.bridge().list_workspaces()["workspaces"]], ["Beta"]
        )

    def test_boot_remains_offline(self):
        with patch("keyhole.bridge.boot_id", return_value="different-boot"):
            self.refused("authorization_changed", self.bridge)
        with patch("keyhole.management.boot_id", return_value="different-boot"):
            self.assertFalse(any(w["enabled"] for w in self.manager.current()["workspaces"]))

    def test_traversal_unauthorized_symlinks_hardlinks_and_fifo(self):
        bridge = self.bridge()
        self.refused("workspace_unavailable", lambda: bridge.read_file("Beta", "other.txt"))
        for path in (
            "../Beta/other.txt",
            "/etc/passwd",
            "child/../../Beta",
            "./proof.md",
            "child//test",
            "child\\test",
            "~/.ssh/id_rsa",
            "nul\x00file",
        ):
            self.refused("invalid_path", lambda path=path: bridge.read_file("Alpha", path))
        (self.a / "linked.txt").symlink_to(self.b / "other.txt")
        (self.a / "linked-dir").symlink_to(self.b, target_is_directory=True)
        self.refused("path_unavailable", lambda: bridge.read_file("Alpha", "linked.txt"))
        self.refused("path_unavailable", lambda: bridge.read_file("Alpha", "linked-dir/other.txt"))
        os.link(self.b / "other.txt", self.a / "hard.txt")
        self.refused("unsafe_file", lambda: bridge.read_file("Alpha", "hard.txt"))
        os.mkfifo(self.a / "pipe")
        self.refused("unsafe_file", lambda: bridge.read_file("Alpha", "pipe"))
        self.assertEqual(
            [e["name"] for e in bridge.list_directory("Alpha")["entries"]], ["proof.md"]
        )

    def test_symlink_root_and_rename_during_read(self):
        alias = self.base / "Alias"
        alias.symlink_to(self.a, target_is_directory=True)
        self.refused("symlink_in_path", lambda: self.manager.open(alias))
        child = self.a / "nested"
        child.mkdir()
        (child / "file.txt").write_text("approved")
        bridge = self.bridge()
        original_read = os.read
        swapped = False

        def racing_read(fd, amount):
            nonlocal swapped
            content = original_read(fd, amount)
            if content == b"approved" and not swapped:
                child.rename(self.base / "moved-outside")
                child.symlink_to(self.b, target_is_directory=True)
                swapped = True
            return content

        with patch("keyhole.filesystem.os.read", side_effect=racing_read):
            self.refused("path_changed", lambda: bridge.read_file("Alpha", "nested/file.txt"))

    def test_builtin_exclusions_match_direct_read_listing_and_search(self):
        for name in (".env", "credentials.json", "private.pem", "runtime.key"):
            (self.a / name).write_text("secret-marker")
        for name in (".git", "node_modules", ".claude", ".codex"):
            folder = self.a / name
            folder.mkdir()
            (folder / "private.txt").write_text("secret-marker")
        bridge = self.bridge()
        self.refused("excluded", lambda: bridge.read_file("Alpha", ".env"))
        self.refused("excluded", lambda: bridge.read_file("Alpha", ".codex/private.txt"))
        self.refused("excluded", lambda: bridge.read_file("Alpha", ".ENV"))
        self.assertEqual(bridge.list_directory("Alpha")["excluded_entries"], 8)
        self.assertEqual(bridge.search_files("Alpha", "secret-marker", kind="text")["matches"], [])

    def test_custom_exclusions_hide_subtrees_ignoring_case_and_normalization(self):
        (self.b / "Private").mkdir()
        (self.b / "Private" / "secret.txt").write_text("secret-marker")
        (self.b / "Secret" / "deep").mkdir(parents=True)
        (self.b / "Secret" / "deep" / "key.txt").write_text("secret-marker")
        decomposed = unicodedata.normalize("NFD", "Café")
        (self.b / decomposed).mkdir()
        (self.b / decomposed / "x.txt").write_text("secret-marker")
        (self.b / "notes.log").write_text("secret-marker")
        self.manager.open(self.b, exclusions=["Private/", "Secret/*", "Café", "*.LOG"])
        grant = next(w for w in self.store.read()["workspaces"] if w["name"] == "Beta")
        self.assertEqual(grant["exclusions"], ["Private", "Secret/*", "Café", "*.LOG"])
        bridge = self.bridge()
        hidden = (
            "Private/secret.txt",
            "private/secret.txt",
            "SECRET/deep/key.txt",
            "Café/x.txt",
            decomposed + "/x.txt",
            "notes.log",
        )
        for path in hidden:
            self.refused("excluded", lambda path=path: bridge.read_file("Beta", path))
        self.refused("excluded", lambda: bridge.list_directory("Beta", "Private"))
        self.assertEqual(
            [e["name"] for e in bridge.list_directory("Beta")["entries"]], ["other.txt"]
        )
        self.assertEqual(bridge.search_files("Beta", "secret-marker", kind="text")["matches"], [])
        self.assertEqual(
            bridge.list_workspaces()["workspaces"][1]["exclusions"], grant["exclusions"]
        )
        self.refused("invalid_pattern", lambda: self.manager.open(self.a, exclusions=["../x"]))
        self.refused("policy_conflict", lambda: self.manager.open(self.b, exclusions=["Other"]))

    def test_roots_are_stored_canonically_and_control_paths_are_protected(self):
        system_data = Path("/System/Volumes/Data")
        expected = "protected_root" if system_data.exists() else "path_missing"
        self.refused(expected, lambda: self.manager.open(system_data))
        self.refused("path_missing", lambda: self.manager.open(self.base / "does-not-exist"))
        self.refused("protected_root", lambda: self.manager.open(self.state))
        self.refused("protected_root", lambda: self.manager.open(self.base))
        spellings = [Path("/System/Volumes/Data" + str(self.b)), Path(str(self.b).swapcase())]
        usable = [p for p in spellings if p.exists() and p != self.b]
        if not usable:
            self.skipTest("no alias spelling of the fixture directory exists on this filesystem")
        result = self.manager.open(usable[0])
        self.assertEqual(result["configuration"]["workspaces"][1]["path"], str(self.b))
        self.assertFalse(self.manager.open(usable[0])["changed"])

    def test_pagination_invalidates_on_change_and_tampering(self):
        for i in range(5):
            (self.a / f"part-{i}.txt").write_text("needle\n")
        bridge = self.bridge()
        first = bridge.list_directory("Alpha", limit=2)
        second = bridge.list_directory("Alpha", limit=2, cursor=first["next_cursor"])
        self.assertFalse(
            {e["name"] for e in first["entries"]} & {e["name"] for e in second["entries"]}
        )
        self.refused(
            "invalid_cursor",
            lambda: bridge.list_directory("Alpha", cursor=first["next_cursor"] + "x"),
        )
        found = bridge.search_files("Alpha", "needle", kind="text", limit=2)
        self.assertTrue(found["next_cursor"])
        (self.a / "part-0.txt").write_text("changed\n")
        self.refused(
            "content_changed", lambda: bridge.list_directory("Alpha", cursor=first["next_cursor"])
        )
        self.refused(
            "content_changed",
            lambda: bridge.search_files(
                "Alpha", "needle", kind="text", cursor=found["next_cursor"]
            ),
        )

    def test_private_state_and_text_limits(self):
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.state / "grants.json").stat().st_mode & 0o777, 0o600)
        (self.a / "large.txt").write_bytes(b"z" * (8 * 1024 * 1024 + 1))
        result = self.bridge().read_file("Alpha", "large.txt")
        self.assertFalse(result["content_returned"])
        self.assertTrue(result["unsupported"])
        (self.a / "utf16.txt").write_bytes("Ünïcode\nZweite Zeile".encode("utf-16"))
        self.assertEqual(
            self.bridge().read_file("Alpha", "utf16.txt")["lines"][1]["text"], "Zweite Zeile"
        )
        (self.a / "unknown.bin").write_bytes(b"\x00\xff")
        self.refused(
            "unsupported_encoding", lambda: self.bridge().read_file("Alpha", "unknown.bin")
        )
        (self.state / "grants.json").chmod(0o644)
        self.refused("state_permissions", self.store.read)

    def test_actual_stdio_protocols_and_remote_admin_absence(self):
        async def check(mode):
            async with Client(self.server_params(), mode=mode, read_timeout_seconds=10) as client:
                tools = (await client.list_tools()).tools
                self.assertEqual({t.name for t in tools}, TOOLS)
                reads = {
                    "list_workspaces",
                    "list_directory",
                    "search_files",
                    "read_file",
                    "list_changes",
                }
                self.assertTrue(
                    all(
                        t.annotations.read_only_hint == (t.name in reads)
                        and t.annotations.destructive_hint == (t.name not in reads)
                        and not t.annotations.open_world_hint
                        for t in tools
                    )
                )
                result = await client.call_tool(
                    "read_file", {"workspace": "Alpha", "path": "proof.md"}
                )
                self.assertFalse(result.is_error)
                self.assertEqual(
                    result.structured_content["sha256"],
                    hashlib.sha256((self.a / "proof.md").read_bytes()).hexdigest(),
                )
                denied = await client.call_tool("open", {"path": str(self.b)})
                self.assertTrue(denied.is_error)
                denied = await client.call_tool(
                    "read_file", {"workspace": "Alpha", "path": "../Beta/other.txt"}
                )
                self.assertTrue(denied.is_error)

        for mode in ("auto", "legacy"):
            asyncio.run(check(mode))


if __name__ == "__main__":
    unittest.main()
