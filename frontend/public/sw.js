// Kill-switch service worker: unregistra qualquer SW ativo e limpa caches.
// Substitui o SW gerado pelo next-pwa enquanto estamos em fase de bugfix.
// Quando estabilizar, re-habilita next-pwa em next.config.js.

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      // Take control of all open clients.
      await self.clients.claim();
      // Delete every cache this origin owns.
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
      // Unregister self.
      await self.registration.unregister();
      // Force-reload all controlled pages so they fetch fresh.
      const clients = await self.clients.matchAll({ type: 'window' });
      for (const client of clients) {
        client.navigate(client.url);
      }
    })()
  );
});
