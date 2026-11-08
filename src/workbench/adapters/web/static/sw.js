/* Workbench PWA — minimal passthrough worker (Installability, kein Offline-Shell-Anspruch) */
const CACHE = "wb-passthrough-v1";
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(fetch(event.request));
});
