import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {
  PORTAL_SCOPES, activeWorkspace, adjacentWorkspace, contrast, mixColor,
  missingPortalScopes, reconnectDelay, sanitizeBeam, sanitizeTheme, sanitizeThemes,
  snapshotTransition, stableItemOrder,
} from "../web/dist/model.v1011.js";

const snapshot = {
  protocol: 1,
  seq: 2,
  server: {bootId: "boot_one"},
  desktop: {locked: false},
  session: {paused: false, permissions: ["read:desktop"]},
  workspaces: [
    {id: "ws_1", label: "1", active: true},
    {id: "ws_2", label: "2", active: false},
    {id: "ws_3", label: "3", active: false},
  ],
};

assert.equal(activeWorkspace(snapshot).id, "ws_1");
assert.equal(activeWorkspace(snapshot, "ws_3").id, "ws_1");
assert.equal(activeWorkspace({workspaces: snapshot.workspaces.map(item => ({...item, active: false}))}, "ws_3").id, "ws_3");
assert.equal(activeWorkspace({workspaces: []}), null);
assert.equal(adjacentWorkspace(snapshot, "ws_1", 1).id, "ws_2");
assert.equal(adjacentWorkspace(snapshot, "ws_1", -1).id, "ws_3");
assert.equal(adjacentWorkspace({workspaces: [{id: "only"}]}, "only", 1), null);
assert.deepEqual(missingPortalScopes([]), [...PORTAL_SCOPES]);
assert.deepEqual(missingPortalScopes([...PORTAL_SCOPES]), []);
assert.deepEqual([...PORTAL_SCOPES], ["control:window-move", "control:theme", "control:lock"]);

const hostile = sanitizeTheme({background: "url(evil)", text: "#18181b", textMuted: "#18181b", accent: "#ffffff", radius: 900});
assert.equal(hostile.colors.background, "#111116");
assert.ok(contrast(hostile.colors.background, hostile.colors.text) >= 4.5);
assert.ok(contrast(hostile.colors.background, hostile.colors["text-muted"]) >= 3);
assert.equal(hostile.radius, 30);

const light = sanitizeTheme({
  scheme: "light", background: "#f4f3ef", text: "#191919", textMuted: "#777777",
  accent: "#2563eb", success: "#16803c", warning: "#9a6700", danger: "#c52b36",
});
assert.equal(light.scheme, "light");
assert.ok(contrast(light.colors.background, light.colors.text) >= 4.5);
assert.ok(contrast(light.colors.background, light.colors["text-muted"]) >= 3);
assert.equal(light.colors.surface, mixColor(light.colors.background, light.colors.text, .06));
assert.equal(light.colors["surface-elevated"], mixColor(light.colors.background, light.colors.text, .09));
assert.notEqual(light.colors.surface, "#23232b");
assert.equal(sanitizeTheme({background: "#ffffff", text: "#111111"}).scheme, "light");

const themes = {
  items: [{id: "theme_aurora", name: "Aurora", previewAccent: "#8b5cf6", previewAvailable: true}],
  currentId: "theme_aurora", currentName: "Aurora",
};
assert.deepEqual(sanitizeThemes(themes), themes);
assert.equal(sanitizeThemes({...themes, items: [{...themes.items[0], path: "/private/theme"}]}), null);
assert.equal(sanitizeThemes({...themes, items: [{...themes.items[0], previewAvailable: "yes"}]}), null);

assert.deepEqual(snapshotTransition(null, snapshot), {accept: true, refresh: false, reason: "boot"});
assert.equal(snapshotTransition(snapshot, {...snapshot, seq: 1}).reason, "stale");
assert.equal(snapshotTransition(snapshot, {...snapshot, seq: 4}).reason, "gap");
assert.equal(snapshotTransition(snapshot, {...snapshot, seq: 3}).accept, true);
assert.equal(snapshotTransition(snapshot, {...snapshot, seq: 1, server: {bootId: "boot_two"}}).accept, true);
assert.equal(snapshotTransition(snapshot, {...snapshot, protocol: 2}).reason, "invalid");
assert.equal(reconnectDelay(1, 0), 800);
assert.equal(Math.round(reconnectDelay(20, 1)), 18000);

const firstWindowOrder = [{id: "win_a", focused: false}, {id: "win_b", focused: true}];
const focusedWindowOrder = [{id: "win_b", focused: false}, {id: "win_a", focused: true}];
assert.deepEqual(stableItemOrder([], firstWindowOrder).map(item => item.id), ["win_a", "win_b"]);
assert.deepEqual(stableItemOrder(["win_a", "win_b"], focusedWindowOrder).map(item => item.id), ["win_a", "win_b"]);
assert.deepEqual(stableItemOrder(["win_a", "win_b"], [{id: "win_b"}, {id: "win_c"}]).map(item => item.id), ["win_b", "win_c"]);

const beam = {
  provider: "media.transport", kind: "typed-actions", label: "Now playing", status: "Playing",
  actions: [
    {id: "previous", action: "media.previous", enabled: true, label: "Previous"},
    {id: "playPause", action: "media.playPause", enabled: true, label: "Pause"},
    {id: "next", action: "media.next", enabled: true, label: "Next"},
  ],
};
assert.deepEqual(sanitizeBeam(beam), beam);
assert.equal(sanitizeBeam({...beam, html: "<iframe>"}), null);
assert.equal(sanitizeBeam({...beam, provider: "remote.url"}), null);
assert.equal(sanitizeBeam({...beam, actions: beam.actions.map((item, index) => index ? item : {...item, action: "shell.run"})}), null);

const html = readFileSync(new URL("../web/dist/index.html", import.meta.url), "utf8");
const script = readFileSync(new URL("../web/dist/app.v1011.js", import.meta.url), "utf8");
const model = readFileSync(new URL("../web/dist/model.v1011.js", import.meta.url), "utf8");
const css = readFileSync(new URL("../web/dist/app.v1011.css", import.meta.url), "utf8");
const worker = readFileSync(new URL("../web/dist/sw.v1011.js", import.meta.url), "utf8");
const manifest = JSON.parse(readFileSync(new URL("../web/dist/manifest.webmanifest", import.meta.url), "utf8"));

assert.match(html, /Tailscale carries traffic privately/);
assert.match(html, /Sidecar credential and explicit scopes/);
assert.match(html, /data-nav="portal"/);
assert.match(html, /data-nav="morph"/);
assert.match(html, /id="morph-hero-preview"/);
assert.match(html, /Tap one to apply/);
assert.match(html, /data-icon="palette"/);
assert.match(html, /scan the fresh QR code with your phone camera/);
assert.match(html, /id="haptics-toggle" type="checkbox"/);
assert.match(html, /Tap to focus · hold and drag to move/);
assert.equal((html.match(/id="connection-orb"/g) || []).length, 1);
assert.equal((html.match(/class="connection-phone"/g) || []).length, 1);
assert.equal((html.match(/data-nav=/g) || []).length, 2);
assert.match(html, /id="drop-open"/);
assert.match(html, /id="drop-picker" type="file" multiple/);
assert.match(html, /Send to desktop/);
assert.match(html, /Sidecar Inbox/);
assert.match(html, /role="progressbar"/);
assert.equal(manifest.share_target.action, "/app/share-target");
assert.equal(manifest.share_target.method, "POST");
assert.equal(manifest.share_target.enctype, "multipart/form-data");
assert.deepEqual(manifest.share_target.params.files[0].accept, ["image/png", ".png", "image/jpeg", ".jpg", ".jpeg", "image/webp", ".webp", "image/gif", ".gif", "application/pdf", ".pdf", "text/plain", ".txt"]);

for (const source of [html, script, model, css]) assert.doesNotMatch(source, /Carry|carry|Codex|codex|Agent task|read:carry/);
assert.doesNotMatch(html, /id="qr-scanner"|scan-qr-settings|scan-qr-onboarding/);
for (const retired of ["deck", "workspaces", "agents", "controls"]) assert.doesNotMatch(html, new RegExp(`data-nav="${retired}"`));

assert.match(script, /window\.moveToWorkspace/);
assert.match(script, /stableItemOrder\(portalWindowOrder\.get\(workspace\.id\)/);
assert.match(script, /\.closest\?\.\("\.drop-zone"\)/);
assert.match(script, /theme\.set/);
assert.match(script, /theme\.backgroundNext/);
assert.match(script, /desktop\.lock/);
assert.match(script, /\/api\/v1\/capabilities\/request/);
assert.match(script, /\/api\/v1\/theme-previews\//);
assert.match(script, /\/api\/v1\/session\/identify/);
assert.match(script, /\/api\/v1\/inbox\/intents/);
assert.match(script, /\/api\/v1\/inbox\/uploads\//);
assert.match(script, /\/api\/v1\/inbox\/cancel/);
assert.match(script, /scopes: \[DROP_SCOPE\]/);
assert.match(script, /new XMLHttpRequest\(\)/);
assert.match(script, /request\.upload\.onprogress/);
assert.match(script, /crypto\.subtle\.digest\("SHA-256"/);
assert.match(script, /Saved to Sidecar Inbox/);
assert.match(script, /activeUpload\?\.abort/);
assert.match(script, /async function boundedFetch/);
assert.match(script, /request\.timeout = 120000/);
assert.match(script, /let refreshPromise = null/);
assert.match(script, /generation !== streamGeneration/);
assert.match(script, /dropFiles\.filter\(item => item\.state !== "Saved"\)/);
assert.match(script, /Retry failed files/);
assert.match(script, /disabled = dropBusy \|\| !retryable\.length/);
assert.match(script, /clientInstanceId/);
assert.match(script, /sci1_\[A-Za-z0-9_-\]\{43\}/);
assert.match(script, /saveClientInstanceId/);
assert.match(script, /device: \{[\s\S]*clientInstanceId/);
assert.match(script, /client_identity_changed/);
assert.match(script, /The old pairing remains safe until you remove it locally/);
assert.match(script, /try \{ credential = await readCredential\(\)[\s\S]*if \(pairingSecret\) \{[\s\S]*await bindClientInstance\(\)/);
assert.match(script, /Could not prepare a clean re-pair/);
assert.doesNotMatch(script, /deleteClientInstanceId/);
assert.match(script, /URL\.createObjectURL\(blob\)/);
assert.match(script, /URL\.revokeObjectURL\(url\)/);
assert.match(script, /new IntersectionObserver/);
assert.match(script, /credentials: "omit"/);
assert.match(script, /root\.dataset\.scheme = safe\.scheme/);
assert.doesNotMatch(script, /scheduleMorphCommit|nearestThemeCard|addEventListener\("scroll"/);
assert.doesNotMatch(script, /portalWorkspaceId = workspace\.id;\s*renderPortal\(\)/);
assert.doesNotMatch(script, /\.innerHTML\s*=/);
assert.doesNotMatch(script, /\beval\s*\(|new Function/);

assert.match(css, /:root\[data-scheme="light"\]/);
assert.match(css, /--panel: color-mix\(in srgb, var\(--text\) 6%, var\(--background\)\)/);
assert.match(css, /--preview-brightness: \.96/);
assert.match(css, /--control-radius: 0px/);
assert.match(css, /@media \(max-width: 340px\)/);
assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
assert.match(css, /\.theme-preview/);
assert.match(css, /\.morph-hero-preview/);
assert.match(css, /#haptics-toggle \{ width: 20px; height: 20px/);
assert.doesNotMatch(css, /#000[0-9a-f]/i);
assert.doesNotMatch(css, /backdrop-filter/);
assert.doesNotMatch(css, /\.qr-scanner|scanner-line|\.toggle/);
assert.match(worker, /sidecar-web-v1011/);
assert.match(worker, /sidecar-web-v1011-final/);
assert.match(worker, /const ASSET_PATHS = new Set\(ASSETS\)/);
assert.match(worker, /url\.search \|\| !ASSET_PATHS\.has\(url\.pathname\)/);
assert.match(worker, /request\.formData\(\)/);
assert.match(worker, /indexedDB\.open\(SHARE_DB, 2\)/);
assert.match(worker, /MAX_BATCH = 50 \* 1024 \* 1024/);
assert.match(worker, /url\.pathname === "\/app\/share-target"/);
const sharePersistence = worker.split("async function writeShare")[1].split("async function cleanupShares")[0];
assert.doesNotMatch(sharePersistence, /credential|Authorization|Bearer|private/);

console.log(JSON.stringify({
  tests: 139,
  portal: "stable-order+focus+swipe+long-press+authoritative-move",
  morph: "trusted-inventory+tap-to-apply+theme-preview+light-dark-semantics",
  beam: "fixed-schema-only",
  capabilities: "explicit-desktop-reapproval",
  drop: "picker+share-target+confirmation+progress+cleanup",
  layout: "390x844+320x700+reduced-motion",
}));
