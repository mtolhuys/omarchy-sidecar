# Screenshot contract

Repository media must come from the current `v1012` graph and contain no real credentials, QR secrets, device names, tailnet names, paths, URLs, window titles, or private desktop content.

Required phone captures:

- Portal at 390×844 in one dark and one light Omarchy theme;
- Morph at 390×844 in the same themes with real wallpaper previews;
- Portal or Morph at 320×700 proving the small breakpoint.
- Drop confirmation in dark and light at 390×844, Drop success, and one Drop state at 320×700.

Required desktop capture:

- clean disposable Plugin Lab desktop with the Sidecar panel showing generic Drop attention.
- current pairing panel with a deterministic, non-authorizing demo QR replacing the captured short-lived secret.

Current phone media:

- `phone-portal-dark-390.png` and `phone-portal-light-390.png`;
- `phone-morph-dark-390.png` and `phone-morph-light-390.png`;
- `phone-portal-320.png`;
- `phone-drop-dark-390.png`, `phone-drop-light-390.png`, and `phone-drop-success-390.png`;
- `phone-drop-320.png`;
- `sidecar-showcase.gif`, a widescreen six-scene product tour built from the current pairing, Portal, Morph, Drop, and disposable-desktop captures.

The `-390` and `-320` suffixes record the browser viewport used for verification. Portal and Morph repository images are losslessly content-cropped for presentation, so they do not retain the empty portion of the original viewport canvas or browser scrollbar gutter.

Current desktop media:

- `desktop-sidecar-drop-attention.png`, captured only in the disposable Plugin Lab.
- `desktop-sidecar-pairing.png`, based on disposable Plugin Lab run `20260830-205434`; its QR payload is the fixed non-authorizing text `SIDECAR-DEMO-NOT-A-PAIRING-SECRET`, never a pairing URL or secret.

The repository-header GIF uses six concise scenes at a calm reading pace: product overview → normal-camera pairing → Portal → Morph → Drop → private-by-design. It must use real current captures, remain legible at README width, and never imply remote terminal, agent, screen sharing, arbitrary file access, cloud relay, or unsupported platform behavior. Rebuild it deterministically with `make marketing-gif` after replacing the required current-product captures.

Screenshots prove visual composition only. Protocol, action, lock, route, and cleanup claims require machine assertions.
