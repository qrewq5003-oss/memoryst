const CACHE_NAME = 'memoryst-v1';
const STATIC_ASSETS = [
  '/ui',
  '/static/styles.css',
  '/static/manifest.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Don't cache API calls
  if (url.pathname.startsWith('/memory/') || url.pathname.startsWith('/ui/store') || url.pathname.startsWith('/ui/retrieve') || url.pathname.startsWith('/ui/create') || url.pathname.startsWith('/ui/') && event.request.method === 'POST') {
    event.respondWith(fetch(event.request));
    return;
  }

  // Network-first for pages, cache-first for static
  if (url.pathname === '/ui') {
    event.respondWith(
      fetch(event.request).catch(() => caches.match(event.request))
    );
  } else {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
  }
});
