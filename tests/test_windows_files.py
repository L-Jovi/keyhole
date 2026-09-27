"""Native NTFS trust-boundary checks, run on real Windows only."""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if sys.platform == "win32":
    from keyhole import windows_files as win
    from keyhole import windows_security as security


@unittest.skipUnless(sys.platform == "win32", "requires native Windows")
class WindowsFilesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="keyhole-native-"))
        self.addCleanup(shutil.rmtree, self.root)
        self.shared = self.root / "Shared 笔记"
        self.shared.mkdir()
        self.note = self.shared / "Resume 笔记.txt"
        self.note.write_bytes(b"original\n")

    def test_component_walk_and_crt_descriptors(self):
        with win.walk(self.shared) as directory:
            with win.child(directory, self.note.name) as opened:
                original_id = opened.identity()
                fd = opened.detach_fd()
                with os.fdopen(fd, "rb") as source:
                    self.assertEqual(source.read(), b"original\n")
                    self.assertTrue(stat.S_ISREG(os.fstat(fd).st_mode))
            with win.child(directory, "resume 笔记.txt") as alias:
                self.assertEqual(original_id, alias.identity())
        with win.walk(self.shared) as directory:
            fd = directory.detach_fd()
            try:
                self.assertTrue(stat.S_ISDIR(os.fstat(fd).st_mode))
            finally:
                os.close(fd)

    def test_junction_cannot_be_followed_at_any_component(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("synthetic outside marker")
        junction = self.shared / "junction"
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            check=True,
            capture_output=True,
        )
        try:
            with win.walk(self.shared) as directory, self.assertRaises(OSError):
                win.child(directory, "junction", directory=True)
            with self.assertRaises(OSError), win.walk(junction):
                self.fail("A junction was followed.")
        finally:
            junction.rmdir()

    def test_hardlink_is_refused(self):
        linked = self.shared / "linked.txt"
        os.link(self.note, linked)
        with win.walk(self.shared) as directory:
            for name in (self.note.name, linked.name):
                with self.subTest(name=name), self.assertRaises(OSError):
                    win.child(directory, name)

    def test_held_directory_cannot_be_moved_away(self):
        with win.walk(self.shared) as directory:
            original_id = directory.identity()
            with self.assertRaises(OSError):
                self.shared.rename(self.root / "moved")
            with win.child(directory, self.note.name) as opened:
                self.assertEqual(opened.info().size_low, len(b"original\n"))
            self.assertEqual(directory.identity(), original_id)
        self.shared.rename(self.root / "moved")
        self.assertTrue((self.root / "moved" / self.note.name).exists())

    def test_atomic_replace_keeps_old_handle_usable(self):
        stage = self.shared / "stage.txt"
        stage.write_bytes(b"replacement\n")
        with win.walk(self.shared) as directory, win.child(directory, self.note.name) as before:
            old_id = before.identity()
            fd = before.detach_fd()
            with os.fdopen(fd, "rb") as source:
                with win.child(directory, stage.name, delete=True) as replacement:
                    new_id = replacement.identity()
                    win.rename(replacement, directory, self.note.name, replace=True)
                self.assertEqual(source.read(), b"original\n")
                with win.child(directory, self.note.name) as current:
                    self.assertEqual(current.identity(), new_id)
                    self.assertNotEqual(current.identity(), old_id)
        self.assertEqual(self.note.read_bytes(), b"replacement\n")
        self.assertFalse(stage.exists())

    def test_exclusive_publication_does_not_overwrite(self):
        stage = self.shared / "stage.txt"
        stage.write_bytes(b"replacement\n")
        with (
            win.walk(self.shared) as directory,
            win.child(directory, stage.name, delete=True) as source,
        ):
            with self.assertRaises(FileExistsError):
                win.rename(source, directory, self.note.name)
            win.rename(source, directory, "new.txt")
        self.assertEqual(self.note.read_bytes(), b"original\n")
        self.assertEqual((self.shared / "new.txt").read_bytes(), b"replacement\n")

    def test_special_namespaces_and_aliases_are_refused(self):
        for name in (
            "..",
            ".",
            "",
            "a/b",
            "a\\b",
            "note:stream",
            "NUL.txt",
            "COM1",
            "LPT².txt",
            "name.",
            "name ",
            "SHORT~1",
            "a\x00b",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                win.component(name)
        for path in ("relative", "C:relative", "\\\\server\\share", "\\\\?\\C:\\", "\\\\.\\C:\\"):
            with self.subTest(path=path), self.assertRaises(ValueError), win.walk(path):
                self.fail("Unexpected namespace accepted.")

    def test_private_acl_is_present_at_creation_and_preserved(self):
        descriptor = security.private_descriptor()
        self.assertEqual(security.owner(descriptor), security.user_sid())
        self.assertTrue(security.is_private(descriptor))
        with (
            win.walk(self.shared) as directory,
            win.child(
                directory,
                "private.txt",
                write=True,
                create=True,
                security=descriptor,
            ) as file,
        ):
            before = security.snapshot(file.value)
            self.assertTrue(security.is_private(before))
            security.restore_dacl(file.value, before)
            self.assertTrue(security.is_private(security.snapshot(file.value)))

    def test_acl_allowing_other_users_is_not_private(self):
        sid = security.user_sid()
        descriptor = security.descriptor_from_sddl(f"O:{sid}D:P(A;;FA;;;{sid})(A;;GR;;;WD)")
        self.assertEqual(security.owner(descriptor), sid)
        self.assertFalse(security.is_private(descriptor))
        with (
            win.walk(self.shared) as directory,
            win.child(
                directory,
                "public-fixture.txt",
                write=True,
                create=True,
                security=descriptor,
            ) as file,
        ):
            self.assertFalse(security.is_private(security.snapshot(file.value)))

    def test_replacement_retains_inherited_and_protected_file_acls(self):
        with win.walk(self.shared) as directory:
            for name, descriptor in (
                (self.note.name, None),
                ("protected.txt", security.private_descriptor()),
            ):
                with self.subTest(name=name):
                    with win.child(
                        directory, name, create=descriptor is not None, security=descriptor
                    ) as original:
                        before = security.snapshot(original.value)
                    with win.child(
                        directory,
                        name + ".stage",
                        write=True,
                        create=True,
                        security=security.private_descriptor(),
                    ) as staged:
                        security.restore_dacl(staged.value, before)
                        self.assertEqual(
                            security.to_sddl(security.snapshot(staged.value)),
                            security.to_sddl(before),
                        )

    def test_null_or_foreign_owner_acl_is_not_private(self):
        for sddl in ("O:SYD:P(A;;FA;;;SY)", f"O:{security.user_sid()}D:NO_ACCESS_CONTROL"):
            with self.subTest(sddl=sddl):
                self.assertFalse(security.is_private(security.descriptor_from_sddl(sddl)))


if __name__ == "__main__":
    unittest.main()
