"""Version-checked, replay-safe, recoverable mutations inside one rw workspace."""

import base64
import contextlib
import copy
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
from contextlib import ExitStack
from datetime import date
from pathlib import Path

from .errors import KeyholeError, require
from .filesystem import identity, version
from .policy import TEXT_LIMIT, capabilities, check_relative, fold
from .state import now

ABSENT = {"kind": "absent"}
INPUT_LIMIT = 1024 * 1024
# Recovery history is bounded; when full, the oldest completed records are evicted, never the
# in-flight ones. SQLite's page cap (below) is the hard backstop for the file itself.
MAX_RECORDS = 1000
MAX_BYTES = 450 * 1024 * 1024
# A restore must still be journaled when ordinary history is full. Its retry reuses this row.
RECOVERY_RESERVE_BYTES = 48 * 1024 * 1024
PACKAGE_DIR = Path(__file__).resolve().parent
SHELL_HOOKS = {
    ".zshrc",
    ".zprofile",
    ".bashrc",
    ".bash_profile",
    ".profile",
    ".zshenv",
    ".direnv",
    ".envrc",
}


def protected_directories(store) -> list[Path]:
    """Trees the remote side must never modify: this code, its environment, and the state."""
    return [PACKAGE_DIR, Path(sys.prefix), store.path]


def protected_identities(store) -> set[tuple]:
    result = set()
    for directory in protected_directories(store):
        try:
            result.add(identity(os.stat(directory)))
        except OSError:
            continue
    return result


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def file_image(data, mode=0o600):
    require(
        len(data) <= TEXT_LIMIT,
        "file_too_large",
        "Mutations and recovery snapshots are limited to 8 MiB per file.",
    )
    return {
        "kind": "file",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "mode": mode,
        "data": base64.b64encode(data).decode(),
    }


def public_image(image):
    return {k: v for k, v in image.items() if k not in ("data", "identity", "version")}


def same(a, b):
    return public_image(a) == public_image(b) and (
        a["kind"] != "directory" or not b.get("identity") or a.get("identity") == b["identity"]
    )


def strip_images(record):
    """A stored copy without file contents: paths, hashes and modes only."""
    stored = copy.deepcopy(record)
    for images in (stored["before"], stored["after"]):
        for image in images:
            image.pop("data", None)
    return stored


def receipt(record, replayed=False):
    return {
        "change_id": record["change_id"],
        "request_id": record["request_id"],
        "workspace": record["workspace"],
        "operation": record["operation"],
        "status": record["status"],
        "created_at": record["created_at"],
        "replayed": replayed,
        "recoverable": record.get("recoverable", True),
        "evicted_records": record.get("evicted_records", 0),
        "changes": [
            {"path": p, "before": public_image(a), "after": public_image(b)}
            for p, a, b in zip(record["paths"], record["before"], record["after"], strict=True)
        ],
    }


class Journal:
    """Private recovery data; no source content is returned by history tools."""

    def __init__(self, store):
        self.store = store

    def __enter__(self):
        self.directory = self.store.directory()
        fd = self.directory.__enter__()
        try:
            dbfd = os.open(
                "changes.sqlite3", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=fd
            )
            try:
                st = os.fstat(dbfd)
                require(
                    stat.S_ISREG(st.st_mode)
                    and st.st_nlink == 1
                    and st.st_uid == os.getuid()
                    and stat.S_IMODE(st.st_mode) == 0o600,
                    "history_unsafe",
                    "Recovery database must be an owned private regular file.",
                )
                self.db_identity = identity(st)
            finally:
                os.close(dbfd)
            self.db = sqlite3.connect(self.store.path / "changes.sqlite3", timeout=5)
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA max_page_count=131072")
            if self.db.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0:
                # Only effective before the first table exists; lets eviction return disk space.
                self.db.execute("PRAGMA auto_vacuum=INCREMENTAL")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS changes (id TEXT PRIMARY KEY, scope TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, summary TEXT NOT NULL, record TEXT NOT NULL)"
            )
            self.db.commit()
            self.fd = fd
            return self
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            self.directory.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        self.db.close()
        try:
            require(
                identity(os.stat("changes.sqlite3", dir_fd=self.fd, follow_symlinks=False))
                == self.db_identity,
                "history_unsafe",
                "Recovery database was replaced during access.",
            )
        finally:
            self.directory.__exit__(*args)

    def get(self, change_id):
        row = self.db.execute("SELECT record FROM changes WHERE id=?", (change_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def usage(self) -> dict:
        count, used = self.db.execute(
            "SELECT count(*), coalesce(sum(length(CAST(record AS BLOB))),0) FROM changes"
        ).fetchone()
        return {
            "records": count,
            "bytes": used,
            "max_records": MAX_RECORDS,
            "max_bytes": MAX_BYTES,
            "recovery_reserve_records": 1,
            "recovery_reserve_bytes": RECOVERY_RESERVE_BYTES,
        }

    def make_room(self, incoming: int, slots: int = 1, *, protected=(), recovery=False) -> int:
        """Called inside a transaction; pending and explicitly pinned records cannot be evicted."""
        evicted = 0
        while True:
            usage = self.usage()
            count, size = usage["records"] + slots, usage["bytes"] + incoming
            if count <= MAX_RECORDS and size <= MAX_BYTES:
                break
            exclusions = (
                " AND id NOT IN (" + ",".join("?" for _ in protected) + ")" if protected else ""
            )
            row = self.db.execute(
                "SELECT id FROM changes WHERE status IN ('committed','restored')"
                + exclusions
                + " ORDER BY created_at ASC, id ASC LIMIT 1",
                tuple(protected),
            ).fetchone()
            if (
                row is None
                and recovery
                and count <= MAX_RECORDS + 1
                and size <= MAX_BYTES + RECOVERY_RESERVE_BYTES
            ):
                break
            require(
                row is not None,
                "history_full",
                "Recovery history is full. Inspect `keyhole history`; retry an interrupted restore "
                "with its original request_id, or restore an interrupted change before making new edits.",
            )
            self.db.execute("DELETE FROM changes WHERE id=?", (row[0],))
            evicted += 1
        return evicted

    @staticmethod
    def values(record) -> tuple:
        # With recovery off, contents are kept only while an operation is in flight.
        stored = (
            record
            if record.get("recoverable", True) or record["status"] == "prepared"
            else strip_images(record)
        )
        blob = json.dumps(stored, ensure_ascii=False)
        return (
            record["change_id"],
            record["scope"],
            record["fingerprint"],
            record["status"],
            record["created_at"],
            json.dumps(receipt(record), ensure_ascii=False),
            blob,
        )

    def save(self, record) -> int:
        """Account for both inserts and updates; failed reservations roll back their evictions."""
        values = self.values(record)
        previous = self.db.execute(
            "SELECT length(CAST(record AS BLOB)) FROM changes WHERE id=?", (record["change_id"],)
        ).fetchone()
        protected = (record["change_id"],) + (
            (record["restores"],) if record.get("restores") else ()
        )
        with self.db:
            evicted = self.make_room(
                len(values[-1].encode()) - (previous[0] if previous else 0),
                slots=int(previous is None),
                protected=protected,
                recovery=record["operation"] == "restore",
            )
            self.db.execute("INSERT OR REPLACE INTO changes VALUES (?,?,?,?,?,?,?)", values)
        return evicted

    def finish_restore(self, record, original) -> None:
        """Complete both sides atomically before releasing the temporary recovery allowance."""
        original["status"] = "restored"
        with self.db:
            for item in (original, record):
                self.db.execute(
                    "INSERT OR REPLACE INTO changes VALUES (?,?,?,?,?,?,?)", self.values(item)
                )
            # The original may now be evicted, but can never be reinserted after eviction.
            while True:
                evicted = self.make_room(0, slots=0)
                record["evicted_records"] = record.get("evicted_records", 0) + evicted
                if not self.get(record["change_id"]):
                    break
                self.db.execute(
                    "INSERT OR REPLACE INTO changes VALUES (?,?,?,?,?,?,?)", self.values(record)
                )
                if not evicted:
                    break

    def pending(self, scope):
        return [
            json.loads(x[0])
            for x in self.db.execute(
                "SELECT record FROM changes WHERE scope=? AND status='prepared'", (scope,)
            )
        ]

    def history(self, scope=None, limit=20):
        require(1 <= limit <= 100, "invalid_limit", "History limit must be 1 to 100.")
        clause, values = (" WHERE scope=?", [scope]) if scope else ("", [])
        rows = self.db.execute(
            "SELECT summary FROM changes" + clause + " ORDER BY created_at DESC LIMIT ?",
            values + [limit],
        ).fetchall()
        return {
            "changes": [json.loads(r[0]) for r in rows],
            "limit": limit,
            "usage": self.usage(),
            "retention": "Bounded history: when full, the oldest completed records are evicted automatically; "
            "interrupted operations are never evicted. `keyhole purge-history` removes completed records "
            "before a date. Workspaces opened with recovery off keep paths and hashes only.",
        }

    def purge(self, before):
        cutoff = date.fromisoformat(before).isoformat()
        require(
            cutoff <= date.today().isoformat(),
            "invalid_cutoff",
            "Use today's date or an earlier ISO date.",
        )
        result = self.db.execute(
            "DELETE FROM changes WHERE created_at < ? AND status IN ('committed','restored')",
            (cutoff,),
        )
        self.db.commit()
        self.db.execute("PRAGMA incremental_vacuum")
        return {
            "purged_records": result.rowcount,
            "before": cutoff,
            "source_files_changed": False,
            "note": "Recovery and replay protection for these records are permanently removed; database free pages are reused.",
        }


class Entry:
    def __init__(self, fd, name, walk):
        self.fd, self.name, self.walk = fd, name, walk

    def snapshot(self):
        self.walk.validate()
        try:
            st = os.stat(self.name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return dict(ABSENT)
        if stat.S_ISDIR(st.st_mode):
            return {
                "kind": "directory",
                "mode": stat.S_IMODE(st.st_mode),
                "identity": list(identity(st)),
            }
        require(
            stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and st.st_uid == os.getuid(),
            "unsafe_file",
            "Mutations require owned single-link regular files; links and special files are refused.",
        )
        fd = os.open(self.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            initial = os.fstat(fd)
            require(
                version(initial) == version(st),
                "content_changed",
                "Source identity changed before snapshot.",
            )
            require(st.st_size <= TEXT_LIMIT, "file_too_large", "Mutation snapshot exceeds 8 MiB.")
            chunks, size = [], 0
            while True:
                chunk = os.read(fd, min(1024 * 1024, TEXT_LIMIT + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                require(size <= TEXT_LIMIT, "file_too_large", "Mutation snapshot exceeds 8 MiB.")
            require(
                version(os.fstat(fd)) == version(initial),
                "content_changed",
                "File changed during snapshot.",
            )
            require(
                version(os.stat(self.name, dir_fd=self.fd, follow_symlinks=False))
                == version(initial),
                "path_changed",
                "File was replaced during snapshot.",
            )
            return {
                **file_image(b"".join(chunks), stat.S_IMODE(st.st_mode)),
                "version": list(version(st)),
            }
        finally:
            os.close(fd)

    def apply(self, before, after, stage_name):
        current = self.snapshot()
        require(
            same(current, before)
            and (not before.get("version") or current.get("version") == before["version"]),
            "version_conflict",
            "File changed before commit; original content was retained in recovery history.",
        )
        if same(current, after):
            return current
        if before["kind"] == "file":
            require(
                before["mode"] & stat.S_IWUSR,
                "file_read_only",
                "The source file is not owner-writable; change its local permissions before editing.",
            )
        self.walk.validate()
        if after["kind"] == "absent":
            if before["kind"] == "directory":
                os.rmdir(self.name, dir_fd=self.fd)
            else:
                os.unlink(self.name, dir_fd=self.fd)
        elif after["kind"] == "directory":
            require(
                before["kind"] == "absent",
                "path_conflict",
                "Directory creation cannot replace an existing path.",
            )
            os.mkdir(self.name, after["mode"], dir_fd=self.fd)
        else:
            require(
                before["kind"] in ("absent", "file"),
                "path_conflict",
                "A file cannot replace a directory.",
            )
            data = base64.b64decode(after["data"], validate=True)
            require(
                hashlib.sha256(data).hexdigest() == after["sha256"],
                "history_corrupt",
                "Recovery snapshot hash mismatch.",
            )
            tmpfd = os.open(
                stage_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=self.fd,
            )
            try:
                with os.fdopen(tmpfd, "wb") as f:
                    f.write(data)
                    os.fchmod(f.fileno(), after["mode"] & 0o777)
                    f.flush()
                    os.fsync(f.fileno())
                latest = self.snapshot()
                require(
                    same(latest, current) and latest.get("version") == current.get("version"),
                    "version_conflict",
                    "File changed while the new content was staged.",
                )
                self.walk.validate()
                if before["kind"] == "absent":
                    # An exclusive link publishes a new file without overwriting a concurrent creator.
                    os.link(
                        stage_name,
                        self.name,
                        src_dir_fd=self.fd,
                        dst_dir_fd=self.fd,
                        follow_symlinks=False,
                    )
                    os.unlink(stage_name, dir_fd=self.fd)
                else:
                    os.replace(stage_name, self.name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(stage_name, dir_fd=self.fd)
        os.fsync(self.fd)
        self.walk.validate()
        actual = self.snapshot()
        require(
            same(actual, after),
            "commit_uncertain",
            "Post-commit verification failed; inspect change history and restore only after resolving concurrent edits.",
        )
        return actual

    def clean_stage(self, name):
        try:
            st = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        require(
            stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and st.st_nlink in (1, 2),
            "history_unsafe",
            "Unexpected interrupted staging entry; inspect locally.",
        )
        if st.st_nlink == 2:
            target = os.stat(self.name, dir_fd=self.fd, follow_symlinks=False)
            require(
                identity(target) == identity(st),
                "history_unsafe",
                "Interrupted staging link does not match its destination.",
            )
        self.walk.validate()
        os.unlink(name, dir_fd=self.fd)
        os.fsync(self.fd)


def scope_for(grant):
    return digest({k: grant[k] for k in ("name", "path", "device", "inode")})


def check_write_path(bridge, grant, path):
    parts = check_relative(path, tuple(grant.get("exclusions", [])))
    require(bool(parts), "invalid_path", "A workspace root cannot be modified.")
    absolute = fold(str(Path(grant["path"]).joinpath(*parts)))
    for directory in protected_directories(bridge.store):
        candidate = fold(str(directory))
        require(
            absolute != candidate
            and not absolute.startswith(candidate + "/")
            and not candidate.startswith(absolute + "/"),
            "protected_control_path",
            "Keyhole code, environment, runtime configuration and their ancestors cannot be modified remotely.",
        )
    require(
        not any(fold(p) in SHELL_HOOKS for p in parts),
        "protected_control_path",
        "Shell startup and automatic environment hooks cannot be modified remotely.",
    )
    return parts


def check_write_walk(bridge, walk) -> None:
    """Refuse writes whose directory chain reaches a protected tree under any spelling."""
    protected = protected_identities(bridge.store)
    for fd in walk.fds:
        require(
            identity(os.fstat(fd)) not in protected,
            "protected_control_path",
            "Keyhole code, environment and runtime configuration cannot be modified remotely.",
        )


def text_from(image):
    if image["kind"] == "absent":
        return ""
    require(image["kind"] == "file", "not_text", "Text editing requires a regular file.")
    try:
        text = base64.b64decode(image["data"]).decode("utf-8")
    except UnicodeError as exc:
        raise KeyholeError(
            "unsupported_encoding",
            "Text writes require UTF-8; convert other encodings locally first.",
        ) from exc
    require("\x00" not in text, "not_text", "Binary data cannot be edited as text.")
    return text


def line_endings(text, original, setting):
    require(setting in ("preserve", "lf", "crlf"), "invalid_newline", "Use preserve, lf or crlf.")
    if setting == "preserve":
        if (
            original
            and "\r\n" in original
            and "\n" not in original.replace("\r\n", "")
            and "\r" not in original.replace("\r\n", "")
        ):
            setting = "crlf"
        else:
            return text
    value = text.replace("\r\n", "\n").replace("\r", "\n")
    return value.replace("\n", "\r\n") if setting == "crlf" else value


class Mutations:
    def __init__(self, bridge):
        self.bridge = bridge

    def history(self, workspace, limit=20):
        with self.bridge.store.lock():
            grant, _ = self.bridge.workspace(workspace)
            with Journal(self.bridge.store) as journal:
                result = journal.history(scope_for(grant), limit)
            result["changes"] = [
                r
                for r in result["changes"]
                if all(self.visible(grant, c["path"]) for c in r["changes"])
            ]
            return self.bridge.done(result)

    def visible(self, grant, path):
        try:
            check_write_path(self.bridge, grant, path)
            return True
        except KeyholeError:
            return False

    def execute(self, operation, workspace, request_id, **args):
        require(
            re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_id or "") is not None,
            "invalid_request_id",
            "Supply a unique 8-80 character request_id using letters, digits, hyphens or underscores; retry unchanged requests with the same ID.",
        )
        with self.bridge.store.lock():
            grant, fs = self.bridge.workspace(workspace)
            require(
                grant.get("access", "ro") == "rw",
                "read_only",
                "This space is read-only. Only the local owner can grant rw using keyhole.",
            )
            fs.available()
            scope = scope_for(grant)
            change_id = digest([scope, request_id])[:32]
            fingerprint = digest([operation, args])
            with Journal(self.bridge.store) as journal, ExitStack() as stack:
                old = journal.get(change_id)
                if old:
                    require(
                        old["scope"] == scope and old["fingerprint"] == fingerprint,
                        "request_id_reused",
                        "This request_id was already used for different arguments.",
                    )
                    require(
                        all(self.visible(grant, p) for p in old["paths"]),
                        "excluded",
                        "Current policy excludes this earlier operation.",
                    )
                    require(
                        old["status"] != "prepared" or operation == "restore",
                        "operation_incomplete",
                        "This request was interrupted. Inspect list_changes and use restore_change; do not retry with a new ID.",
                    )
                    if old["status"] != "prepared":
                        return self.bridge.done(receipt(old, True))
                resuming = old is not None
                original = None
                if operation == "restore":
                    require(
                        re.fullmatch(r"[0-9a-f]{32}", args["change_id"] or "") is not None,
                        "invalid_change_id",
                        "Use a change_id from list_changes.",
                    )
                    original = journal.get(args["change_id"])
                    require(
                        original is not None and original["scope"] == scope,
                        "change_unavailable",
                        "This change does not belong to the current physical workspace.",
                    )
                    require(
                        original["status"] in ("prepared", "committed"),
                        "already_restored",
                        "This change has already been restored.",
                    )
                    require(
                        original.get("recoverable", True) or original["status"] == "prepared",
                        "recovery_disabled",
                        "Recovery was off for this workspace when the change was made, so its previous "
                        "contents were not kept; use your own version control.",
                    )
                    paths = original["paths"]
                else:
                    paths = (
                        [args["destination"], args["path"]]
                        if operation == "move"
                        else [args["path"], args["destination"]]
                        if operation == "copy"
                        else [args["path"]]
                    )
                require(
                    len(set(paths)) == len(paths),
                    "invalid_path",
                    "Source and destination must differ.",
                )
                entries = []
                for path in paths:
                    parts = check_write_path(self.bridge, grant, path)
                    fd, _, walk = stack.enter_context(
                        fs.open("/".join(parts[:-1]) or ".", directory=True)
                    )
                    check_write_walk(self.bridge, walk)
                    entries.append(Entry(fd, parts[-1], walk))
                for interrupted in (original, old):
                    if interrupted and interrupted["status"] == "prepared":
                        for i, entry in enumerate(entries):
                            entry.clean_stage(
                                ".keyhole-stage-" + interrupted["change_id"] + "-" + str(i)
                            )
                before = [entry.snapshot() for entry in entries]
                for pending in journal.pending(scope):
                    require(
                        pending["change_id"] in (args.get("change_id"), change_id)
                        or not {fold(p) for p in paths}.intersection(
                            fold(p) for p in pending["paths"]
                        ),
                        "operation_incomplete",
                        (
                            "An interrupted restore affects this path. Retry restore_change with "
                            f"change_id={pending.get('restores')} and request_id={pending['request_id']}."
                            if pending.get("restores")
                            else "An interrupted operation affects this path. Restore that change before making another edit."
                        ),
                    )
                if operation == "restore":
                    recovery_source = old if resuming else original
                    for i, current in enumerate(before):
                        require(
                            not (
                                recovery_source["status"] == "prepared"
                                and current["kind"] == "directory"
                                and recovery_source["after"][i]["kind"] == "directory"
                                and not recovery_source["after"][i].get("identity")
                            ),
                            "restore_conflict",
                            "An interrupted directory creation has no durable identity; inspect that empty directory locally before resolving history.",
                        )
                        require(
                            same(current, recovery_source["after"][i])
                            or (
                                recovery_source["status"] == "prepared"
                                and same(current, recovery_source["before"][i])
                            ),
                            "restore_conflict",
                            "A later or external edit exists; recovery will not overwrite it.",
                        )
                    after = copy.deepcopy(old["after"] if resuming else original["before"])
                else:
                    source_index = 1 if operation == "move" else 0
                    source = before[source_index]
                    expected = args.get("expected_sha256", "absent")
                    require(
                        (source["kind"] == "absent" and expected == "absent")
                        or (source["kind"] == "file" and source["sha256"] == expected),
                        "version_conflict",
                        "Expected SHA-256 does not match. Use 'absent' only for a new path; read the current hash before replacing a file.",
                    )
                    if operation in ("write", "patch"):
                        require(
                            capabilities(args["path"], 0)["kind"] == "text",
                            "not_text",
                            "Use text writing for code and documents such as Markdown, not Office/PDF/image binaries.",
                        )
                        original_text = text_from(source)
                        if operation == "write":
                            require(
                                len(args["text"].encode()) <= INPUT_LIMIT,
                                "input_too_large",
                                "Full-text input is limited to 1 MiB; use a bounded patch for larger text files.",
                            )
                            text = line_endings(
                                args["text"], original_text, args.get("line_ending", "preserve")
                            )
                            if original_text.startswith("\ufeff") and not text.startswith("\ufeff"):
                                text = "\ufeff" + text
                        else:
                            require(
                                source["kind"] == "file",
                                "path_unavailable",
                                "Patches require an existing UTF-8 file.",
                            )
                            edits = args["edits"]
                            require(
                                1 <= len(edits) <= 64
                                and len(json.dumps(edits).encode()) <= INPUT_LIMIT,
                                "invalid_patch",
                                "Use 1-64 bounded literal replacements.",
                            )
                            text = original_text
                            for edit in edits:
                                old_text = line_endings(edit["old"], original_text, "preserve")
                                new_text = line_endings(edit["new"], original_text, "preserve")
                                count = edit.get("count", 1)
                                require(
                                    old_text
                                    and type(count) is int
                                    and 1 <= count <= 100
                                    and text.count(old_text) == count,
                                    "patch_mismatch",
                                    "Literal patch match count differs; reread and narrow the replacement.",
                                )
                                text = text.replace(old_text, new_text)
                        require(
                            "\x00" not in text, "not_text", "Text writes cannot contain NUL bytes."
                        )
                        after = [file_image(text.encode(), source.get("mode", 0o600))]
                    elif operation == "mkdir":
                        require(
                            source["kind"] == "absent",
                            "path_conflict",
                            "Directory path already exists.",
                        )
                        after = [{"kind": "directory", "mode": 0o700}]
                    elif operation in ("copy", "move"):
                        require(
                            source["kind"] == "file",
                            "unsafe_file",
                            "Copy and move operate on single regular files, not directory trees.",
                        )
                        target = before[1 - source_index]
                        require(
                            target["kind"] == "absent",
                            "destination_exists",
                            "Destination must be absent; use a separate explicit edit to replace it.",
                        )
                        after = [source, dict(ABSENT)] if operation == "move" else [source, source]
                    elif operation == "delete":
                        require(
                            source["kind"] == "file",
                            "unsafe_file",
                            "Recoverable deletion operates on one regular file, not directories.",
                        )
                        after = [dict(ABSENT)]
                    else:
                        raise KeyholeError("invalid_operation", "Unknown mutation.")
                record = {
                    "change_id": change_id,
                    "request_id": request_id,
                    "scope": scope,
                    "workspace": workspace,
                    "fingerprint": fingerprint,
                    "operation": operation,
                    "created_at": now(),
                    "status": "prepared",
                    "recoverable": grant.get("recovery", "on") == "on",
                    "paths": paths,
                    "before": before,
                    "after": after,
                }
                if original:
                    record["restores"] = original["change_id"]
                if resuming:
                    record = old
                for a, b in zip(before, after, strict=True):
                    if a["kind"] == "file" and not same(a, b):
                        require(
                            a["mode"] & stat.S_IWUSR,
                            "file_read_only",
                            "An affected file is not owner-writable; change permissions locally first.",
                        )
                if not resuming:
                    record["evicted_records"] = journal.save(record)
                try:
                    for i, entry in enumerate(entries):
                        self.bridge.authorized()
                        record["after"][i] = entry.apply(
                            before[i], after[i], ".keyhole-stage-" + change_id + "-" + str(i)
                        )
                    for i, entry in enumerate(entries):
                        require(
                            same(entry.snapshot(), record["after"][i]),
                            "commit_uncertain",
                            "A path changed during commit; inspect recovery history.",
                        )
                    self.bridge.authorized()
                    record["status"] = "committed"
                    if original:
                        journal.finish_restore(record, original)
                    else:
                        journal.save(record)
                    return self.bridge.done(receipt(record))
                except Exception as exc:
                    raise KeyholeError(
                        "operation_incomplete",
                        "Commit did not finish cleanly. Recovery change_id="
                        + change_id
                        + (
                            f"; retry restore_change with change_id={original['change_id']} and "
                            f"request_id={request_id}."
                            if original
                            else "; inspect list_changes before retrying or restoring."
                        ),
                    ) from exc
