# Maintenance

First-time users: follow [Your first file in ChatGPT](setup.md) instead. These operations are optional after
setup. Commands below assume the default state directory and the release named in the current release notes.

The setup/status options below require 0.4.0 or later; v0.3.2 users should use its
[versioned maintenance guide](https://github.com/L-Jovi/keyhole/blob/v0.3.2/docs/maintenance.md).

## Upgrades

- **Keyhole:** close the folders you are sharing, run `uv tool upgrade keyhole-mcp`, then resume only the
  folders you intend to reopen. If you installed a release wheel directly, use `uv tool install --reinstall`
  with the exact new wheel URL on the [release page](https://github.com/L-Jovi/keyhole/releases).
  Do not use `pip install keyhole`: that PyPI
  name belongs to an unrelated project.
- **Distribution rename:** releases from 0.3.2 use `keyhole-mcp`; the CLI and Python module remain `keyhole`.
  When upgrading from 0.3.1, first run `uv tool uninstall keyhole`, then `uv tool install keyhole-mcp`.
  This removes the old executable entry without deleting grants, keys or history. The release wheel
  URL remains an alternative to the PyPI package name.
- **Tool definitions:** refresh the private app in ChatGPT only when the changelog says definitions changed.
  Then start a new chat. Opening folders, changing permissions and renaming the app do not require Refresh.
- **Keyhole-managed tunnel-client:** `keyhole setup --update-client` installs the version pinned by the
  current Keyhole release, after confirmation. It does not automatically chase OpenAI's latest version.
  Older versions are retained; there is no automatic cleanup or background updater.
- **External tunnel-client:** `brew upgrade openai/tools/tunnel-client` may install a version Keyhole has not accepted.
  Review the client's release notes, then run `keyhole setup --accept-client-version` and resume the intended
  folders. New starts are refused while versions differ. Closing folders and saving read-only permissions
  still revoke old access even if the new runtime cannot start; inspect `keyhole status` afterwards.

## Custom state and client paths

Use global options before the command. Repeat a custom state directory on every call; setup now saves
the chosen client path, so the client flag need not be repeated:

```sh
keyhole --state-dir /absolute/private/state --tunnel-client /absolute/bin/tunnel-client setup
keyhole --state-dir /absolute/private/state status --human
```

Resolution is explicit `--tunnel-client`, then saved path, then PATH for older configurations without a
saved path. A saved path that disappears produces an error instead of silently choosing another binary.
Run setup with an explicit new path to change it. External clients remain their original installer's
responsibility. Managed files are under `clients/VERSION/PLATFORM-ARCH/` inside the private state directory
and need no separate PATH entry.

State paths must not contain symbolic links. The state directory and its parent folders cannot be shared.
Keep **one active Keyhole installation and state directory per OS user**: the official runtime alias is
`keyhole`, so custom state directories are alternatives, not independent simultaneous instances. On another
computer, create its own tunnel and key; two machines must not serve the same tunnel.

For an existing Local Evidence Bridge installation, retain its established state directory and pass it
explicitly. Changing the CLI name does not migrate local grants, history or credentials. Never copy a key
into a chat or repository to perform the migration.

## Recovery

Use `keyhole history --limit 100` to inspect recent retained records. For a normal interrupted operation,
ask ChatGPT to call `restore_change` with its change id. If a restore itself was interrupted, retry the
**same original change id and request id** reported in the error, rather than starting another restore.
Recovery requires a locally granted `rw` workspace and refuses paths that have changed externally.

History normally stays within 1000 records and 450 MiB. One restore may temporarily use one extra record
and up to 48 MiB so repair remains possible at capacity. Successful recovery returns it to the normal
bounds. The oldest completed records can be evicted; pending records are never evicted. Eviction and purge
also remove replay protection for those records. Keep version control or backups for long-term retention.

## Key rotation

Create a new restricted Read + Use key on OpenAI Platform, run `keyhole setup --rotate-key`, and resume the
folders you intend to share. Test a real ChatGPT read, then revoke the old key on Platform. The terminal
prompt hides the new key.

## Uninstall

1. Run `keyhole close --all` and verify `shutdown_confirmed: true`.
2. Remove your private app in ChatGPT, and revoke its runtime key and tunnel on OpenAI Platform.
3. Run `uv tool uninstall keyhole` for 0.3.1, or `uv tool uninstall keyhole-mcp` for the renamed distribution.
4. A package uninstall preserves managed clients, keys, grants and history. After confirming shutdown,
   you may remove only the state directory's `clients/` subtree to discard its downloaded clients.
   Uninstall external clients with their original installer if no other tool uses them.
5. Optional: remove your state directory only if you also want to permanently discard saved grants and
   recovery history. Removing the package alone preserves that data and never deletes shared source files.

The sample directory is yours too: close its grant before removing it, and check it contains no work you
want to keep. No uninstall command deletes user documents or account resources automatically.

## Directory evaluation (maintainers)

The [Glama listing](https://glama.ai/mcp/servers/L-Jovi/keyhole) evaluates the real stdio server against
one disposable, read-only `Demo` workspace. It contains only a generated `WELCOME.txt`; no Mac folders,
OpenAI credentials or tunnel are connected. Each start creates fresh private state using the current
boot identity and directory identity. The normal CLI still starts with no grants.

`glama.json` names the listing maintainer. The container recipe and bootstrap live in `contrib/glama/`,
outside the installed Python package. From a source checkout with Docker available:

```sh
uv sync --locked --group dev
docker build -f contrib/glama/Dockerfile -t keyhole-glama-test .
uv run python contrib/glama/smoke_test.py --image keyhole-glama-test
```

The smoke test uses a non-root image with networking disabled, a read-only root filesystem and temporary
storage. It checks all 12 tool definitions against the production snapshot, introspection, a real read,
read-only write rejection and path-boundary rejection in both current and legacy MCP modes. Without
`--image`, it tests the same bootstrap locally and checks temporary-state cleanup.

Glama generates its own Dockerfile in **Admin → Dockerfile**. Select Python 3.12, use the following build
steps, and keep the environment schema empty (no required parameters):

```json
["UV_PYTHON_INSTALL_DIR=/opt/glama-python uv sync --locked --no-dev --no-editable --managed-python --python 3.12", "useradd --create-home --uid 10001 keyhole"]
```

The explicit Python install directory keeps the interpreter accessible to the unprivileged server user.
Set CMD arguments to:

```json
["mcp-proxy", "--", "runuser", "-u", "keyhole", "--", "/app/.venv/bin/python", "-I", "/app/contrib/glama/bootstrap.py"]
```

Sync the repository and pin the reviewed commit in Glama before building. Inspect the successful build's
captured tools before creating a Glama release or adding its score badge to a directory submission.
Glama's generated proxy is part of its evaluation environment; it is not used by Keyhole's normal setup.
Keep the listing description explicit that this is a synthetic demo. Container success does not verify
the macOS tunnel workflow or expand the supported OS table. Users should follow [setup](setup.md).
