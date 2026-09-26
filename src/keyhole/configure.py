"""Private runtime configuration: tunnel id, key reference and the accepted client version."""

import contextlib
import json
import os
import re
import stat
from pathlib import Path

from .errors import KeyholeError, require
from .filesystem import local_path_error
from .state import StateStore

TUNNEL_ID = re.compile(r"tunnel_[A-Za-z0-9_-]{8,128}")
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def validate_tunnel_id(value: str) -> None:
    require(
        TUNNEL_ID.fullmatch(value) is not None,
        "invalid_tunnel_id",
        "Expected the tunnel_... id shown in OpenAI Platform > Settings > Tunnels.",
    )


def validate_key(value: str) -> None:
    require(
        value.startswith("sk-")
        and not value.startswith("sk-admin-")
        and 24 <= len(value) <= 2048
        and all(33 <= ord(c) <= 126 for c in value),
        "invalid_key",
        "Expected a runtime API key with Tunnels Read + Use permission, not an admin key or a placeholder.",
    )


def open_private_directory(path: Path) -> int:
    """Create the state directory if needed, refusing symlinks at every component."""
    require(
        path.is_absolute() and ".." not in path.parts,
        "invalid_state_dir",
        "The state directory must be an absolute, normalized path.",
    )
    fd = os.open("/", DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            try:
                try:
                    child = os.open(part, DIR_FLAGS, dir_fd=fd)
                except FileNotFoundError:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                    child = os.open(part, DIR_FLAGS, dir_fd=fd)
            except OSError as exc:
                raise local_path_error(path, exc) from exc
            os.close(fd)
            fd = child
        st = os.fstat(fd)
        require(
            st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o700,
            "state_permissions",
            f"{path} must be owned by you with mode 0700.",
        )
        return fd
    except BaseException:
        os.close(fd)
        raise


def write_exclusive(fd: int, name: str, content: bytes) -> None:
    child = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(child, "wb") as out:
        out.write(content)
        out.flush()
        os.fsync(out.fileno())


def save_runtime(path: Path, tunnel_id: str, key: str, client_version: str) -> dict:
    """First-time setup: store the key privately and record the tunnel id and client version."""
    validate_tunnel_id(tunnel_id)
    validate_key(key)
    fd = open_private_directory(path)
    key_created = False
    try:
        for name in ("runtime.key", "runtime.json"):
            try:
                os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise KeyholeError(
                "already_configured",
                f"{path / name} already exists. Use `keyhole setup --rotate-key` or "
                "`keyhole setup --accept-client-version`, or remove the state directory deliberately.",
            )
        write_exclusive(fd, "runtime.key", (key + "\n").encode("ascii"))
        key_created = True
        config = {
            "schema_version": 1,
            "tunnel_id": tunnel_id,
            "runtime_api_key_ref": "file:" + str(path / "runtime.key"),
            "tunnel_client_version": client_version,
        }
        write_exclusive(fd, "runtime.json", (json.dumps(config, indent=2) + "\n").encode())
        os.fsync(fd)
    except BaseException:
        if key_created:
            os.unlink("runtime.key", dir_fd=fd)
        raise
    finally:
        os.close(fd)
    return {"state_dir": str(path), "tunnel_id": tunnel_id, "tunnel_client_version": client_version}


def accept_client_version(store: StateStore, version: str) -> dict:
    """Record a reviewed tunnel-client upgrade; the key file is left untouched."""
    config = store.read("runtime.json")
    previous = config.get("tunnel_client_version")
    config["tunnel_client_version"] = version
    store.write_json("runtime.json", config)
    return {"previous_version": previous, "tunnel_client_version": version}


def rotate_key(store: StateStore, key: str) -> dict:
    """Replace runtime.key atomically; runtime.json keeps referencing the same path."""
    validate_key(key)
    store.read("runtime.json")
    with store.directory() as directory:
        tmp = ".runtime.key.tmp"
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp, dir_fd=directory)
        write_exclusive(directory, tmp, (key + "\n").encode("ascii"))
        os.replace(tmp, "runtime.key", src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    return {"rotated": True}
