// Offline shell for the admin UI.
//
// The previous version precached /static/styles.css and then served it
// cache-first with no revalidation, under a cache name ('memoryst-v1') that was
// hardcoded and never bumped. So the first stylesheet a phone ever fetched was
// the one it kept for good - a CSS change could not reach the device at all,
// and the `activate` cleanup could never fire because the name never changed.
// Exactly the trap the SillyTavern extension hit with its cached ES modules,
// which is why its imports carry a build stamp.
//
// Two changes stop it recurring:
//   - the stylesheet URL now carries a fingerprint (?v=...), so an edited file
//     is a different resource that no cache can already hold;
//   - static assets are served stale-while-revalidate instead of cache-first,
//     so even an unversioned URL self-heals on the next load.
const CACHE_NAME = 'memoryst-v2';
const STATIC_ASSETS = [
  '/ui',
  '/static/manifest.json',
];

self.addEventListener('install', (event) => {
  // The stylesheet is deliberately absent here: its URL carries a fingerprint,
  // so precaching a fixed path would store an entry nothing ever requests.
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

function isApiRequest(url, request) {
  return (
    url.pathname.startsWith('/memory/')
    || url.pathname.startsWith('/ui/store')
    || url.pathname.startsWith('/ui/retrieve')
    || url.pathname.startsWith('/ui/create')
    || (url.pathname.startsWith('/ui/') && request.method === 'POST')
  );
}

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  if (isApiRequest(url, event.request)) {
    event.respondWith(fetch(event.request));
    return;
  }

  // Pages: network first, cache only as an offline fallback.
  if (url.pathname === '/ui') {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Static: answer from cache at once for speed, but always refetch in the
  // background so the next load is current. A failed refetch falls back to the
  // cached copy - the point of the cache is that being offline still works.
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const fresh = fetch(event.request)
        .then((response) => {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() => cached);
      return cached || fresh;
    })
  );
});
