"""One policy for grants, discovery, search and direct reads."""

import unicodedata
from fnmatch import fnmatchcase
from pathlib import Path

from .errors import require

TEXT_LIMIT = 8 * 1024 * 1024
FILE_LIMIT = 64 * 1024 * 1024
RESPONSE_LIMIT = 64 * 1024
LINE_LIMIT = 400
DIR_LIMIT = 10_000
TREE_LIMIT = 20_000
DOCUMENT_TYPES = {".pdf": "pdf", ".docx": "docx", ".pptx": "pptx", ".xlsx": "xlsx"}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}
UNSUPPORTED_TYPES = {
    ".doc",
    ".xls",
    ".ppt",
    ".zip",
    ".gz",
    ".7z",
    ".tar",
    ".dmg",
    ".exe",
    ".app",
    ".sqlite",
    ".db",
    ".so",
    ".dylib",
    ".mp4",
    ".mov",
    ".mp3",
}
# Hidden at any depth, ignoring case: version control, credentials, agent state, caches.
EXCLUDED_NAMES = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        ".ssh",
        ".gnupg",
        ".aws",
        ".azure",
        ".kube",
        ".config",
        ".codex",
        ".claude",
        ".openclaw",
        ".hermes",
        ".ollama",
        ".agents",
        ".cache",
        ".npm",
        ".gradle",
        ".m2",
        ".cargo",
        ".rustup",
        ".pyenv",
        ".local",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".next",
        ".turbo",
        "deriveddata",
        "library",
        ".ds_store",
        "runtime.key",
        "auth.json",
        "cookies.txt",
        "credentials",
        "credentials.json",
        "credentials.yaml",
        "credentials.yml",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".git-credentials",
        ".docker",
    }
)
EXCLUDED_PATTERNS = (
    ".keyhole-stage-*",
    ".env*",
    "*.key",
    "*.pem",
    "*.p12",
    "*.pfx",
    "*.kdbx",
    "*.keystore",
    "*.ovpn",
    "id_rsa*",
    "id_ed25519*",
    "secrets.*",
    "*.cookie",
    "cookies.*",
    "*.session",
    "*.session-journal",
)
EXCLUSION_RULES = (
    "Built-in names and patterns are hidden at any depth. A custom pattern without a slash "
    "hides matching names at any depth; a pattern with a slash is anchored at the workspace "
    "root and hides that whole subtree. Matching ignores case and Unicode normalization form."
)


def fold(value: str) -> str:
    """Normalize a name for comparison so alias spellings of one path compare equal."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", value).casefold())


def normalize_pattern(pattern: str) -> str:
    """Validate a user exclusion pattern and strip decoration that would silently disable it."""
    require(
        isinstance(pattern, str) and 0 < len(pattern) <= 200,
        "invalid_pattern",
        "Exclusion patterns must be 1 to 200 characters.",
    )
    require(
        "\\" not in pattern and not any(ord(c) < 32 for c in pattern),
        "invalid_pattern",
        "Exclusion patterns cannot contain backslashes or control characters.",
    )
    value = pattern.strip("/").removeprefix("./")
    require(
        bool(value) and all(part not in ("", ".", "..") for part in value.split("/")),
        "invalid_pattern",
        f"Exclusion pattern {pattern!r} has an empty, dot or parent component.",
    )
    return value


def relative_parts(value: str) -> tuple[str, ...]:
    require(
        isinstance(value, str) and len(value) <= 4096,
        "invalid_path",
        "Use a relative path of at most 4096 characters.",
    )
    require(
        not value.startswith(("/", "~"))
        and "\\" not in value
        and not any(ord(c) < 32 for c in value),
        "invalid_path",
        "Absolute paths, backslashes and control characters are not allowed.",
    )
    if value in ("", "."):
        return ()
    parts = tuple(value.split("/"))
    require(
        all(p not in ("", ".", "..") for p in parts),
        "invalid_path",
        "Use normalized relative paths without dot or parent components.",
    )
    require(len(parts) <= 64, "invalid_path", "Path depth exceeds the limit.")
    return parts


def excluded(parts: tuple[str, ...], extra: tuple[str, ...] = ()) -> bool:
    folded = tuple(fold(part) for part in parts)
    for name in folded:
        if name in EXCLUDED_NAMES or any(fnmatchcase(name, p) for p in EXCLUDED_PATTERNS):
            return True
    for pattern in extra:
        rule = fold(pattern)
        if "/" in rule:
            # Anchored at the root: a match on any leading prefix hides the whole subtree,
            # and "dir/*" hides the directory entry itself too.
            prefixes = ["/".join(folded[:i]) for i in range(1, len(folded) + 1)]
            rules = (rule, rule[:-2]) if rule.endswith("/*") else (rule,)
            if any(fnmatchcase(prefix, r) for prefix in prefixes for r in rules):
                return True
        elif any(fnmatchcase(name, rule) for name in folded):
            return True
    return False


def check_relative(value: str, extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    parts = relative_parts(value)
    require(
        not excluded(parts, extra), "excluded", "This path is excluded by the local sharing policy."
    )
    return parts


def check_root(path: Path) -> None:
    require(
        path.is_absolute() and ".." not in path.parts,
        "invalid_root",
        "An absolute path without parent components is required.",
    )
    require(
        len(path.parts) >= 4 and path != Path.home(),
        "protected_root",
        "Share a specific project directory, not your home directory or a top-level root.",
    )
    require(
        not str(path).startswith("/System/"),
        "protected_root",
        "Directories under /System cannot be shared; use the canonical path under /Users or /Volumes.",
    )
    require(
        not excluded(tuple(path.parts[1:])),
        "excluded_root",
        "This directory is protected by the default exclusion policy.",
    )


def capabilities(path: str, size: int) -> dict:
    suffix = Path(path).suffix.lower()
    if suffix in DOCUMENT_TYPES:
        kind = DOCUMENT_TYPES[suffix]
    elif suffix in IMAGE_TYPES:
        kind = "image"
    elif suffix in UNSUPPORTED_TYPES:
        kind = "unsupported"
    else:
        kind = "text"
    limit = TEXT_LIMIT if kind == "text" else FILE_LIMIT
    readable = kind != "unsupported" and size <= limit
    if not readable:
        modes = ["metadata"]
    elif kind == "image":
        modes = ["metadata", "image"]
    elif kind == "pdf":
        modes = ["metadata", "text", "image"]
    else:
        modes = ["metadata", "text"]
    encoding = "UTF-8 or BOM-marked UTF-16/32; binary detection is checked at read time"
    return {
        "kind": kind,
        "readable": readable,
        "max_source_bytes": limit,
        "modes": modes,
        "text_encoding": encoding if kind == "text" else None,
    }


def policy_summary() -> dict:
    return {
        "excluded_names": sorted(EXCLUDED_NAMES),
        "excluded_patterns": list(EXCLUDED_PATTERNS),
        "exclusion_rules": EXCLUSION_RULES,
        "links": "Symbolic links and regular files with multiple hard links are denied.",
        "text_file_bytes": TEXT_LIMIT,
        "document_image_bytes": FILE_LIMIT,
        "response_text_bytes": RESPONSE_LIMIT,
        "text_lines": LINE_LIMIT,
        "search": "Literal filename or plain-text content; no OCR or Office/PDF full-text indexing.",
        "scope": (
            "Permitted current and future files remain readable until revoked. rw adds bounded "
            "mutations; ro never grants writes. Filename exclusions are not content-based secret "
            "detection."
        ),
        "writes": {
            "text_encoding": "UTF-8 only",
            "full_text_input_bytes": 1024 * 1024,
            "file_or_snapshot_bytes": TEXT_LIMIT,
            "permission": "Explicit local rw grant; existing grants default to ro",
            "recovery": (
                "Version checks, request_id replay protection and persistent change history; "
                "no automatic purge"
            ),
            "protected": (
                "Keyhole installation, state and ancestors; shell startup and automatic "
                "environment hooks"
            ),
            "file_operations": (
                "Single files within one workspace; absent destinations; no recursive directory "
                "operations or Office editing transport"
            ),
        },
    }
