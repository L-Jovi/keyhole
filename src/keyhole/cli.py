"""keyhole: manage local directory grants and the official tunnel runtime from your own terminal."""

import argparse
import getpass
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, configure
from .errors import KeyholeError, require
from .management import Manager
from .runtime import TESTED_CLIENT_VERSIONS, NativeRuntime
from .state import DEFAULT_STATE, StateStore

NEXT_STEP = "keyhole open /path/to/project --access ro"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="keyhole",
        description="Share local directories with your private ChatGPT app: read-only by default, "
        "explicit rw for version-checked, recoverable text edits.",
    )
    parser.add_argument("--version", action="version", version=f"keyhole {__version__}")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=DEFAULT_STATE,
        help="private state directory (default: %(default)s)",
    )
    parser.add_argument(
        "--tunnel-client",
        metavar="PATH",
        help="tunnel-client executable; otherwise resolved from PATH",
    )
    actions = parser.add_subparsers(dest="action", required=True, metavar="COMMAND")

    setup = actions.add_parser(
        "setup", help="store the tunnel id and runtime key, or record a reviewed client upgrade"
    )
    group = setup.add_mutually_exclusive_group()
    group.add_argument(
        "--accept-client-version",
        action="store_true",
        help="accept the installed tunnel-client version after an upgrade",
    )
    group.add_argument("--rotate-key", action="store_true", help="replace the stored runtime key")

    opening = actions.add_parser(
        "open", help="share a directory (read-only unless --access rw) and start the runtime"
    )
    opening.add_argument("path", type=Path, help="absolute directory to share")
    opening.add_argument("--name", help="workspace name ChatGPT will use (default: directory name)")
    opening.add_argument(
        "--access", choices=["ro", "rw"], default=None, help="ro (default for new grants) or rw"
    )
    opening.add_argument(
        "--exclude",
        action="append",
        default=None,
        metavar="PATTERN",
        help="hide matching names or root-anchored paths; repeatable",
    )
    opening.add_argument(
        "--recovery",
        choices=["on", "off"],
        default=None,
        help="on (default): keep a private copy of each file before ChatGPT changes it so it can be restored; off: keep paths and hashes only",
    )

    access = actions.add_parser("access", help="change a saved workspace between ro and rw")
    access.add_argument("name")
    access.add_argument("mode", choices=["ro", "rw"])

    for action, text in (
        ("close", "stop sharing; saved paths and recovery history remain"),
        ("resume", "re-verify saved directories and share them again"),
        ("forget", "stop sharing and remove the saved configuration (files are untouched)"),
    ):
        sub = actions.add_parser(action, help=text)
        sub.add_argument("names", nargs="*", metavar="NAME")
        sub.add_argument("--all", action="store_true")

    actions.add_parser("status", help="show configuration, environment checks and runtime state")
    history = actions.add_parser(
        "history", help="list recent recoverable changes, including interrupted ones"
    )
    history.add_argument("--limit", type=int, default=20)
    purge = actions.add_parser(
        "purge-history", help="permanently delete completed recovery records before a date"
    )
    purge.add_argument("--before", required=True, metavar="YYYY-MM-DD")
    purge.add_argument("--confirm", action="store_true", required=True)
    return parser


def require_terminal(parser: argparse.ArgumentParser) -> None:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        parser.error("run this in your own terminal; secret input needs a TTY")


def confirm_untested(version: str) -> None:
    if version in TESTED_CLIENT_VERSIONS:
        return
    print(
        f"tunnel-client {version} has not been verified with this release "
        f"(tested: {', '.join(TESTED_CLIENT_VERSIONS)}).",
        file=sys.stderr,
    )
    typed = input("Type the version number to accept it anyway, or press Enter to stop: ").strip()
    require(typed == version, "client_version_untested", "Setup stopped; nothing was written.")


def run_setup(args, parser: argparse.ArgumentParser) -> dict:
    store = StateStore(args.state_dir)
    runtime = NativeRuntime(store, args.tunnel_client)
    version = runtime.client_version()
    if args.accept_client_version:
        require_terminal(parser)
        confirm_untested(version)
        return {
            **configure.accept_client_version(store, version),
            "next_step": "keyhole resume --all",
        }
    if args.rotate_key:
        require_terminal(parser)
        key = getpass.getpass("New runtime API key (hidden): ")
        return {**configure.rotate_key(store, key), "next_step": "keyhole resume --all"}
    require_terminal(parser)
    confirm_untested(version)
    tmux = shutil.which("tmux")
    if not tmux:
        print(
            "note: tmux not found; tunnel-client will keep the tunnel in a detached background process.",
            file=sys.stderr,
        )
    tunnel_id = input("Tunnel id (tunnel_...): ").strip()
    key = getpass.getpass("Runtime API key with Tunnels Read + Use (hidden): ")
    result = configure.save_runtime(args.state_dir, tunnel_id, key, version)
    return {**result, "tmux": tmux, "next_step": NEXT_STEP}


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = StateStore(args.state_dir)
    manager = Manager(store, NativeRuntime(store, args.tunnel_client))
    try:
        if args.action == "setup":
            result = run_setup(args, parser)
        elif args.action == "open":
            result = manager.open(args.path, args.name, args.exclude, args.access, args.recovery)
        elif args.action == "access":
            result = manager.access(args.name, args.mode)
        elif args.action in ("history", "purge-history"):
            from .writes import Journal

            with store.lock(), Journal(store) as journal:
                if args.action == "history":
                    result = journal.history(limit=args.limit)
                else:
                    result = journal.purge(args.before)
        elif args.action == "status":
            result = manager.status()
        else:
            result = manager.select(
                args.names,
                args.all,
                enable=args.action == "resume",
                forget=args.action == "forget",
            )
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    except KeyholeError as exc:
        print(
            json.dumps(
                {"ok": False, "error": {"code": exc.code, "message": exc.message}},
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1) from None
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        error = {"code": "local_failure", "message": f"{type(exc).__name__}: {exc}"}
        print(json.dumps({"ok": False, "error": error}, ensure_ascii=False, indent=2))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
