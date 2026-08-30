# Changelog

## 0.2.1 — 2026-08-30

- Replaced the narrow raw-screenshot README animation with a reproducible widescreen six-scene tour of normal-camera pairing, Portal, Morph, Drop, and Sidecar's private-by-design boundary; slowed the sequence for readability and tightened the README introduction into a fast feature overview.
- Removed an undeclared remote volume-control action and its `pactl` command surface; Beam now exposes exactly previous, play/pause, and next as documented.
- Losslessly cropped the Portal and Morph marketing captures to remove oversized empty viewport canvas and the small-screen browser gutter, then retuned the banner composition around the denser screenshots.

- Hardened the v1012 release graph: stale authenticated snapshot/stream/capability races now re-read current policy, route and lock loss invalidate incomplete Drop state under the action boundary, HTTP framing is single-request and fail-closed, browser reconnects are bounded/deduplicated, PDF action-name/object-stream tricks are rejected, and final inbox commit uses verified directory descriptors and inode checks.

- Added Drop: an explicitly confirmed Android share-target and in-app picker feeding a fixed, bounded Sidecar Inbox.
- Added separate `write:inbox` approval, streamed exact-length/hash/type validation, active-content checks, private staging, collision-safe mode-0600 commits, literal recovery, desktop attention, and local reveal.
- Added lock/pause/revoke/rescope cleanup, restart/update lifecycle handling, upload performance evidence, responsive browser coverage, and disposable-lab acceptance.

- Built secure QR pairing with matching words, explicit local approval, independent credentials, exact per-phone scopes, capability upgrades, revoke, pause, replay binding, rate limits, and lock/unknown-lock redaction.
- Added Portal workspace navigation, stable-order app focus, deliberate hold-and-drag workspace moves, and a contextual content-free media Beam.
- Added Morph with actual installed-theme wallpaper previews, tap-to-apply behavior, one-step undo, next background, bounded preview transport, and no phone-supplied paths or downloads.
- Reworked the phone UI around Omarchy/Aether's flat square surfaces, sans-serif hierarchy, monospaced metadata, selective accent, responsive touch targets, reduced motion, and semantic theme colors.
- Added distinct dark/light layer recipes, contrast repair, guaranteed Omarchy palette fallbacks, and removal of hardcoded dark shadows/glass so light themes render cleanly.
- Added compact haptics, one connection/settings entry, normal-camera QR pairing, literal recovery copy, and a visible Security Receipt.
- Fixed initial missing-Tailscale recovery, Tailscale status handling, FQDN/Serve URL construction, helper startup recovery, stale graph updates, duplicate reconnect growth, and window reorder-on-focus. Re-pairing the exact same browser installation now atomically replaces its old credential, while equally named separate phones remain independent.
- Removed the experimental Carry/Codex integration after the product review found no provider-neutral Omarchy task/conversation API and no universal plug-and-play value. Removed its hooks, provider socket, task store, UI, tests, media, setup path, and active scopes.
- Preserved retired scope strings as parse-only values so existing device records remain readable; they authorize no action and cannot be requested.
- Added deterministic artifacts, checksums, forbidden-primitive scans, root/listener/artifact/runtime assertions, disposable Plugin Lab lifecycle checks, and explicit real-device/signing boundaries.

## Unpublished prototypes

Earlier 0.1/0.2 graphs established the pairing, Portal, Morph, lifecycle, and security foundation. They were never public releases and are superseded by the current immutable graph.
