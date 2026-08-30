// Sidecar immutable Portal model graph v1012.
export const FALLBACK_THEME = Object.freeze({
  background: "#111116", surface: "#23232b", "surface-elevated": "#373743",
  text: "#fafafa", "text-muted": "#aaaab7", accent: "#8b5cf6",
  success: "#4ade80", warning: "#facc15", danger: "#fb7185",
});

export const PORTAL_SCOPES = Object.freeze([
  "control:window-move", "control:theme", "control:lock",
]);

const OPAQUE_ID = /^[A-Za-z0-9_-]{1,128}$/;

function exactKeys(value, required, optional = []) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const expected = new Set([...required, ...optional]);
  const keys = Object.keys(value);
  return required.every(key => keys.includes(key)) && keys.every(key => expected.has(key));
}

function boundedLabel(value, maximum) {
  return typeof value === "string" && value.length > 0 && value.length <= maximum
    && !/[\u0000-\u001f\u007f]/.test(value)
    && !/(?:[/\\]|https?:\/\/|`|\$\(|\b(?:sudo|ssh|bash|sh|cmd|powershell)\b)/i.test(value);
}

export function safeColor(value, fallback) {
  return typeof value === "string" && /^#[0-9a-fA-F]{6}$/.test(value)
    ? value.toLowerCase() : fallback;
}

export function luminance(hex) {
  const channels = [1, 3, 5]
    .map(index => parseInt(hex.slice(index, index + 2), 16) / 255)
    .map(value => value <= .03928 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
  return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2];
}

export function contrast(left, right) {
  const values = [luminance(left), luminance(right)].sort((a, b) => b - a);
  return (values[0] + .05) / (values[1] + .05);
}

export function mixColor(left, right, amount) {
  const bounded = Math.min(1, Math.max(0, Number(amount) || 0));
  const channels = [1, 3, 5].map(index => Math.round(
    parseInt(left.slice(index, index + 2), 16) * (1 - bounded)
    + parseInt(right.slice(index, index + 2), 16) * bounded,
  ));
  return `#${channels.map(value => value.toString(16).padStart(2, "0")).join("")}`;
}

export function sanitizeTheme(theme = {}) {
  const background = safeColor(theme.background, FALLBACK_THEME.background);
  const declaredScheme = theme.scheme === "light" || theme.scheme === "dark" ? theme.scheme : null;
  const scheme = declaredScheme || (luminance(background) > .42 ? "light" : "dark");
  let text = safeColor(theme.text, scheme === "light" ? "#171717" : FALLBACK_THEME.text);
  if (contrast(background, text) < 4.5) text = scheme === "light" ? "#111111" : "#ffffff";
  let muted = safeColor(theme.textMuted, mixColor(background, text, scheme === "light" ? .62 : .68));
  if (contrast(background, muted) < 3) muted = mixColor(background, text, scheme === "light" ? .68 : .72);
  const surface = safeColor(theme.surface, mixColor(background, text, scheme === "light" ? .06 : .04));
  const elevated = safeColor(theme.surfaceElevated, mixColor(background, text, scheme === "light" ? .09 : .07));
  const colors = {
    background,
    surface,
    "surface-elevated": elevated,
    text,
    "text-muted": muted,
    accent: safeColor(theme.accent, FALLBACK_THEME.accent),
    success: safeColor(theme.success, FALLBACK_THEME.success),
    warning: safeColor(theme.warning, FALLBACK_THEME.warning),
    danger: safeColor(theme.danger, FALLBACK_THEME.danger),
  };
  return {
    colors,
    radius: Number.isFinite(theme.radius) ? Math.min(30, Math.max(10, theme.radius)) : 22,
    scheme,
    accentInk: luminance(colors.accent) > .45 ? "#111114" : "#ffffff",
  };
}

export function sanitizeThemes(value) {
  if (!exactKeys(value, ["items", "currentId", "currentName"]) || !Array.isArray(value.items)
    || value.items.length > 48 || (value.currentId !== "" && !OPAQUE_ID.test(value.currentId))
    || (value.currentName !== "" && !boundedLabel(value.currentName, 64))) return null;
  const items = [];
  const ids = new Set();
  for (const item of value.items) {
    if (!exactKeys(item, ["id", "name", "previewAccent", "previewAvailable"])
      || !OPAQUE_ID.test(item.id) || ids.has(item.id) || !boundedLabel(item.name, 64)
      || typeof item.previewAvailable !== "boolean"
      || typeof item.previewAccent !== "string"
      || safeColor(item.previewAccent, "") !== item.previewAccent.toLowerCase()) return null;
    ids.add(item.id);
    items.push({
      id: item.id,
      name: item.name,
      previewAccent: item.previewAccent.toLowerCase(),
      previewAvailable: item.previewAvailable,
    });
  }
  if (value.currentId && !ids.has(value.currentId)) return null;
  return {items, currentId: value.currentId, currentName: value.currentName};
}

export function snapshotTransition(current, next) {
  if (!next || next.protocol !== 1 || !next.server?.bootId || !Number.isInteger(next.seq)) {
    return {accept: false, refresh: true, reason: "invalid"};
  }
  if (!current || current.server?.bootId !== next.server.bootId) return {accept: true, refresh: false, reason: "boot"};
  if (next.seq < current.seq) return {accept: false, refresh: false, reason: "stale"};
  if (next.seq > current.seq + 1) return {accept: false, refresh: true, reason: "gap"};
  return {accept: true, refresh: false, reason: "next"};
}

export function reconnectDelay(attempt, randomValue = .5) {
  const boundedAttempt = Math.min(8, Math.max(1, Number(attempt) || 1));
  const base = Math.min(15000, 500 * (2 ** boundedAttempt));
  return base * (.8 + Math.min(1, Math.max(0, randomValue)) * .4);
}

export function activeWorkspace(snapshot = {}, preferredId = "") {
  const workspaces = Array.isArray(snapshot.workspaces) ? snapshot.workspaces : [];
  return workspaces.find(item => item?.active)
    || workspaces.find(item => item?.id === preferredId)
    || workspaces[0]
    || null;
}

export function adjacentWorkspace(snapshot = {}, currentId = "", direction = 1) {
  const workspaces = Array.isArray(snapshot.workspaces) ? snapshot.workspaces : [];
  if (workspaces.length < 2) return null;
  const current = Math.max(0, workspaces.findIndex(item => item?.id === currentId));
  const next = (current + (direction < 0 ? -1 : 1) + workspaces.length) % workspaces.length;
  return workspaces[next] || null;
}

export function stableItemOrder(previousIds = [], items = []) {
  const unique = new Map();
  for (const item of Array.isArray(items) ? items : []) {
    if (item && typeof item.id === "string" && !unique.has(item.id)) unique.set(item.id, item);
  }
  const ordered = [];
  for (const id of Array.isArray(previousIds) ? previousIds : []) {
    if (!unique.has(id)) continue;
    ordered.push(unique.get(id));
    unique.delete(id);
  }
  ordered.push(...unique.values());
  return ordered;
}

export function missingPortalScopes(permissions = []) {
  return PORTAL_SCOPES.filter(scope => !permissions.includes(scope));
}

export function sanitizeBeam(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const keys = Object.keys(value).sort().join(",");
  if (keys !== "actions,kind,label,provider,status") return null;
  if (value.provider !== "media.transport" || value.kind !== "typed-actions") return null;
  if (typeof value.label !== "string" || value.label.length > 32 || typeof value.status !== "string" || value.status.length > 24) return null;
  if (!Array.isArray(value.actions) || value.actions.length !== 3) return null;
  const allow = new Map([
    ["previous", "media.previous"], ["playPause", "media.playPause"], ["next", "media.next"],
  ]);
  const actions = [];
  for (const item of value.actions) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return null;
    if (Object.keys(item).sort().join(",") !== "action,enabled,id,label") return null;
    if (allow.get(item.id) !== item.action || typeof item.enabled !== "boolean"
      || typeof item.label !== "string" || !item.label || item.label.length > 16) return null;
    actions.push({id: item.id, action: item.action, enabled: item.enabled, label: item.label});
  }
  return {provider: value.provider, kind: value.kind, label: value.label, status: value.status, actions};
}
