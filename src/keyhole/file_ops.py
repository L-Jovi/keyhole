"""Small descriptor API shared by the POSIX and native NTFS boundaries.

Windows paths are never reconstructed to perform an operation: each name is
opened relative to a held parent handle. CRT descriptors only transport those
handles to Python's stream and SQLite-facing code.
"""

import os
import stat as stat_module
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

WINDOWS = sys.platform == "win32"
O_DIRECTORY = getattr(os, "O_DIRECTORY", 1 << 24)
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 1 << 25)
O_NONBLOCK = getattr(os, "O_NONBLOCK", 1 << 26)
O_CLOEXEC = getattr(os, "O_CLOEXEC", 1 << 27)
DIR_FLAGS = os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC

if WINDOWS:
    import msvcrt

    from . import windows_files as win
    from . import windows_security as security
else:
    import fcntl


def handle(fd):
    """Borrow a descriptor's handle; the descriptor remains its owner."""
    return win.Handle(msvcrt.get_osfhandle(fd))


def open(path, flags, mode=0o600, *, dir_fd=None):
    if not WINDOWS:
        return os.open(path, flags, mode, dir_fd=dir_fd)
    if dir_fd is None:
        path = Path(path)
        if str(path) == path.anchor:
            return win.volume(path.anchor).detach_fd()
        with win.walk(path.parent) as parent:
            return _open(parent, path.name, flags, mode)
    return _open(handle(dir_fd), str(path), flags, mode)


def _open(parent, name, flags, mode):
    directory = bool(flags & O_DIRECTORY)
    write = bool(flags & (os.O_WRONLY | os.O_RDWR))
    create = bool(flags & os.O_CREAT)
    exclusive = bool(flags & os.O_EXCL)
    if flags & (os.O_TRUNC | os.O_APPEND):
        raise ValueError("Truncation and append must not bypass staged publication.")
    kwargs = {"directory": directory, "write": write}
    if create and not exclusive:
        try:
            return win.child(parent, name, **kwargs).detach_fd(writable=write)
        except FileNotFoundError:
            pass
    try:
        opened = win.child(
            parent,
            name,
            **kwargs,
            create=create,
            security=security.private_descriptor() if create else None,
        )
    except FileExistsError:
        if not create or exclusive:
            raise
        opened = win.child(parent, name, **kwargs)
    with opened:
        return opened.detach_fd(writable=write)


def fstat(fd):
    st = os.fstat(fd)
    if not WINDOWS:
        return st
    native = handle(fd)
    info, basic = native.info(), native.basic()
    epoch = 116444736000000000
    # Python <=3.12 calls the birth time st_ctime on Windows. The real NTFS
    # change time also catches ACL and stream changes during version checks.
    return SimpleNamespace(
        st_dev=info.volume,
        st_ino=(info.index_high << 32) | info.index_low,
        st_mode=st.st_mode,
        st_nlink=info.links,
        st_size=(info.size_high << 32) | info.size_low,
        st_mtime_ns=(basic.written - epoch) * 100,
        st_ctime_ns=(basic.changed - epoch) * 100,
    )


def stat(path, *, dir_fd=None, follow_symlinks=False):
    if not WINDOWS:
        return os.stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
    if follow_symlinks:
        raise ValueError("Following links is not permitted.")
    if dir_fd is None:
        path = Path(path)
        with win.walk(path.parent) as parent:
            return _stat(parent, path.name)
    return _stat(handle(dir_fd), str(path))


def _stat(parent, name):
    with win.child(parent, name, directory=None) as item:
        fd = item.detach_fd()
        try:
            return fstat(fd)
        finally:
            os.close(fd)


def owned(fd):
    if WINDOWS:
        return security.owner(security.snapshot(handle(fd).value)) == security.user_sid()
    return fstat(fd).st_uid == os.getuid()


def private(fd, mode):
    if WINDOWS:
        return security.is_private(security.snapshot(handle(fd).value))
    st = fstat(fd)
    return st.st_uid == os.getuid() and stat_module.S_IMODE(st.st_mode) == mode


def permissions(fd):
    result = {"mode": stat_module.S_IMODE(fstat(fd).st_mode)}
    if WINDOWS:
        result["windows_acl"] = security.to_sddl(security.snapshot(handle(fd).value))
    return result


def private_permissions(*, directory=False):
    if WINDOWS:
        return {
            "mode": 0o777 if directory else 0o666,
            "windows_acl": security.to_sddl(security.private_descriptor()),
        }
    return {"mode": 0o700 if directory else 0o600}


def apply_permissions(fd, image):
    if WINDOWS:
        descriptor = security.descriptor_from_sddl(image["windows_acl"])
        security.restore_dacl(handle(fd).value, descriptor)
        win.readonly(handle(fd), not image["mode"] & stat_module.S_IWUSR)
    else:
        os.fchmod(fd, image["mode"] & 0o777)


def check_mutable(name, *, directory):
    if WINDOWS:
        with win.child(handle(directory), name, write=True) as source:
            win.mutation_supported(source)


def mkdir(path, mode=0o700, *, dir_fd=None):
    if not WINDOWS:
        return os.mkdir(path, mode, dir_fd=dir_fd)
    if dir_fd is None:
        raise ValueError("Directory creation requires a held parent.")
    with win.child(
        handle(dir_fd),
        str(path),
        directory=True,
        create=True,
        security=security.private_descriptor(),
    ):
        pass


def rename(src, dst, *, src_dir_fd, dst_dir_fd, replace=False):
    if not WINDOWS:
        operation = os.replace if replace else os.rename
        return operation(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
    with win.child(handle(src_dir_fd), str(src), directory=None, delete=True) as source:
        win.rename(source, handle(dst_dir_fd), str(dst), replace=replace)


def replace(src, dst, *, src_dir_fd, dst_dir_fd):
    return rename(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd, replace=True)


def publish_new(src, dst, *, directory):
    if WINDOWS:
        rename(src, dst, src_dir_fd=directory, dst_dir_fd=directory)
    else:
        os.link(src, dst, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        os.unlink(src, dir_fd=directory)


def unlink(path, *, dir_fd):
    if not WINDOWS:
        return os.unlink(path, dir_fd=dir_fd)
    with win.child(handle(dir_fd), str(path), delete=True) as item:
        win.remove(item)


def rmdir(path, *, dir_fd):
    if not WINDOWS:
        return os.rmdir(path, dir_fd=dir_fd)
    with win.child(handle(dir_fd), str(path), directory=True, delete=True) as item:
        win.remove(item)


def fsync(fd):
    # Windows does not support FlushFileBuffers on these read-only directory
    # handles. File content is flushed before rename; metadata power-loss
    # durability is left to NTFS. We do not claim a directory-fsync equivalent.
    if WINDOWS and stat_module.S_ISDIR(fstat(fd).st_mode):
        return
    os.fsync(fd)


def listdir(fd):
    return list(win.names(handle(fd))) if WINDOWS else os.listdir(fd)


class WindowsEntry:
    def __init__(self, parent, name):
        self.parent, self.name = parent, name

    def stat(self, *, follow_symlinks=False):
        return stat(self.name, dir_fd=self.parent, follow_symlinks=follow_symlinks)


@contextmanager
def scandir(fd):
    if WINDOWS:
        yield (WindowsEntry(fd, name) for name in win.names(handle(fd)))
    else:
        with os.scandir(fd) as iterator:
            yield iterator


def remove_staging(name, *, directory):
    """Delete only flat installer staging entries through the held parent."""
    fd = open(name, DIR_FLAGS, dir_fd=directory)
    try:
        for child in listdir(fd):
            unlink(child, dir_fd=fd)
    finally:
        os.close(fd)
    rmdir(name, dir_fd=directory)


def canonical_path(fd):
    if WINDOWS:
        return Path(win.final_path(handle(fd)))
    if hasattr(fcntl, "F_GETPATH"):
        raw = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024))
        return Path(os.fsdecode(raw.rstrip(b"\0")))
    try:
        return Path(os.readlink(f"/proc/self/fd/{fd}"))
    except OSError:
        return None


def lock(fd):
    if WINDOWS:
        # msvcrt.locking locks one byte starting at the current position.
        # LK_NBLCK is non-blocking; closing the descriptor releases the lock.
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 33 or exc.errno in (13, 36):
                raise BlockingIOError from exc
            raise
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
