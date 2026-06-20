// Basic Service Worker for PWA installability
const CACHE_NAME = 'd2ha-cache-v2';
const urlsToCache = [
  '/static/css/main.css',
  '/static/img/favicon.png'
];

self.addEventListener('install', event => {
  // Activate this worker as soon as it finishes installing.
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      // Cache best-effort: a single failing asset must not abort the install,
      // otherwise the worker never activates and the app stays non-installable.
      return Promise.allSettled(
        urlsToCache.map(url => cache.add(url))
      );
    })
  );
});

self.addEventListener('activate', event => {
  // Take control of open clients immediately and drop old caches.
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  // Only handle GET requests
  if (event.request.method !== 'GET') return;

  event.respondWith(
    fetch(event.request)
      .then(response => {
        // If the response is valid, clone it and cache it
        if (response && response.status === 200 && response.type === 'basic') {
          const responseToCache = response.clone();
          caches.open(CACHE_NAME).then(cache => {
            cache.put(event.request, responseToCache);
          });
        }
        return response;
      })
      .catch(() => {
        // On network failure, serve from cache
        return caches.match(event.request);
      })
  );
});
