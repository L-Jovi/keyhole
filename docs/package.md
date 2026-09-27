# Keyhole — local files for ChatGPT

Let ChatGPT read, and carefully edit, only the local folders you choose. No shell, no public port.

Keyhole connects your Mac, Linux or Windows computer to your private ChatGPT app through OpenAI's official
Secure MCP Tunnel. Folders start read-only. Explicit local write grants allow hash-checked text edits
with a private recovery history. ChatGPT cannot open other folders, run commands or widen its own access.

## Install

```sh
uv tool install keyhole-mcp
keyhole setup
```

On Windows, use PowerShell and request x64 Python, on both x64 and ARM PCs, then follow the
[Windows guide](https://github.com/L-Jovi/keyhole/blob/v0.5.0/docs/windows.md):

```powershell
uv tool install --python cpython-3.12-windows-x86_64-none --no-build keyhole-mcp
```

The package is **keyhole-mcp**; its command is **keyhole**. Do not install the unrelated package named
`keyhole`. `uv` supplies Python and isolates dependencies; setup can download and verify the official
tunnel client after confirmation.

You need ChatGPT Developer mode and OpenAI Platform tunnel permissions. Installing this package alone
does not create those permissions or a private ChatGPT app. Follow the
[complete first-use guide](https://github.com/L-Jovi/keyhole/blob/v0.5.0/docs/setup.md), including the
read-only example, account setup and PATH checks. Release wheels remain available on
[GitHub Releases](https://github.com/L-Jovi/keyhole/releases).

Verified scope: Apple-silicon macOS, Ubuntu x86_64 and Windows 11 with x64 Python; Python 3.11–3.14 in
CI. Real ChatGPT acceptance was completed on macOS 15.7.7, Ubuntu 24.04 and a hosted Windows 11 ARM
machine. Ubuntu 22.04 and Windows Server 2025 have automated coverage; no physical Windows consumer PC
has been tested. See [platform evidence](https://github.com/L-Jovi/keyhole/blob/v0.5.0/docs/platforms.md).

[See the step-by-step demonstration](https://github.com/L-Jovi/keyhole/blob/v0.5.0/docs/demo.md)
or read the [tool and safety reference](https://github.com/L-Jovi/keyhole/blob/v0.5.0/docs/reference.md).
Document parsers retain the user's OS permissions; process separation is not an OS sandbox.

Unofficial project; not affiliated with OpenAI. [Source and issues](https://github.com/L-Jovi/keyhole).
Licensed under MIT.
