# Platform evidence

Test mechanisms and demonstrated user outcomes are different. A green unsupported-platform check does
not mean the application works on that platform. Updated 2026-09-27.

| Platform | Checks provided by this change | Support statement |
| --- | --- | --- |
| macOS | Regression suite, wheel/sdist installation, fresh tool PATH, real pinned client download; existing ChatGPT acceptance on Apple silicon 15.6/15.7.7 | Supported on the verified Apple silicon machines; Intel/older macOS untested |
| Ubuntu 22.04/24.04 x86_64 | Required Python 3.11–3.14 matrix, artifact installation and official client download; existing real Linux Docker MCP fixture | Experimental until these jobs pass and a real Linux-hosted ChatGPT workflow is accepted |
| Windows Server 2025 | Help/version/unsupported response; separate standard-user NTFS feasibility probe | Unsupported; the probe is not a shipped filesystem backend |
| Windows 11 desktop | No completed acceptance | Unsupported |

Workflow definitions alone are not results; inspect the [Actions run](https://github.com/L-Jovi/keyhole/actions/workflows/ci.yml)
for the exact commit. Hosted runners already contain development tools. Artifact tests do not establish
that a fresh consumer machine, account eligibility or every dependency version has been tested.

## Linux acceptance before promotion

Use a disposable Linux environment and its own Platform tunnel/key, with only synthetic notes. Do not
serve the Mac's active tunnel from another machine. Secrets belong only in a trusted manual acceptance
run, never in a pull request from an untrusted branch.

1. Require all Ubuntu/Python and artifact jobs to pass, without `continue-on-error` exemptions.
2. Run setup from the installed artifact; verify the official client path/version and read-only demo.
3. In a real ChatGPT conversation, read the note and its hash; verify read-only write refusal.
4. Explicitly grant `rw`, edit, inspect local bytes, restore and compare the original hash.
5. Close the demo and require a new read to fail. Confirm shutdown and remove disposable account
   resources according to their owner's instructions.

Retain the artifact hash, OS/architecture/Python/client versions, tool receipts and resulting file hashes.
Redact ids and paths from public evidence. Only then change the README support row and package classifiers.
A simulated client or direct SDK call cannot substitute for step 3.

The optional **Manual Linux ChatGPT acceptance** workflow makes a disposable runner available for these
calls. It is manual, runs only from `main`, and requires a reviewed `linux-acceptance` environment with
its own `KEYHOLE_ACCEPTANCE_TUNNEL` and `KEYHOLE_ACCEPTANCE_KEY` secrets. Configure those only with the
maintainer's explicit authorization; never reuse the Mac's key or tunnel.

After the wheel and client are installed, the session provides four minutes read-only, four minutes rw,
then 90 seconds closed. Watch the live job log for each phase and use the private Linux ChatGPT app with
workspace `LinuxDemo` and `notes.md`. These timed local CLI changes are authorized by dispatching the
manual workflow; ChatGPT cannot advance the phases. The job checks retained edit/restore receipts and
the restored bytes, and revokes access in cleanup. Only a redacted report is uploaded. A green job still
needs screenshots/tool receipts proving the three manual ChatGPT observations listed in the report.

## Windows feasibility

`contrib/windows/probe.py` is outside the package. A disposable CI job creates a standard user and tests
file ids across case aliases, hard-link counts, junction behavior, ACL denial, cross-process locks,
replacement with held handles, and a SQLite recovery record surviving process exit. Symlink creation may
require privileges; the report explicitly says when that case was not exercised.

The central counterexample is deliberate: `FILE_FLAG_OPEN_REPARSE_POINT` on a file does **not** make a
whole path safe when an earlier component is a junction. A Windows backend needs handle-relative access
and identity validation for every component, including writes. Catching the `fcntl` import or replacing
one flag would weaken the existing boundary.

Remaining work includes ACL ownership, special namespaces/alternate streams/short names, parser handle
passing, boot identity and process-tree cleanup. Process-exit recovery is not power-loss durability proof.
Windows Server CI is not Windows 11 desktop acceptance. No Windows support is advertised.

Primary references: Microsoft [CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)
and [GetFileInformationByHandle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfileinformationbyhandle).
