// Paper Plane's service worker. It makes the app installable on a phone, shows notifications
// pushed to this device when no tab is open (web push), and keeps the look (styles, scripts,
// fonts, icons) ready on a slow connection.
// Pages always come from the network, so chats are never out of date; without a connection
// a small "You're offline" page shows instead.
"use strict";

const CACHE = "paperplane-v2"; // change the number when these files change (v2: the new logo)
const FILES = [
  "/static/style.css",
  "/static/app.js",
  "/static/theme.js",
  "/static/socket.js",
  "/static/chat.js",
  "/static/fonts/inter-latin-400-normal.woff2",
  "/static/fonts/outfit-latin-700-normal.woff2",
  "/static/icons/icon-192.png",
  "/static/icons/logo.png",
  "/static/offline.html",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(FILES)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // Throw away the caches of older versions.
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((name) => name !== CACHE).map((name) => caches.delete(name)))),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET" || new URL(request.url).origin !== self.location.origin) return;
  if (request.mode === "navigate") {
    // A page: always the network; the offline page only when there's none.
    event.respondWith(fetch(request).catch(() => caches.match("/static/offline.html")));
    return;
  }
  if (FILES.includes(new URL(request.url).pathname)) {
    // Our own styles/scripts/icons: the network first (so updates show), the cache as a backup.
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match(request)),
    );
  }
});

// A message arrived while no tab was open (notify.push on the server).
self.addEventListener("push", (event) => {
  let notice = { title: "Paper Plane", body: "", url: "/chats" };
  try {
    notice = { ...notice, ...event.data.json() };
  } catch (error) {
    if (event.data) notice.body = event.data.text();
  }
  event.waitUntil(self.registration.showNotification(notice.title, {
    body: notice.body,
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/icon-192.png",
    tag: `chat-${notice.conversation_id || 0}`, // one notification per chat, the newest
    renotify: true,
    data: { url: notice.url || "/chats" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL((event.notification.data && event.notification.data.url) || "/chats", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: "window" }).then((windows) => {
    const open = windows.find((client) => client.url === url);
    return open ? open.focus() : self.clients.openWindow(url);
  }));
});
