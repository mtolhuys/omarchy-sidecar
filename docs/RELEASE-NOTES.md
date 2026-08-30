# Sidecar 0.2.1

Sidecar puts a small, purpose-built Omarchy companion on a phone already connected through the same Tailscale tailnet.

## Highlights

- **Portal:** switch workspaces, focus apps, and deliberately move a window between workspaces.
- **Morph:** preview installed Omarchy themes, apply one, undo, or advance the desktop background.
- **Beam:** previous, play/pause, and next—three typed media actions, with no remote shell.
- **Drop:** explicitly send up to five allowed screenshots or small documents to the fixed `Downloads/Sidecar` inbox after separate local approval.
- **Pairing:** normal-camera QR flow, matching verification words, per-phone credentials, exact scopes, pause, revoke, and fail-closed lock behavior.
- **Topbar placement:** installation asks for the left, center, or right section, with right preselected.

## Security boundary

Sidecar runs unprivileged, listens only on loopback, reuses an existing signed-in Tailscale identity, owns one exact private Serve route, and never changes Tailscale SSH, firewall, sudoers, accounts, packages, or system services. It has no cloud relay, telemetry, arbitrary filesystem access, terminal, screen stream, keyboard, pointer, or agent control.

## Verification

Release `0.2.1` uses immutable runtime graph `v1012`. Source and web suites, exact responsive browser acceptance, a 300-second idle/performance probe, release-security and reproducible-artifact checks, and three disposable Omarchy Plugin Lab scenarios passed. Physical Android foreground pairing was previously observed; the installed-PWA share-target and complete Drop journey remain an explicitly documented physical-device boundary.

See [release evidence](RELEASE-EVIDENCE.md), [security design](SECURITY.md), and the root [changelog](../CHANGELOG.md) for detail.
