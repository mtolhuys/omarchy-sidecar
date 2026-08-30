# Sidecar engineering contract

Sidecar 0.2.1 uses runtime graph `v1011`. The source tree, immutable graph, artifact, documentation, tests, evidence, and screenshots must describe the same product.

Read these files before changing runtime behavior:

1. `docs/PRODUCT.md`
2. `docs/UX.md`
3. `docs/ARCHITECTURE.md`
4. `docs/SECURITY.md`
5. `docs/PROTOCOL.md`
6. `docs/TESTING.md`
7. `docs/DECISIONS.md`
8. `docs/RELEASE-EVIDENCE.md`

For Omarchy integration work, also read the maintained Plugin Lab `AGENTS.md`, `README.md`, and `TESTING.md`, then inspect the Omarchy source selected by the lab. Every desktop integration test runs in the disposable Plugin Lab, never on the daily host.

## Product boundary

Sidecar includes one bounded phone-to-desktop Drop path. It accepts only PNG, JPEG, WebP, GIF, PDF, and UTF-8 `.txt`; at most five files, 25 MiB each, and 50 MiB per batch. The fixed destination is the user's Downloads directory plus `Sidecar`. This is an inbox, not remote filesystem access or a bidirectional file bridge. Completed files are user data and lifecycle cleanup must preserve them.

Sidecar is a private phone companion with two destinations:

- **Portal**: view bounded application identity, focus workspaces/apps, and deliberately move an app to another workspace.
- **Morph**: preview and apply themes already installed by the user, advance the current theme background, and undo the last phone theme change.

A contextual **Beam** exposes only previous, play/pause, and next when a supported media session exists. The connection sheet contains scopes, compact haptics, lock, and unpair.

There is no agent task, conversation, remote prompt, terminal, generic command, screen, clipboard, file, browser URL, or window-title surface. Omarchy provides a common launcher for several coding agents but no provider-neutral task lifecycle or conversation API. Do not infer one by scraping terminal text, transcripts, logs, process command lines, private state, or UI.

## Security invariants

`write:inbox` is separately approved. Every upload uses an opaque intent, exact length/type/SHA-256 declaration, bounded streaming, content validation, live lock/pause/revoke/rescope checks, private staging, and atomic mode-0600 no-overwrite commit. Never log paths, filenames, payloads, credentials, or private browser share staging.

- Run as the desktop user; fail if UID 0.
- Bind the helper only to `127.0.0.1:47991`.
- Reuse the user's already-active Tailscale identity and own exactly `https://<this-device>.ts.net:48719 -> http://127.0.0.1:47991`.
- Never install or enroll Tailscale, enable Tailscale SSH, use Funnel, mutate unrelated Serve routes, or continue through route uncertainty/conflict.
- Never invoke `sudo`, `su`, `pkexec`, a package manager/AUR helper, install hooks, system services/timers, SSH/OpenSSH, firewall tools, sudoers, SUID, or generic remote commands.
- Tailscale supplies private transport. Sidecar credentials and per-phone scopes supply authorization.
- Pairing requires a fresh QR secret plus matching words and explicit local approval. Each browser installation receives an independent credential; a newly approved credential for that exact installation atomically replaces its older pairing without merging devices by editable name.
- Lock or unknown-lock redacts desktop detail and denies actions. Pause, revoke, rescope, update, disable, and removal fail closed.
- Every action is an exact enum with a strict payload, current opaque target, scope check, serialized replay decision, and rate limit.
- Theme previews are bounded image bytes from currently enumerated installed themes; the phone never supplies a path or URL.
- Diagnostics and logs exclude credentials, pairing secrets, device names, window titles, media metadata, paths, browser URLs, and desktop content.

Retired scope strings (`read:carry`, `notify:carry`, `respond:carry-choice`, `control:focus-mode`, `control:agent-focus`, `control:presentation`) remain parseable only to preserve existing records. They are never proposed or remotely requested and authorize no action.

## Runtime graph

Service, widget, helper, web assets, service worker cache, server allowlist, manifest entrypoints, tests, and release evidence use one immutable graph ID. Never overwrite a shipped graph in place. Create a new graph, update every reference, delete the superseded source graph, and rebuild the artifact.

## Verification gates

Before release:

- `make test`
- browser interaction at 390×844 and 320×700 in dark and light themes, including reduced motion
- `make performance`
- `make dist` twice with identical SHA-256 output
- `make security`, including forbidden-primitive and artifact inspection
- Plugin Lab Phase 0, missing-Tailscale recovery, and complete lifecycle with QMP-visible interaction plus machine assertions
- exact release evidence for verified and unverified boundaries

Physical Android foreground behavior is claimed only from a physical Android test. iOS, signing, and authenticated isolated-real-Tailscale mutation/cleanup remain unverified until separately proven. Screenshots are visual evidence, never state proof.

## Repository hygiene

Keep only runtime source, tests, current evidence, current media, and contributor/user documentation. Do not commit caches, extracted artifacts, VM state, historical prompts, private diagnostics, experimental adapters, dead graphs, or superseded product documents.
