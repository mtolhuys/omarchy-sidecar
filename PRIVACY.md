# Privacy

Drop payloads are sent only after foreground confirmation. The service worker may hold a selected Android share locally in origin-private IndexedDB for at most five minutes; it stores no Sidecar credential with that payload. Cancel, expiry, credential loss, unpair, or completed send deletes that private staging.

Accepted files are written to the fixed `Downloads/Sidecar` directory, labelled **Sidecar Inbox** in the interface. Filenames are normalized and notifications expose only a generic type/count, never a filename or path. Completed files are user-owned data and are not removed by Sidecar disable, update, or uninstall. Incomplete Sidecar staging is removed.

Sidecar has no hosted backend, account, analytics, tracking, advertising, public endpoint, or third-party content.

The phone receives only what its scopes allow: numbered workspaces, bounded application identity and focus state, semantic theme colors, installed theme names/opaque IDs, optional bounded wallpaper previews, coarse media transport availability, and connection/lock state.

Sidecar does not collect or expose window titles, project/file paths, browser URLs, terminal text, prompts, transcripts, commands, model output, media title/artist/artwork, clipboard, screenshots, screen pixels, keyboard/pointer input, microphone/camera data, or location.

Tailscale transports encrypted traffic inside the user's existing tailnet. Sidecar never enrolls a device or changes Tailscale SSH. Tailscale remains a separate service with its own privacy boundary.

The desktop stores salted phone credential verifiers, friendly device metadata, exact scopes, and pause state in an owner-only file. The phone stores its one credential plus local UI preferences. Unpairing invalidates the credential immediately.

Logs and diagnostics are redacted. Release evidence and screenshots must use fake or redacted identities.
