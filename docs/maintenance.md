# Maintenance

First-time users: follow [Your first file in ChatGPT](setup.md) instead. These operations are optional after
setup. Commands below assume the default state directory and the release named in the current release notes.

## Upgrades

- **Keyhole:** close the folders you are sharing, install the new release's wheel, then resume only the
  folders you intend to reopen. Use the exact wheel URL on the
  [release page](https://github.com/L-Jovi/keyhole/releases). Do not use `pip install keyhole`: that PyPI
  name belongs to an unrelated project.
- **Distribution rename:** releases from 0.3.2 use `keyhole-mcp`; the CLI and Python module remain `keyhole`.
  When upgrading from 0.3.1, first run `uv tool uninstall keyhole`, then `uv tool install WHEEL_URL` with the
  actual new release URL. This removes the old executable entry without deleting grants, keys or history.
- **Tool definitions:** refresh the private app in ChatGPT only when the changelog says definitions changed.
  Then start a new chat. Opening folders, changing permissions and renaming the app do not require Refresh.
- **tunnel-client:** `brew upgrade openai/tools/tunnel-client` may install a version Keyhole has not accepted.
  Review the client's release notes, then run `keyhole setup --accept-client-version` and resume the intended
  folders. New starts are refused while versions differ. Closing folders and saving read-only permissions
  still revoke old access even if the new runtime cannot start; inspect `keyhole status` afterwards.

## Custom state and client paths

Use the global options before the command, and repeat them on every call:

```sh
keyhole --state-dir /absolute/private/state --tunnel-client /absolute/bin/tunnel-client setup
keyhole --state-dir /absolute/private/state --tunnel-client /absolute/bin/tunnel-client status
```

Paths must not contain symbolic links. The state directory and its parent folders cannot be shared.
Keep **one active Keyhole installation and state directory per OS user**: the official runtime alias is
`keyhole`, so custom state directories are alternatives, not independent simultaneous instances. On another
Mac, create its own tunnel and key; two machines must not serve the same tunnel.

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
4. Optional: remove your state directory only if you also want to permanently discard saved grants and
   recovery history. Removing the package alone preserves that data and never deletes shared source files.
