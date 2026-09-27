"""Descriptor-relative access with no-follow traversal and identity rechecks."""

import errno
import hashlib
import os
import stat
import sys
from contextlib import contextmanager
from pathlib import Path

from . import file_ops as fileio
from .errors import KeyholeError, require
from .policy import DIR_LIMIT, FILE_LIMIT, check_relative, excluded

DIR_FLAGS = os.O_RDONLY | fileio.O_DIRECTORY | fileio.O_NOFOLLOW | fileio.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | fileio.O_NOFOLLOW | fileio.O_NONBLOCK | fileio.O_CLOEXEC


def identity(st: os.stat_result) -> tuple:
    return st.st_dev, st.st_ino


def version(st: os.stat_result) -> tuple:
    return (
        st.st_dev,
        st.st_ino,
        st.st_mode,
        st.st_nlink,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
    )


canonical_path = fileio.canonical_path


def local_path_error(path: Path, exc: OSError) -> KeyholeError:
    """Translate a failed local directory walk into an error the operator can act on."""
    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
        # Linux reports ELOOP for a link under O_NOFOLLOW, macOS reports ENOTDIR; name the link.
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current = current / part
            try:
                if stat.S_ISLNK(os.lstat(current).st_mode):
                    return KeyholeError(
                        "symlink_in_path",
                        f"{current} is a symbolic link. Use the physical path (run `pwd -P` "
                        "inside it) or choose a directory that is not behind a link.",
                    )
            except OSError:
                break
        if exc.errno == errno.ENOTDIR:
            return KeyholeError(
                "not_a_directory", f"{path} is not a directory, or a parent is a file."
            )
        return KeyholeError("symlink_in_path", f"{path} contains a symbolic link.")
    if exc.errno == errno.ENOENT:
        return KeyholeError("path_missing", f"{path} does not exist.")
    if exc.errno in (errno.EACCES, errno.EPERM):
        hint = (
            "On macOS, allow your terminal in System Settings > Privacy & Security > Files and Folders."
            if sys.platform == "darwin"
            else "Check ownership, directory search permissions and any ACL or sandbox policy."
        )
        return KeyholeError(
            "permission_denied",
            f"{path} is not accessible ({exc.strerror}). {hint}",
        )
    return KeyholeError("local_failure", f"{path}: {exc.strerror}")


class Walk:
    def __init__(self):
        self.fds: list[int] = []
        self.edges: list[tuple[int, str, int]] = []

    def absolute(self, path: Path) -> int:
        require(
            path.is_absolute() and ".." not in path.parts,
            "invalid_path",
            "An absolute normalized local root is required.",
        )
        self.fds.append(fileio.open(path.anchor, DIR_FLAGS))
        for part in path.parts[1:]:
            self.child(part, directory=True)
        return self.fds[-1]

    def child(self, name: str, *, directory: bool) -> int:
        parent = self.fds[-1]
        fd = fileio.open(name, DIR_FLAGS if directory else FILE_FLAGS, dir_fd=parent)
        self.fds.append(fd)
        self.edges.append((parent, name, fd))
        return fd

    def validate(self) -> None:
        for parent, name, fd in self.edges:
            current = fileio.stat(name, dir_fd=parent, follow_symlinks=False)
            require(
                not stat.S_ISLNK(current.st_mode) and identity(current) == identity(os.fstat(fd)),
                "path_changed",
                "The directory or file was replaced during access; retry after local review.",
            )

    def close(self) -> None:
        for fd in reversed(self.fds):
            os.close(fd)


@contextmanager
def absolute_directory(path: Path):
    """Open a local directory component by component for the CLI; errors name the cause."""
    walk = Walk()
    try:
        try:
            fd = walk.absolute(path)
        except OSError as exc:
            raise local_path_error(path, exc) from exc
        walk.validate()
        yield fd, walk
        walk.validate()
    finally:
        walk.close()


class SafeFS:
    def __init__(self, grant: dict):
        self.grant = grant
        self.extra = tuple(grant.get("exclusions", []))

    @contextmanager
    def open(self, relative: str, *, directory: bool = False):
        parts = check_relative(relative, self.extra)
        require(directory or bool(parts), "invalid_path", "A file path is required.")
        walk = Walk()
        try:
            root = walk.absolute(Path(self.grant["path"]))
            require(
                identity(os.fstat(root)) == (self.grant["device"], self.grant["inode"]),
                "root_changed",
                "The approved root was replaced or is unavailable; local reauthorization is required.",
            )
            fd = root
            for i, part in enumerate(parts):
                fd = walk.child(part, directory=directory or i < len(parts) - 1)
            st = os.fstat(fd)
            if not directory:
                require(
                    stat.S_ISREG(st.st_mode) and st.st_nlink == 1,
                    "unsafe_file",
                    "Only single-link regular files may be read.",
                )
            walk.validate()
            yield fd, st, walk
            walk.validate()
        except OSError as exc:
            raise KeyholeError(
                "path_unavailable",
                "The path is absent, inaccessible, a link, or no longer matches its authorized location.",
            ) from exc
        finally:
            walk.close()

    def available(self) -> bool:
        with self.open(".", directory=True):
            return True

    def metadata(self, path: str) -> dict:
        with self.open(path) as (fd, st, walk):
            return metadata(st)

    def read(self, path: str, limit: int = FILE_LIMIT) -> tuple[bytes, dict]:
        with self.open(path) as (fd, before, walk):
            require(
                before.st_size <= limit,
                "file_too_large",
                f"Source exceeds the {limit}-byte reading limit.",
            )
            chunks, total = [], 0
            while True:
                chunk = os.read(fd, min(1024 * 1024, limit + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                require(total <= limit, "file_too_large", "Source grew beyond the reading limit.")
            after = os.fstat(fd)
            require(
                version(before) == version(after) and after.st_nlink == 1,
                "content_changed",
                "The source changed while being read; retry.",
            )
            walk.validate()
            data = b"".join(chunks)
            result = metadata(after)
            result["sha256"] = hashlib.sha256(data).hexdigest()
            return data, result

    def entries(self, path: str) -> tuple[list[dict], int]:
        parts = check_relative(path, self.extra)
        with self.open(path, directory=True) as (fd, before, walk):
            items, omitted, seen = [], 0, 0
            with fileio.scandir(fd) as entries:
                for entry in entries:
                    seen += 1
                    require(
                        seen <= DIR_LIMIT,
                        "directory_budget",
                        f"Directory exceeds {DIR_LIMIT} entries; address a narrower known path.",
                    )
                    child_parts = parts + (entry.name,)
                    if excluded(child_parts, self.extra):
                        omitted += 1
                        continue
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except (OSError, ValueError):
                        omitted += 1
                        continue
                    is_dir = stat.S_ISDIR(st.st_mode)
                    if not is_dir and (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1):
                        omitted += 1
                        continue
                    items.append(
                        {
                            "name": entry.name,
                            "path": "/".join(child_parts),
                            "type": "directory" if is_dir else "file",
                            **metadata(st),
                        }
                    )
            require(
                version(before) == version(os.fstat(fd)),
                "content_changed",
                "The directory changed during enumeration; retry.",
            )
            return sorted(items, key=lambda e: e["name"]), omitted


def metadata(st: os.stat_result) -> dict:
    return {
        "size_bytes": st.st_size,
        "modified_ns": st.st_mtime_ns,
        "changed_ns": st.st_ctime_ns,
        "device": st.st_dev,
        "inode": st.st_ino,
        "links": st.st_nlink,
    }
