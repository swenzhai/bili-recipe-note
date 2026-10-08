const CACHE = "chef-zhai-family-kitchen-shell-v14";
const SHELL = ["/", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png"];
self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    const upgrading = keys.some(key => key.startsWith("chef-zhai-family-kitchen-shell-") && key !== CACHE);
    await Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)));
    await self.clients.claim();
    if (upgrading) {
      // Older installed pages mounted their menu cache without an access gate.
      // Reload those pages once when this security upgrade takes control.
      const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      await Promise.all(clients.filter(client => {
        const url = new URL(client.url);
        return url.origin === self.location.origin && !/^\/admin(?:\/|$)/.test(url.pathname);
      }).map(client => client.navigate(client.url).catch(() => undefined)));
    }
  })());
});
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request).then(async (response) => {
        if (response.ok) await (await caches.open(CACHE)).put("/", response.clone());
        return response;
      }).catch(() => caches.match("/").then(cached => cached || Response.error())),
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
