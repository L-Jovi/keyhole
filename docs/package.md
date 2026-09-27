# Keyhole — local files for ChatGPT

Let ChatGPT read, and carefully edit, only the local folders you choose. No shell, no public port.

Keyhole connects your Mac or Linux computer to your private ChatGPT app through OpenAI's official
Secure MCP Tunnel. Folders start read-only. Explicit local write grants allow hash-checked text edits
with a private recovery history. ChatGPT cannot open other folders, run commands or widen its own access.

## Install

```sh
uv tool install keyhole-mcp
keyhole setup
```

The package is **keyhole-mcp**; its command is **keyhole**. Do not install the unrelated package named
`keyhole`. `uv` supplies Python and isolates dependencies; setup can download and verify the official
tunnel client after confirmation.

You need ChatGPT Developer mode and OpenAI Platform tunnel permissions. Installing this package alone
does not create those permissions or a private ChatGPT app. Follow the
[complete first-use guide](https://github.com/L-Jovi/keyhole/blob/v0.4.0/docs/setup.md), including the
read-only example, account setup and PATH checks. Release wheels remain available on
[GitHub Releases](https://github.com/L-Jovi/keyhole/releases).

Verified scope: Apple-silicon macOS and Ubuntu x86_64; Python 3.11–3.14 in CI. Real ChatGPT acceptance
was completed on macOS 15.7.7 and Ubuntu 24.04. Ubuntu 22.04 has automated coverage. Windows is not
supported by this release. See [platform evidence](https://github.com/L-Jovi/keyhole/blob/v0.4.0/docs/platforms.md).

[Watch the real demonstration](https://github.com/L-Jovi/keyhole/blob/v0.4.0/docs/media/keyhole-demo.mp4)
or read the [tool and safety reference](https://github.com/L-Jovi/keyhole/blob/v0.4.0/docs/reference.md).
Document parsers retain the user's OS permissions; process separation is not an OS sandbox.

Unofficial project; not affiliated with OpenAI. [Source and issues](https://github.com/L-Jovi/keyhole).
Licensed under MIT.
