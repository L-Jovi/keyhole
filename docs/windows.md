# Windows setup

> Native Windows support is under review in this branch. The published 0.4.0 release refuses Windows.
> The candidate requires a reviewed wheel; do not use `uv tool install keyhole-mcp` to obtain this
> unreleased implementation. See [platform evidence](platforms.md) for completed checks and remaining
> acceptance. The steps below are the candidate's intended first-time path.

Keyhole lets your private ChatGPT app read the local folders you choose. New folders are read-only.
You can explicitly allow hash-checked text edits and restore retained changes. It does not give ChatGPT
a command prompt or access to the rest of your computer.

## 1. Check the requirements

- Windows 11, a normal non-administrator PowerShell window, and local NTFS storage.
- x64 CPython 3.11–3.14. On Windows ARM, use x64 Python under Windows emulation; native ARM Python and
  all its document-parser wheels have not been validated. `uv` can supply the specified Python.
- ChatGPT web with Developer mode, and OpenAI Platform permission to create a tunnel and runtime key.
  A ChatGPT subscription alone does not establish Platform permissions. See the
  [official tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).

Start with the fictional sample folder in your user profile. Network shares, mapped network drives,
FAT/exFAT, WSL paths, junctions, symbolic links and cloud placeholder files are refused. A synced
Documents folder can contain links or placeholders; an ordinary local folder is the least surprising
first test. Do not move personal files merely to try Keyhole.

## 2. Install in PowerShell

Install `uv` using its [official Windows instructions](https://docs.astral.sh/uv/getting-started/installation/).
For example, in a normal PowerShell window:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

This runs Astral's installer; inspect it first if desired. The execution-policy option applies to this
installer process, not a permanent machine setting. On a managed computer, use the organization's
approved installation method instead of overriding its policy.

Close and reopen PowerShell, then check:

```powershell
uv --version
```

If `uv` is still not found after the standalone installation, add its usual location to this window:

```powershell
$env:Path = "$HOME\.local\bin;$env:Path"
uv --version
```

For a reviewed candidate wheel saved in Downloads, replace the filename below with its exact name:

```powershell
uv tool install --python cpython-3.12-windows-x86_64-none --no-build "$HOME\Downloads\REVIEWED-CANDIDATE.whl"
$env:Path = "$(uv tool dir --bin);$env:Path"
keyhole --version
keyhole setup
```

`uv` downloads Python if needed and isolates the package. `--no-build` requires prebuilt dependencies;
no Visual Studio, Git, Node, WSL or compiler is needed. Do not install the unrelated PyPI package `keyhole`.
`keyhole.exe` is placed in the directory printed by `uv tool dir --bin`. The PATH assignment above lasts
for this window only. To make it available in future terminals, explicitly run `uv tool update-shell`,
then close and reopen PowerShell and verify `keyhole --version` again.

## 3. Follow the setup wizard

Setup first detects an existing official `tunnel-client`. If none is available, it shows the download
size and destination and asks permission to install a fixed, SHA-256-verified official bundle.
You do not need to choose an archive or add another executable to PATH.

1. Open [Platform → Tunnels](https://platform.openai.com/settings/organization/tunnels). Create a tunnel
   for this computer and associate only your intended ChatGPT workspace. Creation requires
   **Tunnels: Read + Manage**. Copy its `tunnel_...` id into the terminal.
2. Open [Platform → API keys](https://platform.openai.com/settings/organization/api-keys). Create a
   restricted runtime key with **Tunnels: Read + Use**. Enter it only in the terminal's hidden prompt.
   The person selecting the tunnel in ChatGPT also needs Read + Use permission.
3. Accept creation of a new sample directory. The default is `$HOME\KeyholeDemo`, shared as `demo`
   read-only. It contains fictional notes; an existing folder is never overwritten or silently shared.

Expected: configuration is saved, then opening the sample reports `"ready": true` and `"access": "ro"`.
Rerunning `keyhole setup` continues missing steps and preserves keys, grants and recovery history.
Use `keyhole setup --no-browser` to open the displayed official links yourself.

State is stored in `$HOME\.config\keyhole`. Its protected Windows permissions grant access to your
user, Windows SYSTEM and local Administrators. The key is never printed. The client path is saved;
if it disappears, setup reports the problem instead of silently selecting another installation.
Use one active state directory and installation per Windows user.

## 4. Connect ChatGPT and verify a real read

Leave `demo` open. In ChatGPT web:

1. Enable Developer mode in Settings, then create a private MCP app under Plugins.
2. Name it **Keyhole**. Suggested description: “Read only the local folders I open. Explicit local rw
   grants allow hash-checked text edits with recovery. No shell or public port.”
3. Choose **Connection: Tunnel** and the tunnel created above.
4. Choose **Authentication: No Authentication** for the MCP server. The OpenAI tunnel authenticates
   the connection. Keep write confirmations enabled, then save the app.
5. Start a new Chat, type `@Keyhole`, and select the actual app from the menu.

Ask:

> In workspace `demo`, read `notes.md` using Keyhole. Quote the three ideas and give the SHA-256.

Success means a real `read_file` result matching the local sample. “Configuration saved” and “ready”
are useful intermediate states, but do not prove that ChatGPT read a file.

## 5. Edit deliberately, restore, then close

```powershell
keyhole access demo rw
```

Ask ChatGPT to read the note again, turn the ideas into a TODO checklist using the current hash, and
return its change id. Inspect the file locally. Then ask it to restore that change id and read again.
Recovery refuses to overwrite a later external edit and is subject to the retained-history limits.

```powershell
keyhole close demo
```

Ask for a **new tool call** reading the note. It must fail; the last closed folder also stops the tunnel.
Earlier content remains in the chat. After rebooting, resume only a folder you intend to share:

```powershell
keyhole resume demo
keyhole open "$HOME\My local notes" --name notes
keyhole status --human
keyhole status --redact
```

The example path must already exist. Quote paths containing spaces; Chinese names are supported.
Use `status --redact` when asking for help, rather than posting the full state or configuration.

## If something fails

| Symptom | Next step |
| --- | --- |
| `unsupported_platform` | Check the installed version and Windows version. 0.4.0 has no native Windows backend. |
| `keyhole` is not recognized | Run `uv tool dir --bin`, add that directory to this window's PATH, then use `uv tool update-shell` for future terminals. |
| A saved client is missing | Rerun setup, or use `keyhole --tunnel-client 'C:\Tools\tunnel-client.exe' setup` with your actual executable. |
| Download interrupted or checksum mismatch | Rerun setup; check network/proxy and disk space. Do not disable the checksum check. |
| Link, cloud placeholder or unsupported filesystem | Try the new local NTFS sample folder; use the actual local path rather than a junction or network alias. |
| A file cannot be edited | Close editors holding incompatible locks. Read-only ACLs/attributes, EFS encryption, NTFS compression/sparse files and named alternate streams prevent replacement; Keyhole does not remove those protections or metadata. |
| `windows_process_query_failed` | Windows PowerShell or WMI could not answer the OS identity query. Sharing stays closed; do not solve this by running Keyhole as administrator. |
| ChatGPT cannot find the tunnel | Check its workspace association and the selecting user's Tunnels Read + Use permission. A key alone does not associate a workspace. |
| Sample exists after an interrupted setup | Keep it. Use the exact retry command printed by setup, or explicitly open it as `demo`; do not delete it to bypass the prompt. |

In-place Windows edits preserve the owned file's DACL access entries, their order and inheritance
protection, plus the read-only attribute, BOM and line endings. New files, including copy and move
destinations, start private to you, SYSTEM and Administrators. Restoring a move restores the original
file's permissions at its original path. Windows may normalize automatic
inheritance bookkeeping and flags that apply only to child objects, since files have no children.
Other extended metadata and the old file identity are not preserved. Recovery tests cover process
interruption, not sudden power loss. Parsers run with your OS permissions; their Job Object limits are
not a filesystem or network sandbox.

For upgrades and uninstall, use [maintenance](maintenance.md). Close all grants before upgrading or
uninstalling. `uv tool uninstall keyhole-mcp` removes the executable and its Python environment while
preserving state, downloaded clients, recovery history and your documents.
