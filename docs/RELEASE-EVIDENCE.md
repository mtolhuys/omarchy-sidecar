# Release evidence — 0.2.1 / v1012

Updated: 2026-08-30

## Product decision

The active product is Portal, Morph, contextual Beam, secure pairing, lifecycle control, and Drop: a separately authorized, explicitly confirmed, bounded phone-to-desktop inbox. It is not remote filesystem access or a bidirectional bridge. The experimental Carry feature remains removed because Omarchy exposes no provider-neutral task lifecycle or conversation API.

## Verified in this environment

| Area | Evidence |
|---|---|
| Source suite | 71 Python tests pass, including real streamed Drop bodies, policy races, content validation, collision/no-overwrite, restart cleanup, schema-v1 migration, and browser-installation replacement |
| Web contract | 139 assertions pass: picker/share-target contract, private service-worker staging, bounds, explicit confirmation, SHA-256, XHR progress, completed-batch retry guard, remove/cancel/retry, recovery, cleanup, identity, and responsive/reduced-motion rules |
| Browser, 390×844 | authenticated Portal and Morph exercised in dark Aurora and light Lupine; pairing, connection settings, explicit theme application, Drop confirmation/scope upgrade/success, ARIA dialog/progress state, 48px targets, haptics off, no horizontal overflow, and zero console warnings/errors verified |
| Browser, 320×700 | authenticated Portal and Morph exercised in dark Aurora and light Lupine; final navigation remained reachable and no horizontal overflow was present |
| Reduced motion | the web contract asserts the shipped `prefers-reduced-motion: reduce` override disables transitions/animations; the in-app browser does not expose media-preference emulation, so no separate interactive browser claim is made |
| Light themes | no hardcoded black CSS shadow/glass; contrast repair and separate light layer/border/preview recipes exercised |
| Agent removal | hook/plugin/provider/task files absent; active Python/QML/web source scanner rejects Carry/Codex runtime surfaces |
| Pairing identity | same-instance re-pair atomically removes the first credential and stream; same-name/different-instance devices remain separate; raw and hashed instance identity stay out of public state |
| Security model | tests and artifact scanner cover root refusal, loopback, exact owned route, separate `write:inbox`, length/hash/type/magic, active PDFs, Unicode names, reservations/concurrency/rates, mode/ownership/link count, live lock/pause/revoke/rescope, replay, diagnostics, and private staging |
| Performance | Python 3.14.7/x86_64: 300.0 s idle at 0.153% CPU; 31,832 KiB idle RSS; warm snapshots 41.152 ms median/43.079 ms p95; 500 actions at 4/s, 39.169 ms median/41.455 ms p95; exact 25 MiB upload in 67.579 ms with 20 KiB RSS/peak delta; final/peak 48,368 KiB; SHA-256 `46dcc780385019675f4634933190c1e6defd60eebb7543eb4a28875aac4fcb06` |
| Phase 0 | disposable Plugin Lab run `20260830-221541`: Python 3.14 streaming/cancel cleanup, Downloads/reveal, real notification delivery, Android physical boundary, v1012 graph/cache, addressed move, themes/background, and lock truth passed |
| Missing Tailscale | disposable Plugin Lab run `20260830-221730`: fail-closed first run, delayed binary detection, HTTPS consent, exact Serve readiness, visible recovery/Pair, input recovery, and complete process/listener cleanup passed |
| Complete lifecycle | disposable Plugin Lab run `20260830-221918`: QMP pairing and same-credential Drop approval; same-browser replacement; exact digest/location/mode/no-overwrite; attention and rendered reveal; Portal focus/order/move; Morph dark/light/apply/undo; lock/pause/revoke/rescope denial; crash/update; route conflict/retry; completed-file retention through removal; product cleanup; and protected-state restoration passed |
| Repository media | current v1012 Portal and Morph losslessly content-cropped from verified 390/320 browser viewports, authenticated Drop dark/light/success, disposable-lab generic Drop attention from run `20260830-221918`, and pairing from run `20260830-221730`; the pairing capture replaces the ephemeral QR with fixed non-authorizing demo text, and the deterministic 14.15-second widescreen README tour contains no real filenames, identities, paths, URLs, or secrets |
| Artifact | deterministic tarball, checksum repeat, and final release-security/artifact scan recorded below |

Browser evidence used the authenticated in-app browser for both exact viewport matrices, the real picker, dark/light application, Drop scope upgrade/send/success, focus/ARIA, touch targets, overflow, and console inspection. Beam exposed exactly previous, play/pause, and next, with no volume surface. The committed 43-byte fixture arrived at the fixed inbox with mode `0600`, SHA-256 `1458617ef725ef6ac5b398a18cab650edb5c46e10fa71229f616273c95e2eb75`, and an empty staging directory. The isolated browser tab, helper, state, inbox, and listener were stopped and removed after verification. Browser screenshots are visual evidence only; HTTP and lab assertions prove state.

The pre-change performance baseline was 0.160% idle CPU, 31,760 KiB idle RSS, 48,256 KiB peak/final RSS, 38.677/41.353 ms warm snapshot median/p95, 39.505/41.647 ms action median/p95, and 73.127 ms upload latency. The final result reduced idle CPU, action latency, and upload latency. RSS moved by only 72 KiB idle and 112 KiB peak; snapshot median/p95 rose 6.4%/4.2%. No material regression was observed.

## Final artifact

The authoritative SHA-256 is stored beside the tarball in `dist/omarchy-sidecar-0.2.1.tar.gz.sha256` and is verified with `sha256sum -c`. Keeping the digest outside the archive avoids a self-referential artifact. The release process builds twice from the same tree and blocks unless both bytes and digests match.

## Previously observed physical boundary

The user previously physically verified Android Chrome foreground pairing and authenticated Sidecar use through the official Tailscale app on the same tailnet. The current Drop picker, existing-credential capability upgrade, installed-PWA share-sheet registration, closed/background share launch, cancellation, policy recovery, and revocation have not been physically observed. No Android Drop/share-target support claim is made yet.

## Explicitly unverified

- iOS;
- artifact signature/maintainer signing identity;
- mutation and cleanup against an authenticated isolated real Tailscale daemon;
- every physical Android model/browser beyond the observed device;
- the complete current physical Android Drop checklist, including installed-PWA Web Share Target while closed/backgrounded;
- attribution of abandoned pre-v1009 device records (the old graph stored no safe browser identity, so those require explicit local removal);
- any agent integration or remote agent conversation (not implemented or claimed).

The Plugin Lab's strict fake Tailscale can prove exact commands, ownership, conflict behavior, and unchanged machine state. It cannot upgrade the authenticated-real-daemon boundary.
