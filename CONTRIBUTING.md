# Contributing

Thanks for looking. Keyhole is small on purpose; the most useful contributions are bug reports with the JSON
that `keyhole` printed, fixes with a test, and documentation that makes setup clearer.

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

1. Update `CHANGELOG.md` and the version in `src/keyhole/__init__.py` and `.codex-plugin/plugin.json`.
2. `uv lock`, run the full suite from a terminal, run one real ChatGPT check (read, edit, restore, close).
3. Tag `vX.Y.Z` and publish a GitHub release with the changelog section.

## GitHub repository settings (maintainer)

Keep the repository page consistent with the README so the name alone is never the only explanation:

- **About**: `Let ChatGPT read, and carefully edit, only the local folders you choose. No shell, no public port.`
- **Website**: the README (`https://github.com/L-Jovi/keyhole#readme`).
- **Topics**: `chatgpt`, `openai`, `mcp`, `mcp-server`, `model-context-protocol`, `secure-mcp-tunnel`,
  `chatgpt-plugin`, `developer-mode`, `local-files`, `filesystem`, `file-access`, `ai-safety`, `macos`,
  `python`, `cli`.
- **Security**: private vulnerability reporting on; secret scanning and push protection on.
- **Branches**: a ruleset on `main` that blocks force pushes and deletions; releases are tags `vX.Y.Z`.
- **Release notes**: the changelog section for the version, starting with whether the tool definitions
  changed (Refresh needed) or not.
