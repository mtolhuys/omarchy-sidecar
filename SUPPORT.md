# Support

## Tailscale is missing

Open Omarchy Install → Service → Tailscale, finish sign-in, then choose **Try again** in Sidecar. Sidecar never installs it or joins a tailnet for you.

## Tailscale is installed but Sidecar says disconnected

Run:

```bash
tailscale status
```

The desktop must show its existing tailnet identity. On the phone, install/open the official Tailscale app and connect to the same tailnet. A browser alone cannot resolve or reach the private `.ts.net` name.

## The QR opens an unreachable page

Confirm the phone's Tailscale app is connected to the same tailnet. Then reopen Sidecar on the desktop and generate a fresh QR; codes expire after two minutes and are single-use.

## The phone is awaiting approval

Compare the three words on phone and desktop. Approve the named phone locally. If the words differ, deny it and create a fresh code.

## A control is unavailable

Open the connection sheet on the phone and request the exact missing Portal, Morph, or lock capability. Unlock the desktop and approve the clearly labeled request locally. Existing credentials never gain authority silently.

## The desktop locked or disappeared

Unlock the desktop. Unknown lock state is intentionally treated as locked. If Sidecar was paused, resume it from the desktop panel. If this phone was revoked, pair again.

## A theme preview is missing

Sidecar falls back to a palette preview when an installed theme has no safe current wallpaper image. It never guesses paths or downloads artwork. If theme application fails, refresh the installed theme list and tap again.

## Diagnostics

```bash
~/.config/omarchy/plugins/io.github.mtolhuys.sidecar/helper/sidecarctl diagnostics |
  jq '{builds,state,routeState,errorCode,securityReceipt}'
```

Diagnostics intentionally omit credentials, device names, pairing material, Tailscale identity, window titles, media content, URLs, and paths. When an invariant fails, `securityReceipt.recovery` contains literal next steps.

## Update

Use the complete local update command in [README.md](README.md#local-install-or-update). A correct update reports service/helper/web graph `v1011` and preserves existing phone credentials and exact scopes.

## Report a vulnerability

Follow [SECURITY.md](SECURITY.md). Do not include live credentials, pairing QR data, private tailnet names, or unredacted diagnostics in a public report.
Drop requires the separately approved **Send allowed files to Sidecar Inbox** capability. Unlock the desktop and approve that request on the Sidecar panel; the existing credential is retained. Lock, pause, revoke, offline state, insufficient space, an unsupported file, or an expired intent stops the transfer with a literal recovery message. Retry is always explicit.

The accepted formats are PNG, JPEG, WebP, GIF, PDF, and UTF-8 `.txt`, with five files maximum, 25 MiB per file, and 50 MiB per batch. Files land in Downloads/Sidecar through the desktop's fixed Downloads contract. Sidecar never accepts an arbitrary destination.
