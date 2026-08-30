"use strict";

const CACHE = "sidecar-web-v1012-final";
const SHARE_DB = "omarchy-sidecar";
const SHARE_STORE = "shares";
const MAX_FILE = 25 * 1024 * 1024;
const MAX_BATCH = 50 * 1024 * 1024;
const MAX_FILES = 5;
const SHARE_LIFETIME = 5 * 60 * 1000;
const ASSETS = [
  "/app/", "/app/pair", "/app/share-target",
  "/app/app.v1012.css", "/app/app.v1012.js", "/app/model.v1012.js",
  "/app/manifest.webmanifest", "/app/icon.svg", "/app/icon-192.png", "/app/icon-512.png",
];
const ASSET_PATHS = new Set(ASSETS);
const POLICY = Object.freeze({
  "image/png": [".png"], "image/jpeg": [".jpg", ".jpeg"], "image/webp": [".webp"],
  "image/gif": [".gif"], "application/pdf": [".pdf"], "text/plain": [".txt"],
});

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(SHARE_DB, 2);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains("private")) database.createObjectStore("private");
      if (!database.objectStoreNames.contains("settings")) database.createObjectStore("settings");
      if (!database.objectStoreNames.contains(SHARE_STORE)) database.createObjectStore(SHARE_STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function writeShare(key, value) {
  const database = await openDatabase();
  await new Promise((resolve, reject) => {
    const transaction = database.transaction(SHARE_STORE, "readwrite");
    const store = transaction.objectStore(SHARE_STORE);
    store.clear();
    store.put(value, key);
    transaction.oncomplete = resolve;
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
  database.close();
}

async function cleanupShares() {
  const database = await openDatabase();
  await new Promise((resolve, reject) => {
    const transaction = database.transaction(SHARE_STORE, "readwrite");
    const store = transaction.objectStore(SHARE_STORE);
    const request = store.openCursor();
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) return;
      if (!cursor.value || Date.now() - Number(cursor.value.createdAt || 0) >= SHARE_LIFETIME) cursor.delete();
      cursor.continue();
    };
    transaction.oncomplete = resolve;
    transaction.onerror = () => reject(transaction.error);
  });
  database.close();
}

function validFile(file) {
  if (!(file instanceof File) || file.size < 1 || file.size > MAX_FILE || file.name.length > 480) return false;
  const extensions = POLICY[file.type];
  if (!extensions) return false;
  const lower = file.name.normalize("NFKC").toLowerCase();
  return extensions.some(extension => lower.endsWith(extension));
}

async function receiveShare(request) {
  try {
    const formData = await request.formData();
    const files = formData.getAll("files").filter(value => value instanceof File);
    if (files.length < 1 || files.length > MAX_FILES || files.some(file => !validFile(file))
      || files.reduce((total, file) => total + file.size, 0) > MAX_BATCH) {
      return Response.redirect(new URL("/app/?sidecar-share-error=unsupported", self.location.origin), 303);
    }
    const id = crypto.randomUUID();
    await writeShare(id, {createdAt: Date.now(), files});
    return Response.redirect(new URL(`/app/?sidecar-share=${encodeURIComponent(id)}`, self.location.origin), 303);
  } catch (_) {
    return Response.redirect(new URL("/app/?sidecar-share-error=interrupted", self.location.origin), 303);
  }
}

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", event => {
  event.waitUntil(Promise.all([
    caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))),
    cleanupShares(),
  ]).then(() => self.clients.claim()));
});

self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.method === "POST" && url.pathname === "/app/share-target" && !url.search) {
    event.respondWith(receiveShare(request));
    return;
  }
  if (request.method !== "GET") return;
  if (url.pathname === "/app/" && (url.searchParams.has("sidecar-share") || url.searchParams.has("sidecar-share-error"))) {
    event.respondWith(fetch(request).catch(() => caches.match("/app/")));
    return;
  }
  if (url.search || !ASSET_PATHS.has(url.pathname)) return;
  event.respondWith(fetch(request).then(response => {
    if (response.ok && !request.headers.has("Authorization")) {
      const clone = response.clone();
      caches.open(CACHE).then(cache => cache.put(request, clone));
    }
    return response;
  }).catch(() => caches.match(request).then(response => response || caches.match("/app/"))));
});
