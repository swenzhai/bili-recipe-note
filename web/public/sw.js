const CACHE = "chef-zhai-family-kitchen-shell-v13";
const SHELL = ["/", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png"];
self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  if (event.request.mode === "navigate") {
    event.respondWith(
      caches.match("/").then((cached) => {
        const refreshed = fetch(event.request).then(async (response) => {
          if (response.ok) await (await caches.open(CACHE)).put("/", response.clone());
          return response;
        });
        if (cached) { event.waitUntil(refreshed.catch(() => undefined)); return cached; }
        return refreshed.catch(() => Response.error());
      }),
    );
    return;
  }
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const refreshed = fetch(event.request).then((response) => {
        if (response.ok && event.request.url.startsWith(self.location.origin)) {
          const copy = response.clone();
          event.waitUntil(caches.open(CACHE).then((cache) => cache.put(event.request, copy)));
        }
        return response;
      });
      if (cached) {
        event.waitUntil(refreshed.catch(() => undefined));
        return cached;
      }
      return refreshed.catch(() => event.request.mode === "navigate" ? caches.match("/") : Response.error());
    }),
  );
});
