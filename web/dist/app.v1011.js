"use strict";

import {
  activeWorkspace, adjacentWorkspace, missingPortalScopes, reconnectDelay,
  sanitizeBeam, sanitizeTheme, sanitizeThemes, snapshotTransition, stableItemOrder,
} from "/app/model.v1011.js";

const PROTOCOL = 1;
const WEB_BUILD = "sidecar-web-v1011";
const DROP_SCOPE = "write:inbox";
const DROP_POLICY = Object.freeze({
  "image/png": [".png"], "image/jpeg": [".jpg", ".jpeg"], "image/webp": [".webp"],
  "image/gif": [".gif"], "application/pdf": [".pdf"], "text/plain": [".txt"],
});
const ACTION_SCOPES = Object.freeze({
  "workspace.focus": "control:workspace",
  "window.focus": "control:window-focus",
  "window.moveToWorkspace": "control:window-move",
  "media.playPause": "control:media",
  "media.previous": "control:media",
  "media.next": "control:media",
  "theme.set": "control:theme",
  "theme.backgroundNext": "control:theme",
  "desktop.lock": "control:lock",
});
const PERMISSION_LABELS = Object.freeze({
  "read:desktop": "See workspace and app identity",
  "control:workspace": "Switch Portal workspaces",
  "control:window-focus": "Focus Portal apps",
  "control:window-move": "Move apps between workspaces",
  "control:media": "Use the media Beam",
  "control:theme": "Switch installed themes and backgrounds",
  "control:lock": "Lock this desktop",
  [DROP_SCOPE]: "Send allowed files to Sidecar Inbox",
});
const els = Object.fromEntries(Array.from(document.querySelectorAll("[id]")).map(element => [element.id, element]));
const app = els.app;

let pairingSecret = location.hash.length > 1 ? location.hash.slice(1) : "";
if (!/^[A-Za-z0-9_-]{40,64}$/.test(pairingSecret)) pairingSecret = "";
if (location.hash) history.replaceState(null, "", location.pathname);

let credential = "";
let clientInstanceId = "";
let clientInstanceBound = false;
let snapshot = null;
let connectionAbort = null;
let refreshPromise = null;
let streamGeneration = 0;
let reconnectAttempt = 0;
let fallbackTimer = null;
let currentView = "portal";
let portalWorkspaceId = "";
let pendingAction = false;
let installPrompt = null;
let toastTimer = null;
let themeReelSignature = "";
let themePreviewGeneration = 0;
let themePreviewObserver = null;
const themePreviewUrls = new Map();
const themePreviewLoading = new Set();
let morphBusy = false;
let previousThemeId = "";
let hapticsEnabled = true;
let unpairArmed = false;
let unpairTimer = null;
let holdTimer = null;
let settingsOpen = false;
let dropOpen = false;
let dropFiles = [];
let dropIntentId = "";
let activeUpload = null;
let dropBusy = false;
let activeShareKey = "";
const portalWindowOrder = new Map();

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("omarchy-sidecar", 2);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains("private")) database.createObjectStore("private");
      if (!database.objectStoreNames.contains("settings")) database.createObjectStore("settings");
      if (!database.objectStoreNames.contains("shares")) database.createObjectStore("shares");
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function databaseValue(store, key, operation, value) {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(store, operation === "read" ? "readonly" : "readwrite");
    const objectStore = transaction.objectStore(store);
    let request;
    let result;
    if (operation === "read") request = objectStore.get(key);
    else if (operation === "delete") request = objectStore.delete(key);
    else request = objectStore.put(value, key);
    request.onsuccess = () => { result = request.result; };
    request.onerror = () => reject(request.error);
    transaction.oncomplete = () => { database.close(); resolve(result); };
    transaction.onerror = () => { database.close(); reject(transaction.error); };
    transaction.onabort = () => { database.close(); reject(transaction.error || new Error("Browser storage aborted")); };
  });
}

const readCredential = () => databaseValue("private", "credential", "read");
const saveCredential = value => databaseValue("private", "credential", "write", value);
const deleteCredential = () => databaseValue("private", "credential", "delete");
const readClientInstanceId = () => databaseValue("private", "clientInstanceId", "read");
const saveClientInstanceId = value => databaseValue("private", "clientInstanceId", "write", value);
const readSetting = key => databaseValue("settings", key, "read");
const saveSetting = (key, value) => databaseValue("settings", key, "write", value);
const readShare = key => databaseValue("shares", key, "read");
const deleteShare = key => databaseValue("shares", key, "delete");

function platform() {
  const ua = navigator.userAgent.toLowerCase();
  if (/iphone|ipad|ipod/.test(ua)) return "ios-web";
  if (/android/.test(ua)) return "android-web";
  return "desktop-web";
}

function suggestedName() {
  if (platform() === "ios-web") return "My iPhone";
  if (platform() === "android-web") return "My Android phone";
  return "My phone";
}

function requestId(prefix = "req") {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "")}`;
}

function freshClientInstanceId() {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  const encoded = btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
  return `sci1_${encoded}`;
}

async function ensureClientInstanceId() {
  let value = await readClientInstanceId();
  if (/^sci1_[A-Za-z0-9_-]{43}$/.test(value || "")) return value;
  value = freshClientInstanceId();
  await saveClientInstanceId(value);
  if (await readClientInstanceId() !== value) throw new Error("Browser storage did not retain Sidecar identity");
  return value;
}

function announce(message) { els["live-status"].textContent = message; }

function haptic(pattern = 12) {
  if (hapticsEnabled && navigator.vibrate) navigator.vibrate(pattern);
}

function toast(message, tone = "normal") {
  clearTimeout(toastTimer);
  els.toast.textContent = message;
  els.toast.dataset.tone = tone;
  els.toast.hidden = false;
  announce(message);
  toastTimer = setTimeout(() => { els.toast.hidden = true; }, 2800);
}

function setConnection(state, title, banner = "") {
  app.dataset.state = state;
  els["connection-title"].textContent = title;
  els["settings-state"].textContent = title;
  els["status-banner"].hidden = !banner;
  els["status-banner"].textContent = banner;
  announce(banner || title);
  for (const view of document.querySelectorAll(".view")) view.inert = state !== "connected";
}

function showOnboarding(step) {
  els.onboarding.hidden = false;
  els["primary-nav"].hidden = true;
  els["locked-view"].hidden = true;
  for (const view of document.querySelectorAll(".view")) view.hidden = true;
  els["name-step"].hidden = step !== "name";
  els["approval-step"].hidden = step !== "approval";
  els["terminal-step"].hidden = step !== "terminal";
}

function terminalPairing(title, message) {
  showOnboarding("terminal");
  els["terminal-title"].textContent = title;
  els["terminal-message"].textContent = message;
  setConnection("offline", "Pairing stopped");
}

async function boundedFetch(path, options = {}, timeoutMs = 15000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(new DOMException("Sidecar request timed out", "TimeoutError")), timeoutMs);
  const upstream = options.signal;
  const abortFromUpstream = () => controller.abort(upstream.reason);
  if (upstream) {
    if (upstream.aborted) abortFromUpstream();
    else upstream.addEventListener("abort", abortFromUpstream, {once: true});
  }
  try {
    return await fetch(path, {...options, signal: controller.signal});
  } finally {
    clearTimeout(timeout);
    upstream?.removeEventListener("abort", abortFromUpstream);
  }
}

async function jsonRequest(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (credential) headers.set("Authorization", `Bearer ${credential}`);
  let response;
  try {
    response = await boundedFetch(path, {...options, headers, cache: "no-store"});
  } catch (cause) {
    if (cause?.name === "AbortError" && options.signal?.aborted) throw cause;
    const error = new Error("Sidecar did not answer in time. Check Tailscale and retry explicitly.");
    error.code = "service_unavailable";
    throw error;
  }
  let body = {};
  try { body = await response.json(); } catch (_) { body = {}; }
  if (!response.ok) {
    const error = new Error(body.error?.message || `Sidecar request failed (${response.status})`);
    error.code = body.error?.code || "service_unavailable";
    error.status = response.status;
    error.details = body.error?.details || {};
    throw error;
  }
  return body;
}

async function requestPairing(event) {
  event.preventDefault();
  const name = els["device-name"].value.trim();
  if (!name || name.length > 64 || !pairingSecret) return;
  const button = els["pair-form"].querySelector("button");
  button.disabled = true;
  try {
    const pending = await jsonRequest("/api/v1/pair/request", {
      method: "POST",
      body: JSON.stringify({
        secret: pairingSecret,
        device: {
          name, platform: platform(), clientVersion: "0.2.1", protocol: PROTOCOL,
          clientInstanceId,
        },
      }),
    });
    pairingSecret = "";
    els["verification-phrase"].textContent = pending.verificationPhrase.join(" · ");
    showOnboarding("approval");
    setConnection("starting", "Awaiting approval");
    await pollPairing(pending);
  } catch (error) {
    terminalPairing(error.code === "pairing_expired" ? "That code expired" : "Could not ask to pair",
      error.message || "Open Sidecar on the desktop and try a fresh code.");
  } finally {
    button.disabled = false;
  }
}

async function pollPairing(pending) {
  while (true) {
    await new Promise(resolve => setTimeout(resolve, Math.max(750, pending.pollAfterMs || 1000)));
    try {
      const result = await jsonRequest("/api/v1/pair/status", {
        method: "POST",
        body: JSON.stringify({requestId: pending.requestId, pendingCapability: pending.pendingCapability}),
      });
      if (result.status === "pending") { pending.pollAfterMs = result.pollAfterMs; continue; }
      if (result.status === "approved") {
        credential = result.credential;
        try {
          await saveCredential(credential);
        } catch (_) {
          try { await jsonRequest("/api/v1/session/unpair", {method: "POST", body: JSON.stringify({requestId: requestId()})}); } catch (_) {}
          credential = "";
          throw Object.assign(new Error("This browser could not keep the private Sidecar credential. Free browser storage, revoke this phone if it remains listed, and pair again."), {code: "storage_unavailable"});
        }
        haptic([10, 45, 18]);
        app.classList.add("pairing-arrival");
        setTimeout(() => app.classList.remove("pairing-arrival"), 1500);
        await beginSession();
        await consumeIncomingShare();
        return;
      }
    } catch (error) {
      terminalPairing(error.code === "pairing_denied" ? "Pairing was denied" : error.code === "storage_unavailable" ? "Private storage is unavailable" : "Pairing expired",
        error.message || "Ask the desktop for a new code.");
      return;
    }
  }
}

function applyTheme(theme = {}) {
  const root = document.documentElement;
  const safe = sanitizeTheme(theme);
  for (const [key, value] of Object.entries(safe.colors)) root.style.setProperty(`--${key}`, value);
  root.style.setProperty("--radius", `${safe.radius}px`);
  root.style.setProperty("--accent-ink", safe.accentInk);
  root.style.colorScheme = safe.scheme;
  root.dataset.scheme = safe.scheme;
  document.querySelector('meta[name="theme-color"]').content = safe.colors.background;
}

function clearElement(element) { while (element.firstChild) element.firstChild.remove(); }

function make(tag, options = {}, children = []) {
  const element = document.createElement(tag);
  if (options.className) element.className = options.className;
  if (options.text !== undefined) element.textContent = String(options.text);
  if (options.type) element.type = options.type;
  if (options.label) element.setAttribute("aria-label", options.label);
  if (options.disabled) element.disabled = true;
  for (const child of children) if (child) element.append(child);
  return element;
}

function permitted(action) {
  const required = ACTION_SCOPES[action];
  return !required || Boolean(snapshot?.session?.permissions?.includes(required));
}

async function sendAction(action, parameters = {}) {
  if (pendingAction || !snapshot || snapshot.desktop?.locked || snapshot.session?.paused) {
    throw new Error("Sidecar is not ready for that action.");
  }
  if (!permitted(action)) {
    const error = new Error("Approve this ability from the desktop first.");
    error.code = "permission_denied";
    throw error;
  }
  pendingAction = true;
  try {
    const result = await jsonRequest("/api/v1/actions", {
      method: "POST",
      body: JSON.stringify({requestId: requestId(), action, parameters, expectedSeq: snapshot.seq}),
    });
    await refreshSnapshot();
    return result;
  } catch (error) {
    await refreshSnapshot().catch(() => {});
    throw error;
  } finally {
    pendingAction = false;
  }
}

async function runAction(control, action, parameters = {}, success = "Desktop confirmed") {
  control?.classList.add("pending");
  if (control) control.disabled = true;
  try {
    const result = await sendAction(action, parameters);
    haptic(12);
    toast(success);
    return result;
  } catch (error) {
    toast(error.message || "The desktop did not complete that action.", "error");
    return null;
  } finally {
    control?.classList.remove("pending");
    if (control?.isConnected) control.disabled = !permitted(action);
  }
}

function monogram(label) {
  const words = String(label || "App").trim().split(/\s+/).filter(Boolean);
  return words.slice(0, 2).map(word => word[0]?.toUpperCase() || "").join("").slice(0, 2) || "A";
}

function renderWorkspaceRail(workspaces, selected) {
  clearElement(els["workspace-rail"]);
  for (const workspace of workspaces) {
    const button = make("button", {text: workspace.label, label: `Open workspace ${workspace.label}`});
    button.dataset.workspaceId = workspace.id;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(workspace.id === selected.id));
    button.tabIndex = workspace.id === selected.id ? 0 : -1;
    const activate = async () => {
      await runAction(button, "workspace.focus", {workspaceId: workspace.id}, `Workspace ${workspace.label} is here`);
      renderPortal();
    };
    button.addEventListener("click", activate);
    button.addEventListener("keydown", event => {
      const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
      if (!keys.includes(event.key)) return;
      event.preventDefault();
      const current = workspaces.findIndex(item => item.id === workspace.id);
      const index = event.key === "Home" ? 0 : event.key === "End" ? workspaces.length - 1
        : (current + (event.key === "ArrowLeft" ? -1 : 1) + workspaces.length) % workspaces.length;
      const next = els["workspace-rail"].querySelector(`[data-workspace-id="${CSS.escape(workspaces[index].id)}"]`);
      next?.focus();
      next?.click();
    });
    els["workspace-rail"].append(button);
  }
}

function renderDropZones(workspaces, sourceWorkspaceId) {
  clearElement(els["drop-zones"]);
  if (permitted("window.moveToWorkspace")) {
    for (const workspace of workspaces) {
      if (workspace.id === sourceWorkspaceId) continue;
      const zone = make("div", {className: "drop-zone", text: workspace.label});
      zone.dataset.workspaceId = workspace.id;
      zone.setAttribute("aria-label", `Move to workspace ${workspace.label}`);
      els["drop-zones"].append(zone);
    }
  }
}

function attachTileGestures(tile, windowState, workspace) {
  let startX = 0;
  let startY = 0;
  let pointerId = null;
  let timer = null;
  let dragging = false;
  let targetZone = null;
  let moved = false;
  let holdRejected = false;

  function resetDrag() {
    clearTimeout(timer);
    tile.style.transform = "";
    tile.classList.remove("dragging", "pressing");
    targetZone?.classList.remove("target");
    targetZone = null;
    els["drop-zones"].hidden = true;
    document.body.classList.remove("portal-dragging");
  }

  async function finishDrag() {
    const destination = targetZone?.dataset.workspaceId || "";
    resetDrag();
    if (!destination) { tile.classList.add("snapback"); setTimeout(() => tile.classList.remove("snapback"), 360); return; }
    tile.classList.add("settling");
    try {
      if (destination === workspace.id) throw new Error("The app stayed in its current workspace.");
      await sendAction("window.moveToWorkspace", {windowId: windowState.id, workspaceId: destination});
      const confirmed = snapshot?.windows?.some(item => item.id === windowState.id && item.workspaceId === destination);
      if (!confirmed) throw new Error("The desktop did not confirm the new workspace.");
      haptic([8, 28, 12]);
      const label = snapshot.workspaces?.find(item => item.id === destination)?.label || "destination";
      toast(`${windowState.app} moved to workspace ${label}`);
    } catch (error) {
      tile.classList.add("snapback");
      toast(error.message || "The app stayed where it was.", "error");
      setTimeout(() => tile.classList.remove("snapback"), 420);
    } finally {
      tile.classList.remove("settling");
      renderPortal();
    }
  }

  tile.addEventListener("pointerdown", event => {
    if (event.button !== 0 || pendingAction) return;
    pointerId = event.pointerId;
    startX = event.clientX;
    startY = event.clientY;
    moved = false;
    holdRejected = false;
    tile.setPointerCapture(pointerId);
    tile.classList.add("pressing");
    timer = setTimeout(() => {
      if (!permitted("window.moveToWorkspace")) {
        holdRejected = true;
        tile.classList.remove("pressing");
        openSettings();
        toast("Ask the desktop to approve app moving first.");
        return;
      }
      dragging = true;
      tile.classList.remove("pressing");
      tile.classList.add("dragging");
      renderDropZones(snapshot.workspaces || [], workspace.id);
      els["drop-zones"].hidden = false;
      document.body.classList.add("portal-dragging");
      haptic(8);
    }, 420);
  });
  tile.addEventListener("pointermove", event => {
    if (event.pointerId !== pointerId) return;
    const dx = event.clientX - startX;
    const dy = event.clientY - startY;
    if (!dragging && Math.hypot(dx, dy) > 12) {
      moved = true;
      clearTimeout(timer);
      tile.classList.remove("pressing");
    }
    if (!dragging) return;
    tile.style.transform = `translate3d(${dx}px,${dy}px,0) scale(1.06)`;
    const found = document.elementFromPoint(event.clientX, event.clientY)?.closest?.(".drop-zone") || null;
    if (found !== targetZone) {
      targetZone?.classList.remove("target");
      targetZone = found;
      targetZone?.classList.add("target");
    }
  });
  tile.addEventListener("pointerup", async event => {
    if (event.pointerId !== pointerId) return;
    clearTimeout(timer);
    tile.classList.remove("pressing");
    if (dragging) await finishDrag();
    else if (!moved && !holdRejected && Math.hypot(event.clientX - startX, event.clientY - startY) < 12) {
      await runAction(tile, "window.focus", {windowId: windowState.id}, `${windowState.app} is focused`);
    }
    dragging = false;
    pointerId = null;
  });
  tile.addEventListener("pointercancel", () => { dragging = false; pointerId = null; resetDrag(); });
  tile.addEventListener("keydown", event => {
    if (!event.repeat && ["Enter", " ", "Spacebar"].includes(event.key)) {
      event.preventDefault();
      runAction(tile, "window.focus", {windowId: windowState.id}, `${windowState.app} is focused`);
    }
  });
}

function renderPortal() {
  if (!snapshot || snapshot.desktop?.locked) return;
  const workspaces = Array.isArray(snapshot.workspaces) ? snapshot.workspaces : [];
  const workspaceIds = new Set(workspaces.map(item => item.id));
  for (const id of portalWindowOrder.keys()) if (!workspaceIds.has(id)) portalWindowOrder.delete(id);
  const workspace = activeWorkspace(snapshot, portalWorkspaceId);
  if (!workspace) {
    els["portal-count"].textContent = "Unavailable";
    els["portal-empty"].hidden = false;
    clearElement(els["app-grid"]);
    return;
  }
  portalWorkspaceId = workspace.id;
  renderWorkspaceRail(workspaces, workspace);
  els["portal-workspace-label"].textContent = `Workspace ${workspace.label}`;
  els["portal-count"].textContent = `${workspaces.length} ${workspaces.length === 1 ? "space" : "spaces"}`;
  clearElement(els["app-grid"]);
  const candidates = (snapshot.windows || []).filter(item => item.workspaceId === workspace.id);
  const windows = stableItemOrder(portalWindowOrder.get(workspace.id) || [], candidates);
  portalWindowOrder.set(workspace.id, windows.map(item => item.id));
  els["portal-empty"].hidden = windows.length > 0;
  for (const windowState of windows) {
    const badge = make("span", {className: "app-badge", text: monogram(windowState.app)});
    const copy = make("span", {className: "app-copy"}, [
      make("strong", {text: windowState.app}),
      make("small", {text: windowState.focused ? "Here now" : "Tap to focus"}),
    ]);
    const tile = make("button", {
      className: `app-tile${windowState.focused ? " focused" : ""}`,
      label: `${windowState.app}. ${windowState.focused ? "Focused" : "Activate to focus"}. Hold and drag to move.`,
    }, [badge, copy]);
    tile.dataset.windowId = windowState.id;
    attachTileGestures(tile, windowState, workspace);
    els["app-grid"].append(tile);
  }
  renderBeam();
}

function renderBeam() {
  const beam = sanitizeBeam(snapshot?.beam);
  clearElement(els.beam);
  if (!beam) { els.beam.hidden = true; return; }
  const heading = make("div", {className: "beam-heading"}, [
    make("div", {}, [make("p", {className: "card-kicker", text: "Beam"}), make("strong", {text: beam.label})]),
    make("span", {className: "quiet-pill", text: beam.status}),
  ]);
  const controls = make("div", {className: "beam-controls"});
  const icons = {previous: "‹", playPause: snapshot.media?.playing ? "Ⅱ" : "▶", next: "›"};
  for (const item of beam.actions) {
    const button = make("button", {disabled: !item.enabled || !permitted(item.action), label: item.label}, [
      make("span", {className: "beam-icon", text: icons[item.id]}), make("small", {text: item.label}),
    ]);
    button.addEventListener("click", () => runAction(button, item.action, {}, `${item.label} confirmed`));
    controls.append(button);
  }
  els.beam.append(heading, controls);
  els.beam.hidden = false;
}

function portalSwipeStart(event) {
  if (event.target.closest(".app-tile,button") || !snapshot) return;
  const startX = event.clientX;
  const startY = event.clientY;
  const pointerId = event.pointerId;
  els["portal-stage"].setPointerCapture(pointerId);
  const finish = async end => {
    if (end.pointerId !== pointerId) return;
    els["portal-stage"].removeEventListener("pointerup", finish);
    els["portal-stage"].removeEventListener("pointercancel", cancel);
    const dx = end.clientX - startX;
    const dy = end.clientY - startY;
    if (Math.abs(dx) < 54 || Math.abs(dx) <= Math.abs(dy)) return;
    const target = adjacentWorkspace(snapshot, portalWorkspaceId, dx < 0 ? 1 : -1);
    if (!target) return;
    els["portal-stage"].classList.add(dx < 0 ? "swipe-left" : "swipe-right");
    await runAction(els["portal-stage"], "workspace.focus", {workspaceId: target.id}, `Workspace ${target.label} is here`);
    renderPortal();
    setTimeout(() => els["portal-stage"].classList.remove("swipe-left", "swipe-right"), 300);
  };
  const cancel = () => {
    els["portal-stage"].removeEventListener("pointerup", finish);
    els["portal-stage"].removeEventListener("pointercancel", cancel);
  };
  els["portal-stage"].addEventListener("pointerup", finish);
  els["portal-stage"].addEventListener("pointercancel", cancel);
}

function themeCard(theme, currentId) {
  const mood = make("span", {className: "theme-mood"});
  mood.style.setProperty("--preview-accent", theme.previewAccent || "#8b5cf6");
  if (theme.previewAvailable) {
    const image = make("img", {className: "theme-preview"});
    image.alt = "";
    image.decoding = "async";
    image.setAttribute("aria-hidden", "true");
    mood.append(image);
    observeThemePreview(theme, mood, image);
  }
  mood.append(make("span"), make("span"), make("span"));
  const card = make("button", {className: "theme-card", label: `${theme.name}${theme.id === currentId ? ", current theme" : ""}`}, [
    mood, make("strong", {text: theme.name}), make("small", {text: theme.id === currentId ? "On your desktop" : "Tap to apply"}),
  ]);
  card.dataset.themeId = theme.id;
  card.setAttribute("aria-current", theme.id === currentId ? "true" : "false");
  card.addEventListener("click", () => {
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    card.scrollIntoView({behavior: reducedMotion ? "auto" : "smooth", inline: "center", block: "nearest"});
    setTimeout(() => commitMorph(theme.id), reducedMotion ? 0 : 280);
  });
  return card;
}

function clearMorphHeroPreview() {
  const image = els["morph-hero-preview"];
  image.closest(".morph-hero")?.classList.remove("preview-ready");
  image.removeAttribute("src");
  image.dataset.themeId = "";
}

function showMorphHeroPreview(theme, url) {
  if (!theme || !url || theme.id !== snapshot?.themes?.currentId) return;
  const image = els["morph-hero-preview"];
  image.dataset.themeId = theme.id;
  image.addEventListener("load", () => {
    if (image.dataset.themeId === snapshot?.themes?.currentId) image.closest(".morph-hero")?.classList.add("preview-ready");
  }, {once: true});
  image.src = url;
}

function releaseThemePreviews(invalidateReel = false) {
  themePreviewGeneration += 1;
  if (invalidateReel) themeReelSignature = "";
  if (themePreviewObserver) themePreviewObserver.disconnect();
  themePreviewObserver = null;
  for (const url of themePreviewUrls.values()) URL.revokeObjectURL(url);
  themePreviewUrls.clear();
  themePreviewLoading.clear();
  clearMorphHeroPreview();
}

async function loadThemePreview(theme, mood, image, generation) {
  if (generation !== themePreviewGeneration || !credential || !theme.previewAvailable
    || !snapshot?.session?.permissions?.includes("control:theme")
    || snapshot?.desktop?.locked || snapshot?.session?.paused) return;
  const existing = themePreviewUrls.get(theme.id);
  if (existing) {
    image.src = existing;
    mood.classList.add("preview-ready");
    showMorphHeroPreview(theme, existing);
    return;
  }
  if (themePreviewLoading.has(theme.id)) return;
  themePreviewLoading.add(theme.id);
  mood.classList.add("preview-loading");
  try {
    const response = await boundedFetch(`/api/v1/theme-previews/${encodeURIComponent(theme.id)}`, {
      headers: {Authorization: `Bearer ${credential}`},
      credentials: "omit",
      cache: "no-store",
    });
    const contentType = (response.headers.get("Content-Type") || "").split(";", 1)[0].toLowerCase();
    if (!response.ok || !["image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp"].includes(contentType)) return;
    const blob = await response.blob();
    if (blob.size < 1 || blob.size > 2 * 1024 * 1024 || generation !== themePreviewGeneration || !image.isConnected) return;
    const url = URL.createObjectURL(blob);
    if (generation !== themePreviewGeneration || !image.isConnected) {
      URL.revokeObjectURL(url);
      return;
    }
    themePreviewUrls.set(theme.id, url);
    image.addEventListener("load", () => mood.classList.add("preview-ready"), {once: true});
    image.src = url;
    showMorphHeroPreview(theme, url);
  } catch (_) {
    // A missing or newly removed preview keeps the safe palette fallback.
  } finally {
    themePreviewLoading.delete(theme.id);
    mood.classList.remove("preview-loading");
  }
}

function observeThemePreview(theme, mood, image) {
  const generation = themePreviewGeneration;
  if (theme.id === snapshot?.themes?.currentId) {
    loadThemePreview(theme, mood, image, generation);
    return;
  }
  if (!("IntersectionObserver" in window)) {
    if (theme.id === snapshot?.themes?.currentId) loadThemePreview(theme, mood, image, generation);
    return;
  }
  if (!themePreviewObserver) {
    themePreviewObserver = new IntersectionObserver((entries, observer) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        observer.unobserve(entry.target);
        const details = entry.target.__sidecarThemePreview;
        if (details) loadThemePreview(details.theme, entry.target, details.image, details.generation);
      }
    }, {root: els["theme-reel"], rootMargin: "80px"});
  }
  mood.__sidecarThemePreview = {theme, image, generation};
  themePreviewObserver.observe(mood);
}

function centerCurrentTheme() {
  const current = els["theme-reel"].querySelector('[aria-current="true"]');
  if (!current) return;
  current.scrollIntoView({behavior: "auto", inline: "center", block: "nearest"});
}

function renderMorph() {
  const themes = sanitizeThemes(snapshot?.themes) || {items: [], currentId: "", currentName: ""};
  els["current-theme-name"].textContent = themes.currentName || "Omarchy";
  const currentTheme = themes.items.find(item => item.id === themes.currentId) || null;
  const heroUrl = currentTheme ? themePreviewUrls.get(currentTheme.id) : "";
  if (currentTheme && heroUrl) showMorphHeroPreview(currentTheme, heroUrl);
  else clearMorphHeroPreview();
  const previewAllowed = Boolean(snapshot?.session?.permissions?.includes("read:desktop")
    && snapshot?.session?.permissions?.includes("control:theme")
    && !snapshot?.desktop?.locked && !snapshot?.session?.paused);
  const signature = JSON.stringify([previewAllowed, (themes.items || []).map(item => [item.id, item.name, item.previewAccent, item.previewAvailable])]);
  if (signature !== themeReelSignature) {
    themeReelSignature = signature;
    releaseThemePreviews(false);
    clearElement(els["theme-reel"]);
    for (const theme of themes.items || []) els["theme-reel"].append(themeCard(theme, themes.currentId));
    requestAnimationFrame(centerCurrentTheme);
  } else {
    for (const card of els["theme-reel"].querySelectorAll(".theme-card")) {
      const current = card.dataset.themeId === themes.currentId;
      card.setAttribute("aria-current", String(current));
      const theme = themes.items.find(item => item.id === card.dataset.themeId);
      card.setAttribute("aria-label", `${theme?.name || "Theme"}${current ? ", current theme" : ""}`);
      const small = card.querySelector("small");
      if (small) small.textContent = current ? "On your desktop" : "Tap to apply";
      if (current && !heroUrl) {
        const mood = card.querySelector(".theme-mood");
        const image = card.querySelector(".theme-preview");
        if (mood && image && currentTheme) loadThemePreview(currentTheme, mood, image, themePreviewGeneration);
      }
    }
  }
  els["theme-undo"].disabled = !previousThemeId || !permitted("theme.set") || morphBusy;
  els["background-next"].disabled = !permitted("theme.backgroundNext") || morphBusy;
}

async function commitMorph(themeId) {
  if (morphBusy || !themeId || themeId === snapshot?.themes?.currentId) return;
  if (!permitted("theme.set")) { openSettings(); toast("Ask the desktop to approve Morph first."); return; }
  const prior = snapshot.themes.currentId;
  morphBusy = true;
  els["morph-status"].textContent = "Morphing…";
  renderMorph();
  try {
    await sendAction("theme.set", {themeId});
    previousThemeId = prior;
    applyTheme(snapshot.theme || {});
    haptic([10, 25, 12]);
    toast(`${snapshot.themes?.currentName || "Theme"} is live`);
    els["morph-status"].textContent = "In sync";
    requestAnimationFrame(centerCurrentTheme);
  } catch (error) {
    els["morph-status"].textContent = "Stayed put";
    toast(error.message || "The desktop kept its current theme.", "error");
  } finally {
    morphBusy = false;
    renderMorph();
  }
}

function renderSettings() {
  const permissions = snapshot?.session?.permissions || [];
  clearElement(els["permission-list"]);
  for (const permission of permissions) {
    if (PERMISSION_LABELS[permission]) els["permission-list"].append(make("li", {text: PERMISSION_LABELS[permission]}));
  }
  els["capability-card"].hidden = !snapshot || missingPortalScopes(permissions).length === 0;
  els["hold-lock"].disabled = !permissions.includes("control:lock") || snapshot?.desktop?.locked;
  els["haptics-toggle"].checked = hapticsEnabled;
}

function render() {
  if (!snapshot) return;
  els.onboarding.hidden = true;
  applyTheme(snapshot.theme || {});
  if (snapshot.desktop?.locked) {
    releaseThemePreviews(true);
    closeSettings();
    els["locked-view"].hidden = false;
    for (const view of document.querySelectorAll(".view")) view.hidden = true;
    els["primary-nav"].hidden = true;
    setConnection("locked", "Desktop locked");
    return;
  }
  els["locked-view"].hidden = true;
  els["primary-nav"].hidden = false;
  if (snapshot.session?.paused) setConnection("paused", "Sidecar paused", "Resume phone connections from the desktop panel.");
  else setConnection("connected", "Desktop · Connected");
  renderPortal();
  renderMorph();
  renderSettings();
  navigate(currentView, false);
}

function navigate(view, announceView = true) {
  currentView = view === "morph" ? "morph" : "portal";
  app.dataset.view = currentView;
  for (const element of document.querySelectorAll(".view")) element.hidden = element.dataset.view !== currentView;
  for (const button of document.querySelectorAll("[data-nav]")) {
    if (button.dataset.nav === currentView) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  if (currentView === "morph") requestAnimationFrame(centerCurrentTheme);
  if (announceView) announce(currentView === "portal" ? "Portal" : "Morph");
}

async function refreshSnapshot() {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    await bindClientInstance();
    const next = await jsonRequest("/api/v1/snapshot", {method: "GET"});
    if (next.protocol !== PROTOCOL || next.server?.webBuildId !== WEB_BUILD) {
      throw Object.assign(new Error("The phone and desktop Sidecar builds do not match. Update the older component."), {code: "protocol_mismatch"});
    }
    snapshot = next;
    render();
  })();
  try {
    return await refreshPromise;
  } finally {
    refreshPromise = null;
  }
}

async function bindClientInstance() {
  if (clientInstanceBound) return;
  try {
    await jsonRequest("/api/v1/session/identify", {
      method: "POST",
      body: JSON.stringify({clientInstanceId}),
    });
  } catch (error) {
    if (error.code === "bad_request") {
      error.code = "client_identity_changed";
      error.message = "This browser no longer matches its saved Sidecar credential.";
    }
    throw error;
  }
  clientInstanceBound = true;
}

function applyEvent(event, data) {
  if (event === "snapshot") {
    const transition = snapshotTransition(snapshot, data);
    if (transition.refresh) { refreshSnapshot().catch(connectionFailed); return; }
    if (!transition.accept) return;
    snapshot = data;
    render();
  } else if (event === "session.revoked") revoked();
  else if (event === "permissions.changed") refreshSnapshot().catch(connectionFailed);
}

async function streamEvents(generation = ++streamGeneration) {
  if (connectionAbort) connectionAbort.abort();
  connectionAbort = new AbortController();
  const controller = connectionAbort;
  const headers = {Authorization: `Bearer ${credential}`};
  if (snapshot?.seq) headers["Last-Event-ID"] = String(snapshot.seq);
  const response = await fetch("/api/v1/events", {headers, cache: "no-store", signal: controller.signal});
  if (response.status === 401) return revoked();
  if (!response.ok || !response.body) throw new Error("Event stream unavailable");
  reconnectAttempt = 0;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    if (generation !== streamGeneration) return;
    const part = await reader.read();
    if (part.done) throw new Error("Event stream closed");
    buffer += decoder.decode(part.value, {stream: true});
    if (buffer.length > 256 * 1024) throw new Error("Event stream framing overflow");
    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      if (!frame || frame.startsWith(":")) continue;
      let event = "message";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) applyEvent(event, JSON.parse(data));
    }
  }
}

async function restartEventStream() {
  const generation = ++streamGeneration;
  if (connectionAbort) connectionAbort.abort();
  await refreshSnapshot();
  if (generation !== streamGeneration || !credential) return;
  return streamEvents(generation);
}

function connectionFailed(error) {
  if (error?.name === "AbortError") return;
  setConnection("offline", "Reconnecting", "Desktop unreachable. Check Tailscale and that Sidecar is running.");
  scheduleReconnect();
}

function scheduleReconnect() {
  clearTimeout(fallbackTimer);
  reconnectAttempt = Math.min(8, reconnectAttempt + 1);
  fallbackTimer = setTimeout(async () => {
    try { restartEventStream().catch(connectionFailed); }
    catch (error) { if (error.status === 401) revoked(); else scheduleReconnect(); }
  }, reconnectDelay(reconnectAttempt, Math.random()));
}

async function beginSession() {
  showOnboarding("terminal");
  setConnection("starting", "Opening your Portal");
  try { restartEventStream().catch(connectionFailed); maybeShowInstallTip().catch(() => {}); }
  catch (error) {
    if (error.status === 401) revoked();
    else if (error.code === "client_identity_changed") {
      credential = "";
      await deleteCredential().catch(() => {});
      terminalPairing("This browser identity changed", "Open Sidecar on your desktop and scan a fresh QR code. The old pairing remains safe until you remove it locally.");
    } else connectionFailed(error);
  }
}

function openSettings() {
  renderSettings();
  settingsOpen = true;
  els["settings-sheet"].hidden = false;
  els["sheet-backdrop"].hidden = false;
  els["connection-orb"].setAttribute("aria-expanded", "true");
  requestAnimationFrame(() => {
    els["settings-sheet"].classList.add("open");
    els["settings-close"].focus();
  });
}

function closeSettings() {
  if (!settingsOpen) return;
  settingsOpen = false;
  els["settings-sheet"].classList.remove("open");
  els["connection-orb"].setAttribute("aria-expanded", "false");
  setTimeout(() => {
    if (!els["settings-sheet"].classList.contains("open")) {
      els["settings-sheet"].hidden = true;
      els["sheet-backdrop"].hidden = true;
      els["connection-orb"].focus({preventScroll: true});
    }
  }, 220);
}

async function requestCapabilities() {
  const scopes = missingPortalScopes(snapshot?.session?.permissions || []);
  if (!scopes.length) return;
  const button = els["request-capabilities"];
  button.disabled = true;
  button.textContent = "Waiting on desktop…";
  try {
    let pending = await jsonRequest("/api/v1/capabilities/request", {
      method: "POST", body: JSON.stringify({requestId: requestId("cap"), scopes}),
    });
    toast("Approval is waiting on your desktop");
    while (pending.status === "pending") {
      await new Promise(resolve => setTimeout(resolve, Math.max(900, pending.pollAfterMs || 1200)));
      pending = await jsonRequest("/api/v1/capabilities/status", {
        method: "POST", body: JSON.stringify({requestId: pending.requestId}),
      });
    }
    await refreshSnapshot();
    haptic([10, 30, 14]);
    toast("The full Portal is unlocked");
  } catch (error) {
    toast(error.message || "The capability request stopped.", "error");
  } finally {
    button.disabled = false;
    button.textContent = "Ask desktop";
    renderSettings();
  }
}

function startHoldLock(event) {
  if (event.type === "keydown" && event.key !== " " && event.key !== "Spacebar") return;
  if (els["hold-lock"].disabled || holdTimer) return;
  event.preventDefault();
  els["hold-lock"].classList.add("holding");
  holdTimer = setTimeout(async () => {
    holdTimer = null;
    els["hold-lock"].classList.remove("holding");
    closeSettings();
    const result = await runAction(els["hold-lock"], "desktop.lock", {}, "Desktop locked");
    if (result) haptic([18, 45, 18]);
  }, 1000);
}

function cancelHoldLock(event) {
  if (event.type === "keyup" && event.key !== " " && event.key !== "Spacebar") return;
  clearTimeout(holdTimer);
  holdTimer = null;
  els["hold-lock"].classList.remove("holding");
}

async function unpairPhone() {
  if (!unpairArmed) {
    unpairArmed = true;
    els["unpair-phone"].textContent = "Tap again to unpair";
    clearTimeout(unpairTimer);
    unpairTimer = setTimeout(() => {
      unpairArmed = false;
      els["unpair-phone"].textContent = "Unpair this phone";
    }, 5000);
    return;
  }
  try {
    await jsonRequest("/api/v1/session/unpair", {method: "POST", body: JSON.stringify({requestId: requestId()})});
    await revoked();
  } catch (error) {
    if (error.status === 401) { await revoked(); return; }
    unpairArmed = false;
    els["unpair-phone"].textContent = "Unpair this phone";
    toast("Could not reach the desktop. This phone is still paired; try again when Sidecar reconnects.", "error");
  }
}

async function revoked() {
  if (connectionAbort) connectionAbort.abort();
  releaseThemePreviews(true);
  credential = "";
  snapshot = null;
  try { await deleteCredential(); } catch (_) {}
  closeSettings();
  terminalPairing("This phone was unpaired", "Pair it again from the Sidecar panel on your desktop.");
  app.dataset.state = "revoked";
  await discardSharedSelection();
}

function formatBytes(value) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

function dropFileAllowed(file) {
  if (!(file instanceof File) || file.size < 1 || file.size > 25 * 1024 * 1024 || file.name.length > 480) return false;
  const extensions = DROP_POLICY[file.type];
  const lower = file.name.normalize("NFKC").toLowerCase();
  return Boolean(extensions?.some(extension => lower.endsWith(extension)));
}

function setDropStatus(message, tone = "normal") {
  els["drop-status"].textContent = message;
  els["drop-status"].dataset.tone = tone;
  announce(message);
}

function renderDrop() {
  clearElement(els["drop-file-list"]);
  const total = dropFiles.reduce((sum, item) => sum + item.file.size, 0);
  els["drop-selection"].hidden = dropFiles.length === 0;
  els["drop-actions"].hidden = dropFiles.length === 0;
  els["drop-total"].textContent = `${dropFiles.length}/${5} · ${formatBytes(total)}`;
  for (const item of dropFiles) {
    const copy = make("div", {className: "drop-file-copy"}, [
      make("strong", {text: item.file.name}),
      make("small", {text: `${item.file.type} · ${formatBytes(item.file.size)}${item.state ? ` · ${item.state}` : ""}`}),
    ]);
    const remove = make("button", {text: "Remove", label: `Remove ${item.file.name}`});
    remove.disabled = dropBusy;
    remove.addEventListener("click", () => {
      dropFiles = dropFiles.filter(candidate => candidate !== item);
      renderDrop();
      if (!dropFiles.length) setDropStatus("Choose up to 5 files.");
    });
    const row = make("li", {className: "drop-file"}, [copy, remove]);
    if (item.state === "Saved" || item.state === "Failed") row.dataset.state = item.state.toLowerCase();
    els["drop-file-list"].append(row);
  }
  const retryable = dropFiles.filter(item => item.state !== "Saved");
  els["drop-send"].disabled = dropBusy || !retryable.length;
  els["drop-send"].textContent = retryable.some(item => item.state === "Failed") ? "Retry failed files" : "Send to desktop";
  els["drop-choose"].disabled = dropBusy;
  els["drop-cancel"].textContent = dropBusy ? "Stop transfer" : "Cancel";
}

function acceptDropFiles(files, source = "picker") {
  const chosen = Array.from(files || []);
  const total = chosen.reduce((sum, file) => sum + file.size, 0);
  if (!chosen.length || chosen.length > 5 || total > 50 * 1024 * 1024 || chosen.some(file => !dropFileAllowed(file))) {
    setDropStatus("Choose 1–5 PNG, JPEG, WebP, GIF, PDF, or UTF-8 .txt files; 25 MiB each and 50 MiB total.", "error");
    if (source === "share") discardSharedSelection();
    return false;
  }
  dropFiles = chosen.map(file => ({file, state: ""}));
  renderDrop();
  setDropStatus("Review the filenames and sizes, then send deliberately.");
  return true;
}

function openDrop() {
  closeSettings();
  dropOpen = true;
  els["drop-sheet"].hidden = false;
  els["sheet-backdrop"].hidden = false;
  requestAnimationFrame(() => {
    els["drop-sheet"].classList.add("open");
    els["drop-open"].setAttribute("aria-expanded", "true");
    els["drop-sheet"].focus();
  });
  if (!dropFiles.length) setDropStatus(snapshot?.desktop?.locked
    ? "Unlock the desktop, then choose files and send again."
    : snapshot?.session?.paused ? "Resume Sidecar on the desktop before sending."
      : navigator.onLine ? "Choose up to 5 files." : "You are offline. Reconnect, then retry explicitly.");
}

function closeDrop() {
  if (dropBusy) return;
  dropOpen = false;
  els["drop-sheet"].classList.remove("open");
  els["drop-open"].setAttribute("aria-expanded", "false");
  els["sheet-backdrop"].hidden = true;
  setTimeout(() => { if (!dropOpen) els["drop-sheet"].hidden = true; }, 210);
  els["drop-open"].focus();
}

async function discardSharedSelection() {
  if (!activeShareKey) return;
  const key = activeShareKey;
  activeShareKey = "";
  await deleteShare(key).catch(() => {});
}

async function digestFile(file) {
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, "0")).join("");
}

async function ensureDropScope() {
  if (snapshot?.session?.permissions?.includes(DROP_SCOPE)) return;
  setDropStatus("Approval needed: ask the unlocked desktop once for Drop access.");
  let pending = await jsonRequest("/api/v1/capabilities/request", {
    method: "POST", body: JSON.stringify({requestId: requestId("dropcap"), scopes: [DROP_SCOPE]}),
  });
  setDropStatus("Waiting for the unlocked desktop to approve “Send files to Sidecar Inbox”.");
  while (pending.status === "pending") {
    await new Promise(resolve => setTimeout(resolve, Math.max(900, pending.pollAfterMs || 1200)));
    pending = await jsonRequest("/api/v1/capabilities/status", {
      method: "POST", body: JSON.stringify({requestId: pending.requestId}),
    });
  }
  await refreshSnapshot();
  if (!snapshot?.session?.permissions?.includes(DROP_SCOPE)) throw new Error("Drop was not approved. Ask again when the desktop is unlocked.");
}

function uploadRaw(upload, file, completedBytes, totalBytes) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    activeUpload = request;
    request.open("POST", `/api/v1/inbox/uploads/${encodeURIComponent(upload.uploadId)}`);
    request.timeout = 120000;
    request.setRequestHeader("Authorization", `Bearer ${credential}`);
    request.setRequestHeader("Content-Type", file.type);
    request.upload.onprogress = event => {
      if (!event.lengthComputable) return;
      const percent = Math.min(100, Math.round(((completedBytes + event.loaded) / totalBytes) * 100));
      els["drop-progress"].setAttribute("aria-valuenow", String(percent));
      els["drop-progress"].querySelector("span").style.width = `${percent}%`;
      setDropStatus(`Sending ${formatBytes(completedBytes + event.loaded)} of ${formatBytes(totalBytes)}…`);
    };
    request.onerror = () => reject(new Error("The connection stopped. Reconnect, then press Send to desktop to retry explicitly."));
    request.ontimeout = () => reject(new Error("The transfer timed out. Reconnect, then press Send to desktop to retry explicitly."));
    request.onabort = () => reject(new Error("Transfer cancelled. Nothing incomplete was saved."));
    request.onload = () => {
      let body = {};
      try { body = JSON.parse(request.responseText || "{}"); } catch (_) {}
      if (request.status >= 200 && request.status < 300) resolve(body);
      else {
        const error = new Error(body.error?.message || "The desktop rejected this file.");
        error.code = body.error?.code || "upload_rejected";
        reject(error);
      }
    };
    request.send(file);
  }).finally(() => { activeUpload = null; });
}

async function sendDrop() {
  const pendingFiles = dropFiles.filter(item => item.state !== "Saved");
  if (dropBusy || !pendingFiles.length) return;
  dropBusy = true;
  renderDrop();
  els["drop-progress"].hidden = false;
  els["drop-progress"].setAttribute("aria-valuenow", "0");
  els["drop-progress"].querySelector("span").style.width = "0";
  const total = pendingFiles.reduce((sum, item) => sum + item.file.size, 0);
  let completed = 0;
  try {
    if (!credential) throw new Error("Pair this phone from the unlocked desktop before using Drop.");
    if (!navigator.onLine) throw new Error("You are offline. Reconnect, then press Send to desktop again.");
    await ensureDropScope();
    setDropStatus("Checking files before transfer…");
    const declared = [];
    for (const item of pendingFiles) {
      declared.push({name: item.file.name, mediaType: item.file.type, size: item.file.size, sha256: await digestFile(item.file)});
    }
    const intent = await jsonRequest("/api/v1/inbox/intents", {
      method: "POST", body: JSON.stringify({requestId: requestId("drop"), files: declared}),
    });
    dropIntentId = intent.intentId;
    let failures = 0;
    for (let index = 0; index < pendingFiles.length; index++) {
      const item = pendingFiles[index];
      try {
        await uploadRaw(intent.files[index], item.file, completed, total);
        item.state = "Saved";
      } catch (error) {
        item.state = "Failed";
        failures += 1;
        setDropStatus(error.message || "This file was rejected.", "error");
      }
      completed += item.file.size;
      renderDrop();
    }
    dropIntentId = "";
    await discardSharedSelection();
    if (failures) {
      setDropStatus(`${pendingFiles.length - failures} saved; ${failures} failed. Press Retry failed files when ready.`, "error");
    } else {
      setDropStatus("Saved to Sidecar Inbox", "success");
      haptic([10, 35, 14]);
      els["drop-progress"].setAttribute("aria-valuenow", "100");
      els["drop-progress"].querySelector("span").style.width = "100%";
    }
  } catch (error) {
    setDropStatus(error.message || "The Drop stopped. Follow the recovery above and retry explicitly.", "error");
  } finally {
    dropBusy = false;
    renderDrop();
  }
}

async function cancelDrop() {
  activeUpload?.abort();
  if (dropIntentId) {
    await jsonRequest("/api/v1/inbox/cancel", {method: "POST", body: JSON.stringify({intentId: dropIntentId})}).catch(() => {});
  }
  dropIntentId = "";
  dropBusy = false;
  dropFiles = [];
  await discardSharedSelection();
  renderDrop();
  setDropStatus("Drop cancelled. No incomplete file was saved.");
  closeDrop();
}

async function consumeIncomingShare() {
  const url = new URL(location.href);
  const error = url.searchParams.get("sidecar-share-error");
  const key = url.searchParams.get("sidecar-share") || "";
  if (!error && !key) return;
  history.replaceState(null, "", location.pathname);
  openDrop();
  if (error) {
    setDropStatus(error === "unsupported"
      ? "That share was not staged: choose 1–5 supported files within the 25/50 MiB limits."
      : "Android stopped the share before Sidecar could stage it. Share again explicitly.", "error");
    return;
  }
  activeShareKey = key;
  const record = await readShare(key).catch(() => null);
  if (!record || Date.now() - Number(record.createdAt || 0) >= 5 * 60 * 1000) {
    await discardSharedSelection();
    setDropStatus("That share expired. Return to the source app and share it again.", "error");
    return;
  }
  acceptDropFiles(record.files, "share");
}

async function maybeShowInstallTip() {
  if (window.matchMedia("(display-mode: standalone)").matches || await readSetting("install-tip-dismissed")) return;
  els["install-tip"].hidden = false;
  if (platform() === "ios-web") els["install-copy"].textContent = "In Safari, tap Share, then Add to Home Screen.";
  else if (installPrompt) els["install-copy"].textContent = "Use Install from your browser menu to keep Portal on your home screen.";
}

window.addEventListener("beforeinstallprompt", event => { event.preventDefault(); installPrompt = event; });
els["dismiss-install"].addEventListener("click", async () => { els["install-tip"].hidden = true; await saveSetting("install-tip-dismissed", true).catch(() => {}); });
els["pair-form"].addEventListener("submit", requestPairing);
els["portal-stage"].addEventListener("pointerdown", portalSwipeStart);
els["theme-undo"].addEventListener("click", () => {
  const target = previousThemeId;
  commitMorph(target);
});
els["background-next"].addEventListener("click", () => runAction(els["background-next"], "theme.backgroundNext", {}, "Background changed"));
els["connection-orb"].addEventListener("click", openSettings);
els["drop-open"].addEventListener("click", openDrop);
els["drop-close"].addEventListener("click", () => { if (!dropBusy) closeDrop(); });
els["drop-choose"].addEventListener("click", () => els["drop-picker"].click());
els["drop-picker"].addEventListener("change", () => {
  acceptDropFiles(els["drop-picker"].files);
  els["drop-picker"].value = "";
});
els["drop-send"].addEventListener("click", sendDrop);
els["drop-cancel"].addEventListener("click", cancelDrop);
els["settings-close"].addEventListener("click", closeSettings);
els["sheet-backdrop"].addEventListener("click", () => { if (dropOpen) closeDrop(); else closeSettings(); });
els["drop-sheet"].addEventListener("keydown", event => {
  if (event.key === "Escape") { event.preventDefault(); if (!dropBusy) closeDrop(); return; }
  if (event.key !== "Tab") return;
  const focusable = Array.from(els["drop-sheet"].querySelectorAll("button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex='-1'])"));
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});
els["settings-sheet"].addEventListener("keydown", event => {
  if (event.key === "Escape") { event.preventDefault(); closeSettings(); return; }
  if (event.key !== "Tab") return;
  const focusable = Array.from(els["settings-sheet"].querySelectorAll("button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex='-1'])"));
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});
els["request-capabilities"].addEventListener("click", requestCapabilities);
els["haptics-toggle"].addEventListener("change", async () => {
  hapticsEnabled = els["haptics-toggle"].checked;
  await saveSetting("haptics", hapticsEnabled).catch(() => {});
  if (hapticsEnabled) haptic(10);
});
els["hold-lock"].addEventListener("pointerdown", startHoldLock);
els["hold-lock"].addEventListener("pointerup", cancelHoldLock);
els["hold-lock"].addEventListener("pointercancel", cancelHoldLock);
els["hold-lock"].addEventListener("pointerleave", cancelHoldLock);
els["hold-lock"].addEventListener("keydown", startHoldLock);
els["hold-lock"].addEventListener("keyup", cancelHoldLock);
els["hold-lock"].addEventListener("click", event => event.preventDefault());
els["unpair-phone"].addEventListener("click", unpairPhone);
for (const button of document.querySelectorAll("[data-nav]")) button.addEventListener("click", () => navigate(button.dataset.nav));
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && credential) {
    clearTimeout(fallbackTimer);
    restartEventStream().catch(connectionFailed);
  }
});
window.addEventListener("pagehide", () => releaseThemePreviews(true));

async function start() {
  els["device-name"].value = suggestedName();
  try {
    const setting = await readSetting("haptics");
    if (typeof setting === "boolean") hapticsEnabled = setting;
  } catch (_) {}
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/app/sw.v1011.js", {scope: "/app/"}).catch(() => {});
  try {
    clientInstanceId = await ensureClientInstanceId();
  } catch (_) {
    terminalPairing("Private storage is unavailable", "Sidecar needs browser storage to keep this phone as one clean pairing. Free browser storage, then reopen the pairing link.");
    return;
  }
  try { credential = await readCredential() || ""; } catch (_) { credential = ""; }
  if (pairingSecret) {
    if (credential) {
      try {
        await bindClientInstance();
      } catch (error) {
        if (error.status === 401 || error.code === "client_identity_changed") {
          credential = "";
          clientInstanceBound = false;
          await deleteCredential().catch(() => {});
        } else {
          terminalPairing("Could not prepare a clean re-pair", "Check Tailscale, close this page, and scan a fresh Sidecar QR code.");
          return;
        }
      }
    }
    showOnboarding("name");
    setConnection("starting", "Ready to pair");
    return;
  }
  if (!credential) {
    terminalPairing("Open Sidecar on your desktop", "Pair this phone before sending the staged Drop.");
    await consumeIncomingShare();
    return;
  }
  await beginSession();
  await consumeIncomingShare();
}

start();
