## What changes

## Why

## Checks

- [ ] `uv run ruff check . && uv run ruff format --check . && uv run pytest -q` pass locally
- [ ] Tool definitions unchanged, or `tests/tool_snapshot.json` updated on purpose (users must then Refresh their ChatGPT app)
- [ ] Any change to a trust boundary (grants, writes, exclusions, protected paths) has a test
- [ ] Docs updated (README, docs/setup.md, SECURITY.md or CHANGELOG.md) where behavior changed
