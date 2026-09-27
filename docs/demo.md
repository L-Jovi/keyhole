# A real notes workflow

[Watch the 59-second walkthrough](media/keyhole-demo.mp4)
· [20-second GIF](images/keyhole-demo.gif) · [Static preview](images/keyhole-demo.png)

The walkthrough uses cropped captures of a real ChatGPT conversation, local command explanations and
the verified file contents. It is an edited sequence of results, not an uninterrupted desktop recording.
Setup and waits are omitted. No ChatGPT response text was rewritten or generated for the video.

## What happened

On 2026-09-27, the development CLI at commit `01054bc` was used with macOS 15.7.7, Apple silicon,
Python 3.12.9, official `tunnel-client` 0.0.14 and ChatGPT Pro on the web in Chat mode.
The only folder opened for this test was `KeyholeDemo`, containing one fictional `notes.md` file.

| Step | Observed result |
| --- | --- |
| Open read-only, then ask ChatGPT to read | ChatGPT returned the three ideas from the local note. Its contents were not included in the prompt. |
| Run `keyhole access KeyholeDemo rw`, then request TODO checkboxes | ChatGPT used the observed file hash, wrote three checkboxes and read the file back. The local file was independently checked. |
| Ask ChatGPT to restore the recorded change | The original three bullets returned; the entire local file matched its original SHA-256. |
| Run `keyhole close KeyholeDemo`, then request a new read | Shutdown was confirmed. ChatGPT reported HTTP 404, `tunnel_client_not_connected`, and no file contents. |

The final disconnected request took 2 minutes 51 seconds before ChatGPT displayed the failure. The
walkthrough says that this wait was omitted. Closing access prevents new reads; it does not erase
previously returned contents from the conversation. Other saved grants were unchanged after the test.

The command card uses the portable example path `./demo-notes`. Account setup, private paths, keys,
account controls and unrelated folder names are excluded from the published material. The local file
card displays the bytes checked after the edit, rather than a fabricated terminal transcript.

## Verification and privacy

- Original and restored file SHA-256: `cc299cbd4b616388f1c544a9a4f62a011f2e37f991771bc0a20689fe39f18f45`.
- Edited file SHA-256: `c812d1de32ac19a2d1f46fbe6360ac263d57b8f4afccd7f496b9652f9b8cf32e`.
- Only the chat content region was captured for these assets. Desktop, browser tabs, address bar,
  account information, sidebar and unrelated conversations are outside the captured region.
- All ten composed scenes and four response crops were visually inspected. Every one of the 1,770
  exported video frames was decoded and compared with its reviewed source scene. All 40 GIF frames
  matched one of four reviewed scenes after scaling and palette conversion.
- The MP4 has one video stream and no audio. Exported metadata has no private paths, location tags,
  comments or creation timestamps. Raw captures and local verification receipts remain outside Git.

This verifies the demonstrated macOS workflow. It does not establish Linux/Windows support,
fresh-account onboarding, or every document format. See [platform evidence](platforms.md) for those limits.
