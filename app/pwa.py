"""The Home Screen app: its manifest, the iPhone and iPad launch images, and a small service worker whose only
job is a "Can't reach your server" page. It never stores your pages or data: every page still comes from the
server, and only when the server can't be reached does the saved offline page show instead."""

from . import __version__, settings

# iPhone and iPad launch images (tools/make_logo.py draws them): device pixels, portrait, and the pixel ratio.
SPLASH = [(1320, 2868, 3), (1206, 2622, 3), (1290, 2796, 3), (1179, 2556, 3), (1284, 2778, 3), (1170, 2532, 3),
          (1125, 2436, 3), (1242, 2688, 3), (828, 1792, 2), (1242, 2208, 3), (750, 1334, 2), (640, 1136, 2),
          (1488, 2266, 2), (1640, 2360, 2), (1668, 2388, 2), (2048, 2732, 2), (1620, 2160, 2)]
DARK_PAGE, DARK_BAR = "#111113", "#1c1c1f"  # the Dark look the app opens in
LIGHT_PAGE, LIGHT_BAR = "#f5f5f7", "#ffffff"  # the Light look (--bg and --surface in base.css)


def splash_links(asset) -> list[dict]:
    """<link rel="apple-touch-startup-image"> attributes, one per device size."""
    return [{"href": asset(f"splash/splash-{w}x{h}.png"),
             "media": f"(device-width: {w // r}px) and (device-height: {h // r}px) and "
                      f"(-webkit-device-pixel-ratio: {r}) and (orientation: portrait)"} for w, h, r in SPLASH]


def manifest(asset, scanning: bool, mode: str = "dark") -> dict:
    """The Home Screen app. mode is the person's look, so its bar and launch colours match the header."""
    name = settings.app_name()
    shortcuts = ([{"name": "Scan", "url": "/scans", "icons": [{"src": asset("icon-192.png"), "sizes": "192x192"}]}]
                 if scanning else [])
    shortcuts += [{"name": "Find", "url": "/search", "icons": [{"src": asset("icon-192.png"), "sizes": "192x192"}]},
                  {"name": "My Stash", "url": "/collection", "icons": [{"src": asset("icon-192.png"), "sizes": "192x192"}]}]
    return {
        "id": "/", "name": name, "short_name": name, "start_url": "/", "scope": "/", "display": "standalone",
        "description": "Your Pokémon card collection on your own server: what you have, what it's worth, and what you chase.",
        "categories": ["lifestyle", "entertainment", "utilities"],
        "background_color": LIGHT_PAGE if mode == "light" else DARK_PAGE,
        "theme_color": LIGHT_BAR if mode == "light" else DARK_BAR,
        "icons": [{"src": asset("icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
                  {"src": asset("icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
                  {"src": asset("icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
                  {"src": asset("icon-monochrome-512.png"), "sizes": "512x512", "type": "image/png",
                   "purpose": "monochrome"}],
        "shortcuts": shortcuts,
    }


OFFLINE_FILES = ("css/offline.css", "js/offline.js", "icon-192.png")


def service_worker(asset) -> str:
    """The service worker, versioned with the app so a new bundle replaces the old offline page. It answers only
    page loads, and only when the server doesn't (no connection, or the proxy says the app is down)."""
    files = ["/offline"] + [asset(f) for f in OFFLINE_FILES] + ["/static/fonts/nunito-latin.woff2"]
    return f"""// dexSTASH {__version__}: shows a saved "Can't reach your server" page when a page can't load. Nothing else
// is stored: every page and all your data still come from the server.
const CACHE = "dexstash-offline-{__version__}";
const FILES = {files!r};
self.addEventListener("install", e => {{
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES)).then(() => self.skipWaiting()));
}});
self.addEventListener("activate", e => {{
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
}});
self.addEventListener("fetch", e => {{
  const url = new URL(e.request.url);
  if (url.origin === location.origin && FILES.includes(url.pathname + url.search)) {{
    e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
    return;
  }}
  if (e.request.mode !== "navigate") return;
  e.respondWith(fetch(e.request).then(r => [502, 503, 504].includes(r.status) ? offline(r) : r, () => offline()));
}});
const offline = r => caches.match("/offline").then(page => page || r || Response.error());
"""
