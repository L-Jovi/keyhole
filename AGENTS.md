# Keyhole Agent Contract

## Scope and authority

- This repository is canonical for the Keyhole server, CLI, tests and docs. Runtime state, keys, grants and
  recovery history live in the user's `~/.config/keyhole/` and are never read from or written to by an agent
  working on this repository.
- Commits, pushes, releases, GitHub settings and OpenAI-side changes need the maintainer's explicit
  authorization for each action.

## Setup and verification

```sh
uv sync --locked --group dev
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
```

Run the tests from a normal terminal: two of them spawn the real server and need `sysctl` and `ps`.

## Conventions

- Python 3.11+, `src/` layout, `ruff` for lint and format, `unittest`-style tests run with `pytest`.
- The MCP tool surface (`tests/tool_snapshot.json`) changes only on purpose, with a changelog entry.
- English only in the repository; no absolute home paths in docs (write `~/...`).
- Conventional Commits.

## Safety invariants (do not weaken)

- Grants start empty and change only through the local CLI; no remote tool may manage grants, run commands,
  use Git, or fetch URLs.
- New grants are `ro`; `rw` requires an explicit local flag.
- Writes require the observed SHA-256 and a request id, are journaled before they are applied, and are
  verified afterwards. Grant changes and writes share one lock.
- Paths are walked with `O_NOFOLLOW` per component; links, hard-linked and special files are refused;
  protected paths are checked by identity as well as by string.
- Only the official `tunnel-client runtimes connect/status/stop/list` are used. No custom supervisor, launch
  agent, auto-start, or public transport.
- Keys never appear in code, tests, logs or model-visible output; tests use obviously fake values.
- A change to a trust boundary is not done until a test shows the boundary holding and, for anything ChatGPT
  can observe, one real call through ChatGPT was made.
