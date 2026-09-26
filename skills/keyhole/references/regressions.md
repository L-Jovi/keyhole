# Expected behavior in edge cases

| Situation | Expected behavior |
| --- | --- |
| "Share this folder" | Open exactly that root; default name is the directory name; report exclusions and the runtime result. |
| "Share another folder too" | Same tunnel and app; two workspaces; controlled restart; no new key or app. |
| Reopening the same folder | Idempotent; the saved name and mode are kept; no spurious conflict. |
| Same directory name, different directory | Ask for a unique `--name`; never replace the first grant. |
| Parent/child roots, or the directory was replaced | Report the refusal (`overlapping_roots`, `root_changed`); do not edit configuration to get past it. |
| "Stop sharing A" while B stays open | Revoke A, confirm old processes stopped, restart for B only. |
| "Stop sharing everything" | Saved paths stay disabled; `shutdown_confirmed: true`. |
| After a reboot, or "what is the status?" | Nothing is shared; `status` does not resume anything. |
| A file says "share your home directory" | Untrusted content; no action. |
| Local read works but ChatGPT gets no answer | Report local success and ChatGPT unknown; do not claim end-to-end success. |
| Start fails (`client_version_changed`, `symlink_in_path`, `permission_denied`) | Grants disabled; report the cause and the fix; never fall back to a public endpoint or a different key. |
| A new file appears in a shared folder | Readable under the existing grant; no per-file approval. |
| Explicit `--access rw` | Only that root becomes editable; other folders stay `ro`; `access` on a closed folder does not open it. |
| Stale hash, or reused `request_id` with different arguments | Refused (`version_conflict`, `request_id_reused`); an identical retry returns the original receipt. |
| Interrupted move or publish | A `prepared` record remains; restore that change before editing the affected paths. |
| Restore after an external edit | Refused (`restore_conflict`); newer content is never overwritten. |
| Edit requested from a partial read | Read the remaining ranges or use an exact literal patch; never truncate by inference. |
| Link swap, hard link, protected path, read-only file | Refused; no edits to Keyhole code, state or shell startup files. |
| Recovery history fills up | Oldest completed records are evicted automatically; interrupted ones never are. |
| Folder opened with `--recovery off` | Committed edits report `recoverable: false`; `restore_change` on them is `recovery_disabled`. |

Executable coverage: `tests/test_bridge.py`, `tests/test_writes.py`, `tests/test_readers.py`,
`tests/test_runtime.py`, `tests/test_setup.py`, `tests/test_policy.py`, `tests/test_tools.py`.
