"""Every request the app makes to another site goes through client() here.

- One honest User-Agent, dexSTASH/<version> (+<repo>), for every site.
- Price and picture sites outside the always-on sources (TCGdex, TCGCSV, PokéAPI, the ECB's euro rates) each
  have a switch in Data sources. A switched-off site gets no request at all: client() refuses it, redirects
  included, so a missed check elsewhere can't reach it. New installs start with them off; an install that
  already had accounts when it updated to 0.14 kept them on (db step 5). Turning one off deletes nothing.
- TCGplayer's and PriceCharting's pages are never read. client() refuses any request to either
  site; only TCGplayer's product pictures (its picture hosts, listed by TCGCSV) are fetched, always on, and
  PriceCharting's official API (/api/) with a token the host entered (round 28).
- Background work (jobs and their download threads) takes turns per site: at most PACE requests a second to one
  site, and none for a while after the site answers 429 or 503. Pages never wait in the queue (#77).
"""

import logging
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from . import REPO_URL, __version__

log = logging.getLogger("dexstash.sites")
USER_AGENT = f"dexSTASH/{__version__} (+{REPO_URL})"
HEADERS = {"User-Agent": USER_AGENT}

# name -> (shown name, what it's used for)
SITES = {
    "limitless": ("Limitless TCG", "Japanese card pictures"),
    "chinese": ("Chinese card datasets", "Mainland Chinese sets and pictures (PTCG-CHS-Datasets) and their English names (PTCG-card-data)"),
    "bulbapedia": ("Bulbapedia", "Pokémon descriptions for Describe"),
}
CACHE_SECONDS = 15  # the scanner container sees a change within this long


class Off(Exception):
    """A request to a site that is switched off in Data sources."""

    def __init__(self, site: str):
        super().__init__(f"{label(site)} is off in Data sources")
        self.site = site


class Blocked(Exception):
    """A request to TCGplayer's or PriceCharting's pages, which dexSTASH never reads."""

    def __init__(self, url: str):
        super().__init__(f"dexSTASH doesn't read pages on {urlsplit(url).hostname}")


TCGPLAYER_PICTURE_HOSTS = {"tcgplayer-cdn.tcgplayer.com", "product-images.tcgplayer.com"}


def blocked(url: str | None) -> bool:
    """True for any address on TCGplayer (other than its picture hosts) or PriceCharting, pictures included."""
    parts = urlsplit(url or "")
    host, path = (parts.hostname or "").lower(), parts.path
    if host == "tcgplayer.com" or host.endswith(".tcgplayer.com"):
        return host not in TCGPLAYER_PICTURE_HOSTS
    if host in ("pricecharting.com", "www.pricecharting.com") and path.startswith("/api/"):
        return False  # round 28: PriceCharting's official API, with the host's own token (pricecharting.py)
    return host == "pricecharting.com" or host.endswith(".pricecharting.com") or (
        host == "storage.googleapis.com" and path.startswith("/images.pricecharting.com/"))


def label(site: str) -> str:
    return SITES[site][0]


def site_of(url: str | None) -> str | None:
    """Which switchable site an address belongs to (None for TCGdex, TCGCSV, CardTrader and the rest)."""
    parts = urlsplit(url or "")
    host, path = (parts.hostname or "").lower(), parts.path
    if "limitlesstcg" in host:
        return "limitless"
    if host == "raw.githubusercontent.com" and path.startswith(("/duanxr/PTCG-CHS-Datasets/", "/zsjy947/PTCG-card-data/")):
        return "chinese"
    if host == "bulbagarden.net" or host.endswith(".bulbagarden.net"):
        return "bulbapedia"
    return None


_cache: dict[str, bool] = {}
_cache_at = 0.0
_lock = threading.Lock()
_override: dict[str, bool] | None = None  # tests can pin the switches without a database


def _read() -> dict[str, bool]:
    from . import db
    conn = db.connect(autocommit=True)
    try:
        rows = conn.execute("SELECT key, value FROM app_meta WHERE key LIKE 'site_%'").fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    return {r[0][5:]: r[1] == "1" for r in rows}


def states() -> dict[str, bool]:
    """Each site's switch; a site with no row (a database before step 5 has run) counts as off."""
    global _cache, _cache_at
    if _override is not None:
        return {s: _override.get(s, False) for s in SITES}
    with _lock:
        if time.monotonic() - _cache_at > CACHE_SECONDS:
            _cache, _cache_at = _read(), time.monotonic()
        return {s: _cache.get(s, False) for s in SITES}


def on(site: str) -> bool:
    return states()[site]


def allowed(url: str | None) -> bool:
    if blocked(url):
        return False
    site = site_of(url)
    return site is None or on(site)


def check(url: str) -> None:
    if blocked(url):
        raise Blocked(url)
    site = site_of(url)
    if site and not on(site):
        raise Off(site)


def set_on(conn, site: str, value: bool) -> None:
    global _cache_at
    if site not in SITES:
        raise ValueError(site)
    conn.execute("INSERT INTO app_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (f"site_{site}", "1" if value else "0"))
    _cache[site] = value
    _cache_at = 0.0
    if _override is not None:
        _override[site] = value  # read again next time, so every thread sees it


def keep(pictures: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Pictures (address, label) without those from switched-off sites, so the phone doesn't load them either."""
    return [p for p in pictures if allowed(p[0])]


# --- the queue: background requests to one site, at a pace that leaves room for pages -------------------------

PACE = 5.0         # background requests a second to one site, shared by every job and download thread
FIRST_PAUSE = 30.0  # a site that says slow down (429/503) without saying how long gets no background request this long,
LONGEST_PAUSE = 300.0  # doubling while it keeps refusing, up to this


class _Turns:
    """One site's queue: when the next background request may go, and how long the site asked us to stop."""

    def __init__(self):
        self.next_at = 0.0
        self.paused_until = 0.0
        self.pause = 0.0


_turns: dict[str, _Turns] = {}
_turns_lock = threading.Lock()


def _host(url) -> str:
    return (urlsplit(str(url)).hostname or "").lower()


def wait_turn(url, now=time.monotonic, sleep=time.sleep) -> float:
    """On a background thread: wait for this site's next turn (PACE a second, longer while it asked us to stop).
    A page's own requests never wait. Returns the seconds waited."""
    from .jobs import in_background
    if not in_background():
        return 0.0
    with _turns_lock:
        turns = _turns.setdefault(_host(url), _Turns())
        at = max(now(), turns.next_at, turns.paused_until)
        turns.next_at = at + 1 / PACE
    wait = at - now()
    if wait > 0:
        sleep(wait)
    return max(wait, 0.0)


def _retry_after(value: str | None) -> float | None:
    try:
        return min(max(float(value), 1.0), LONGEST_PAUSE) if value else None
    except ValueError:
        return None  # an HTTP date: use our own pause


def answered(url, status: int, retry_after: str | None = None, now=time.monotonic) -> None:
    """A site's answer to a background request: 429 or 503 stops its queue a while; a good answer ends the doubling."""
    from .jobs import in_background
    if not in_background():
        return
    host = _host(url)
    with _turns_lock:
        turns = _turns.setdefault(host, _Turns())
        if status in (429, 503):
            turns.pause = min(max(turns.pause * 2, FIRST_PAUSE), LONGEST_PAUSE)
            pause = _retry_after(retry_after) or turns.pause
            if now() + pause > turns.paused_until:
                turns.paused_until = now() + pause
                log.info("%s asked dexSTASH to slow down (%s): background requests to it wait %.0f s", host, status, pause)
        elif status < 400:
            turns.pause = 0.0


def client(**kwargs):
    """An httpx.Client with the User-Agent that refuses switched-off sites (each redirect is checked too). On a
    background job's thread, each request first waits for pages being answered (jobs.pause_for_pages), then for
    its site's turn in the queue (wait_turn)."""
    import httpx
    from .jobs import pause_for_pages
    headers = {**(kwargs.pop("headers", None) or {}), **HEADERS}  # the one User-Agent, whatever else is sent
    hooks = kwargs.pop("event_hooks", None) or {}
    hooks = {**hooks, "request": [lambda request: check(str(request.url)), lambda request: pause_for_pages(),
                                lambda request: wait_turn(request.url), *hooks.get("request", [])],
             "response": [lambda response: answered(response.request.url, response.status_code,
                                                    response.headers.get("Retry-After")), *hooks.get("response", [])]}
    return httpx.Client(headers=headers, event_hooks=hooks, **kwargs)


def get(url: str, **kwargs):
    """One GET, like httpx.get."""
    with client(**{k: kwargs.pop(k) for k in ("timeout", "follow_redirects") if k in kwargs}) as c:
        return c.get(url, **kwargs)
