# Release evidence — 0.2.2 / v1013

Updated: 2026-09-17

## What 0.2.2 changes

One fix and the graph advance it requires. On a stock Omarchy 4.0.3 desktop the 0.2.1 service never started: the shell strips `__sourceDir` from a third-party plugin's public manifest (`publicPluginManifest` in the 4.0.3 `shell.qml`), `service/v1012/Service.qml` read its plugin root from that field, got `""`, and ran `/helper/sidecarctl` and `/helper/sidecard`, which the shell logged as "Process failed to start" once a second while the widget stayed on "starting". Reproduced in the disposable Plugin Lab's stock 4.0.3 guest on `main` at `93800d2`: run `20260917-231030` failed at the first lifecycle step, "service, widget, helper, and web graph agree", with 29 such log lines in three minutes. The `v1013` service resolves its root from its own component URL (`Qt.resolvedUrl("../../")`), keeps a manifest `__sourceDir` only as a fallback and only when absolute, and proves each candidate by loading its `manifest.json` through a `FileView` before any process is started; with no proven candidate the widget shows "Sidecar could not find its own files beside the service" and nothing runs. Same lab, same guest, same script on this graph: run `20260917-235024` passed every lifecycle step, 25 of 25, with 0 such log lines. An earlier run on the same commit, `20260917-234650`, passed the first nine steps, the failed one included, and then missed the 15-second window in which "Open inbox" has to show a Nautilus window; it carried no diagnostics for that step, the step now dumps the client list, processes, MIME handler, helper status and journal when it fails, and the following run passed it in the window. That step's flakiness in the guest is recorded here, not explained.

The desktop development check, six scoped Quickshell instances on the author's desktop with stand-in `sidecard`/`sidecarctl` scripts and a throwaway HOME (never the daily shell): the 0.2.1 service reproduces the bare `/helper/sidecarctl`; the `v1013` service resolves the copy's root with no manifest field, ignores a manifest `__sourceDir` of `/nonexistent/sidecar` in favour of the component root, falls back to an absolute manifest `__sourceDir` when the component root has no `manifest.json`, does the same when that field arrives one second after creation, and reports the unavailable message when no candidate proves itself.

The rows below say which evidence was produced on this graph and which is carried over from 0.2.1, whose helper, web assets and widget this graph renames without changing.

## Product decision

The active product is Portal, Morph, contextual Beam, secure pairing, lifecycle control, and Drop: a separately authorized, explicitly confirmed, bounded phone-to-desktop inbox. It is not remote filesystem access or a bidirectional bridge. The experimental Carry feature remains removed because Omarchy exposes no provider-neutral task lifecycle or conversation API.

## Verified in this environment

| Area | Evidence |
|---|---|
| Source suite | 72 Python tests pass on `v1013` (71 of 0.2.1 plus one holding the service's root resolution: component URL first, absolute manifest fallback, manifest.json proof before any process, no bare helper path, no path in the user-facing message) |
| Web contract | 139 assertions pass on the renamed `v1013` assets |
| Browser, 390×844 | carried over from 0.2.1: no web asset, style or widget text changed; not re-exercised for 0.2.2 |
| Browser, 320×700 | carried over from 0.2.1 |
| Reduced motion | carried over from 0.2.1 |
| Light themes | carried over from 0.2.1 |
| Agent removal | the `v1013` source scanner run of `make security` rejects Carry/Codex runtime surfaces; hook/plugin/provider/task files absent |
| Pairing identity | carried over from 0.2.1; the same tests pass on `v1013` |
| Security model | `make security` on `v1013`: forbidden primitives absent, non-privileged reproducible artifact inspected |
| Performance | Python 3.14.7/x86_64 on `v1013`, measured while the Plugin Lab guest VM was running on the same host: 300.0 s idle at 0.153% CPU; 14,696 KiB idle RSS; warm snapshots 40.379 ms median/62.129 ms p95; 500 actions at 4/s, 39.287 ms median/41.600 ms p95; exact 25 MiB upload in 75.655 ms with 652 KiB RSS/0 peak delta; final/peak 35,320/44,144 KiB; SHA-256 `46dcc780385019675f4634933190c1e6defd60eebb7543eb4a28875aac4fcb06`. The p95 snapshot figure is above 0.2.1's 43.079 ms and was taken under that load; the median and the action figures are level with 0.2.1 |
| Phase 0 | carried over from 0.2.1 run `20260830-221541`; not re-run on the 4.0.3 guest for 0.2.2 |
| Missing Tailscale | carried over from 0.2.1 run `20260830-221730`; not re-run on the 4.0.3 guest for 0.2.2 |
| Complete lifecycle | disposable Plugin Lab, stock 4.0.3 guest, run `20260917-235024` on `v1013`: 25 of 25 steps passed, from "service, widget, helper, and web graph v1013 agree" through product cleanup and protected-state restoration; run `20260917-231030` on 0.2.1 (`main` at `93800d2`) failed at the first step, which is the defect; run `20260917-234650` on `v1013` passed the first nine and missed the inbox-reveal window once, as described above |
| Repository media | carried over from 0.2.1: captured on `v1012`, whose phone UI, widget and panel are unchanged in `v1013`; `docs/SCREENSHOTS.md` names the current graph and the captures were not retaken |
| Artifact | `omarchy-sidecar-0.2.2.tar.gz` built twice from the same tree with identical bytes; the digest is kept beside it in `dist/`, outside the archive, as before; `make security` inspected it |

The 0.2.1 browser evidence (authenticated in-app browser at both viewport matrices, the real picker, dark/light application, Drop scope upgrade/send/success, focus/ARIA, touch targets, overflow, console inspection, the 43-byte fixture at mode `0600` with SHA-256 `1458617ef725ef6ac5b398a18cab650edb5c46e10fa71229f616273c95e2eb75`) stands as recorded on 2026-08-30 and was not repeated for 0.2.2. Browser screenshots are visual evidence only; HTTP and lab assertions prove state.

## Final artifact

The authoritative SHA-256 is stored beside the tarball in `dist/omarchy-sidecar-0.2.2.tar.gz.sha256` and is verified with `sha256sum -c`. Keeping the digest outside the archive avoids a self-referential artifact. The release process builds twice from the same tree and blocks unless both bytes and digests match.

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
