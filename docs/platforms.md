# Platform evidence

Test mechanisms and demonstrated user outcomes are different. A green unsupported-platform check does
not mean the application works on that platform. Updated 2026-09-27.

| Platform | Checks provided by this change | Support statement |
| --- | --- | --- |
| macOS | Regression suite, wheel/sdist installation, fresh tool PATH, real pinned client download; ChatGPT read/edit/restore/close verified on Apple silicon 15.7.7 with this change | Supported on the verified Apple silicon machines; Intel/older macOS untested |
| Ubuntu 24.04 x86_64 | Required Python 3.11–3.14 matrix, artifact installation, official client download, Linux Docker fixture and real official Tunnel → ChatGPT read/edit/restore/close passed | Supported; full ChatGPT acceptance used Python 3.12 |
| Ubuntu 22.04 x86_64 | Required Python 3.11–3.14 matrix, artifact installation and official client download passed | Supported with automated coverage; the account-based ChatGPT session ran on 24.04 |
| Windows Server 2025 | Native filesystem, ACL, locking, parser, process, MCP handshake and artifact checks across x64 Python 3.11–3.14 passed | Supported from 0.5.0 with automated coverage |
| Windows 11 ARM | The same x64 Python matrix under emulation, plus a real official Tunnel → ChatGPT read, read-only refusal, edit, restore and closed-read refusal as a standard user | Supported from 0.5.0 with x64 Python; ChatGPT acceptance ran on a hosted Windows 11 machine, not a physical consumer desktop |

Workflow definitions alone are not results; inspect the [Actions run](https://github.com/L-Jovi/keyhole/actions/workflows/ci.yml)
for the exact commit. Hosted runners already contain development tools. Artifact tests do not establish
that a fresh consumer machine, account eligibility or every dependency version has been tested.

[Run 36291310205](https://github.com/L-Jovi/keyhole/actions/runs/36291310205), commit `01054bc`,
passed all 21 jobs: 12 OS/Python regression jobs, six artifact/client-install jobs, the Linux Docker
fixture, the Windows refusal check and the Windows feasibility probe. The local
[ChatGPT demonstration](demo.md) supplies separate macOS end-to-end evidence.

## Linux ChatGPT acceptance: 2026-09-27

[Run 36293869254](https://github.com/L-Jovi/keyhole/actions/runs/36293869254), commit
`f44cef9e349abc0ea540d7b0c2c5f5ce94871618`, installed the built 0.4.0 wheel and official client 0.0.14
on an Ubuntu 24.04 x86_64 runner. The workflow used Python 3.12; its fixture report records 3.12.3 and
Linux kernel 6.17.0-1022-azure. It used a dedicated tunnel and a one-day restricted runtime key, with no
personal files or production Mac runtime state.

The real ChatGPT web conversation verified:

- A fresh read returned all 191 bytes of the synthetic note and its SHA-256.
- One actual write under the read-only grant returned `read_only`.
- After the runner's explicit local `rw` grant, one edit committed (191 → 213 bytes), followed by a
  committed restore (213 → 191 bytes). A fresh read matched the original SHA-256.
- After closing the last workspace, one new read returned HTTP 404 / `tunnel_client_not_connected`,
  without file content. The result took about 2 minutes 50 seconds; this is a disconnected-tunnel
  response, not a workspace permission error.

The runner independently verified both journal receipts, restored bytes, shutdown and cleanup. Its
`linux-chatgpt-acceptance` artifact deliberately retains `fixture_only: true` and
`full_chatgpt_acceptance: false`: the job cannot observe the ChatGPT UI. The manual observations above
were verified separately and their synthetic screenshots and transcript retained by the maintainer.
The raw runner report was not rewritten to imply automatic end-to-end coverage.

This validates the installed candidate from the stated commit. The workflow did not retain that wheel's
SHA-256, so it is not byte-identity evidence for a later rebuilt or published distribution. The publishing
workflow separately builds once and verifies the same artifacts before uploading. No clean Linux desktop,
fresh OpenAI account, Linux ARM or other distribution was tested by this session.

## Repeat account-based acceptance

Use a disposable Linux environment and its own Platform tunnel/key, with only synthetic notes. Do not
serve the Mac's active tunnel from another machine. Secrets belong only in a trusted manual acceptance
run, never in a pull request from an untrusted branch.

1. Require all Ubuntu/Python and artifact jobs to pass, without `continue-on-error` exemptions.
2. Run setup from the installed artifact; verify the official client path/version and read-only demo.
3. In a real ChatGPT conversation, read the note and its hash; verify read-only write refusal.
4. Explicitly grant `rw`, edit, inspect local bytes, restore and compare the original hash.
5. Close the demo and require a new read to fail. Confirm shutdown. Keep reusable test tunnels, apps
   and protected GitHub environments if the owner intends to repeat acceptance. Revoke or replace
   expired/superseded keys as needed; delete only resources confirmed to be retired.

Retain the artifact hash, OS/architecture/Python/client versions, tool receipts and resulting file hashes.
Redact ids and paths from public evidence. Only then change the README support row and package classifiers.
A simulated client or direct SDK call cannot substitute for step 3.

The optional **Manual ChatGPT acceptance** workflow makes a disposable runner available for these
calls. Select `linux` or `windows`; each uses its own reviewed environment (`linux-acceptance` or
`windows-acceptance`) and dedicated `KEYHOLE_ACCEPTANCE_TUNNEL` / `KEYHOLE_ACCEPTANCE_KEY` secrets.
Configure those only with the maintainer's explicit authorization; never reuse another machine's key
or tunnel. Both environments must require maintainer review before releasing their secrets.

Use `main` by default. For an explicitly reviewed candidate, only the repository owner can dispatch a
non-main branch, and must supply that branch's exact full SHA as `candidate_sha`. The environment
reviewer must verify the displayed commit and its green CI before approving access to secrets. This
manual path does not run on pull-request events or release secrets to arbitrary pull requests.

After the wheel and client are installed, the session provides four minutes read-only, four minutes rw,
then four minutes closed. Watch the live job log for each phase and use the private Linux ChatGPT app with
workspace `LinuxDemo` and `notes.md`. These timed local CLI changes are authorized by dispatching the
manual workflow; ChatGPT cannot advance the phases. Windows uses a separate `WindowsDemo` workspace and
normal, non-administrator local user on a hosted Windows 11 ARM machine with x64 Python. The job now
records the installed candidate wheel's SHA-256. It checks retained edit/restore receipts and
the restored bytes, and revokes access in cleanup. Only a redacted report is uploaded. A green job still
needs screenshots/tool receipts proving the three manual ChatGPT observations listed in the report.

Retaining a test tunnel or app does not require keeping a runner connected. Use a fresh short-lived,
restricted runtime key for a later session when the previous key has expired, and update that platform's
environment secret. Do not extend a credential's lifetime merely to preserve the reusable test setup.

## Windows native support

Native Windows support ships in 0.5.0. [PR #10](https://github.com/L-Jovi/keyhole/pull/10) implements a native NTFS backend. It walks paths with
parent directory handles, refuses reparse points and hard links, holds ancestor handles against rename,
and uses handle-relative atomic replacement without dropping the checked destination handle. Private
state uses protected Windows ACLs. In-place edits retain effective DACL access; new files, copy and move
destinations start private, while recovery keeps the original path's ACL. Raw ACLs and account SIDs
are omitted from public change receipts. Files with named streams, encryption, compression, sparse or
read-only attributes are refused for mutation rather than silently losing those properties.

[Run 36304537741](https://github.com/L-Jovi/keyhole/actions/runs/36304537741), commit `91609da`, passed
all 30 jobs, including real standard-user native tests and the official Windows client
download on Windows Server 2025 x64 and hosted Windows 11 ARM (x64 Python under emulation).
Those tests cover directory escape, ACL privacy, aliases, separate-process locking, edits and restore,
parser handle transfer, and cleanup limited to one state directory. They use synthetic files and no key.

The expanded Windows matrix runs Python 3.11–3.14 on both runners. It also exercises real MCP SDK
handshakes, shared behavior regressions, candidate wheel/sdist installation and a fresh PowerShell PATH.
The broader handshake checks exposed a PowerShell module-discovery stall under the SDK's minimal
environment; the candidate now limits OS queries to the system module directory and tests that case.
Inspect the latest PR checks for any later changes to the candidate.

### Windows ChatGPT acceptance: 2026-09-27

[Run 36307822752](https://github.com/L-Jovi/keyhole/actions/runs/36307822752) installed the wheel built
from `91609dadd3d04254b687b03ec6c1664dc5d7a76b` and official client 0.0.14. The runner was
Windows 11 build 26200 on ARM, with x64 CPython 3.12.10 under emulation. Keyhole and the client ran as
a normal, non-administrator local user. The dedicated tunnel, key and synthetic `WindowsDemo/notes.md`
were separate from the Mac connection and personal folders.

Candidate wheel SHA-256: `e439560bdad7b95afddfb2a7aeb2b8a023653bc838e0020023ede5103d34f1a8`.

The real ChatGPT web conversation verified:

- A new read returned all 191 bytes and the original SHA-256
  `cc299cbd4b616388f1c544a9a4f62a011f2e37f991771bc0a20689fe39f18f45`.
- An actual same-content write with the observed hash returned `read_only` while the local grant was ro.
- After the runner explicitly granted rw, ChatGPT read again, changed three bullets into checkboxes,
  and read back SHA-256 `c812d1de32ac19a2d1f46fbe6360ac263d57b8f4afccd7f496b9652f9b8cf32e`.
- ChatGPT restored the recorded change and read again. The final hash matched the original exactly.
- After local close, a **new** read returned `NOT_FOUND` with "Tunnel-client did not poll after this
  request was enqueued" and no new file contents. The response took about one minute. This is a
  disconnected-tunnel result, not an NTFS permission error.

The runner independently verified both retained journal receipts, original bytes, shutdown and cleanup.
Its `windows-chatgpt-acceptance` report keeps `fixture_only: true` and `full_chatgpt_acceptance: false`:
the runner cannot observe ChatGPT. The manual transcript and screenshots were verified separately and
retained by the maintainer; the raw report was not rewritten.

**Release boundary:** this verifies the installed candidate identified above. 0.5.0 contains that backend
plus a later change that starts the MCP server with `python -I -X utf8` from the state directory; the
ChatGPT session above predates that change. The 0.5.0 artifacts have different bytes and do not inherit
this wheel hash: CI handshakes and the release workflow's Windows installation check cover them.

The [Windows setup guide](windows.md) covers PowerShell, PATH, x64 Python on ARM, ordinary NTFS folders,
account setup and recovery. Native ARM Python, Windows 10, network shares, case-sensitive NTFS
directories and cloud placeholders are outside the tested scope. Process-exit recovery
does not prove power-loss durability; Windows has no directory-fsync guarantee equivalent to the POSIX
path used here. No physical Windows consumer machine has been tested.

### Original feasibility probe

`contrib/windows/probe.py` is outside the package. A disposable CI job creates a standard user and tests
file ids across case aliases, hard-link counts, junction behavior, ACL denial, cross-process locks,
replacement with held handles, and a SQLite recovery record surviving process exit. Symlink creation may
require privileges; the report explicitly says when that case was not exercised.

The central counterexample is deliberate: `FILE_FLAG_OPEN_REPARSE_POINT` on a file does **not** make a
whole path safe when an earlier component is a junction. A Windows backend needs handle-relative access
and identity validation for every component, including writes. Catching the `fcntl` import or replacing
one flag would weaken the existing boundary.

The first real standard-user run also rejected Python's `os.replace` while the destination handle was
open, even with `FILE_SHARE_DELETE`. The probe records this outcome and verifies that both files remain
unchanged before testing replacement after handle closure. A passing feasibility job may therefore
contain `supported: false` for this primitive. Closing identity-check handles to make a test pass is not
a proposed Keyhole fix; a native backend must preserve the race protection during replacement.

The recorded run used Windows Server 2025 build 26100 and Python 3.12.10. It verified standard-user
execution, ACL denial to another user, case-alias file identity, hard-link counts, symlink/reparse
detection, a cross-process lock and a recovery record surviving process exit. Python `os.replace`
returned WinError 5 with the destination handle held; both files remained unchanged. Replacement after
closing the handles succeeded. The report is the run's `windows-feasibility` artifact.

The native backend above addresses the identified primitive gaps. This original probe remains a
separate piece of evidence; its success alone is not application support or ChatGPT acceptance.

Primary references: Microsoft [CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)
and [GetFileInformationByHandle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfileinformationbyhandle).
