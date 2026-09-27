# Reference

Exact behavior of the CLI and the MCP tools. For the ideas behind them see [design.md](design.md).

## CLI

Global options, valid before the command: `--state-dir DIR` (default `~/.config/keyhole`) and
`--tunnel-client PATH` (overrides the path saved by setup; legacy configurations use `PATH`).

| Command | Notes |
| --- | --- |
| `setup [--no-browser]` | Needs a terminal. Reuses configuration and fills missing steps without resetting grants/history. Optional verified client download and new read-only sample. |
| `setup --update-client` | Updates only a Keyhole-managed client to this release's pinned version. Keeps older versions. |
| `setup --accept-client-version` | Records the installed `tunnel-client` version as accepted. |
| `setup --rotate-key` | Replaces `runtime.key`; `runtime.json` is untouched. |
| `open PATH [--name NAME] [--access ro\|rw] [--exclude PATTERN]... [--recovery on\|off]` | New folders default to `ro` and `recovery on`. Reopening a saved folder keeps its settings unless a flag says otherwise; different `--exclude` rules are refused (`forget` first). |
| `access NAME ro\|rw` | Changes a saved folder; does not reopen a closed one. |
| `close [NAME...] \| --all` | Revokes; the last close stops the runtime and reports `shutdown_confirmed`. |
| `resume [NAME...] \| --all` | Re-verifies each root (canonical path, device, inode) before sharing. |
| `forget [NAME...] \| --all` | Revokes and deletes the saved configuration. Files and history stay. |
| `status` | Never starts anything. |
| `status --human` | Readable diagnostics and next action; no automatic repair or grant changes. |
| `status --redact` | Allowlisted JSON with versions, health flags and counts. No free-text errors, paths, ids or workspace names. |
| `history [--limit N]` | 1 to 100 records, newest first, plus usage against the limits. |
| `purge-history --before YYYY-MM-DD --confirm` | Deletes `committed` and `restored` records created before the date. |

Output is JSON on stdout. Success: `{"ok": true, ...}`. Failure: `{"ok": false, "error": {"code", "message"}}`
with exit status 1; usage errors exit 2. `status --human` is the text-output exception. Interactive setup
prompts go to stderr; cancelling does not discard existing configuration or history.

### Error codes (CLI)

| Code | Meaning |
| --- | --- |
| `not_configured` | No state directory or `runtime.json`; run `keyhole setup`. |
| `setup_changed` | Another local operation changed configuration while setup was waiting; rerun setup. |
| `demo_exists` | The requested directory or demo workspace already exists; it was not overwritten. |
| `client_download_failed`, `client_checksum_mismatch`, `client_archive_invalid` | Download failed or violated the pinned size, hash or archive layout. No unverified client is executed. |
| `client_not_managed`, `client_install_damaged` | External client cannot be updated here, or an existing managed bundle differs from verified bytes. |
| `client_install_busy` | Windows still has an installation file in use or denies access after bounded retries. Wait briefly and rerun setup; the previous client was not replaced. |
| `invalid_tunnel_id`, `invalid_key` | The value does not look like a tunnel id / a non-admin runtime key. |
| `native_client_missing`, `native_client_failed` | `tunnel-client` not found, or `--version` failed. |
| `client_version_changed`, `client_version_untested` | Installed version differs from the accepted one; or an untested version was not confirmed. |
| `runtime_config`, `runtime_key_permissions` | `runtime.json` malformed, or `runtime.key` has unsafe ownership or permissions. |
| `symlink_in_path`, `path_missing`, `not_a_directory`, `permission_denied` | The folder or state directory cannot be opened safely; the message names the component. |
| `protected_root`, `excluded_root`, `invalid_root` | Home, `/System`, shallow, state-directory or excluded roots are refused. |
| `alias_conflict`, `overlapping_roots`, `workspace_limit`, `workspace_unknown`, `invalid_selection` | Grant bookkeeping. |
| `root_changed`, `path_not_canonical` | The saved root was replaced or is now spelled differently; `forget` and open again. |
| `policy_conflict`, `invalid_pattern` | Exclusion rules differ from the saved ones, or a pattern is malformed. |
| `native_runtime_failed`, `runtime_timeout`, `runtime_not_ready`, `stop_unconfirmed` | The official client refused, hung, did not become ready, or could not be confirmed stopped. Grants are disabled when a start fails. |
| `manager_busy`, `state_permissions`, `boot_identity_unavailable` | Another command holds the lock; state files have wrong ownership or permissions; the boot id could not be read. |
| `history_full`, `invalid_cutoff` | Ordinary history is full; restore a pending change or retry the interrupted restore with its same request id. Purge date invalid. |
| `unsupported_platform` | The OS is outside this version's platform gate. 0.4.0 and older refuse Windows; 0.5.0 and later require Windows 11 or a corresponding newer Server build. Nothing was changed. |
| `windows_process_query_failed`, `windows_process_stop_failed` | Windows OS identity queries failed, or a verified Keyhole process did not exit. Sharing stays closed; use the diagnostic message rather than running as administrator. |
| `local_failure` | An unexpected OS error; the message contains the exception. |

## State directory

| File | Content |
| --- | --- |
| `grants.json` (0600) | Workspaces, generation, boot id. |
| `runtime.json` (0600) | Tunnel id, key file reference, accepted client version; optional `tunnel_client_path` and `tunnel_client_source` (`managed` or `external`). Schema version remains 1. |
| `runtime.key` (0600) | The runtime API key, read by `tunnel-client` only. |
| `changes.sqlite3` (0600) | Recovery history. |
| `management.lock` | Lock shared by grant changes and writes. |
| `profiles/` | Profile generated by `tunnel-client runtimes connect`. |
| `clients/VERSION/PLATFORM-ARCH/` | Opt-in official bundle, executable files 0700, other files 0600; never shared over MCP. |

The modes above apply to POSIX. On Windows, state uses protected ACLs allowing the current user,
SYSTEM and local Administrators; directories must not sit behind links or junctions.
Environment passed to `tunnel-client`:
`HOME`, `PATH`, `TMPDIR`, `LANG`, `LC_CTYPE`, `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, plus
`HEALTH_LISTEN_ADDR=127.0.0.1:0`, `MCP_STDIO_SEND_INITIALIZED_NOTIFICATION=true` and `PYTHONUTF8=1`.
Windows also retains `SYSTEMROOT`, `WINDIR`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `TEMP`, `TMP`
and `COMSPEC`. No general environment dump is passed to the runtime.

## MCP tools

Every tool takes a `workspace` name (except `list_workspaces`) and relative paths using `/`. Results are JSON
with `observed_at` and `authorization_generation`; images are returned as image content. At most two calls
run at once (`server_busy`), and a response is capped at 64 KiB (`response_budget`).

| Tool | Parameters | Result highlights |
| --- | --- | --- |
| `list_workspaces` | — | `workspaces[]` with `name`, `status`, `access`, `recovery`, `exclusions`; `policy` with limits and rules |
| `list_directory` | `path=".", limit=100 (1–200), cursor` | `entries[]` (name, type, size, modified, capabilities), `excluded_entries`, `next_cursor` |
| `search_files` | `query (1–256), path, kind="name"\|"text", limit=50 (1–100), cursor, case_sensitive` | `matches[]`; text search covers plain-text files only and reports scanned range |
| `read_file` | `path, mode="auto"\|"text"\|"image"\|"metadata"\|"hash", start=1, limit=0, sheet, cell_range, expected_sha256` | `sha256`, `range`, `lines[]` / `items[]` / `rows[]` / image, `truncated`, `next_start` |
| `write_file` | `path, text (≤1 MiB), expected_sha256 \| "absent", request_id, line_ending="preserve"\|"lf"\|"crlf"` | receipt with `change_id`, `recoverable`, `evicted_records` |
| `apply_text_patch` | `path, edits[] {old, new, count=1} (1–64), expected_sha256, request_id` | as above; each `old` must occur exactly `count` times |
| `create_directory` | `path, request_id` | parent must exist |
| `copy_file`, `move_file` | `path, destination, expected_sha256, request_id` | destination must be absent, same workspace |
| `delete_file` | `path, expected_sha256, request_id` | file goes to the recovery history |
| `list_changes` | `limit=20 (1–100)` | `changes[]` summaries; never file contents |
| `restore_change` | `change_id, request_id` | refuses later external edits; retry an interrupted restore with the same two ids |

`request_id` is 8 to 80 characters of letters, digits, `-` and `_`. Repeating a completed request returns
the original receipt with `replayed: true`; the same id with different arguments is `request_id_reused`.

### Error codes (tools)

`authorization_changed`, `offline`, `workspace_unavailable`, `invalid_path`, `excluded`, `path_unavailable`,
`unsafe_file`, `root_changed`, `content_changed`, `path_changed`, `file_too_large`, `unsupported_encoding`,
`binary_file`, `invalid_range`, `invalid_hash`, `unsupported_mode`, `invalid_cursor`, `invalid_limit`,
`invalid_search`, `directory_budget`, `parser_busy`, `parser_timeout`, `parser_failed`, `invalid_document`,
`encrypted_document`, `archive_budget`, `image_budget`, `unsupported_image`, `unsupported_format`;
for writes: `read_only`, `invalid_request_id`, `request_id_reused`, `operation_incomplete`,
`protected_control_path`, `not_text`, `input_too_large`, `invalid_patch`, `patch_mismatch`,
`invalid_newline`, `version_conflict`, `path_conflict`, `destination_exists`, `file_read_only`,
`history_full`, `history_unsafe`, `history_corrupt`, `commit_uncertain`, `invalid_change_id`,
`change_unavailable`, `already_restored`, `restore_conflict`, `recovery_disabled`, `server_busy`,
`response_budget`, `unavailable`.

## Limits

| What | Limit |
| --- | --- |
| Text file read or written | 8 MiB |
| Document or image read | 64 MiB; `mode=hash` also 64 MiB |
| Per read call | 400 lines / 64 KiB of text; 5 PDF pages; 400 DOCX blocks; 20 slides; 2000 cells |
| `write_file` input | 1 MiB |
| Directory listing | 10 000 entries per directory; 200 per page |
| Search | 20 000 entries or 32 levels per tree; 2000 files / 24 MiB / 5 s per call |
| Parser | 20 s wall clock, 15 s CPU, 256 MiB zip expansion, 25 megapixels input, about 2 megapixels / 3 MiB output; POSIX attempts 2 GiB address space; Windows uses a 2 GiB process-memory Job Object limit |
| Workspaces | 64 |
| Recovery history | 1000 records / 450 MiB; oldest completed evicted first. A restore can temporarily reserve one extra record and 48 MiB until it completes. |
| Concurrency | 2 tool calls; 2 parsers |
