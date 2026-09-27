"""Windows feasibility evidence only. This is not a Keyhole filesystem backend."""

import argparse
import ctypes
import json
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path


def main():
    if sys.platform != "win32":
        raise SystemExit(
            "Run this probe on Windows; it cannot be simulated by changing sys.platform."
        )
    import msvcrt

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--denied-file", type=Path, required=True)
    args = parser.parse_args()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    class Information(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("created", wintypes.FILETIME),
            ("accessed", wintypes.FILETIME),
            ("written", wintypes.FILETIME),
            ("volume", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD),
            ("index_low", wintypes.DWORD),
        ]

    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(Information)]

    @contextmanager
    def handle(path, *, share=7):
        value = kernel.CreateFileW(str(path), 0x80000000, share, None, 3, 0x02200000, None)
        if value == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield value
        finally:
            kernel.CloseHandle(value)

    def info(value):
        result = Information()
        if not kernel.GetFileInformationByHandle(value, ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        return {
            "identity": [result.volume, result.index_high, result.index_low],
            "links": result.links,
            "reparse": bool(result.attributes & 0x400),
        }

    report = {
        "platform_support": False,
        "os": platform.platform(),
        "python": platform.python_version(),
    }
    report["non_admin"] = not bool(ctypes.windll.shell32.IsUserAnAdmin())
    assert report["non_admin"], "The probe must run as a standard user, not the CI administrator."
    try:
        args.denied_file.read_bytes()
    except PermissionError:
        report["acl_denies_other_user"] = True
    else:
        raise AssertionError("The standard user could read the administrator-owned fixture.")

    with tempfile.TemporaryDirectory(prefix="keyhole-windows-", dir=args.output.parent) as temp:
        root = Path(temp)
        original = root / "Resume 笔记.txt"
        original.write_bytes(b"original\n")
        with handle(original) as first, handle(root / "resume 笔记.txt") as second:
            assert info(first)["identity"] == info(second)["identity"]
        report["case_alias_same_file_id"] = True
        hardlink = root / "hardlink.txt"
        os.link(original, hardlink)
        with handle(original) as first, handle(hardlink) as second:
            assert info(first)["identity"] == info(second)["identity"]
            assert info(first)["links"] == 2
        hardlink.unlink()
        report["hardlink_count_detectable"] = True

        outside = root / "outside"
        outside.mkdir()
        (outside / "file.txt").write_bytes(b"outside the proposed shared root\n")
        shared = root / "shared"
        shared.mkdir()
        junction = shared / "junction"
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            check=True,
            capture_output=True,
        )
        try:
            with handle(junction) as direct:
                assert info(direct)["reparse"]
            with handle(junction / "file.txt") as through, handle(outside / "file.txt") as actual:
                assert info(through)["identity"] == info(actual)["identity"]
            report["final_component_flag_does_not_protect_parent_junction"] = True
        finally:
            junction.rmdir()
        link = shared / "symlink"
        try:
            link.symlink_to(original)
        except OSError as exc:
            report["symlink_probe"] = {"tested": False, "winerror": exc.winerror}
        else:
            with handle(link) as opened:
                assert info(opened)["reparse"]
            link.unlink()
            report["symlink_probe"] = {"tested": True, "reparse_detected": True}

        lock = root / "lock"
        lock.write_bytes(b"x")
        lock_code = """import msvcrt, sys
with open(sys.argv[1], 'r+b') as f:
    try: msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError: sys.exit(23)
"""
        with lock.open("r+b") as held:
            msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, 1)
            assert subprocess.run([sys.executable, "-c", lock_code, str(lock)]).returncode == 23
            msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, 1)
        assert subprocess.run([sys.executable, "-c", lock_code, str(lock)]).returncode == 0
        report["cross_process_lock"] = True

        replacement = root / "replacement"
        replacement.write_bytes(b"replacement\n")
        before_replace = original.read_bytes()
        with handle(original, share=3):
            try:
                os.replace(replacement, original)
            except PermissionError:
                report["replace_requires_delete_sharing"] = True
            else:
                raise AssertionError("Replacement unexpectedly ignored a held handle's share mode.")
            assert original.read_bytes() == before_replace
            assert replacement.read_bytes() == b"replacement\n"
        with handle(original) as held:
            old_id = info(held)["identity"]
            try:
                os.replace(replacement, original)
            except PermissionError as exc:
                # CPython's MoveFileEx path need not provide POSIX replacement of an open target,
                # even with FILE_SHARE_DELETE. Record the gap; never call it platform support.
                assert exc.winerror in (5, 32)
                assert original.read_bytes() == before_replace
                assert replacement.read_bytes() == b"replacement\n"
                report["replace_with_delete_sharing"] = {
                    "supported": False,
                    "winerror": exc.winerror,
                    "both_files_unchanged": True,
                }
            else:
                with handle(original) as current:
                    assert info(current)["identity"] != old_id
                report["replace_with_delete_sharing"] = {"supported": True}
            assert info(held)["identity"] == old_id
        if replacement.exists():
            os.replace(replacement, original)
        assert original.read_bytes() == b"replacement\n"
        report["replace_after_handles_closed"] = True

        journal = root / "journal.sqlite3"
        crash_code = """import os, sqlite3, sys
from pathlib import Path
p, db = Path(sys.argv[1]), sys.argv[2]
with sqlite3.connect(db) as c:
    c.execute('PRAGMA synchronous=FULL')
    c.execute('CREATE TABLE recovery (old BLOB)')
    c.execute('INSERT INTO recovery VALUES (?)', (p.read_bytes(),))
p.write_bytes(b'new bytes after durable journal')
os._exit(31)
"""
        before = original.read_bytes()
        child = subprocess.run([sys.executable, "-c", crash_code, str(original), str(journal)])
        assert child.returncode == 31
        with sqlite3.connect(journal) as db:
            (previous,) = db.execute("SELECT old FROM recovery").fetchone()
        assert previous == before
        replacement.write_bytes(previous)
        os.replace(replacement, original)
        assert original.read_bytes() == before
        report["sqlite_journal_survives_process_exit"] = True

    report["remaining"] = [
        "Race-resistant handle-relative traversal and writes at every path component",
        "Native atomic replacement while retaining the handles used for identity validation",
        "Windows ACL policy for keys, grants, history and managed clients",
        "Reparse tags, alternate streams, device paths, short names and Unicode alias tests",
        "Parser handle inheritance, process-tree cleanup and boot identity backend",
        "Full Keyhole regression suite and Windows 11 desktop ChatGPT acceptance",
    ]
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
