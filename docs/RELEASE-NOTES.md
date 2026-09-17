# Sidecar 0.2.2

Sidecar puts a small, purpose-built Omarchy companion on a phone already connected through the same Tailscale tailnet.

## What changed in 0.2.2

On a stock Omarchy 4.0.3 desktop the 0.2.1 service never started: the shell strips `__sourceDir` from a third-party plugin's public manifest, the service read an empty plugin root from it, and every stock install logged a helper it could not find, once a second. The service now resolves its root from its own component and proves it before starting anything. The runtime graph is `v1013`; nothing else in the product changed.

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

Release `0.2.2` uses immutable runtime graph `v1013`. Source and web suites, release-security and reproducible-artifact checks, and the disposable Omarchy Plugin Lab lifecycle on a stock 4.0.3 guest passed for this graph; the browser matrix, performance probe, Phase 0 and missing-Tailscale scenarios are carried over from 0.2.1, whose web assets and helper this graph renames without changing, as `RELEASE-EVIDENCE.md` states per row. Physical Android foreground pairing was previously observed; the installed-PWA share-target and complete Drop journey remain an explicitly documented physical-device boundary.

See [release evidence](RELEASE-EVIDENCE.md), [security design](SECURITY.md), and the root [changelog](../CHANGELOG.md) for detail.
