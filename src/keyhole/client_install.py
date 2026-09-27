"""Opt-in installation of an unmodified, checksum-pinned official client bundle."""

import contextlib
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from uuid import uuid4

from . import file_ops as fileio
from .configure import open_private_directory
from .errors import KeyholeError, require
from .filesystem import absolute_directory
from .runtime import TESTED_CLIENT_VERSIONS, parse_version
from .state import StateStore

MANIFEST = json.loads(Path(__file__).with_name("client_manifest.json").read_text())
MAX_UNPACKED = 160 * 1024 * 1024


def asset() -> dict:
    machine = platform.machine().lower()
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64", "amd64": "amd64"}.get(machine)
    target = f"{sys.platform}-{arch}"
    require(
        target in MANIFEST["assets"],
        "client_download_unavailable",
        "No verified client download for this OS/architecture. Install the official client yourself "
        "and pass --tunnel-client /absolute/path/to/tunnel-client. This does not add platform support.",
    )
    version = MANIFEST["version"]
    require(
        version in TESTED_CLIENT_VERSIONS,
        "client_download_unavailable",
        "This Keyhole release has no tested automatic client download. Install a reviewed official "
        "client yourself and pass --tunnel-client /absolute/path/to/tunnel-client.",
    )
    name = f"tunnel-client-v{version}-{target}.zip"
    return {
        **MANIFEST["assets"][target],
        "version": version,
        "target": target,
        "name": name,
        "url": f"https://github.com/openai/tunnel-client/releases/download/v{version}/{name}",
    }


def destination(store: StateStore, release: dict) -> Path:
    return store.path / "clients" / release["version"] / release["target"]


class HTTPSOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require(
            urllib.parse.urlsplit(newurl).scheme == "https",
            "client_download_failed",
            "The download redirected away from HTTPS; no client was installed.",
        )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(release: dict, out) -> None:
    """Bound both transfer size and per-read time; never print remote error bodies."""
    digest, size = hashlib.sha256(), 0
    try:
        opener = urllib.request.build_opener(HTTPSOnly())
        request = urllib.request.Request(
            release["url"], headers={"User-Agent": "keyhole-installer"}
        )
        with opener.open(request, timeout=30) as response:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                require(
                    size <= release["size"], "client_download_invalid", "Download is too large."
                )
                digest.update(chunk)
                out.write(chunk)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise KeyholeError(
            "client_download_failed",
            "Could not download the official client. Check network, proxy, certificate trust and "
            "free disk space, then rerun setup. The previous client was not replaced.",
        ) from exc
    require(
        size == release["size"] and digest.hexdigest() == release["sha256"],
        "client_checksum_mismatch",
        "The client download did not match the pinned SHA-256/size. Nothing was executed or replaced. "
        "Retry setup; do not bypass this check.",
    )
    out.seek(0)


def extract(archive, directory: int, release: dict) -> None:
    stem = release["name"].removesuffix(".zip")
    expected = {
        "tunnel-client",
        "cloudflared",
        "cloudflared-manifest.json",
        "LICENSE",
        "NOTICE",
        f"{stem}-licenses.txt",
        f"{stem}.spdx.json",
    }
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            require(
                len(entries) == len(expected)
                and {entry.filename for entry in entries} == expected
                and sum(entry.file_size for entry in entries) <= MAX_UNPACKED
                and all(
                    not entry.is_dir()
                    and stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG)
                    and not entry.flag_bits & 1
                    for entry in entries
                ),
                "client_archive_invalid",
                "Unexpected files, links or size in the client archive; nothing was installed.",
            )
            for entry in entries:
                mode = 0o700 if entry.filename in ("tunnel-client", "cloudflared") else 0o600
                fd = fileio.open(
                    entry.filename,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | fileio.O_NOFOLLOW,
                    mode,
                    dir_fd=directory,
                )
                with os.fdopen(fd, "wb") as out, bundle.open(entry) as source:
                    shutil.copyfileobj(source, out, length=1024 * 1024)
                    out.flush()
                    fileio.fsync(out.fileno())
            fileio.fsync(directory)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise KeyholeError(
            "client_archive_invalid", "Invalid client ZIP; nothing was installed."
        ) from exc


def install(store: StateStore, release: dict) -> str:
    """Install to a new version directory. Existing installations are never overwritten."""
    target = destination(store, release)
    with tempfile.TemporaryFile() as archive:
        download(release, archive)
        state_fd = open_private_directory(store.path)
        os.close(state_fd)
        parent = open_private_directory(target.parent)
        stage = f".install-{uuid4()}"
        created = False
        try:
            with store.lock(), absolute_directory(target.parent) as (checked, walk):
                require(
                    os.path.samestat(os.fstat(parent), os.fstat(checked)),
                    "path_changed",
                    "The client installation directory changed; retry setup.",
                )
                fileio.mkdir(stage, 0o700, dir_fd=parent)
                created = True
                child = fileio.open(
                    stage, os.O_RDONLY | fileio.O_DIRECTORY | fileio.O_NOFOLLOW, dir_fd=parent
                )
                try:
                    extract(archive, child, release)
                finally:
                    os.close(child)
                candidate = target.parent / stage / "tunnel-client"
                result = subprocess.run(
                    [str(candidate), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                require(
                    result.returncode == 0 and parse_version(result.stdout) == release["version"],
                    "client_verification_failed",
                    "The verified download could not run on this machine. The previous client is unchanged.",
                )
                walk.validate()
                if os.path.lexists(target):
                    # Setup can be cancelled after installation but before credentials are saved.
                    # Reuse only bytes that match the newly verified archive, never a stale receipt.
                    with absolute_directory(target) as (existing, existing_walk):
                        require(
                            fileio.private(existing, 0o700),
                            "state_permissions",
                            "Unsafe managed client directory.",
                        )
                        staged = fileio.open(
                            stage,
                            os.O_RDONLY | fileio.O_DIRECTORY | fileio.O_NOFOLLOW,
                            dir_fd=parent,
                        )
                        try:
                            require(
                                set(fileio.listdir(existing)) == set(fileio.listdir(staged)),
                                "client_install_damaged",
                                "Managed client files differ from the official bundle.",
                            )
                            for name in fileio.listdir(staged):
                                actual = fileio.open(
                                    name,
                                    os.O_RDONLY | fileio.O_NOFOLLOW | fileio.O_NONBLOCK,
                                    dir_fd=existing,
                                )
                                expected = fileio.open(
                                    name, os.O_RDONLY | fileio.O_NOFOLLOW, dir_fd=staged
                                )
                                with (
                                    os.fdopen(actual, "rb") as old,
                                    os.fdopen(expected, "rb") as new,
                                ):
                                    old_stat, new_stat = (
                                        os.fstat(old.fileno()),
                                        os.fstat(new.fileno()),
                                    )
                                    require(
                                        stat.S_ISREG(old_stat.st_mode)
                                        and old_stat.st_nlink == 1
                                        and fileio.private(
                                            old.fileno(),
                                            0o700
                                            if os.name == "posix" and old_stat.st_mode & 0o100
                                            else 0o600,
                                        )
                                        and old_stat.st_mode == new_stat.st_mode
                                        and old_stat.st_size == new_stat.st_size
                                        and hashlib.file_digest(old, "sha256").digest()
                                        == hashlib.file_digest(new, "sha256").digest(),
                                        "client_install_damaged",
                                        "Managed client files differ from the official bundle.",
                                    )
                            existing_walk.validate()
                        finally:
                            os.close(staged)
                    return str(target / "tunnel-client")
                fileio.rename(stage, target.name, src_dir_fd=parent, dst_dir_fd=parent)
                created = False
                fileio.fsync(parent)
        finally:
            if created:
                # Every file in staging was created here; no user directories are removed.
                with contextlib.suppress(FileNotFoundError):
                    fileio.remove_staging(stage, directory=parent)
            os.close(parent)
    return str(target / "tunnel-client")
