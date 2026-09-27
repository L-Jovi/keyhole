# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0.0, minor versions may change
behavior; the CHANGELOG says when the MCP tool definitions changed, because that is when a ChatGPT app needs
a **Refresh**.

## [Unreleased]

## [0.4.0] - 2026-09-27

The MCP tool definitions are unchanged; an existing ChatGPT app needs no Refresh.

### Added

- Resumable terminal setup with optional SHA-256-verified official client installation, saved client
  paths, explicit read-only sample creation, and `--no-browser` / `--update-client` options.
- `status --human` for next steps and `status --redact` for allowlisted issue reports.
- Required Ubuntu 22.04/24.04 Python 3.11–3.14 checks, wheel/sdist/tool-install tests, real official-client
  download checks and a separate non-admin Windows feasibility probe. Windows remains unsupported.
- Ubuntu x86_64 support, with a real Ubuntu 24.04 official Tunnel → ChatGPT read, read-only refusal,
  edit, restore and closed-access acceptance on 2026-09-27. Ubuntu 22.04 has automated coverage.
- PyPI distribution as `keyhole-mcp`, published through a manually gated Trusted Publishing workflow.
  PyPI and GitHub release attachments contain the same verified wheel and sdist bytes.
- Related-project comparison and explicit platform acceptance criteria.
- A 59-second walkthrough, short GIF and static preview from real ChatGPT read/edit/restore/close
  results with fictional notes. Setup and waiting are explicitly omitted.

### Fixed

- Setup can continue a missing key or configuration without deleting grants/history, and key rotation
  does not require a runnable tunnel client. Saved client paths do not silently fall back to PATH.
- Native client failure bodies are withheld from CLI output to avoid echoing secrets or private paths.
  Linux permission errors no longer suggest macOS settings.

### Changed

- Glama maintainer metadata and a disposable, read-only evaluation container using the existing stdio
  server. CI checks the real tool schemas, synthetic reads and permission boundaries in Linux Docker.
  This is a directory evaluation fixture, not a new installation method or end-to-end proof.
  Production tools and dependencies are unchanged; no ChatGPT app Refresh is needed.

## [0.3.2] - 2026-09-26

The MCP tool definitions are unchanged; an existing ChatGPT app needs no Refresh.

### Fixed

- Permission downgrades revoke the old writer before runtime checks, even after a client-version change
  or when the shared root is missing.
- Recovery remains possible when history is full. An interrupted restore retries in place, completed
  recovery stays within retention limits, and a failed reservation does not discard earlier history.
- Pending writes and state-directory protection cover case and Unicode aliases. Runtime cleanup only
  selects parser processes for the requested state directory.
- Source archives include the plugin metadata needed by the test suite. Suggested next commands retain
  custom state and tunnel-client paths.

### Changed

- The distribution is `keyhole-mcp` to avoid the unrelated PyPI package named `keyhole`; the CLI and module
  stay `keyhole`. No PyPI publication is implied; see the
  [upgrade notes](docs/maintenance.md#upgrades).
- One complete first-use guide replaces overlapping README/setup instructions, with an existing demo file,
  explicit account prerequisites and PATH steps. Maintenance is separate.
- Parser documentation states the actual limits: process separation is not an OS sandbox; memory limits are
  best-effort and parsers retain the user's OS permissions.
- Clean-install evidence is described as a simulation, not a freshly erased machine.

## [0.3.1] - 2026-09-26

The MCP tool definitions are unchanged; an existing ChatGPT app needs no Refresh.

### Added

- On Windows, every `keyhole` command prints one `unsupported_platform` error instead of a Python
  traceback. `keyhole setup` on Linux says that only the unit tests run there.
- Each release ships a wheel; installing from it needs no Git or Xcode command line tools.

### Fixed

- `keyhole setup` now checks the tunnel id format the way `tunnel-client` does (`tunnel_` plus 32
  lowercase letters or digits), so a typo is caught at setup instead of at the first `open`.
- `keyhole open` on a directory that does not exist reported `not_configured` instead of
  `path_missing`, because the state-directory check also caught errors raised while the state
  directory was open. Found by the experimental Linux CI job.

### Changed

- Python 3.14 is tested in CI; `uv tool install` picks it on a machine without Python.
- The install commands use the release wheel instead of a Git URL, which failed on Macs without Xcode
  command line tools.
- `tmux` is not mentioned by `keyhole` any more: the tunnel runs in a detached process without it, and that
  path was verified end to end (including after closing the terminal). `keyhole status` no longer lists it.
- Docs: a requirements table states exactly what was verified (macOS 15.6 on Apple silicon, Python
  3.11–3.14, `tunnel-client` 0.0.14, a Pro account in Chat mode) and what was not (Intel Macs, older macOS,
  Linux end to end, other ChatGPT plans, `tunnel-client` 0.0.15); the no-Homebrew install path is spelled
  out and was verified in a simulated environment with developer-tool commands unavailable.

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

[Unreleased]: https://github.com/L-Jovi/keyhole/compare/v0.3.2...HEAD
[0.3.2]: https://github.com/L-Jovi/keyhole/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/L-Jovi/keyhole/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/L-Jovi/keyhole/releases/tag/v0.3.0
