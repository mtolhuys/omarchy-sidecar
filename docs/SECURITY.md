# Security contract

Sidecar is a small private control surface, not remote administration. Tailscale provides private reachability; Sidecar's independent per-phone credential and scopes provide authorization.

## Non-negotiable invariants

1. The helper refuses UID 0 and listens only on `127.0.0.1:47991`.
2. Sidecar uses only an already-active Tailscale identity.
3. It owns exactly `https://<this-device>.ts.net:48719 -> http://127.0.0.1:47991`.
4. It never runs `tailscale up`, `tailscale login`, tailnet enrollment, auth keys, Tailscale SSH, Funnel, SSH/OpenSSH, or a generic remote command.
5. It never uses `sudo`, `su`, `pkexec`, AUR/package managers, install hooks, systemd units/timers, SUID, sudoers, or firewall mutation.
6. A route conflict, unknown ownership, inactive identity, locked/unknown desktop lock, pause, revoked credential, stale target, unknown input, or policy race fails closed.
7. Phone input can select only a fixed action enum and exact bounded payload. It cannot choose an executable, argument vector, address, path, URL, key, pointer event, or adapter method.
8. Diagnostics, logs, screenshots, and release evidence contain no credentials, pairing secrets, device names, window titles, media metadata, paths, URLs, desktop content, or private Tailscale identity.

The visible Security Receipt reports current non-root UID, loopback listener, existing-tailnet-only behavior, untouched Tailscale SSH and system state, exact owned route, transport/authorization split, invariant failures, and literal recovery.

## Supply-chain threat

The Arch Linux report on the malicious `hyprland-fixes` AUR package is a concrete design input: legitimate-looking Hyprland configuration was used as cover for privileged package behavior that installed network services, enrolled an attacker tailnet, enabled Tailscale SSH, added keys, created root SSH/SUID/sudoers/systemd persistence, changed firewall state, and erased logs. The official [Arch incident report](https://lists.archlinux.org/archives/list/aur-general@lists.archlinux.org/thread/TAASU6LTO76UCKYLMG25OJPUY7ZONASN/) is sufficient evidence; the malware must never be downloaded or inspected.

Consequences:

- Sidecar 0.2.1 has no AUR package or privileged installer;
- source is transparent and the release tar is deterministic;
- an adjacent SHA-256 checksum is mandatory;
- archive entries, modes, ordering, ownership, links, and executable surfaces are inspected;
- runtime dependency downloads and install hooks are forbidden;
- the signing path is a detached maintainer signature over the checksum file, published beside the pinned source revision when a signing identity is available;
- signing remains an explicit release boundary until proven.

## Pairing and device identity

The desktop creates a 256-bit QR secret with a two-minute lifetime. It is placed only in the URL fragment and consumed by the first valid pairing request. Three matching words bind the visible phone request to the local desktop approval. Pending polling uses a separate capability. Approval is possible only while lock truth is known and unlocked.

Each phone browser installation receives a separate random credential. A stable 256-bit, non-authorizing installation ID is kept separately in private browser storage; the desktop stores only its SHA-256 digest alongside the salted credential verifier. A newly approved credential for that exact installation atomically removes the old credential, active streams, replay state, pending capability requests, and staged inbox work. It does not inherit hidden authority: the locally approved scopes are authoritative.

Names and platforms are never identity keys, so two separate phones both named “My Android phone” remain independently paired. A credential cannot rebind itself to another installation ID. Legacy schema-v1 records migrate with no guessed identity; the authenticated browser binds its current record on first load. Abandoned pre-v1009 records cannot be attributed safely and require one final explicit local removal rather than browser fingerprinting or name-based deletion.

## Authorization

`write:inbox` is requestable but never silently added to an existing credential. Approval is local, unlocked, separately labelled, and may extend the same credential. Removing the scope immediately invalidates its pending intents and active bodies.

Default scopes:

- `read:desktop`
- `control:workspace`
- `control:window-focus`
- `control:window-move`
- `control:media`
- `control:theme`
- `control:lock`

Only move, theme, lock, and `write:inbox` may be requested later from the phone. The unlocked local panel displays the exact requesting device and human scope labels before approval. Rescope is serialized with actions and active streams receive refreshed state.

Retired scopes are parse-only compatibility values. They authorize no active action and cannot be requested.

## Action safety

For every action Sidecar:

1. re-reads the current device record;
2. refreshes lock and route truth;
3. checks pause and exact scope;
4. binds request ID to the canonical payload;
5. rate-limits the device and operation family;
6. validates an exact schema and numeric bounds;
7. refreshes authoritative desktop state;
8. resolves the opaque target against a current allowlist;
9. dispatches one fixed adapter operation;
10. records the result under the serialized replay boundary.

Exact replay returns the prior result. Reusing the ID with a different payload is rejected. Revocation, rescope, pause, and dispatch share the same serialization boundary.

## Data minimization

Ordinary snapshots include application identity only, not window titles. Workspaces are numbered. Media exposes transport availability, never content metadata. Themes expose bounded semantic colors, trusted names/IDs, and optional bounded current-wallpaper image bytes.

Sidecar never collects or transmits terminal text, prompts, transcripts, model output, browser URLs, file/project paths, clipboard, screenshots, screen pixels, keyboard/pointer input, microphone/camera data, or analytics.

## Lock, pause, revoke, and lifecycle

Locked or unknown lock state produces a minimal snapshot and denies previews/actions. Pause does the same and invalidates active interaction. Revoke terminates the exact credential and stream. Update broadcasts restart and requires a fresh snapshot. Disable/removal stops the helper and removes only the exact owned Serve route. Corrupt private state is quarantined and service fails closed.

## HTTP and browser boundary

Drop rejects chunked or unknown-length bodies, transfer/content-range ambiguity, wrong content type, declared/actual size or SHA-256 disagreement, unsupported extensions, MIME/extension disagreement, malformed magic, binary text, and active PDF constructs. Limits are five files, 25 MiB each, 50 MiB per batch, four pending intents per device, sixteen globally, and two simultaneous uploads; byte/file hourly limits and reserved free-space headroom fail closed.

Names are NFKC-normalized, stripped of controls, bidi controls, separators, and unsafe structure, and bounded before a random collision suffix is added. Staging and final files must be regular, user-owned, single-link entries. Final files are mode 0600 and never overwrite. Incomplete staging is Sidecar-owned and disposable; committed files are user data and survive crash, restart, disable, update, and removal.

Hostile Android share sources are untrusted. The service worker stores only bounded `File` objects and metadata in a dedicated store, never an authorization credential, and cannot initiate upload until the user sees and confirms the foreground sheet. Intent creation consumes the declared file/byte hourly allowance even when the user later cancels, the intent expires, or validation fails; this prevents repeated abandoned reservations from becoming a cheap capacity bypass.

- strict request/header/body/time/count limits;
- duplicate framing-sensitive headers, transfer encodings, ranges, trailers, `Expect`, and bodies on bodyless routes are rejected; every finite response closes its HTTP connection;
- bearer authentication for all private endpoints;
- same-origin enforcement and no API query strings;
- CSP, no inline script, fixed static asset allowlist, immutable cache graph;
- no CORS, WebSocket, remote font/script, analytics, third-party content, or public endpoint;
- service worker caches only versioned app assets, never API snapshots, credentials, pairing state, or theme images;
- pairing fragments are removed after use and excluded from referrers;
- phone storage contains one credential, one random non-authorizing browser-installation ID, and local UI preferences only;
- raw or hashed browser-installation identity is excluded from snapshots, logs, diagnostics, screenshots, and notification payloads.

## Agent boundary

Omarchy exposes no provider-neutral task lifecycle/conversation API. Sidecar therefore has no agent integration. It does not scrape terminal text, transcripts, process command lines, private logs, or unstable UI, and it does not accept prompts or command approvals from a phone. This removes the former local provider socket, hook-installation path, task store, and additional supply-chain surface.

## Release blockers

Automated release checks fail on:

- forbidden privileged/network primitives;
- UID 0 execution;
- non-loopback listener;
- unexpected artifacts, executable modes, links, hooks, services, or package metadata;
- stale runtime graph references;
- runtime/browser source containing retired agent functionality;
- before/after drift in unrelated Tailscale configuration, Tailscale SSH, firewall, system services, sudoers, tailnet identity, or owned-route cleanup;
- source/artifact test or checksum mismatch.

The disposable Plugin Lab proves desktop integration and machine invariants. Authenticated isolated-real-Tailscale mutation/cleanup, physical platforms, and signing are reported as boundaries until actually verified.
