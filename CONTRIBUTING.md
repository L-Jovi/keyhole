# Contributing

Thanks for looking. Keyhole is small on purpose; the most useful contributions are bug reports with
`keyhole status --redact`, fixes with a test, and documentation that makes setup clearer.

## Working on the code

```sh
git clone https://github.com/L-Jovi/keyhole
cd keyhole
uv sync --locked --group dev
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run keyhole --help
```

Tests use temporary directories and a fake `tunnel-client`; they never start a tunnel or touch
`~/.config/keyhole`. The two tests that spawn the real server need `sysctl` and `ps`, so run the suite from a
normal terminal rather than a restricted sandbox.

## Rules of the road

- **Tool definitions are frozen** by `tests/test_tools.py`. Changing a tool's name, parameters, description
  or annotations means every user must press Refresh in ChatGPT; do it deliberately, update
  `tests/tool_snapshot.json`, and say so in the changelog.
- **Trust boundaries need tests.** Anything that touches grants, exclusions, protected paths, the walk in
  `filesystem.py`, or the write pipeline in `writes.py` comes with a test that shows the boundary holding.
- **No new dependencies without a reason** stated in the pull request. Runtime dependencies are the MCP SDK
  and the document parsers, nothing else.
- Keep the style: `ruff` clean, type hints on public functions, short English comments only where the reason
  is not obvious. Commit messages follow Conventional Commits (`fix:`, `feat:`, `docs:` …).
- Security problems go through [SECURITY.md](SECURITY.md), not the issue tracker.

## Releasing (maintainer)

Follow [Publishing a reviewed release](docs/publishing.md). Build once, test wheel and source archive
outside the checkout, then publish the same artifacts through the manually approved workflow. Account
configuration and first PyPI publication require maintainer authorization. Keep the current working install
link until the new public artifacts have been verified. Do not describe an unreleased wizard as part of an
older wheel, or a passing CI matrix as real ChatGPT acceptance on a new OS.

## GitHub repository settings (maintainer)

Keep the repository page consistent with the README so the name alone is never the only explanation:

- **About**: `MCP server for ChatGPT to read and edit local files on macOS, with recoverable writes and no public port.`
- **Website**: the README (`https://github.com/L-Jovi/keyhole#readme`).
- **Topics**: `chatgpt`, `openai`, `mcp`, `mcp-server`, `model-context-protocol`, `secure-mcp-tunnel`,
  `chatgpt-plugin`, `developer-mode`, `local-files`, `filesystem`, `file-access`, `ai-safety`, `macos`,
  `python`, `cli`.
- **Security**: private vulnerability reporting on; secret scanning and push protection on.
- **Branches**: a ruleset on `main` that blocks force pushes and deletions; releases are tags `vX.Y.Z`.
- **Release notes**: the changelog section for the version, starting with whether the tool definitions
  changed (Refresh needed) or not.
