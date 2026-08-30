# Test contract

Run from the repository root:

```bash
make test
make performance
make dist
make security
```

## Source and HTTP

Drop tests cover schema/auth/scope approval, payload-bound replay, expiry, rate/concurrency/reservation limits, Unicode naming, MIME/extension and magic validation, active PDF rejection, UTF-8 streaming, exact length/hash, collisions/no overwrite, mode/owner/link count, low space, partial disconnect, restart, and live lock/pause/revoke/rescope races. Integration uses real loopback streamed bodies and verifies committed user files survive restart.

Python tests cover strict schemas, pairing consumption/expiry, local approval, independent credentials, exact browser-installation replacement, same-name separate devices, schema-v1 migration, identity rebinding denial, stream termination, scope upgrade, replay binding, rates, current-target validation, stable window order, theme derivation, light-theme contrast, preview bounds, lock/unknown-lock, pause, revoke, corrupt state, crash/restart, route ownership, and safe diagnostics.

Web assertions cover immutable graph references, persisted non-authorizing browser identity, authenticated legacy binding, no inline/external code, sanitizer bounds, Portal gestures/order, explicit Morph application, semantic dark/light recipes, real theme previews, compact haptics, accessibility, reduced motion, safe caching, and absence of active agent/terminal/command surfaces.

The fake-adapter HTTP suite performs real pairing/authentication/events/actions over a loopback listener. Screenshots are not accepted as protocol proof.

## Browser interaction

Authenticated browser acceptance covers picker and Android share-target staging, explicit confirmation, remove/cancel/retry, one-time scope upgrade, XHR progress, partial failure, offline/lock/credential recovery, success, haptics-off and reduced-motion code paths, ARIA/focus, hostile filenames, service-worker cache replacement, and staging deletion. Inspect 390×844 and 320×700 in dark and light for horizontal overflow, reachability, 48px targets, safe areas, semantics, console errors, and current screenshots.

Use the live fake helper and a real browser at:

- 390×844 dark Portal and Morph;
- 390×844 light Portal and Morph;
- 320×700 light and dark;
- reduced motion;
- pairing, settings, theme tap/undo, workspace focus, app focus, and hold/move recovery.

Inspect DOM semantics, console errors, horizontal overflow, obscured controls, contrast, active state, and the actual screenshots. Reload after source changes.

## Performance

The standard five-minute idle/500-action probe also commits an exact 25 MiB UTF-8 text file and reports upload latency plus helper RSS/high-water deltas. `HOME`, state, runtime, Downloads, and the completed fixture are isolated in a temporary directory.

The performance probe checks bounded idle CPU/RSS, action latency, stream/connection limits, event queue bounds, and clean shutdown. Full release evidence uses the standard duration; shortened probes are labeled.

## Disposable Plugin Lab

The lifecycle uses QMP for rendered approval, panel attention, local inbox reveal, and unlock. Machine assertions cover graph/cache identity, same-credential `write:inbox`, exact bytes/digest/location/mode/no-overwrite, generic attention, lock/pause/revoke/rescope cleanup, crash/update policy, completed-file retention through removal, no staging/listener/process/route residue, and before/after protected-system snapshots. Physical Android share-target behavior remains a separate boundary.

All desktop integration runs in the maintained disposable [Omarchy Plugin Lab](https://github.com/mtolhuys/omarchy-plugin-lab):

```bash
make phase0
make lab-missing-tailscale
make lab
```

Phase 0 proves current public plugin/QML/Hyprland/theme/lock contracts before implementation assumptions. The lifecycle test installs the artifact in a clean guest, uses QMP-visible interaction plus machine assertions, pairs a client, re-pairs the exact browser installation and proves the old record disappears while the count stays one, focuses and moves an exact window, applies dark/light themes, proves stable ordering and lock redaction, updates without inventing authority, revokes, disables, removes, and verifies route/process/file cleanup.

The lab records before/after Tailscale identity/configuration/SSH, firewall, services, sudoers, SUID, and process/listener state. Its strict fake Tailscale proves the command/ownership contract but not behavior against an authenticated real daemon.

## Release boundaries

Physical Android claims require physical Android observation. iOS requires separate proof. Authenticated isolated-real-Tailscale mutation/cleanup and signing remain explicitly unverified until performed; fake or host evidence cannot upgrade those claims.

### Physical Android Drop checklist

Run only after source, browser, security, and Plugin Lab gates are green, using the current installed PWA and an existing credential:

1. request and locally approve `write:inbox` without re-pairing;
2. send a picker-selected screenshot and verify exact bytes plus desktop mode 0600;
3. install/update the PWA and observe Sidecar in Android's share sheet;
4. share a screenshot while Sidecar is closed/backgrounded, confirm foreground review, send once, and verify arrival;
5. cancel a second share and verify no file arrives;
6. prove lock and pause literal refusal, then explicit retry after recovery;
7. revoke the phone and prove access terminates;
8. inspect screenshots, logs, diagnostics, and notification infrastructure for secrets/private identity.

Record device model, Android version, Chrome/WebAPK version, PWA install/update state, and exact outcomes. Until those observations exist, the picker/share-target implementation is not a physical Android support claim.
