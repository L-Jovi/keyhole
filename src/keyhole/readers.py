"""Bounded in-memory text and isolated document parsing."""

import json
import os
import subprocess
import sys
import tempfile
import threading
from contextlib import nullcontext
from uuid import uuid4

from .errors import KeyholeError, require
from .policy import LINE_LIMIT, RESPONSE_LIMIT

_slots = threading.BoundedSemaphore(2)


def decode_text(data: bytes) -> str:
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        encoding = "utf-32"
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    else:
        encoding = "utf-8-sig"
    try:
        value = data.decode(encoding)
    except UnicodeError as exc:
        raise KeyholeError(
            "unsupported_encoding",
            "This file is not UTF-8 or BOM-marked UTF-16/32 text; only metadata is available.",
        ) from exc
    require(
        "\x00" not in value and not any(ord(c) < 9 or 13 < ord(c) < 32 for c in value[:8192]),
        "binary_file",
        "This appears to be binary data; only metadata is available.",
    )
    return value


def text_range(data: bytes, start: int, limit: int) -> dict:
    text = decode_text(data)
    lines = text.splitlines()
    limit = limit or 100
    require(
        1 <= limit <= LINE_LIMIT,
        "invalid_range",
        f"Read between 1 and {LINE_LIMIT} lines per call.",
    )
    require(
        1 <= start <= max(1, len(lines)),
        "invalid_range",
        "The requested starting line is outside the file.",
    )
    selected, size, partial = [], 0, False
    for number in range(start - 1, min(len(lines), start - 1 + limit)):
        line = lines[number]
        encoded = (line + "\n").encode()
        if size + len(encoded) > RESPONSE_LIMIT:
            if not selected:
                line = encoded[: RESPONSE_LIMIT - 256].decode("utf-8", errors="ignore")
                selected.append({"line": number + 1, "text": line, "partial_line": True})
                partial = True
            break
        selected.append({"line": number + 1, "text": line})
        size += len(encoded)
    last = selected[-1]["line"] if selected else 0
    without_crlf = text.replace("\r\n", "")
    if "\r\n" in text and "\n" not in without_crlf:
        newline = "crlf"
    elif "\r" in text:
        newline = "mixed"
    else:
        newline = "lf"
    return {
        "format": "text",
        "range": {"unit": "line", "start": start, "end": last, "total": len(lines)},
        "lines": selected,
        "truncated": partial or last < len(lines),
        "next_start": last + 1 if last < len(lines) else None,
        "partial_line": partial,
        "ends_with_newline": text.endswith(("\n", "\r")),
        "newline": newline,
        "utf8_bom": data.startswith(b"\xef\xbb\xbf"),
        "note": (
            "A partial oversized line cannot be fully retrieved through this interface; never "
            "replace a whole file from partial contents."
            if partial
            else None
        ),
    }


def parse_document(
    data: bytes,
    kind: str,
    options: dict,
    generation: str,
    timeout: float = 20,
    *,
    state_dir: str | None = None,
) -> dict:
    require(
        _slots.acquire(blocking=False),
        "parser_busy",
        "Two document readers are already running; retry shortly.",
    )
    try:
        # Only an anonymous, already validated source snapshot crosses into the parser.
        with tempfile.TemporaryFile() as source:
            source.write(data)
            source.flush()
            source.seek(0)
            command = [
                sys.executable,
                "-I",
                "-X",
                "utf8",
                "-m",
                "keyhole.parsers",
                "--input-fd",
                str(source.fileno()),
                "--kind",
                kind,
                "--generation",
                generation,
                "--nonce",
                str(uuid4()),
            ]
            if state_dir is not None:
                command.extend(["--state-dir", state_dir])
            env = {
                "PATH": "/usr/bin:/bin",
                "LANG": "en_US.UTF-8",
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
            }
            transfer = nullcontext((None, None))
            options_for_process = {"pass_fds": (source.fileno(),)}
            if sys.platform == "win32":
                from .windows_parser import input_handle

                transfer = input_handle(source.fileno())
                options_for_process = {
                    "close_fds": True,
                    "creationflags": subprocess.CREATE_NO_WINDOW,
                }
                for key in ("SYSTEMROOT", "WINDIR", "USERPROFILE", "TEMP", "TMP"):
                    if key in os.environ:
                        env[key] = os.environ[key]
            with transfer as (native_input, startup):
                if native_input is not None:
                    position = command.index("--input-fd")
                    command[position : position + 2] = ["--input-handle", str(native_input)]
                    options_for_process["startupinfo"] = startup
                child = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env=env,
                    **options_for_process,
                )
            with child:
                try:
                    stdout, _ = child.communicate(json.dumps(options).encode(), timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    child.kill()
                    child.wait()
                    raise KeyholeError(
                        "parser_timeout", "Document parsing exceeded its time budget."
                    ) from exc
                require(
                    child.returncode == 0 and len(stdout) <= 6 * 1024 * 1024,
                    "parser_failed",
                    "The parser failed or exceeded its resource budget.",
                )
            try:
                result = json.loads(stdout)
            except (ValueError, UnicodeError) as exc:
                raise KeyholeError(
                    "parser_failed", "The parser returned an invalid result."
                ) from exc
            if "error" in result:
                raise KeyholeError(result["error"]["code"], result["error"]["message"])
            return result
    finally:
        _slots.release()
