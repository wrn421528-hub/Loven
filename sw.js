const CACHE_NAME = 'afterglow-shell-v08';
const SHELL = ['/', '/manifest.webmanifest', '/icons/icon-192.png', '/icons/icon-512.png'];
self.addEventListener('install', event => { event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener('activate', event => { event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)))).then(() => self.clients.claim())); });
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.pathname.startsWith('/api/')) return;
  if (url.origin === self.location.origin) {
    event.respondWith(fetch(event.request).then(response => {
      if (response && response.ok && (url.pathname === '/' || url.pathname.endsWith('.webmanifest') || url.pathname.startsWith('/icons/'))) {
        const copy = response.clone(); caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
      }
      return response;
    }).catch(() => caches.match(event.request).then(cached => cached || caches.match('/'))));
  }
});
