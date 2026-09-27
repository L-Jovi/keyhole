"""Private runtime configuration: tunnel id, key reference and the accepted client version."""

import contextlib
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from . import file_ops as fileio
from .errors import KeyholeError, require
from .filesystem import local_path_error
from .state import StateStore

TUNNEL_ID = re.compile(r"tunnel_[a-z0-9]{32}")
DIR_FLAGS = os.O_RDONLY | fileio.O_DIRECTORY | fileio.O_NOFOLLOW


def validate_tunnel_id(value: str) -> None:
    require(
        TUNNEL_ID.fullmatch(value) is not None,
        "invalid_tunnel_id",
        "Expected the tunnel id shown in OpenAI Platform > Settings > Tunnels: "
        "`tunnel_` followed by 32 lowercase letters or digits.",
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
    fd = fileio.open(path.anchor, DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            try:
                try:
                    child = fileio.open(part, DIR_FLAGS, dir_fd=fd)
                except FileNotFoundError:
                    fileio.mkdir(part, mode=0o700, dir_fd=fd)
                    child = fileio.open(part, DIR_FLAGS, dir_fd=fd)
            except OSError as exc:
                raise local_path_error(path, exc) from exc
            os.close(fd)
            fd = child
        require(
            fileio.private(fd, 0o700),
            "state_permissions",
            f"{path} must be private and owned by you (0700 on POSIX; owner-only ACL on Windows).",
        )
        return fd
    except BaseException:
        os.close(fd)
        raise


def write_exclusive(fd: int, name: str, content: bytes) -> None:
    child = fileio.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | fileio.O_NOFOLLOW, 0o600, dir_fd=fd
    )
    with os.fdopen(child, "wb") as out:
        out.write(content)
        out.flush()
        fileio.fsync(out.fileno())


def save_runtime(path: Path, tunnel_id: str, key: str, client_version: str) -> dict:
    """First-time setup: store the key privately and record the tunnel id and client version."""
    validate_tunnel_id(tunnel_id)
    validate_key(key)
    fd = open_private_directory(path)
    key_created = False
    try:
        for name in ("runtime.key", "runtime.json"):
            try:
                fileio.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise KeyholeError(
                "already_configured",
                f"{path / name} already exists. Use `keyhole setup --rotate-key` or "
                "`keyhole setup --accept-client-version`. Rerun `keyhole setup` to continue safely.",
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
        fileio.fsync(fd)
    except BaseException:
        if key_created:
            fileio.unlink("runtime.key", dir_fd=fd)
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
        tmp = f".runtime.key-{uuid4()}.tmp"
        try:
            write_exclusive(directory, tmp, (key + "\n").encode("ascii"))
            fileio.replace(tmp, "runtime.key", src_dir_fd=directory, dst_dir_fd=directory)
            fileio.fsync(directory)
        finally:
            with contextlib.suppress(FileNotFoundError):
                fileio.unlink(tmp, dir_fd=directory)
    return {"rotated": True}


def setup_config(store: StateStore) -> dict:
    """Read partial configuration without reading the secret or resetting other state."""
    try:
        config = store.read("runtime.json")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise KeyholeError(
            "runtime_config",
            "runtime.json is not valid UTF-8 JSON. Keep it for local review; do not delete your state.",
        ) from exc
    except KeyholeError as exc:
        if exc.code == "not_configured":
            return {}
        raise
    require(
        isinstance(config, dict) and config.get("schema_version") == 1,
        "runtime_config",
        "Invalid runtime.json; keep it for local review, do not delete your state.",
    )
    if config.get("tunnel_id") is not None:
        require(isinstance(config["tunnel_id"], str), "runtime_config", "Invalid tunnel id type.")
        validate_tunnel_id(config["tunnel_id"])
    if config.get("runtime_api_key_ref") is not None:
        require(
            config["runtime_api_key_ref"] == "file:" + str(store.path / "runtime.key"),
            "runtime_config",
            "The saved key reference belongs to another state directory.",
        )
    return config


def key_present(store: StateStore) -> bool:
    from .runtime import NativeRuntime

    try:
        NativeRuntime(store).check_key()
    except KeyholeError as exc:
        if exc.code == "not_configured":
            return False
        raise
    return True


def complete_setup(
    store: StateStore,
    *,
    expected: dict,
    tunnel_id: str,
    key: str | None,
    client: str,
    version: str,
    source: str,
) -> dict:
    """Merge missing values under the management lock, preserving every unrelated field."""
    validate_tunnel_id(tunnel_id)
    if key is not None:
        validate_key(key)
    require(
        source in ("managed", "external") and Path(client).is_absolute(),
        "runtime_config",
        "The client must have an absolute path and known owner.",
    )
    fd = open_private_directory(store.path)
    os.close(fd)
    with store.lock():
        current = setup_config(store)
        require(
            current == expected, "setup_changed", "Configuration changed during setup. Rerun setup."
        )
        present = key_present(store)
        require(
            not (present and key is not None),
            "setup_changed",
            "A key was saved while setup was waiting. Rerun setup to reuse it or rotate it explicitly.",
        )
        require(present or key is not None, "not_configured", "A runtime key is still needed.")
        # If interrupted after writing the key, the next setup reuses it without displaying it.
        if not present:
            with store.directory() as directory:
                write_exclusive(directory, "runtime.key", (key + "\n").encode("ascii"))
                fileio.fsync(directory)
        updated = {
            **current,
            "schema_version": 1,
            "tunnel_id": tunnel_id,
            "runtime_api_key_ref": "file:" + str(store.path / "runtime.key"),
            "tunnel_client_version": version,
            "tunnel_client_path": client,
            "tunnel_client_source": source,
        }
        if updated != current:
            store.write_json("runtime.json", updated)
    return {
        "configured": True,
        "client": client,
        "client_version": version,
        "client_source": source,
    }
