"""Thin local management of the official tunnel-client runtime."""

import contextlib
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

from . import file_ops as fileio
from .errors import KeyholeError, require
from .state import StateStore

ALIAS = "keyhole"
PROFILE = "keyhole"
# Versions of the official tunnel-client that this release was verified against end to end.
TESTED_CLIENT_VERSIONS = ("0.0.14",)
TUNNEL_ID = re.compile(r"tunnel_[a-z0-9]{32}")
VERSION = re.compile(r"^\s*v?(\d+\.\d+\.\d+)")
SERVER_MODULE = "keyhole.server"
PARSER_MODULE = "keyhole.parsers"
INSTALL_HINT = (
    "Run `keyhole setup` to install the official tunnel-client, or pass "
    "--tunnel-client /absolute/path/to/tunnel-client. A missing saved path is never replaced by PATH."
)


def environment() -> dict:
    keep = {"HOME", "PATH", "TMPDIR", "LANG", "LC_CTYPE", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"}
    if sys.platform == "win32":
        keep.update(
            {
                "SYSTEMROOT",
                "WINDIR",
                "USERPROFILE",
                "APPDATA",
                "LOCALAPPDATA",
                "TEMP",
                "TMP",
                "COMSPEC",
            }
        )
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env["PYTHONUTF8"] = "1"
    env.update(HEALTH_LISTEN_ADDR="127.0.0.1:0", MCP_STDIO_SEND_INITIALIZED_NOTIFICATION="true")
    return env


def parse_version(text: str) -> str | None:
    match = VERSION.match(text.splitlines()[0] if text else "")
    return match.group(1) if match else None


def owned_processes(state_dir: Path | None = None) -> dict[int, str]:
    """Server and parser processes of this user, optionally limited to one state directory."""
    if sys.platform == "win32":
        from .windows_process import owned_processes as windows_processes

        return windows_processes(state_dir)
    ps = shutil.which("ps") or "/bin/ps"
    listing = subprocess.check_output([ps, "-axo", "pid=,uid=,command="], text=True)
    result = {}
    for line in listing.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3 or fields[1] != str(os.getuid()):
            continue
        try:
            words = shlex.split(fields[2])
        except ValueError:
            continue
        modules = (SERVER_MODULE, PARSER_MODULE)
        if not any(
            words[i : i + 2] == ["-m", module] for module in modules for i in range(len(words))
        ):
            continue
        if state_dir is not None and not any(
            words[i : i + 2] == ["--state-dir", str(state_dir)] for i in range(len(words))
        ):
            continue
        result[int(fields[0])] = fields[2]
    return result


class NativeRuntime:
    def __init__(self, store: StateStore, client: str | None = None):
        self.store = store
        self.explicit_client = client

    @property
    def client(self) -> str | None:
        if self.explicit_client:
            return str(Path(self.explicit_client).expanduser().absolute())
        try:
            config = self.store.read("runtime.json")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise KeyholeError(
                "runtime_config", "runtime.json is not valid UTF-8 JSON. Keep it for local review."
            ) from exc
        except KeyholeError as exc:
            if exc.code != "not_configured":
                raise
        else:
            require(isinstance(config, dict), "runtime_config", "Invalid runtime configuration.")
            if "tunnel_client_path" in config:
                path = config["tunnel_client_path"]
                require(
                    isinstance(path, str) and Path(path).is_absolute(),
                    "runtime_config",
                    "Invalid saved tunnel-client path. Rerun setup.",
                )
                return path
        return shutil.which("tunnel-client")

    # Local checks -----------------------------------------------------------------

    def key_reference(self) -> str:
        return "file:" + str(self.store.path / "runtime.key")

    def client_version(self) -> str:
        require(
            bool(self.client) and Path(self.client).is_file(), "native_client_missing", INSTALL_HINT
        )
        try:
            output = subprocess.check_output(
                [self.client, "--version"], text=True, stderr=subprocess.PIPE, timeout=15
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise KeyholeError(
                "native_client_failed",
                "The selected tunnel-client could not run --version. "
                "Check the executable path, permissions and OS/architecture, then rerun setup.",
            ) from exc
        version = parse_version(output)
        require(
            version is not None,
            "native_client_failed",
            "Unrecognized tunnel-client version output. Select an official tunnel-client executable.",
        )
        return version

    def config(self) -> dict:
        try:
            config = self.store.read("runtime.json")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise KeyholeError(
                "runtime_config", "runtime.json is not valid UTF-8 JSON. Keep it for local review."
            ) from exc
        require(
            isinstance(config, dict)
            and config.get("schema_version") == 1
            and isinstance(config.get("tunnel_id"), str)
            and TUNNEL_ID.fullmatch(config["tunnel_id"]) is not None
            and config.get("runtime_api_key_ref") == self.key_reference()
            and isinstance(config.get("tunnel_client_version"), str),
            "runtime_config",
            "runtime.json is not in the expected format for this state directory. Run "
            f"`{self.store.command('setup', client=self.client)}`.",
        )
        return config

    def check_key(self) -> None:
        with self.store.directory() as directory:
            try:
                fd = fileio.open("runtime.key", fileio.FILE_FLAGS, dir_fd=directory)
            except FileNotFoundError:
                raise KeyholeError(
                    "not_configured",
                    f"runtime.key is missing. Run `{self.store.command('setup', client=self.client)}`.",
                ) from None
            try:
                key = os.fstat(fd)
                require(
                    stat.S_ISREG(key.st_mode) and key.st_nlink == 1 and fileio.private(fd, 0o600),
                    "runtime_key_permissions",
                    "runtime.key must be an owned private single-link regular file "
                    "(0600 on POSIX; private ACL on Windows).",
                )
            finally:
                os.close(fd)

    def preflight(self) -> dict:
        """Check the configuration, credentials and client version before starting."""
        config = self.config()
        self.check_key()
        installed = self.client_version()
        accepted = config["tunnel_client_version"]
        require(
            installed == accepted,
            "client_version_changed",
            f"tunnel-client {installed} is installed, but {accepted} was accepted during setup. "
            "Review the upgrade, then run "
            f"`{self.store.command('setup', '--accept-client-version', client=self.client)}`.",
        )
        return {
            "client": self.client,
            "client_version": installed,
            "client_version_tested": installed in TESTED_CLIENT_VERSIONS,
            "tunnel_id": config["tunnel_id"],
        }

    def checks(self) -> dict:
        """Diagnostics for `keyhole status`; never raises."""
        result: dict = {}
        try:
            result["tunnel_client"] = self.client
            result["client_version"] = self.client_version()
            result["client_version_tested"] = result["client_version"] in TESTED_CLIENT_VERSIONS
        except KeyholeError as exc:
            result["client_error"] = {"code": exc.code, "message": exc.message}
        try:
            config = self.config()
            result["accepted_client_version"] = config["tunnel_client_version"]
            result["tunnel_id"] = config["tunnel_id"]
            self.check_key()
        except KeyholeError as exc:
            result["config_error"] = {"code": exc.code, "message": exc.message}
        result["ok"] = (
            "client_error" not in result
            and "config_error" not in result
            and result.get("client_version") == result.get("accepted_client_version")
        )
        return result

    # Official runtime commands ---------------------------------------------------------

    def invoke(self, *args: str) -> dict:
        require(bool(self.client), "native_client_missing", INSTALL_HINT)
        inspect = shlex.join([self.client, "runtimes", "status", ALIAS])
        try:
            result = subprocess.run(
                [self.client, "runtimes", *args, "--json"],
                env=environment(),
                capture_output=True,
                text=True,
                timeout=45,
            )
        except subprocess.TimeoutExpired as exc:
            raise KeyholeError(
                "runtime_timeout",
                f"`tunnel-client runtimes {args[0]}` exceeded 45 seconds. Inspect with "
                f"`{inspect}` before retrying.",
            ) from exc
        except OSError as exc:
            raise KeyholeError("native_client_failed", INSTALL_HINT) from exc
        require(
            result.returncode == 0,
            "native_runtime_failed",
            f"`tunnel-client runtimes {args[0]}` failed (exit {result.returncode}). Inspect locally with "
            f"`{inspect}`. Check network access, tunnel/workspace association "
            "and Tunnels Read + Use permission; this error alone does not identify which one failed. "
            "Native output is withheld because it may contain credentials or private paths.",
        )
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise KeyholeError(
                "native_runtime_failed", "tunnel-client returned invalid JSON."
            ) from exc

    def aliases(self) -> set[str] | None:
        """Locally registered runtime aliases; None when the listing cannot be interpreted."""
        entries = self.invoke("list").get("aliases")
        if not isinstance(entries, list):
            return None
        return {e.get("alias") for e in entries if isinstance(e, dict)}

    def alias_exists(self) -> bool:
        # Unknown listing shapes fail closed: treat the alias as present so stop still runs.
        aliases = self.aliases()
        return aliases is None or ALIAS in aliases

    def status(self) -> dict:
        if not self.alias_exists():
            return {
                "alias": ALIAS,
                "runtime_state": "not_created",
                "process_running": False,
                "healthy": False,
                "ready": False,
                "stale": False,
                "tunnel_id": None,
                "profile_name": None,
                "control_plane_poll_health": None,
            }
        result = self.invoke("status", ALIAS)
        keys = (
            "alias",
            "runtime_state",
            "process_running",
            "healthy",
            "ready",
            "stale",
            "tunnel_id",
            "profile_name",
            "control_plane_poll_health",
        )
        return {key: result.get(key) for key in keys}

    def stop(self) -> dict:
        if self.alias_exists():
            self.invoke("stop", ALIAS)
        if sys.platform == "win32":
            from .windows_process import terminate_owned

            terminate_owned(self.store.path)
        else:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                for pid, command in owned_processes(self.store.path).items():
                    if owned_processes(self.store.path).get(pid) == command:
                        with contextlib.suppress(ProcessLookupError):
                            os.kill(pid, sig)
                end = time.monotonic() + 3
                while owned_processes(self.store.path) and time.monotonic() < end:
                    time.sleep(0.1)
                if not owned_processes(self.store.path):
                    break
        state = self.status()
        require(
            state.get("process_running") is False and not owned_processes(self.store.path),
            "stop_unconfirmed",
            "Old runtime or parser shutdown is not confirmed. Authorization is revoked, but do not "
            "report complete shutdown.",
        )
        return state

    def connect(self, generation: str) -> dict:
        info = self.preflight()
        command = shlex.join(
            [
                sys.executable,
                "-m",
                SERVER_MODULE,
                "--state-dir",
                str(self.store.path),
                "--generation",
                generation,
            ]
        )
        self.invoke(
            "connect",
            "--alias",
            ALIAS,
            "--tunnel-id",
            info["tunnel_id"],
            "--profile",
            PROFILE,
            "--profile-dir",
            str(self.store.path / "profiles"),
            "--runtime-api-key",
            self.key_reference(),
            "--mcp-command",
            command,
        )
        deadline = time.monotonic() + 20
        while True:
            result = self.status()
            if result.get("process_running") and result.get("healthy") and result.get("ready"):
                return result
            if not result.get("process_running") or time.monotonic() >= deadline:
                break
            time.sleep(0.5)
        raise KeyholeError(
            "runtime_not_ready",
            "The tunnel runtime did not become ready. Grants are disabled; inspect "
            f"`{shlex.join([self.client, 'runtimes', 'status', ALIAS])}`, then "
            f"`{self.store.command('resume', client=self.client)}` with the intended workspace name.",
        )
