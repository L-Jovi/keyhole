# Keyhole — local files for ChatGPT

Let ChatGPT read, and carefully edit, only the local folders you choose. No shell, no public port.

[![Real ChatGPT results: read a note, edit its checklist, restore it and close access](https://raw.githubusercontent.com/L-Jovi/keyhole/main/docs/images/keyhole-demo.gif)](https://github.com/L-Jovi/keyhole/blob/main/docs/media/keyhole-demo.mp4)

**[Watch the 59-second walkthrough](https://github.com/L-Jovi/keyhole/blob/main/docs/media/keyhole-demo.mp4)**
· [Static preview](https://github.com/L-Jovi/keyhole/blob/main/docs/images/keyhole-demo.png) · [What was verified](https://github.com/L-Jovi/keyhole/blob/main/docs/demo.md)

Real ChatGPT responses and checked local files, edited into a walkthrough with fictional notes. Setup and
waiting are omitted; response text is unchanged. Open read-only → allow an edit → restore → close access.

[![CI](https://github.com/L-Jovi/keyhole/actions/workflows/ci.yml/badge.svg)](https://github.com/L-Jovi/keyhole/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/keyhole-mcp.svg)](https://pypi.org/project/keyhole-mcp/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/L-Jovi/keyhole/blob/main/LICENSE)

Keyhole is a small [MCP](https://modelcontextprotocol.io) server that runs on your Mac or Linux computer, plus a command-line
tool, `keyhole`. You open a folder with `keyhole open`; ChatGPT on the web can then read it through OpenAI's
official [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels). Give a folder
`rw`, and ChatGPT can also create and edit text files in it. Every edit is checked against the file's current
hash and recorded before it is applied. Individual file replacements are atomic; retained changes can be restored without overwriting later edits.

Unofficial project; not affiliated with OpenAI.

## What you get

- **Read-only by default.** ChatGPT sees a folder only after you open it, and cannot change a file until you
  say `--access rw`. Nothing ChatGPT does can widen its own access: grants are managed by the local CLI only.
- **Careful edits you can undo.** Writes require the file's current SHA-256; updates based on a stale hash
  are refused. The previous content is kept in a private local history, and `restore_change` rolls it back.
- **Several folders, useful file formats.** Open folders independently with their own ro/rw permissions.
  Text, PDF, Word, PowerPoint, Excel and images come back with hashes and ranges,
  so answers can cite what was read. Document parsers have CPU, time and input-size limits, but run with
  your OS permissions; they are not an OS sandbox.

The connection uses OpenAI's official client. It exposes no public endpoint and no remote shell.
Close the last folder and the tunnel stops. [Compare related projects](https://github.com/L-Jovi/keyhole/blob/main/docs/alternatives.md).

## Start here

**[Follow the complete setup guide →](docs/setup.md)**

```sh
uv tool install keyhole-mcp
keyhole setup
```

The setup wizard can download the verified official client, resume incomplete configuration and open a
read-only example. If PyPI is unavailable, use the exact wheel URL from
[GitHub Releases](https://github.com/L-Jovi/keyhole/releases).

That is the complete first-time path: check your account access, install the tools, connect your own tunnel,
create a private ChatGPT app, and read a real demo file. Follow it once, from top to bottom. No Python, Git,
Node, Docker or tmux installation is required. You need macOS or Ubuntu Linux, ChatGPT Developer mode and permission to create
an OpenAI Platform tunnel and restricted runtime key. Budget about 15 minutes after account access is ready.

The GitHub repository supplies the software. **You create your own tunnel and private ChatGPT app**;
there is no public Keyhole app to add in one click. OpenAI's Secure MCP Tunnel is for private connections,
not public plugin-store distribution.

### What makes it useful

Open several folders independently, keep some read-only, and allow recoverable text edits in others.
Read documents as well as source code, then close a folder when the task is finished. Folder access always
stays under your local control.

### Compatibility

| Surface | Verified / limitation |
| --- | --- |
| macOS | Apple silicon, macOS 15.6 and 15.7.7. Intel and older macOS remain untested. |
| Python | 3.11–3.14 in CI; `uv` supplies Python automatically. |
| Linux | Supported on Ubuntu x86_64. Real ChatGPT read/edit/restore/close verified on Ubuntu 24.04; Ubuntu 22.04/24.04 have Python 3.11–3.14 CI and artifact coverage. Other distributions and Linux ARM are untested. [Evidence and limits](https://github.com/L-Jovi/keyhole/blob/main/docs/platforms.md). |
| Windows | Unsupported. CI checks the refusal message and separate NTFS feasibility probes; neither establishes Windows support. |
| OpenAI client | `tunnel-client` 0.0.14. Newer versions require explicit acceptance at setup. |
| ChatGPT | Pro, web, Chat mode. Other eligible plans and fresh-account onboarding have not been tested here. |
| Clean install | Release wheel tested with Git/Python developer-tool commands unavailable in a simulated environment; not a freshly erased Mac. |

## How it works

```mermaid
flowchart LR
    C[ChatGPT web] -->|tool call| T[OpenAI tunnel endpoint]
    T <-->|outbound long-poll only| TC[tunnel-client on your computer]
    TC -->|stdio JSON-RPC| K[keyhole MCP server]
    K -->|opened folders only| F[(Your files)]
    CLI[keyhole CLI] -.->|grants, ro / rw| K
```

`tunnel-client` is OpenAI's open-source client. It opens an outbound connection and forwards each request to
the Keyhole server over stdio; no port on your computer is exposed. The server reads and writes only inside folders
you opened, and refuses anything else.

## Daily use

```sh
keyhole open ~/Documents/notes                    # read-only
keyhole open ~/code/app --access rw --name app    # editable, shown to ChatGPT as "app"
keyhole open ~/code/app --exclude Private --exclude 'Secret/*'
keyhole access app ro                             # back to read-only
keyhole status                                    # configuration, checks, runtime state
keyhole close --all                               # stop sharing; the tunnel stops too
keyhole resume notes                             # after a reboot, resume only an intended folder
keyhole history --limit 20                        # recent recoverable changes
```

Nothing is shared after a reboot until you run `keyhole resume`. Closing or forgetting a folder never touches
its files.

Example requests in ChatGPT:

> Read `app/src/config.py`, fix the typo in the error message, and read the file back to confirm.

> Search `notes` for "invoice", then quote the matching lines with their file paths.

For an edit, ChatGPT reads the file and its hash, proposes the change, and calls `apply_text_patch` or
`write_file`. Whether ChatGPT asks you before a write is ChatGPT's own setting for the app (it asks by
default and can remember your answer for a conversation); check the app's details if you never see a prompt.

## What ChatGPT can do with each file type

| File type | Read | Create, replace, patch | Copy, move, rename, delete, restore |
| --- | --- | --- | --- |
| UTF-8 text: code, Markdown, JSON, YAML, TOML, shell, HTML, CSS, plain text, any other extension not listed below | by line range | yes (`rw`) | yes (`rw`) |
| UTF-16 / UTF-32 text with a BOM | by line range | no, convert to UTF-8 first | yes (`rw`) |
| PDF | text per page, one page as an image; no OCR | no | yes (`rw`) |
| Word `.docx` | body paragraphs and tables | no | yes (`rw`) |
| PowerPoint `.pptx` | slide and table text | no | yes (`rw`) |
| Excel `.xlsx` | cells, formulas and cached values; no recalculation | no | yes (`rw`) |
| PNG, JPEG, WebP | the image, downscaled if large | no | yes (`rw`) |
| Archives, executables, databases, media, legacy Office (`.doc`, `.xls`, `.ppt`) | metadata only | no | yes (`rw`) |

Copy, move, delete and restore treat every regular file up to 8 MiB as opaque bytes, so they work for
binaries too.
Keyhole cannot write Office documents, PDFs or images, and does not run code, macros or formulas.

Limits: text files up to 8 MiB; documents and images up to 64 MiB; 400 lines or 64 KiB per read; 1 MiB per
`write_file`; single files only, no recursive directory operations. Every response says when it was truncated.

## Recovery history

Every change ChatGPT makes is written to `~/.config/keyhole/changes.sqlite3` (readable by you only) before
the file is touched: the paths, hashes and, by default, the previous content. That is what `restore_change`
and `keyhole history` use. ChatGPT can see paths, hashes and change ids, never the stored content.

- History is bounded to 1000 records or 450 MiB. When it is full, the oldest completed records are dropped
  automatically; an interrupted operation is never dropped. Recovery can temporarily reserve one extra
  record and 48 MiB so a full history does not prevent repair. Retry an interrupted restore with the same
  request id; successful recovery returns history to the normal limits.
- Closing or forgetting a folder keeps its history. `keyhole purge-history --before 2026-01-01 --confirm`
  deletes completed records before a date. Eviction or purge removes both recovery and replay protection
  for those records; history is not a permanent audit log.
- Prefer your own version control? `keyhole open <dir> --access rw --recovery off` keeps paths and hashes
  only. Edits are still hash-checked and interrupted operations can still be repaired, but `restore_change`
  refuses committed edits for that folder.

## Safety on one screen

- Grants live in `~/.config/keyhole/grants.json` and change only through `keyhole`. There is no remote tool to
  add folders, run commands, use Git, or fetch URLs.
- Names such as `.git`, `.ssh`, `.env*`, `*.key`, `*.pem`, `credentials.json`, `node_modules` and agent
  state directories are hidden at any depth, in any letter case. `keyhole open` prints the full list.
- Your own `--exclude` rules: a pattern without a slash hides matching names at any depth (`Private`,
  `*.log`); a pattern with a slash is anchored at the folder root and hides that subtree (`Secret/*`,
  `docs/*.md`). Matching ignores case and Unicode normalization.
- ChatGPT can never modify Keyhole's own code or environment, the state directory, or shell startup files
  such as `.zshrc` and `.envrc`, whatever spelling of the path it uses.
- Symbolic links, hard-linked files and special files are refused; every path is opened component by
  component with `O_NOFOLLOW` and re-checked before and after each operation.
- Hiding by file name is not secret detection: a token inside a shared source file is readable while the
  folder is open, and anything already sent to ChatGPT cannot be recalled by closing the folder.

Threat model, what is out of scope, and how to report a vulnerability: [SECURITY.md](https://github.com/L-Jovi/keyhole/blob/main/SECURITY.md).

## Commands and tools

| Command | What it does |
| --- | --- |
| `keyhole setup` | Resumable wizard; optional verified client installation and read-only sample; `--no-browser` prints links only |
| `keyhole setup --update-client` | Update only a Keyhole-managed client to the version verified by this release |
| `keyhole setup --accept-client-version` / `--rotate-key` | Accept a reviewed external-client update / replace the stored runtime key |
| `keyhole open PATH [--name N] [--access ro\|rw] [--exclude P]... [--recovery on\|off]` | Share a folder and start the runtime; new folders are `ro` |
| `keyhole access NAME ro\|rw` | Change a saved folder's mode |
| `keyhole close [NAME...\|--all]` | Stop sharing; saved paths and history stay |
| `keyhole resume [NAME...\|--all]` | Re-verify saved folders and share them again |
| `keyhole forget [NAME...\|--all]` | Stop sharing and remove the saved configuration |
| `keyhole status` | Configuration, environment checks, runtime state, open folders |
| `keyhole status --human` / `--redact` | Readable next steps / issue-safe JSON without ids, private paths or names |
| `keyhole history [--limit N]` | Recent recoverable changes, including interrupted ones |
| `keyhole purge-history --before DATE --confirm` | Permanently delete completed history before a date |

Commands print JSON by default; `"ok": false` comes with an `error.code` you can act on.
`status --human` prints readable text. The setup/status options above require Keyhole 0.4.0 or later.

| Tool ChatGPT calls | Effect |
| --- | --- |
| `list_workspaces` | Open folders, their `ro`/`rw` mode, exclusions and limits |
| `list_directory`, `search_files` | Paged listing; literal search over names or plain text |
| `read_file` | Text ranges, document pages, images, or just the SHA-256 |
| `write_file`, `apply_text_patch` | Create or replace a UTF-8 file; apply exact literal replacements |
| `create_directory`, `copy_file`, `move_file`, `delete_file` | Single-item file operations, each recoverable |
| `list_changes`, `restore_change` | Recent changes and undo |

The five read tools are marked read-only and the seven write tools destructive, which is what ChatGPT's
write confirmation keys on. Full parameters, error codes and limits: [docs/reference.md](https://github.com/L-Jovi/keyhole/blob/main/docs/reference.md). Upgrades, custom paths and
uninstall: [maintenance](https://github.com/L-Jovi/keyhole/blob/main/docs/maintenance.md).

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| `not_configured` | Run `keyhole setup`. The state directory is `~/.config/keyhole` unless you pass `--state-dir`. |
| Missing or interrupted client download | Rerun `keyhole setup`; check proxy, certificate trust and disk space. Never bypass a checksum failure. |
| Setup was interrupted or already ran | Rerun setup to reuse complete values and finish missing steps. Do not delete the state directory. |
| `client_version_changed` | `tunnel-client` was upgraded. Check the release notes, then `keyhole setup --accept-client-version`. |
| `symlink_in_path` | The folder, the state directory, or one of their parents is behind a symbolic link. Use the physical path (`pwd -P`). |
| `permission_denied` on Desktop, Documents or Downloads | macOS is protecting the folder. Allow your terminal app under System Settings → Privacy & Security → Files and Folders, then retry. |
| `runtime_not_ready` or the app says the server is unreachable | `keyhole status --human`, then follow its message; native diagnostic commands include the selected client's path. Resume only the intended folder with `keyhole resume NAME`. Readiness still needs a real ChatGPT call. |
| Tools missing in ChatGPT after an upgrade | Open the app's details in ChatGPT and press **Refresh**, then start a new chat. New folders never need a refresh. |

## Glossary

- **Workspace**: one opened folder, shown to ChatGPT under its name. Not a ChatGPT workspace (the team
  account).
- **ro / rw**: read-only / read-write. New folders are `ro`.
- **Runtime**: the `tunnel-client` process that keeps your tunnel connected and runs the Keyhole server.
- **Chat vs Work**: ChatGPT composer modes. Keyhole was tested in plain Chat; Work is not required.

## Non-goals, and when to use something else

Keyhole will not run shell commands, tests or Git, drive your screen, expose a public endpoint, or let the
remote side manage grants. If you use the ChatGPT desktop app, its local Work mode may already cover your
needs on that machine. If you want ChatGPT to run commands in a repository, use a coding-oriented tool.
Keyhole is for ChatGPT on the web, documents as evidence, and careful edits.

## Optional: Codex companion

`skills/keyhole/` is a skill for OpenAI Codex that lets it run `keyhole` on your explicit instruction and
explains the boundaries. Link that directory into `~/.codex/skills/` (the layout Codex discovers);
`.codex-plugin/plugin.json` describes the same skill for Codex's plugin installer. Not needed for ChatGPT.

## Repository

- Tracked: the server and CLI (`src/keyhole/`), tests, docs, the Codex skill. Runtime state, keys, grants and
  recovery history live in `~/.config/keyhole/` and are never part of the repository.
- Maintained by [Jovi](https://github.com/L-Jovi); best-effort, single maintainer. Bugs and ideas:
  [issues](https://github.com/L-Jovi/keyhole/issues). How to work on it: [CONTRIBUTING.md](https://github.com/L-Jovi/keyhole/blob/main/CONTRIBUTING.md).
  Changes: [CHANGELOG.md](https://github.com/L-Jovi/keyhole/blob/main/CHANGELOG.md).
- Platform evidence: [validation](https://github.com/L-Jovi/keyhole/blob/main/docs/platforms.md). Release maintainers: [Trusted Publishing](https://github.com/L-Jovi/keyhole/blob/main/docs/publishing.md).

## License

[MIT](https://github.com/L-Jovi/keyhole/blob/main/LICENSE).
