// Caches static assets only; pages and API responses always go to the network.
const CACHE = "ledgerline-static-v3";
importScripts("/static/js/capture-queue.js");

const OFFLINE_URL = "/static/offline.html";
const PRECACHE = [
  OFFLINE_URL,
  "/static/css/app.css",
  "/static/js/app.js",
  "/static/js/capture.js",
  "/static/js/capture-queue.js",
  "/static/img/mark.svg",
  "/static/img/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => caches.match(request))
    );
  }
});

self.addEventListener("sync", (event) => {
  if (event.tag !== "capture-queue") return;
  // Rejecting while items remain makes the browser schedule another sync.
  event.waitUntil(
    self.captureQueue.flush().then((result) => {
      if (result.remaining) throw new Error("queued uploads remain");
    })
  );
});
