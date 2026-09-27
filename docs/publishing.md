# Publishing a reviewed release

PyPI publication is prepared, not enabled merely by adding a workflow. The package name is `keyhole-mcp`;
the executable/module remains `keyhole`. A missing PyPI page does not reserve that name.

## One-time account setup

After maintainer authorization, add a GitHub Trusted Publisher (or a pending publisher for the first release)
in the maintainer's PyPI account with these exact values:

| Field | Value |
| --- | --- |
| PyPI project | `keyhole-mcp` |
| GitHub owner / repository | `L-Jovi` / `keyhole` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

Create GitHub environments `pypi` and `release`, restrict them to `main`, and require maintainer approval.
The workflow uses OIDC; no long-lived PyPI API key goes into repository secrets. If the name is unavailable,
keep wheel installation and resolve the distribution name before publication.

See PyPI's [pending publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
and [publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/) instructions.

## Per release

1. Review/merge changes, pass required CI, and complete acceptance for any platform whose support
   statement changes. This workflow does not perform account-based ChatGPT tests.
2. Set the version in `src/keyhole/__init__.py` and `.codex-plugin/plugin.json`, refresh `uv.lock`, and move
   the corresponding changelog entries out of Unreleased. Review these changes before tagging.
3. With release authorization, create the version tag and a GitHub release with reviewed notes.
   Dispatch **Publish reviewed release** from `main` with that existing tag (for example `v0.4.0`).
4. The workflow checks main ancestry/version, builds once, tests those artifacts outside the checkout
   on macOS and both Ubuntu versions, then waits for the `pypi` environment approval before uploading.
5. After PyPI succeeds, approve the `release` job to attach the **same bytes** and SHA-256 sums to GitHub.
   It will not overwrite assets. If interrupted after PyPI publication, upload the preserved successful
   run's artifacts to GitHub; do not rebuild or republish a version with different bytes.
6. Verify an isolated `uv tool install keyhole-mcp==VERSION`, `keyhole --version` and setup. Only then
   remove the development notice and make `uv tool install keyhole-mcp` the README's primary command.
   Retain the exact release wheel URL as a fallback.

Do not publish v0.3.2 with new code: that version is already a GitHub release. Choose and review the next
version before tagging. Package download counts are not counts of successful users.
