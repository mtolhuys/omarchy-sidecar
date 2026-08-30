# Product contract

Sidecar makes a few high-frequency Omarchy actions pleasant from a phone without turning the phone into a remote shell or miniature desktop.

## Portal

- Show numbered workspaces and bounded application identity only.
- Preserve compositor order when focus changes; the focused tile gains an active border and never jumps position because it was tapped.
- Tap an app to focus its exact current opaque target.
- Hold and drag an app to a separately rendered workspace destination. An ordinary tap never moves a window.
- Swipe the stage to move between adjacent workspaces.
- Never expose window titles, paths, URLs, terminal contents, screenshots, or arbitrary input.

## Morph

- List themes already trusted and installed by Omarchy.
- Show the real current wallpaper as the hero and as bounded theme-card previews when available.
- Swiping only browses; applying requires an explicit tap.
- Support one-step undo for the last theme applied from this phone session.
- Advance the current theme background through the fixed Omarchy adapter.
- Derive the interface from the theme's semantic background, foreground, muted, accent, selection, and surface colors.
- Use separate dark/light layer recipes so light themes never inherit black shadows or dark glass.

## Beam

- Appear only for a supported active media session.
- Expose fixed previous, play/pause, and next actions.
- Never expose media title, artist, artwork, URL, playlist, or arbitrary provider commands.

## Connection sheet

- Explain connection and authorization literally.
- Show only the phone's human-readable scopes.
- Keep one clean pairing per browser installation; a fresh credential for that exact installation replaces its older credential without merging phones by name.
- Keep haptics an optional compact local setting.
- Require a one-second hold for lock.
- Allow unpairing this exact credential.
- Offer a capability request only for the exact allowlisted missing scopes; approval happens locally on the unlocked desktop.

## Agent decision

Omarchy launches multiple coding agents through one window class, but it does not expose a provider-neutral task lifecycle or conversation API. Product-neutral support for Claude, Grok, Kimi, Codex, and others would otherwise require private-state scraping or generic remote input. Both are unreliable and outside the security boundary.

Therefore Sidecar 0.2.1 contains no agent integration, task companion, prompt surface, or agent marketing claim. A future agent feature requires an upstream, provider-neutral, versioned contract and a separate threat review. Retired agent-related scope strings authorize nothing.

## Non-goals

- remote filesystem access, arbitrary upload destinations, folder transfer, synchronization, or a bidirectional file bridge;
- a permanent files tab or file-management dashboard;
- claiming Android share-sheet behavior until observed on a physical current Android/Chromium installation, or inferring iOS support.

- remote terminal, prompt, command approval, chat, transcript, clipboard, file, screen, camera, microphone, keystroke, pointer, browser, power, package, SSH, firewall, or service control;
- arbitrary adapter names, payloads, executable names, paths, URLs, HTML, Markdown, JavaScript, QML, or CSS from the phone;
- installing or configuring Tailscale;
- public internet exposure or a hosted Sidecar backend.

## Acceptance

Release requires secure pairing, strict scopes, current-target validation, replay/rate controls, lock redaction, pause/revoke termination, stable Portal order, explicit Morph application, bounded explicitly confirmed Drop, dark/light responsiveness at 390×844 and 320×700, immutable reproducible artifacts, security scans, and disposable Plugin Lab lifecycle proof.
## Drop

Drop is a bounded phone-to-desktop inbox. An installed Android PWA advertises the supported file types to the system share sheet; every invocation opens the foreground confirmation sheet. The same sheet is available from the persistent header picker when installation/share-target support is absent. It shows safe filenames, type, each size, total size, and only the destination label `Sidecar Inbox`.

The user explicitly sends one to five PNG, JPEG, WebP, GIF, PDF, or UTF-8 `.txt` files, no more than 25 MiB each or 50 MiB total. Successful files become desktop user data under Downloads/Sidecar. Sidecar provides no remote browsing, arbitrary paths, downloads back to the phone, synchronization, or bidirectional file bridge.
