"""Resumable terminal onboarding; account permissions always remain with the user."""

import getpass
import os
import sys
import webbrowser
from pathlib import Path

from . import configure
from . import file_ops as fileio
from .client_install import asset, client_name, destination, install
from .diagnostics import human_status
from .errors import KeyholeError, require
from .filesystem import absolute_directory
from .management import Manager
from .runtime import TESTED_CLIENT_VERSIONS, NativeRuntime
from .state import StateStore

TUNNELS_URL = "https://platform.openai.com/settings/organization/tunnels"
KEYS_URL = "https://platform.openai.com/settings/organization/api-keys"
DEMO_TEXT = """# Reading weekend

These are fictional notes for the Keyhole demo.

Ideas:
- Read the field guide on Saturday.
- Summarize the chapter about night skies.
- Pack a notebook for Sunday's walk.
"""


def say(text: str) -> None:
    print(text, file=sys.stderr)


def ask(prompt: str) -> str:
    print(prompt + " ", end="", file=sys.stderr, flush=True)
    return input().strip()


def yes(prompt: str) -> bool:
    return ask(prompt + " [y/N]").lower() in ("y", "yes")


def page(url: str, no_browser: bool) -> None:
    say(url)
    if not no_browser:
        try:
            opened = webbrowser.open(url)
        except webbrowser.Error:
            opened = False
        if not opened:
            say("Open the link above in your browser to continue.")


def confirm_version(version: str, previous: str | None) -> None:
    if version not in TESTED_CLIENT_VERSIONS:
        say(
            f"tunnel-client {version} is untested here; tested: {', '.join(TESTED_CLIENT_VERSIONS)}."
        )
        require(
            ask("Type the version number to accept it, or Enter to stop:") == version,
            "client_version_untested",
            "Setup stopped; the accepted client version was not changed.",
        )
    elif previous and previous != version:
        require(
            yes(f"Accept client version {version} (previously {previous})?"),
            "setup_cancelled",
            "The accepted client version was not changed.",
        )


def select_client(
    store: StateStore, explicit: str | None, config: dict, update: bool
) -> tuple[str, str, str]:
    runtime = NativeRuntime(store, explicit)
    if update:
        require(
            config.get("tunnel_client_source") == "managed" and not explicit,
            "client_not_managed",
            "Only a Keyhole-managed client can be updated here. "
            "Update your external client with its installer, then run setup --accept-client-version.",
        )
        release = asset()
        if runtime.client == str(destination(store, release) / client_name(release)):
            version = runtime.client_version()
            if version == release["version"]:
                say(
                    f"Managed client {version} is already the version verified by this Keyhole release."
                )
                return runtime.client, version, "managed"
    else:
        try:
            version = runtime.client_version()
        except KeyholeError as exc:
            if exc.code != "native_client_missing" or explicit:
                raise
            say("The selected official client is missing.")
        else:
            source = (
                config.get("tunnel_client_source", "external")
                if not explicit or runtime.client == config.get("tunnel_client_path")
                else "external"
            )
            say(f"Using {runtime.client} (version {version}; {source}).")
            confirm_version(version, config.get("tunnel_client_version"))
            return runtime.client, version, source
    release = asset()
    say(
        f"Official tunnel-client {release['version']}, {release['target']}: "
        f"{release['size'] / 1024 / 1024:.1f} MiB download, SHA-256 verified."
    )
    say(f"Install location: {destination(store, release)}")
    require(yes("Download and install this client?"), "setup_cancelled", "No client was installed.")
    client = install(store, release)
    return client, release["version"], "managed"


def create_demo(store: StateStore, runtime: NativeRuntime, path: Path) -> dict:
    """Create only a new directory, then use the same local grant checks as `open`."""
    path = path.expanduser().absolute()
    with store.lock():
        state = store.read(missing=True)
        require(
            not any(w["name"].casefold() == "demo" for w in state["workspaces"]),
            "demo_exists",
            "Workspace 'demo' already exists. It was not changed; "
            "use it explicitly or choose another name with keyhole open.",
        )
        with absolute_directory(path.parent) as (parent, walk):
            try:
                fileio.mkdir(path.name, mode=0o700, dir_fd=parent)
            except FileExistsError:
                raise KeyholeError(
                    "demo_exists",
                    "The demo directory already exists; nothing was overwritten. "
                    "Rerun setup and choose a new path, or open the existing folder explicitly.",
                ) from None
            fd = fileio.open(
                path.name, os.O_RDONLY | fileio.O_DIRECTORY | fileio.O_NOFOLLOW, dir_fd=parent
            )
            try:
                configure.write_exclusive(fd, "notes.md", DEMO_TEXT.encode())
                fileio.fsync(fd)
                walk.validate()
            finally:
                os.close(fd)
    try:
        return Manager(store, runtime).open(path, name="demo", access="ro")
    except KeyholeError as exc:
        retry = store.command("open", str(path), "--name", "demo", "--access", "ro")
        raise KeyholeError(
            exc.code,
            f"{exc.message} The sample files were kept. After fixing the reported issue, "
            f"explicitly retry this folder with: {retry}",
        ) from exc


def app_instructions() -> None:
    say(
        "\nIn ChatGPT web: enable Developer mode, create your private MCP app, and select your tunnel."
    )
    say("App name: Keyhole")
    say(
        "Description: Read only the local folders I open. Explicit local rw grants allow "
        "hash-checked text edits with recovery. No shell or public port."
    )
    say(
        "Connection: Tunnel. MCP authentication: No Authentication (the OpenAI tunnel authenticates access)."
    )
    say("Keep write confirmations enabled. Start a new Chat and select @Keyhole from the app menu.")
    say(
        'Demo prompt: In workspace "demo", read "notes.md" using Keyhole. Quote the three ideas and give the SHA-256.'
    )
    say(
        "Success means a real read_file result matching your local file, not just a ready connection."
    )


def run(args) -> dict:
    store = StateStore(args.state_dir)
    config = configure.setup_config(store)
    if args.rotate_key:
        require(bool(config), "not_configured", "Run setup first to choose a tunnel.")
        key = getpass.getpass("New runtime API key (hidden): ")
        with store.lock():
            result = configure.rotate_key(store, key)
        return {**result, "next_step": store.command("status", "--human")}
    client, version, source = select_client(store, args.tunnel_client, config, args.update_client)
    if args.accept_client_version or args.update_client:
        require(
            bool(config.get("tunnel_id")), "not_configured", "Run setup first to choose a tunnel."
        )
        result = configure.complete_setup(
            store,
            expected=config,
            tunnel_id=config["tunnel_id"],
            key=None,
            client=client,
            version=version,
            source=source,
        )
        return {**result, "next_step": store.command("status", "--human")}
    if sys.platform.startswith("linux"):
        say(
            "Linux: ChatGPT read/edit/restore/close verified on Ubuntu 24.04 x86_64; "
            "Ubuntu 22.04 also has automated coverage. See docs/platforms.md for the tested scope."
        )
    tunnel_id = config.get("tunnel_id")
    if not tunnel_id:
        say("\nTwo OpenAI surfaces, not necessarily two different accounts:")
        say(
            "ChatGPT needs Developer mode. OpenAI Platform needs Tunnels Read + Manage to create "
            "a tunnel; the runtime key needs Read + Use. Keyhole cannot grant these permissions."
        )
        page(TUNNELS_URL, args.no_browser)
        say(
            "Create a tunnel and associate only your intended ChatGPT workspace. Copy its tunnel id."
        )
        tunnel_id = ask("Tunnel id (tunnel_...):")
        configure.validate_tunnel_id(tunnel_id)
    key = None
    if not configure.key_present(store):
        page(KEYS_URL, args.no_browser)
        say("Create a restricted runtime key with Tunnels Read + Use. Never paste it into a chat.")
        key = getpass.getpass("Runtime API key (hidden): ")
        configure.validate_key(key)
    result = configure.complete_setup(
        store,
        expected=config,
        tunnel_id=tunnel_id,
        key=key,
        client=client,
        version=version,
        source=source,
    )
    say("\nLocal configuration saved. Credentials have not been verified by a ChatGPT read.")
    runtime = NativeRuntime(store)
    status = Manager(store, runtime).status()
    say(human_status(status, store.command("setup")))
    example_path = (
        str(Path.home() / "Documents" / "notes")
        if sys.platform == "win32"
        else "/absolute/path/to/folder"
    )
    next_step = store.command("open", example_path, "--name", "notes")
    if yes("Create a new sample folder and share ONLY that new folder read-only?"):
        default = Path.home() / "KeyholeDemo"
        path = Path(ask(f"New demo folder [{default}]:") or str(default))
        opened = create_demo(store, runtime, path)
        result["demo"] = {"path": str(path.expanduser().absolute()), "name": "demo", "access": "ro"}
        result["runtime_ready"] = opened["runtime"].get("ready") is True
        say("Demo shared read-only. Other saved directories were not automatically resumed.")
        next_step = store.command("status", "--human")
    else:
        say(f"No new folder was shared. To open one you choose: {next_step}")
        say("The demo prompt below applies only after you have explicitly opened the demo.")
    app_instructions()
    return {
        **result,
        "chatgpt_read_verified": False,
        "next_step": next_step,
    }
