# Accepted decisions

## D-001: Private transport and separate authorization

Tailscale supplies reachability only. Sidecar uses independent per-phone credentials, exact scopes, local approval, replay protection, and rate limits.

## D-002: One user-session helper

A same-user loopback helper keeps protocol and security policy testable. It refuses root and requires no privileged service.

## D-003: One exact owned Serve route

Sidecar owns only port 48719 to its loopback listener. Existing identity is mandatory; membership, SSH, Funnel, firewall, and unrelated routes are never changed. Conflict or uncertainty fails closed.

## D-004: Typed adapters only

Every remote operation maps to an exact enum and strict payload. No generic command, method, executable, path, URL, key, pointer, terminal, prompt, or dynamic UI is accepted.

## D-005: Local approval and per-phone scopes

QR possession is insufficient. Matching words plus unlocked desktop approval issue one independent credential. Capability upgrades preserve identity and require a second explicit local approval.

## D-006: Lock uncertainty is locked

Unknown lock truth redacts private state and denies all actions. Pause, revoke, rescope, restart, disable, and corruption likewise fail closed.

## D-007: Minimal desktop projection

Portal exposes numbered workspaces, bounded app identity, opaque targets, and focus state. Window titles, paths, URLs, screen content, terminal data, and media metadata are excluded.

## D-008: Portal focus preserves order

The phone renders the authoritative compositor order it received. Focusing changes active styling only; it never sorts the tapped window to the front. Hold-and-drag is the only window-move gesture.

## D-009: Morph uses trusted installed themes

The phone selects only enumerated installed theme IDs. Theme images are bounded files resolved by the desktop adapter; the phone never provides a path or URL. Browsing does not apply; tapping does.

## D-010: Semantic light and dark recipes

Optional Omarchy color keys cannot be assumed. Guaranteed colors are sanitized, text contrast is repaired, and surfaces are derived by mixing foreground into background. Light themes use stronger hairlines/layers and softer previews; dark-only shadows and glass are forbidden.

## D-011: Omarchy-native visual grammar

Sidecar follows Aether/Omarchy with flat square surfaces, strong sans-serif hierarchy, compact monospaced metadata, quiet separators, selective accent, large touch targets, and restrained motion. Orbital geometry is limited to spatial/theme atmosphere.

## D-012: Beam stays contextual and content-free

Media exposes previous, play/pause, and next only when available. No titles, artwork, provider URLs, queues, or generic player commands.

## D-013: Deterministic transparent distribution

Sidecar 0.2.1 ships source plus a normalized reproducible tar and checksum, never an AUR package or privileged installer. Artifact scanning blocks hooks, services, privilege, SSH, firewall, tailnet enrollment, and runtime downloads. Detached signing is required before a signed-release claim.

## D-014: Immutable runtime graphs

Every behavioral or visual change advances the service, helper, widget, web, cache, allowlist, manifest, tests, and evidence together. Superseded graph sources are removed.

## D-015: Disposable desktop integration

All QML/Hyprland/Omarchy lifecycle work is tested in the maintained Plugin Lab with QMP-visible interaction plus machine assertions. The daily host is used only for narrow user-directed real-phone verification.

## D-016: Do not ship Carry without universal real value

The Carry experiment proved bounded lifecycle display but not a compelling or universal user outcome. Omarchy's `omarchy-agent` is a launcher for multiple tools; it is not a provider-neutral lifecycle or conversation API. Keeping Carry would make support provider-specific, while adding phone conversation would require unstable private-state scraping or generic remote input.

Carry and its Codex hook, provider socket, task store, phone renderer, desktop setup, marketing, and active scopes are removed from this release. Old scope strings remain parseable solely to preserve durable records and authorize nothing. A future agent feature requires an upstream provider-neutral versioned contract, plug-and-play proof across Omarchy's supported agents, and a new security decision.

## D-017: Normal camera pairing only

An embedded phone QR scanner added browser zoom/permission complexity and was unreliable. Sidecar uses the normal phone camera for pairing and keeps no camera permission or scanner code.

## D-018: Replace pairings by browser installation, never by name

The editable phone name and coarse platform cannot distinguish two real phones. The PWA therefore persists a random non-authorizing installation ID separately from its credential; the desktop stores only its digest. Local approval for that exact installation atomically creates the new credential and removes the old one, including streams and pending policy state. This prevents duplicate growth without merging equally named devices or fingerprinting a browser.

Schema-v1 devices are migrated without guessed identity. The authenticated current browser can claim its own legacy record, but an abandoned legacy record remains explicit local cleanup because no safe evidence links it to a physical phone.
## D-019: One bounded inbox, not file access

Accepted. Sidecar adds one fixed phone-to-desktop inbox under the maintained Downloads contract. `write:inbox` is separately approved; the phone cannot choose or learn a desktop path. The supported types/count/size, in-memory intents, browser staging lifetime, concurrency/rate/space reservations, streamed validation, and atomic no-overwrite mode-0600 commit are fixed protocol/security bounds. Completed files are user data; incomplete staging is product state. This does not create remote filesystem access, synchronization, or a bidirectional bridge.

Android Web Share Target is implemented according to current Chromium documentation, but product support is claimed only after physical observation. iOS is not inferred.

## D-020: One live-authority and commit boundary

Accepted. Authenticated handler state is an identity hint, never lasting authority. Snapshot, event subscription, capability, action, route, pause, rescope, replacement, and revoke paths re-read the current durable device under the shared action boundary. Route or lock loss invalidates incomplete Drop state before a minimal projection is published.

Upload validation keeps a descriptor for the exact staging inode; final no-overwrite linking, mode checks, link-count checks, and fsync use verified no-follow directory descriptors. Finite HTTP responses close their connection so rejected or unread request bytes cannot become a second request. These choices trade keep-alive throughput for a smaller framing and filesystem race surface on a bounded phone control plane.
