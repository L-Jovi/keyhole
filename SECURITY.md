# Security

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository (**Security → Report a
vulnerability**). Do not open a public issue for anything that could let a remote party read or change files
outside an opened folder. I aim to acknowledge reports within a week; this is a single-maintainer project,
so fixes are best effort.

Only the latest release is supported.

## What Keyhole protects

Keyhole's job is to make sure that ChatGPT, or anything else talking to the tunnel, can only:

- read files inside folders you opened, minus the exclusion rules;
- edit UTF-8 text inside folders you opened with `rw`, and copy, move or delete regular files (including
  binaries), only with the current file hash; never modify Keyhole's own code, environment, state or shell
  startup files;
- never run programs, expand its own access, or reach anything through a public port.

Every path is opened component by component with `O_NOFOLLOW` and checked against the grant's device and
inode; links, hard-linked files and special files are refused; roots are stored canonically so alias
spellings do not bypass checks; grant changes and writes are serialized under one lock; every write is
journaled before it is applied and verified after.

## What it does not protect against

| Risk | Why it is out of scope, and what helps |
| --- | --- |
| Content you shared is now in ChatGPT | Anything ChatGPT read has left your machine. Closing the folder stops later reads; it cannot recall earlier ones. Share the narrowest folder you can, read-only. |
| Secrets inside an opened folder | Exclusions work on file names, not on content. A token inside `config.py` is readable while the folder is open. Keep secrets in files the built-in rules hide (`.env`, `*.key`, …), or exclude them explicitly. |
| Prompt injection from files | A file in an opened folder can contain text that tries to steer ChatGPT. Keyhole marks such content as untrusted data, but the model still reads it. Keep `rw` off for folders you do not fully control, and keep write confirmation on. |
| A compromised OpenAI account or workspace | Anyone who can use your ChatGPT app can use your folders. The tunnel should be associated only with your own workspace; the runtime key should have Tunnels *Read + Use* only, so a leaked key cannot create or modify tunnels. |
| Another local program editing the same file | Keyhole checks the file before and after each step and refuses on change, but it does not lock files against other editors. Avoid editing the same file in two places at once. |
| Loss of metadata | POSIX mode bits, BOM and line endings are preserved; ACLs, extended attributes and the inode are not. |
| Bugs in third-party parsers | Documents are parsed in a separate process with CPU, wall-clock, input-size and zip-expansion limits. A 2 GiB address-space limit is attempted where the OS supports it; macOS may reject it. The process retains the current user's file and network permissions. Process separation limits failures, but is not an OS sandbox or protection against a compromised parser. |
| Other users on the same Mac | State files are 0600/0700, but the design assumes one user per state directory. |

## Data flow and storage

- Outbound only: `tunnel-client` connects to OpenAI and long-polls for requests. Keyhole opens no listening
  socket. No telemetry, no third party.
- What leaves the machine: the tool results ChatGPT asked for (file excerpts, listings, hashes, rendered
  images) and the runtime key, which `tunnel-client` sends to OpenAI to authenticate.
- What stays: `~/.config/keyhole/` holds `grants.json`, `runtime.json`, `runtime.key`, `changes.sqlite3`
  (recovery snapshots, private to you) and the profile `tunnel-client` generated. The official client also
  manages its own runtime registry and logs. `keyhole` output never contains the key.

## Hardening checklist

- One tunnel and one restricted key per machine; rotate with `keyhole setup --rotate-key`.
- Open folders read-only unless you are about to ask for an edit; switch back with `keyhole access NAME ro`.
- Use `--exclude` for subfolders that must never be visible, and check the `exclusions` in
  `list_workspaces`.
- `keyhole close --all` when you are done; nothing is shared after a reboot until you resume.
- Keep `tunnel-client` at a version Keyhole was tested with, or review its release notes before accepting a
  newer one.
