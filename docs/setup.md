# Setup, step by step

This guide takes you from nothing to "ChatGPT can read my folder" on macOS. OpenAI's pages change from
time to time; when a screen differs from the description, the official
[Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) and
[Developer mode guide](https://developers.openai.com/api/docs/guides/developer-mode) are authoritative.

## 1. Create a tunnel and a runtime key (OpenAI Platform)

1. Open **Settings → Organization → Tunnels** on OpenAI Platform
   ([platform.openai.com/settings/organization/tunnels](https://platform.openai.com/settings/organization/tunnels))
   and create a tunnel. Give it a recognizable name, for example `keyhole-macbook`.
2. Associate the tunnel **only with the ChatGPT workspace you use yourself**. Anyone in an associated
   workspace who holds the Tunnels *Read + Use* permission can select this tunnel when creating an app,
   which would connect their ChatGPT to your Mac.
3. Copy the tunnel id: `tunnel_` followed by 32 lowercase letters or digits. `keyhole setup` checks the
   format, and `tunnel-client` refuses anything else.
4. Open **Settings → Organization → API keys**
   ([platform.openai.com/settings/organization/api-keys](https://platform.openai.com/settings/organization/api-keys))
   and create a **restricted** key with exactly two permissions:
   **Tunnels: Read** and **Tunnels: Use**. Do not use an admin key; `keyhole setup` refuses keys that start
   with `sk-admin-`. Keep the key in your clipboard for the next step; you will not see it again.

Creating and editing tunnels needs *Tunnels: Read + Manage* on your own account; running the client only
needs the restricted key.

## 2. Install the tools and run `keyhole setup`

```sh
brew install uv openai/tools/tunnel-client
uv tool install "keyhole @ git+https://github.com/L-Jovi/keyhole@v0.3.0"
keyhole setup
```

No Python installation is needed: `uv` downloads a private interpreter for Keyhole when none is available.

Without Homebrew (verified on a machine with nothing installed):

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh          # installs uv into ~/.local/bin
uv tool install "keyhole @ git+https://github.com/L-Jovi/keyhole@v0.3.0"
```

Then download `tunnel-client-v0.0.14-darwin-arm64.zip` (Intel: `-darwin-amd64.zip`) and `SHA256SUMS.txt`
from the [releases page](https://github.com/openai/tunnel-client/releases/tag/v0.0.14), and:

```sh
grep " tunnel-client-v0.0.14-darwin-arm64.zip$" SHA256SUMS.txt | shasum -a 256 -c -
unzip -o tunnel-client-v0.0.14-darwin-arm64.zip tunnel-client -d ~/.local/bin
keyhole setup
```

Both paths end with `keyhole` and `tunnel-client` in `~/.local/bin`; the installers tell you if that
directory is not on your `PATH`.

`keyhole setup`:

- checks that `tunnel-client` runs and reports a version this release was tested with (currently 0.0.14).
  A newer version is accepted only when you type its number back;
- asks for the tunnel id, then for the key with hidden input;
- writes `~/.config/keyhole/runtime.json` (tunnel id, a *reference* to the key file, accepted client
  version) and `~/.config/keyhole/runtime.key` (the key, mode 0600). The key is never printed, logged, or
  sent anywhere except to OpenAI by `tunnel-client`.

Use `keyhole --state-dir /path setup` to keep the state somewhere else; then pass the same `--state-dir` to
every later command. The path must not contain symbolic links.

`tunnel-client` keeps the tunnel in a `tmux` session when `tmux` is installed and in a detached background
process otherwise; Keyhole has been tested with `tmux` present.

## 3. Open a folder

```sh
keyhole open ~/Documents/project
keyhole status
```

The first `open` registers a `tunnel-client` runtime named `keyhole`, starts it, and waits until it reports
`"ready": true`. If it does not, the grant is disabled again and the error tells you what to check
(`tunnel-client runtimes status keyhole` shows the client's own view).

## 4. Create your private ChatGPT app

1. In ChatGPT on the web, open **Settings → Security and login** and turn on **Developer mode**. On a
   Business, Enterprise or Edu workspace an admin must allow it first.
2. Go to the **Plugins** page, press **+**, and choose to create an app for your own MCP server.
3. Name it, for example `Keyhole`. A short description helps the model, such as
   *Read and edit the local folders I opened with keyhole.*
4. Under **Connection**, choose **Tunnel** and select the tunnel from step 1 (or paste its id). ChatGPT lists
   only tunnels associated with your workspace.
5. Under **Authentication**, choose **No Authentication**. The tunnel is already authenticated: OpenAI checks
   the caller's workspace and the runtime key on your side. The Keyhole server does not have user accounts,
   so there is nothing for a second login to protect. This is also why the tunnel must stay associated with
   your own workspace only.
6. Leave the write confirmation at its default: ChatGPT asks before tools marked destructive (`write_file`,
   `delete_file`, …) and can remember your answer for a conversation.
7. Save. ChatGPT contacts the server while creating the app; if it reports that the server did not respond,
   check that `keyhole status` shows `"ready": true` and try again.

## 5. Smoke test

Start a new chat, type `@` and pick the app (or choose it from the composer's **+** menu), and try:

> List my workspaces.

> Read `README.md` in `project` and tell me its SHA-256 and line count.

For an editable folder (`keyhole open ~/code/app --access rw --name app`):

> In `app`, read `notes.md`, append a line "reviewed", read it back, then restore the change.

Every response carries `observed_at`, the path, and the hash of what was read or written. If ChatGPT claims
a change that `keyhole history` does not show, the change did not happen.

## 6. Maintenance

- **Upgrading `tunnel-client`.** `brew upgrade openai/tools/tunnel-client` installs a version Keyhole has
  not accepted yet; the next `keyhole open` refuses with `client_version_changed`. Read the client's release
  notes, then run `keyhole setup --accept-client-version` and `keyhole resume --all`. Revoking access
  (`keyhole close`) always works, whatever the installed version.
- **Upgrading Keyhole.** Reinstall with the new tag:
  `uv tool install --reinstall "keyhole @ git+https://github.com/L-Jovi/keyhole@vX.Y.Z"`. When the changelog says
  the tool definitions changed, open the app's details in ChatGPT and press **Refresh**, then start a new
  chat. Opening new folders never needs a refresh.
- **Rotating the key.** Create a new restricted key on OpenAI Platform, run `keyhole setup --rotate-key`,
  then `keyhole resume --all`, and delete the old key.
- **Removing everything.** `keyhole close --all`; delete the app in ChatGPT; delete the key and the tunnel on
  OpenAI Platform; `uv tool uninstall keyhole`; remove `~/.config/keyhole` (this deletes the recovery
  history too).
- **More than one Mac.** Create one tunnel and one key per machine. A tunnel is served by one runtime at a
  time; two machines on the same tunnel would compete for requests.
