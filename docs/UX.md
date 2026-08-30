# UX contract

## Visual language

Sidecar should feel like Omarchy moved onto a small screen, not like a glassy mobile dashboard:

- square, flat surfaces;
- strong sans-serif hierarchy and compact monospaced metadata;
- faint separators and one restrained accent;
- no decorative black shadows, frosted-glass blur, or fixed dark overlays;
- theme-derived colors with separate dark/light layer recipes;
- minimum 48×48 CSS-pixel touch targets;
- motion used only to confirm navigation, pairing, focus, or theme application;
- complete reduced-motion behavior.

Dark surfaces mix 4% and 7% foreground into the theme background; light surfaces use 6% and 9%. Hairlines use 8% foreground in dark themes and 16% in light themes. Text and muted text are contrast-corrected if an installed theme provides an unsafe pair.

## Navigation

The connection/phone mark at top left is the single settings entry. The bottom navigation contains only Portal and Morph. It remains reachable above safe-area insets and never hides the final interactive content after scrolling.

## Portal

- The stage is spatial, calm, and readable at 320×700 and 390×844.
- A tap focuses the exact app and changes only its active styling. It does not reorder tiles.
- A hold reveals explicit workspace destinations. Releasing on a destination performs the move; releasing elsewhere snaps back.
- Workspace tabs and stage swipes change workspaces without accidental window movement.
- Beam is visually secondary and appears only when available.

## Morph

- The hero names the current theme and faintly carries its current wallpaper.
- Cards show real installed-theme wallpaper previews when safe and available, with a palette fallback.
- Horizontal movement browses only. A tap applies.
- Applying provides authoritative toast/haptic confirmation after the desktop reports success.
- Undo appears only after a successful phone-initiated theme change.
- Light themes use light semantic surfaces, stronger hairlines, softer imagery, and no dark shadow residue.

## Pairing and recovery

The QR is scanned with the normal phone camera. Pairing asks for a friendly name, shows three matching words on both screens, and waits for local desktop approval. Copy explains: “Tailscale carries traffic privately. Desktop approval gives this phone a separate Sidecar credential and explicit scopes.”

The friendly name is presentation only. Re-pairing the same browser installation replaces its old credential immediately, so the desktop panel stays to one row for that installation. Separate phones with the same default name remain separate. Pre-v1009 orphaned rows may require one last explicit removal because the old build stored no safe installation identity.

Failures use literal recovery:

- missing Tailscale → open Omarchy Install → Service → Tailscale;
- Tailscale not connected → connect it, then retry;
- expired QR → ask the desktop for a new code;
- phone not on the same tailnet → connect the Tailscale phone app;
- locked/unknown lock → unlock the desktop;
- browser identity changed while a credential remained → discard that local credential, pair again, and leave the old desktop record for explicit local removal;
- revoked → pair again.

## Accessibility

Drop preserves 48×48 CSS-pixel controls, safe-area padding, focusable dialog semantics, keyboard operation, a labelled progressbar, and live literal status. Motion is disabled under `prefers-reduced-motion`; success never depends only on color or vibration. The 390×844 and 320×700 layouts must have no horizontal overflow or bottom-nav/control overlap.

- Semantic headings, regions, tabs, buttons, status, and live announcements are required.
- Focus state must not depend on color alone.
- Current theme and workspace are exposed with ARIA state and refreshed labels.
- Haptics are optional, compact, and never the only feedback.
- No interaction depends only on a hidden gesture; the visible hint explains hold-and-drag.

## Drop

Drop is a prominent header action, not a third navigation tab. It opens a flat confirmation sheet above Portal or Morph. The sheet always names the fixed destination, keeps remove/cancel/send reachable, announces progress and errors, and leaves failed items explicitly retryable while completed items cannot be resent accidentally.

The desktop panel shows only bounded generic attention such as `Text received` and `Saved to Sidecar Inbox`, followed by a local `Open inbox` control. It never shows a filename or path.
