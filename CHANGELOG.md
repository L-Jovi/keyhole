# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0.0, minor versions may change
behavior; the CHANGELOG says when the MCP tool definitions changed, because that is when a ChatGPT app needs
a **Refresh**.

## [Unreleased]

### Fixed

- `keyhole open` on a directory that does not exist reported `not_configured` instead of
  `path_missing`, because the state-directory check also caught errors raised while the state
  directory was open. Found by the experimental Linux CI job.

### Changed

- Docs: `tmux` is optional (tunnel-client falls back to a detached process without it); Python is
  downloaded by `uv`, no separate install. `keyhole setup` phrases the missing-tmux note accordingly.

## [0.3.0] - 2026-09-26

First public release, renamed from the private project "Local Evidence Bridge" (CLI `leb`). The MCP tool
definitions are unchanged from 0.2.0; an existing app does not need a refresh.

### Added

- `--recovery on|off` per folder: `off` keeps paths and hashes only instead of the previous file contents.
- `keyhole setup --accept-client-version` and `keyhole setup --rotate-key`.
- `keyhole status` reports whether setup was done, the installed and accepted `tunnel-client` versions, and
  whether `tmux` is present.
- Documentation: setup guide, reference, design notes, security policy.

### Changed

- Packaging: `src/` layout, `keyhole` console script, `python -m keyhole.server`; install with
  `uv tool install`. The custom symlink installer and launcher are gone. Python 3.11 to 3.13.
- Recovery history no longer refuses writes when full: the oldest completed records are evicted;
  interrupted operations are never evicted.
- `tunnel-client` version policy: the version accepted at setup is recorded; a different installed version
  blocks new starts until accepted, and never blocks `close`.
- Error codes for first-day problems are specific and actionable (`not_configured`, `symlink_in_path`,
  `client_version_changed`, `native_client_missing`, `permission_denied`).
- `.claude` joined the built-in exclusions; a project-specific name left the list.
- Setup is `keyhole setup` instead of a separate script.

### Fixed

- Custom `--exclude` patterns could be bypassed by reading a path directly or by changing letter case; they
  now hide the whole subtree and match after case folding and Unicode normalization.
- A folder could be opened through an alias spelling such as `/System/Volumes/Data/Users/…`, which also let
  a read-write grant reach protected paths. Roots are now stored canonically and protected paths are checked
  by device and inode.
- The first `keyhole open` on a fresh machine failed because the runtime alias did not exist yet.

## [0.2.0] - 2026-09-14

### Added

- Read-write grants for UTF-8 text: `write_file`, `apply_text_patch`, `create_directory`, `copy_file`,
  `move_file`, `delete_file`, `restore_change`, `list_changes`; hash-checked, idempotent by request id,
  journaled and recoverable. Verified end to end in ChatGPT Chat mode.

## [0.1.0] - 2026-09-09

### Added

- Read-only local folders for ChatGPT over OpenAI's Secure MCP Tunnel: `list_workspaces`, `list_directory`,
  `search_files`, `read_file` for text, PDF, DOCX, PPTX, XLSX and images.

[Unreleased]: https://github.com/L-Jovi/keyhole/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/L-Jovi/keyhole/releases/tag/v0.3.0
