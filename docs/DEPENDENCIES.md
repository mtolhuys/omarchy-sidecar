# Dependencies

## Runtime

Drop adds no runtime dependency. It uses Python 3.14 standard-library hashing, incremental UTF-8 decoding, filesystem primitives, `http.server`, and the maintained desktop `xdg-open`/`omarchy-notification-send` contracts. Browser integration uses only manifest Web Share Target, service workers, IndexedDB, Web Crypto, XMLHttpRequest progress, and ordinary file inputs.

The maintained browser contract was checked against Chromium's [Web Share Target documentation](https://developer.chrome.com/docs/capabilities/web-apis/web-share-target), web.dev's [receiving shared files guidance](https://web.dev/articles/files/receive-shared-files), and [PWA OS integration guidance](https://web.dev/learn/pwa/os-integration) on 2026-08-30. These sources describe installed PWA behavior on current Chromium-class platforms; they do not establish iOS support or replace physical Android acceptance.

- Python 3 standard library
- Omarchy plugin shell and maintained command surface
- Quickshell/QML supplied by Omarchy
- Hyprland's `hyprctl`
- an already-installed, already-connected Tailscale CLI
- `qrencode`
- a modern browser

The web app is dependency-free JavaScript/CSS/HTML. No package manager, AUR helper, runtime download, SSH service, firewall tool, privileged helper, agent CLI, or cloud SDK is used.

## Build and verification

- GNU Make, tar, gzip, SHA-256 tooling
- Node.js for source-level web assertions
- the disposable Omarchy Plugin Lab for desktop integration
- optional image tooling for repository media only

Release artifacts vendor no third-party runtime code. Dependency changes require architecture, security, artifact, and release-evidence review.
