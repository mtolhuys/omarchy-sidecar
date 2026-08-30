# Architecture

```text
Omarchy shell
  ├─ BarWidget.qml                 local UI and approvals
  └─ Service.qml                   user-session lifecycle
        └─ helper/sidecard
             ├─ SidecarCore        policy, snapshots, replay, events
             ├─ OmarchyAdapter     fixed Hyprland/theme/media/lock operations
             ├─ DeviceStore        owner-only credentials and scopes
             ├─ RouteManager       one exact Tailscale Serve route
             └─ HTTP server        127.0.0.1:47991 only
                    ↑
          Tailscale Serve :48719
                    ↑
             paired phone PWA
```

## Processes and privilege

The QML service launches one same-user Python helper. The helper refuses UID 0 and binds an explicit IPv4 loopback socket. There is no installer hook, daemon, systemd unit, SSH component, firewall integration, package manager, or privilege boundary.

## Desktop adapter

The production adapter invokes fixed, inspected Omarchy/Hyprland operations with fixed argument structure. It enumerates numbered workspaces, mapped windows, installed themes, current wallpaper previews, coarse media transport, and lock truth. Phone data never becomes an executable, path, address, option, or generic dispatch string.

Window IDs, workspace IDs, and theme IDs are bounded opaque projections. Every action refreshes current compositor/theme truth and rejects stale or ineligible targets.

## Theme projection

The adapter consumes Omarchy theme colors that are actually guaranteed by the maintained source: background, foreground, muted/dark foreground, accent, selection, and lighter background. Missing optional values are derived, never replaced with a hardcoded dark surface.

The phone sanitizer validates hex colors, measures luminance/contrast, repairs unsafe text/muted pairs, detects light versus dark, and derives semantic surfaces. Wallpaper bytes are available only for an enumerated installed theme, under read plus theme scope, after lock/pause/revoke checks, with MIME and 2 MiB limits.

## State and streaming

`SidecarCore` keeps a private adapter snapshot and publishes a bounded per-device projection. Lock, unknown lock, or pause returns only protocol/session/desktop state. Server-sent events carry snapshot invalidations and terminal session events; bounded queues and stream counts prevent resource growth. Reconnect fetches a fresh authoritative snapshot.

Snapshot creation, event subscription, capability requests, actions, route transitions, pause, rescope, replacement, and revocation re-read durable device policy under the shared action boundary. Route loss and lock transitions invalidate incomplete inbox work before publishing a minimal projection, so a previously authenticated request cannot reuse a stale device object as authority.

## Persistence

`$XDG_STATE_HOME/omarchy-sidecar/devices.json` is held in a mode-0700 directory and written mode 0600 with atomic replacement. It stores salted credential verifiers, device metadata, exact scopes, pause state, schema version, and an optional SHA-256 binding for the phone browser installation. The raw browser-installation ID stays in private browser storage and is never written to desktop state, diagnostics, or snapshots. Plain credentials exist only at one-time delivery to the approved phone.

Approval creates the new credential and removes every older credential bound to that exact browser installation in one store transaction. Stream termination, replay removal, pending-capability denial, and inbox invalidation share the core policy boundary. Device names are display labels only and never identity keys. Schema-v1 records migrate without an invented binding; the authenticated phone can bind its surviving legacy credential on first v1009 load.

Retired scopes remain readable to avoid corrupting an existing record, but no active action maps to them.

## Lifecycle

Enable starts the helper and reconciles the exact Serve route. Disable and removal stop the helper and remove only the owned route. Update broadcasts restart, preserves device state, advances the immutable graph, and does not invent state. Route conflict or uncertain ownership fails closed.

## Deliberate omission of agents

The maintained Omarchy source exposes a shared launcher/window class for multiple agents, not a provider-neutral task or conversation contract. Sidecar has no provider socket, hook, watcher, task store, process-ancestry matching, transcript reader, or phone agent renderer.

## Drop data path

Drop uses a two-step intent/upload protocol. The authenticated JSON intent reserves bounded capacity and returns opaque, short-lived upload capabilities. Each raw upload is read in 64 KiB chunks through the standard-library HTTP server, incrementally hashed and validated, and repeatedly rechecks route, lock, pause, device, and `write:inbox` policy. The final check and commit share the core action lock.

The fixed destination is resolved from `XDG_DOWNLOAD_DIR` or the maintained user-dirs file without shell evaluation, constrained under the user's home, and falls back to Downloads. Private `.sidecar-staging` is on the same filesystem. A validated file is hard-linked through verified no-follow staging and inbox directory descriptors to a collision-free sanitized basename, chmod 0600, inode/link-count checked, fsynced, and the staging link removed; this supplies atomic no-overwrite commit without trusting path components re-read after validation.

The service worker intercepts only manifest-declared POST share-target launches, applies the same public count/type/size envelope, stages the selection in origin-private IndexedDB without credentials, and redirects to the foreground confirmation route. Five-minute expiry, cancel, send, credential loss, and unpair clear browser staging.
