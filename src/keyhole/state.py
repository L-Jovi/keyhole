"""Private atomic grants, local locking, and explicit per-boot activation."""

import contextlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from . import file_ops as fileio
from .errors import KeyholeError, require
from .filesystem import absolute_directory

DEFAULT_STATE = Path.home() / ".config/keyhole"
SETUP_HINT = "Run `keyhole setup` first to store the tunnel id and runtime key."


def command_hint(parts) -> str:
    if sys.platform == "win32":
        return "& " + " ".join("'" + str(part).replace("'", "''") + "'" for part in parts)
    return shlex.join(parts)


def now() -> str:
    return datetime.now(UTC).isoformat()


def boot_id() -> str:
    try:
        if sys.platform == "win32":
            from .windows_process import boot_id as windows_boot_id

            value = windows_boot_id()
        elif sys.platform == "darwin":
            value = subprocess.check_output(
                ["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=3,
            ).strip()
        else:
            value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise KeyholeError(
            "boot_identity_unavailable",
            "Could not read the boot identity (sysctl kern.bootsessionuuid on macOS, "
            "/proc/sys/kernel/random/boot_id on Linux). Run keyhole from a normal terminal; "
            "sandboxes may block it. Sharing stays closed.",
        ) from exc
    require(bool(value), "boot_identity_unavailable", "Empty boot identity; sharing stays closed.")
    return value


def empty_state() -> dict:
    return {
        "schema_version": 1,
        "generation": str(uuid4()),
        "boot_id": boot_id(),
        "updated_at": now(),
        "workspaces": [],
    }


def validate_state(value: dict) -> None:
    require(
        value.get("schema_version") == 1 and isinstance(value.get("workspaces"), list),
        "state_invalid",
        "Invalid grant configuration.",
    )
    require(
        isinstance(value.get("generation"), str)
        and re.fullmatch(r"[0-9a-f-]{36}", value["generation"]) is not None,
        "state_invalid",
        "Invalid authorization generation.",
    )
    require(
        len(value["workspaces"]) <= 64,
        "state_invalid",
        "At most 64 configured workspaces are supported.",
    )
    names: set[str] = set()
    paths: list[Path] = []
    for item in value["workspaces"]:
        require(
            isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and re.fullmatch(r"[^/\\\x00-\x1f]{1,80}", item["name"]) is not None
            and item["name"] not in (".", ".."),
            "state_invalid",
            "Invalid workspace name.",
        )
        require(
            item["name"].casefold() not in names,
            "state_invalid",
            "Workspace aliases must be unique ignoring case.",
        )
        names.add(item["name"].casefold())
        path = Path(item.get("path", ""))
        require(
            path.is_absolute() and ".." not in path.parts,
            "state_invalid",
            "Invalid configured path.",
        )
        require(
            not any(path == p or path in p.parents or p in path.parents for p in paths),
            "overlapping_roots",
            "Parent and child directory grants cannot overlap.",
        )
        paths.append(path)
        require(
            type(item.get("device")) is int
            and type(item.get("inode")) is int
            and type(item.get("enabled")) is bool,
            "state_invalid",
            "Invalid root identity or activation flag.",
        )
        require(
            item.get("access", "ro") in ("ro", "rw"),
            "state_invalid",
            "Invalid workspace access mode.",
        )
        require(
            item.get("recovery", "on") in ("on", "off"),
            "state_invalid",
            "Invalid recovery setting.",
        )
        exclusions = item.get("exclusions", [])
        require(
            isinstance(exclusions, list)
            and all(isinstance(x, str) and len(x) <= 200 for x in exclusions),
            "state_invalid",
            "Invalid local exclusion patterns.",
        )


class StateStore:
    def __init__(self, path: Path = DEFAULT_STATE):
        self.path = path

    def command(self, *args: str, client: str | None = None) -> str:
        parts = ["keyhole"]
        if self.path != DEFAULT_STATE:
            parts.extend(["--state-dir", str(self.path)])
        if client:
            parts.extend(["--tunnel-client", client])
        return command_hint([*parts, *args])

    @contextmanager
    def directory(self):
        # Only a missing state directory means "not configured"; errors raised by the caller's
        # block (for example a missing directory it tries to share) must keep their own code.
        try:
            opened = absolute_directory(self.path)
            fd, _walk = opened.__enter__()
        except KeyholeError as exc:
            if exc.code == "path_missing":
                raise KeyholeError("not_configured", SETUP_HINT) from exc
            raise
        try:
            require(
                fileio.private(fd, 0o700),
                "state_permissions",
                "Private state must be owned by the current user (0700 on POSIX; private ACL on Windows).",
            )
            yield fd
        except BaseException:
            if not opened.__exit__(*sys.exc_info()):
                raise
        else:
            opened.__exit__(None, None, None)

    def read(self, name: str = "grants.json", *, missing: bool = False) -> dict:
        with self.directory() as directory:
            try:
                fd = fileio.open(
                    name, os.O_RDONLY | fileio.O_NOFOLLOW | fileio.O_NONBLOCK, dir_fd=directory
                )
            except FileNotFoundError:
                if missing:
                    return empty_state()
                if name == "runtime.json":
                    raise KeyholeError("not_configured", SETUP_HINT) from None
                raise KeyholeError("offline", "No directory authorization is active.") from None
            with os.fdopen(fd, encoding="utf-8") as source:
                st = fileio.fstat(source.fileno())
                require(
                    stat.S_ISREG(st.st_mode)
                    and fileio.private(source.fileno(), 0o600)
                    and st.st_nlink == 1
                    and st.st_size <= 128 * 1024,
                    "state_permissions",
                    "Private configuration must be an owned single-link regular file with mode 0600 "
                    "and bounded size.",
                )
                result = json.load(source)
            if name == "grants.json":
                validate_state(result)
            return result

    def write(self, value: dict) -> None:
        validate_state(value)
        self.write_json("grants.json", value)

    def write_json(self, name: str, value: dict) -> None:
        """Replace one private JSON file atomically, never following links."""
        blob = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
        with self.directory() as directory:
            tmp = f".{name}-{uuid4()}.tmp"
            fd = fileio.open(
                tmp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | fileio.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            try:
                with os.fdopen(fd, "wb") as out:
                    out.write(blob)
                    out.flush()
                    fileio.fsync(out.fileno())
                fileio.replace(tmp, name, src_dir_fd=directory, dst_dir_fd=directory)
                fileio.fsync(directory)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    fileio.unlink(tmp, dir_fd=directory)

    @contextmanager
    def lock(self, timeout: float = 10):
        with self.directory() as directory:
            fd = fileio.open(
                "management.lock",
                os.O_RDWR | os.O_CREAT | fileio.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            try:
                st = fileio.fstat(fd)
                require(
                    stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and fileio.private(fd, 0o600),
                    "state_permissions",
                    "Unsafe management lock.",
                )
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        fileio.lock(fd)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.05)
                yield
            except BlockingIOError as exc:
                raise KeyholeError(
                    "manager_busy", "Another local authorization change is running."
                ) from exc
            finally:
                os.close(fd)
