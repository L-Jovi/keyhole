# Design

One page on how Keyhole works and where its trust boundaries are. Command and tool details are in
[reference.md](reference.md).

Windows details below describe the native candidate. See [platform evidence](platforms.md) for its
acceptance status; the published 0.4.0 release supports macOS and Ubuntu.

## Components

| Part | Runs as | Responsibility |
| --- | --- | --- |
| `keyhole` CLI (`keyhole.cli`) | your terminal, on demand | The only place grants change: open, close, resume, forget, access, setup, history |
| Grants and runtime state | `~/.config/keyhole/` (POSIX 0700; protected owner ACL on Windows) | `grants.json`, `runtime.json`, `runtime.key`, `changes.sqlite3`, `management.lock`, `profiles/` |
| `tunnel-client` runtime | background, supervised by OpenAI's client (a `tmux` session if `tmux` is installed, a detached process otherwise; both keep running after the terminal closes) | Outbound connection to OpenAI; forwards JSON-RPC to the server over stdio |
| MCP server (`keyhole.server`) | child of the runtime | Twelve tools; every call re-checks authorization before touching a file |
| Parser subprocess (`keyhole.parsers`) | per document read | PDF, Office and image parsing under CPU, time and size limits; best-effort address-space limit |

The server is started by the runtime with `python -m keyhole.server --state-dir … --generation …`. It never
listens on a port.

## Grants and generations

`grants.json` holds up to 64 workspaces: name, canonical path, device and inode of the root, `ro`/`rw`,
`recovery`, exclusion patterns, and an `enabled` flag. Every change of that file gets a fresh `generation`
UUID and records the current boot id.

The server is launched with one generation. On every call it re-reads `grants.json` and refuses to continue
(`authorization_changed`) if the generation or boot id differs. Revoking a folder therefore invalidates
in-flight servers immediately, even before they are stopped. After a reboot the boot id differs, so nothing
is shared until `keyhole resume`.

A grant change is a transaction: write the new state, stop the old runtime, confirm that no server or parser
process of this state directory survives, then start the new runtime. If stopping or starting fails, all
grants are disabled and the error is reported. The state is never left claiming more than the runtime
actually provides.

Roots are stored as the kernel reports them (`F_GETPATH` on macOS), so `/System/Volumes/Data/Users/…`,
different letter case, or a symlinked spelling cannot create a second grant for the same directory or
sidestep the checks below.

## Reading

Every path from ChatGPT is a workspace name plus a relative path. The server walks from `/` to the file one
component at a time with `O_NOFOLLOW` on POSIX. Windows opens one component relative to its held parent
NTFS handle, refuses reparse points and holds ancestor handles against rename. Both paths verify before
and after the read that no component was swapped. Links, files with more than one hard link, and special
files are refused. The root's volume and file identity must still match the grant.

Exclusions are applied to every component of every path, on listing, search, read and write alike. Built-in
names (`.git`, `.ssh`, `.env*`, keys, credentials, package caches, agent state) match at any depth; user
patterns follow the rules in the README. Names are compared after Unicode NFC normalization and case
folding, because macOS file systems are case-insensitive and normalization-insensitive.

Documents are parsed in a separate interpreter (`python -I -m keyhole.parsers`) that receives only an
anonymous copy of the bytes, and is killed at 20 seconds wall clock or 15 seconds CPU. It retains the
current user's OS file and network permissions; there is no OS sandbox. A 2 GiB address-space limit is
attempted on POSIX, but may be rejected on macOS. Windows assigns each parser to a Job Object with a
15-second CPU limit and 2 GiB process-memory limit; failure to install these limits aborts parsing. OOXML
containers are checked for size, member paths, entities and DTDs before any library opens them.

## Writing

Writes are allowed only in `rw` workspaces, for UTF-8 text at paths whose extension is not a known document,
image, archive or binary type, and never inside the exclusion rules, the shell startup files, Keyhole's own
code and environment, or the state directory. The last three are protected by string comparison of the
canonical path *and* by device/inode identity of every directory on the way, so no alternative spelling gets
around them.

A mutation runs under the same lock as grant changes:

1. Validate the request id (8 to 80 characters). A repeated id with identical arguments returns the original
   receipt; with different arguments it is refused.
2. Snapshot every affected path (content, mode, identity and Windows DACL) and compare with `expected_sha256`.
3. Record the operation as `prepared` in `changes.sqlite3`, including the snapshots.
4. Stage the new content in the same directory, verify the target has not changed, then publish it with an
   atomic rename. New files use an exclusive hard link on POSIX or a no-replace handle-relative rename
   on Windows, so a concurrent creator never loses.
5. Re-read every path and compare with the intended result; only then mark the record `committed`.
6. If anything fails after step 3, the record stays `prepared`. Further edits to those paths are refused
   until `restore_change` repairs them using the snapshots.

Moves and copies are two single-file steps, not a multi-file transaction; the journal makes an interrupted
step visible and repairable rather than atomic. A UTF-8 BOM and CRLF line endings are preserved.
POSIX preserves mode bits, but not ACLs or extended attributes. Windows retains the owned file's DACL
and read-only attribute; mutation refuses read-only, encrypted, compressed or sparse files and files
with named alternate streams. Other extended metadata and the old file identity are not preserved.
Windows flushes file contents before publication but has no equivalent directory-fsync guarantee;
process-interruption recovery tests do not establish sudden-power-loss durability.

## Recovery history

`changes.sqlite3` (private POSIX mode or Windows ACL, page-count capped) stores each record twice: a public summary (paths, hashes,
status) that tools may return, and the full record with snapshots. Windows ACLs and account SIDs stay
in the private record and are never included in the tool receipt. With `--recovery off` the snapshots are
stripped once a record is committed, so the file cannot be used to restore old content; they are kept while
an operation is in flight so an interruption is still repairable.

The history is bounded to 1000 records or 450 MiB of records. Before a new record is written, the oldest
`committed` or `restored` records are evicted until it fits; `prepared` records are never evicted, and if
only those remain the write is refused with `history_full`. A restore may reserve one extra record and 48 MiB until it completes; retries reuse the same prepared
restore record. The original is pinned during recovery, and both statuses are committed atomically before
eviction returns usage to the normal limits. Freed SQLite pages are reused; `purge-history` additionally
requests incremental vacuum. The database file has a separate 512 MiB page cap.

## The tunnel

Keyhole uses OpenAI's `tunnel-client` exactly as shipped: `runtimes connect` creates a supervised runtime
with a profile written by the client, `runtimes status` and `runtimes stop` manage it, and `runtimes list`
tells a fresh machine that the alias does not exist yet. There is no custom supervisor, no launch agent, no
auto-start at login. The runtime key is referenced as `file:…/runtime.key`; the client reads it, Keyhole never
does. Only a fixed set of environment variables reaches the client.

The client version is pinned by policy, not by code: `runtime.json` records the version you accepted, and a
different installed version blocks new starts until you accept it. Stopping is never blocked.

## Why not more

- **No Office, PDF or image writing.** ChatGPT's document tools do not hand an edited original back through
  MCP, and a rebuilt document is not the user's file. Text editing needs no such transport.
- **No shell.** With a shell, a folder boundary is decorative. Everything Keyhole guarantees depends on the
  absence of code execution.
- **No public endpoint or relay.** Both would add a party that can see the traffic and a surface that can be
  reached without the user's account.
- **No OS sandbox of its own.** Keyhole is an application-level boundary. An OS-level sandbox around the
  server would add defense in depth and is a candidate for a later release.
