# Related projects

Reviewed 2026-09-27 against public documentation and setup code. These are documented design differences,
not independent security audits or end-to-end compatibility tests of the other projects.

| Project | Connection and setup | Useful emphasis | Keyhole's different fit |
| --- | --- | --- | --- |
| [RepoRelay](https://github.com/Lukie-81/RepoRelay) | Official OpenAI Secure MCP Tunnel; npm and a terminal wizard that installs the official client. Platform tunnel/key and a private ChatGPT app are still required. | One repository, read/search and narrowly targeted handoff writes; clear video and onboarding. | Several independently granted folders, document/image reading, bounded file edits with hash checks and retained recovery. |
| [chatgpt-filebridge](https://github.com/wuyinglai/chatgpt-filebridge) | Cloudflare Quick Tunnel with a public URL; ChatGPT connector/OAuth setup. A quick tunnel does not require a Cloudflare account. | A local administration interface and file/command tools. | Official OpenAI transport and no remote shell or command execution. |
| [local-files-mcp](https://github.com/Meteoryte/local-files-mcp) | GUI-assisted setup with ngrok; a Cloudflare alternative is documented. The ngrok route needs its account/token. | Folder selection, pairing and a local prepare/approve/commit interface. | Terminal-first, independent ro/rw grants and hash-checked recovery; no local approval GUI. |

A graphical approval interface can be valuable; Keyhole does not claim a terminal is easier for everyone.
RepoRelay's narrow handoff writers suit code review without general editing. A public quick tunnel can
reduce account setup, while creating a different transport and authentication arrangement.

Keyhole borrows the onboarding idea of downloading a verified official client with consent. This does not
remove OpenAI's account, tunnel and private-app steps. Keyhole is presently verified on specific macOS
machines; [Linux and Windows evidence](platforms.md) is narrower than full platform support.

References: [RepoRelay installer](https://github.com/Lukie-81/RepoRelay/blob/main/src/tunnel-client-install.ts),
[Cloudflare quick tunnels](https://developers.cloudflare.com/tunnel/get-started/),
[ngrok free-plan limits](https://ngrok.com/docs/pricing-limits/free-plan-limits),
[OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).
Features and service limits may change; follow those sources for current details.
