"""keyhole: manage local directory grants and the official tunnel runtime from your own terminal."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import __version__
from .errors import KeyholeError


def platform_problem() -> str | None:
    if sys.platform == "win32":
        if sys.getwindowsversion().build < 22000:
            return (
                "Windows 11 or Windows Server 2025 on local NTFS is required. Nothing was changed."
            )
        return None
    if sys.platform not in ("darwin", "linux"):
        return "This OS or compatibility layer is unsupported. Use native Python on macOS, Linux or Windows 11. Nothing was changed."
    return None


def fail(code: str, message: str) -> None:
    error = {"ok": False, "error": {"code": code, "message": message}}
    print(json.dumps(error, ensure_ascii=False, indent=2))
    raise SystemExit(1)


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
        default=None,
        help="private state directory (default: ~/.config/keyhole)",
    )
    parser.add_argument(
        "--tunnel-client",
        metavar="PATH",
        help="override saved client path (legacy configurations fall back to PATH)",
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
    group.add_argument(
        "--update-client",
        action="store_true",
        help="update a Keyhole-managed client to the verified version",
    )
    setup.add_argument(
        "--no-browser", action="store_true", help="print official setup links without opening them"
    )

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

    status = actions.add_parser(
        "status", help="show configuration, environment checks and runtime state"
    )
    output = status.add_mutually_exclusive_group()
    output.add_argument("--human", action="store_true", help="readable status and next step")
    output.add_argument(
        "--redact",
        action="store_true",
        help="issue-safe JSON without paths, ids or workspace names",
    )
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


def run_setup(args, parser: argparse.ArgumentParser) -> dict:
    from .setup import run

    require_terminal(parser)
    return run(args)


def main(argv: list[str] | None = None) -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    problem = platform_problem()
    if problem:
        fail("unsupported_platform", problem)
    # Load only the filesystem backend supported by this interpreter and OS.
    from .management import Manager
    from .runtime import NativeRuntime
    from .state import DEFAULT_STATE, StateStore

    args.state_dir = (args.state_dir or DEFAULT_STATE).expanduser().absolute()
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
            if not result["configured"]:
                result["next_step"] = store.command("setup", client=args.tunnel_client)
            if args.human:
                from .diagnostics import human_status

                print(human_status(result, store.command("setup", client=args.tunnel_client)))
                return
            if args.redact:
                from .diagnostics import redact_status

                result = redact_status(result)
        else:
            result = manager.select(
                args.names,
                args.all,
                enable=args.action == "resume",
                forget=args.action == "forget",
            )
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    except KeyholeError as exc:
        if args.action == "status" and args.redact:
            from .diagnostics import error_code

            print(
                json.dumps(
                    {
                        "ok": False,
                        "redacted": True,
                        "error": {"code": error_code({"code": exc.code})},
                    }
                )
            )
            raise SystemExit(1) from None
        fail(exc.code, exc.message)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if args.action == "status" and args.redact:
            fail("local_failure", "Local check failed; private details withheld.")
        fail("local_failure", f"{type(exc).__name__}: {exc}")
    except (EOFError, KeyboardInterrupt):
        fail(
            "cancelled",
            "Stopped. Existing configuration and files were kept; rerun setup to continue.",
        )


if __name__ == "__main__":
    main()
