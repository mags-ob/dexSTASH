"""Thin client for the TCGdex API (https://tcgdex.dev). No API key needed.

All calls to the card data source go through this module, so switching to a
fallback (pokemontcg.io) later only touches this file.

Set and card fields are confirmed against live responses. The search filters
in search_cards follow the TCGdex docs and are not yet confirmed.
"""

import json
import logging
import os
import re
import threading
import time
from datetime import timedelta
from contextlib import contextmanager
from urllib.parse import quote

import httpx

from . import clock, sites

BASE_URL = os.environ.get("TCGDEX_URL", "https://api.tcgdex.net/v2")

# TCGdex language codes. Each language has its own sets, numbers and names.
LANGUAGES = {
    "en": "English",
    "ja": "Japanese",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "pt-br": "Portuguese (Brazil)",
    "ko": "Korean",
    "zh-tw": "Chinese",
    "zh-cn": "Chinese",
}

# The languages people pick from. "zh" is one Chinese choice covering both of
# TCGdex's: zh-tw (Taiwan and Hong Kong printings) and zh-cn (mainland China).
CHOICES = {"en": "English", "ja": "Japanese", "zh": "Chinese", "ko": "Korean"}
CHINESE = ("zh-tw", "zh-cn")
# Scanning only offers languages the reader and the card data can handle.
# Chinese scans match mainland sets (loaded ones and cards added by hand).
SCAN_CHOICES = {"en": "English", "ja": "Japanese", "zh-cn": "Chinese"}


def expand(choice: str) -> list[str]:
    """The TCGdex languages behind a choice: 'zh' -> both Chinese ones."""
    return list(CHINESE) if choice == "zh" else [choice]


def choice_of(lang: str) -> str:
    return "zh" if lang in CHINESE else lang


RETRY_PAUSE = 2.0      # seconds before the one retry
SETS_MAX_AGE = 12 * 3600  # the set list is fetched again after this, so new sets show up the next day


log = logging.getLogger("dexstash.tcgdex")

# A page never waits long on TCGdex. While a page loads (page_wait, set for every
# page in main.py) a request gets PAGE_TIMEOUT seconds and no retry, and after a timeout or a dropped
# connection the next DOWN_PAUSE seconds of page requests don't try at all. Background jobs keep 20 s and a retry.
PAGE_TIMEOUT = 8.0
DOWN_PAUSE = 60.0
_page = threading.local()
_down_until = 0.0


@contextmanager
def page_wait():
    before = getattr(_page, "on", False)
    _page.on = True
    try:
        yield
    finally:
        _page.on = before


def in_page() -> bool:
    return getattr(_page, "on", False)


class Unreachable(httpx.TransportError):
    """TCGdex timed out for a page a moment ago, so this page doesn't try."""


def _page_call(send):
    """send(timeout) for a page: one quick try, skipped while TCGdex looks down."""
    global _down_until
    if time.monotonic() < _down_until:
        raise Unreachable("TCGdex didn't answer a moment ago")
    try:
        return send(PAGE_TIMEOUT)
    except Exception as e:
        if _worth_retrying(e):
            _down_until = time.monotonic() + DOWN_PAUSE
        raise
DISK_DAYS = 7  # saved replies for browsing are used for this long


class DiskCache:
    """TCGdex replies saved in the database (tcgdex_cache), so a card or set looked at this week opens
    without asking TCGdex again, even after a restart. Only for browsing: prices are always fetched."""

    def __init__(self, max_age: float = DISK_DAYS * 86400):
        self.max_age = max_age
        self._conn = None
        self._lock = threading.Lock()
        from . import settings
        self.enabled = settings.on("TCGDEX_CACHE")
        self._writes = 0

    def _db(self):
        if self._conn is None:
            from . import db
            self._conn = db.connect()
        return self._conn

    def get(self, key: str):
        if not self.enabled:
            return None
        try:
            with self._lock:
                row = self._db().execute("SELECT body FROM tcgdex_cache WHERE url = ? AND fetched_at > ?",
                                         (key, clock.since(timedelta(seconds=self.max_age)))).fetchone()
            return json.loads(row[0]) if row else None
        except Exception as e:  # no table yet, or a busy database: just ask TCGdex
            log.debug("tcgdex cache read %s: %s", key, e)
            return None

    def put(self, key: str, value) -> None:
        if not self.enabled:
            return
        try:
            with self._lock:
                conn = self._db()
                conn.execute("INSERT OR REPLACE INTO tcgdex_cache (url, body, fetched_at) VALUES (?, ?, ?)",
                             (key, json.dumps(value), clock.now()))
                self._writes += 1
                if self._writes % 500 == 0:  # forget old replies now and then
                    conn.execute("DELETE FROM tcgdex_cache WHERE fetched_at < ?", (clock.since(timedelta(seconds=self.max_age)),))
                conn.commit()
        except Exception as e:
            log.debug("tcgdex cache write %s: %s", key, e)


DISK = DiskCache()

# One GraphQL request for a whole set's card details. TCGdex's GraphQL has no prices.
GRAPHQL_FIELDS = ("id localId name image rarity illustrator dexId types stage suffix trainerType "
                  "variants { normal reverse holo firstEdition } variants_detailed { type foil }")


def _worth_retrying(e: Exception) -> bool:
    """A blip (timeout, dropped connection, busy or failing server), not an answer like 'not found'."""
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code == 429 or e.response.status_code >= 500
    return isinstance(e, httpx.TransportError)


# Pokémon TCG Pocket sets are digital only, so they're hidden everywhere. Their ids come from
# TCGdex's "tcgp" series, refreshed with the set list and kept in app_meta; until the first fetch works, this list.
POCKET_FALLBACK = frozenset({"A1", "A1a", "A2", "A2a", "A2b", "A3", "A3a", "A3b", "A4", "A4a", "B1", "B1a", "B2",
                             "P-A", "P-B"})
_pocket: set[str] | None = None
_pocket_lock = threading.Lock()


def pocket_sets() -> set[str]:
    """The TCGdex ids of TCG Pocket sets (the same in every language)."""
    global _pocket
    with _pocket_lock:
        if _pocket is None:
            _pocket = set(POCKET_FALLBACK)
            try:
                from . import db
                conn = db.connect()
                try:
                    row = conn.execute("SELECT value FROM app_meta WHERE key = 'tcgp_sets'").fetchone()
                finally:
                    conn.close()
                if row and row[0]:
                    _pocket = set(json.loads(row[0]))
            except Exception as e:  # no database yet (tests, first start)
                log.debug("pocket sets not read: %s", e)
        return _pocket


def _save_pocket(ids: set[str]) -> None:
    global _pocket
    with _pocket_lock:
        _pocket = set(ids)
    try:
        from . import db
        conn = db.connect()
        try:
            conn.execute("INSERT INTO app_meta (key, value) VALUES ('tcgp_sets', ?) "
                         "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (json.dumps(sorted(ids)),))
        finally:
            conn.close()
    except Exception as e:
        log.debug("pocket sets not saved: %s", e)


# Every illustrator's name as TCGdex writes it (/illustrators), for artist search and follow.
_artists: dict = {}
ARTISTS_RETRY = 3600


def artist_names(api=None, max_age: float = 86400) -> list[str]:
    """TCGdex's illustrator list: kept in app_meta, asked again at most once a day (and by Check for updates,
    with max_age=0). The last list is kept when TCGdex can't be reached."""
    import json as _json
    import time as _time
    from . import db
    if _artists.get("names") is not None and _time.time() - _artists.get("at", 0) < max_age:
        return _artists["names"]
    conn = None
    try:
        conn = db.connect()
        row = conn.execute("SELECT value FROM app_meta WHERE key = 'tcgdex_artists'").fetchone()
        saved = _json.loads(row[0]) if row else {}
        if saved.get("names") and _time.time() - saved.get("at", 0) < max_age:
            _artists.update(saved)
            return saved["names"]
        # After a failed fetch, searches wait an hour before asking again.
        if api is not None and (max_age == 0 or _time.time() - _artists.get("failed_at", 0) > ARTISTS_RETRY):
            try:
                names = sorted({n.strip() for n in api._get("/illustrators") if isinstance(n, str) and n.strip()})
                if names:
                    saved = {"names": names, "at": _time.time()}
                    conn.execute("INSERT INTO app_meta (key, value) VALUES ('tcgdex_artists', ?) "
                                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (_json.dumps(saved),))
                    conn.commit()
            except Exception as e:
                _artists["failed_at"] = _time.time()
                log.info("illustrator list not loaded: %s", e)
        _artists.update({"names": saved.get("names") or [], "at": _time.time() if saved.get("names") else 0})
    except Exception as e:
        log.info("illustrator list not read: %s", e)
        return _artists.get("names") or []
    finally:
        if conn is not None:
            conn.close()
    return _artists["names"]


def is_pocket(set_id: str | None) -> bool:
    """set_id as TCGdex has it ('A1') or as stored ('ja:A1')."""
    return bool(set_id) and set_id.split(":", 1)[-1] in pocket_sets()


def card_set_id(card_id: str) -> str:
    """A TCGdex card id's set ('A1-001' -> 'A1', 'P-A-007' -> 'P-A')."""
    return card_id.rsplit("-", 1)[0] if "-" in (card_id or "") else card_id


def without_pocket(cards: list[dict]) -> list[dict]:
    return [c for c in cards if not is_pocket(card_set_id(c.get("id") or ""))]


class TCGdex:
    def __init__(self, lang: str = "en", client: httpx.Client | None = None):
        self.lang = lang
        self.client = client or sites.client(base_url=f"{BASE_URL}/{lang}", timeout=20)
        self._sets: list[dict] | None = None
        self._sets_at = 0.0
        self._sets_lock = threading.Lock()
        self._sets_first = threading.Lock()
        self._sets_fetching = False
        self._cards: dict[str, tuple[float, dict]] = {}
        self._release: dict[str, str | None] = {}

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        """GET with one retry after a short pause for a network blip."""
        if in_page():
            def send(timeout):
                resp = self.client.get(path, params=params, timeout=timeout) if params else self.client.get(path, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            return _page_call(send)
        for attempt in (1, 2):
            try:
                resp = self.client.get(path, params=params) if params else self.client.get(path)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if attempt == 2 or not _worth_retrying(e):
                    raise
                time.sleep(RETRY_PAUSE)

    def list_sets(self) -> list[dict]:
        """All sets, oldest first. Fetched again when older than SETS_MAX_AGE, so a new set shows up
        the next day without a restart; if that fetch fails, the list we have is kept.
        Only the very first fetch is waited for. After that, a search keeps
        using the list it has while a fresh one is fetched in the background."""
        with self._sets_lock:
            have = self._sets
            stale = have is None or time.monotonic() - self._sets_at > SETS_MAX_AGE
            if stale and have is not None and not self._sets_fetching:
                self._sets_fetching = True
                threading.Thread(target=self._fetch_sets, name="tcgdex-sets", daemon=True).start()
        if have is None:
            with self._sets_first:  # one first fetch at a time; the others wait for it
                if self._sets is None:
                    self._fetch_sets(first=True)
            have = self._sets
        return [x for x in have if not is_pocket(x.get("id"))]

    def _fetch_sets(self, first: bool = False) -> None:
        try:
            sets = self._get("/sets")
            with self._sets_lock:
                self._sets, self._sets_at = sets, time.monotonic()
            self._refresh_pocket()
        except Exception:
            if first:
                raise
            with self._sets_lock:
                self._sets_at = time.monotonic() - SETS_MAX_AGE + 3600  # try again in an hour
        finally:
            with self._sets_lock:
                self._sets_fetching = False

    def _refresh_pocket(self) -> None:
        """The TCG Pocket set ids, from TCGdex's tcgp series; the last list is kept when this fails."""
        try:
            series = self._get("/series/tcgp")
            ids = {x["id"] for x in (series.get("sets") or []) if x.get("id")}
            if ids:
                _save_pocket(ids | set(POCKET_FALLBACK))
        except Exception as e:
            log.info("TCG Pocket set list not refreshed: %s", e)

    def get_set(self, set_id: str) -> dict:
        return self._get(f"/sets/{quote(set_id, safe='')}")

    def get_card(self, card_id: str) -> dict:
        return self._get(f"/cards/{quote(card_id, safe='')}")

    def get_set_cached(self, set_id: str) -> dict:
        """get_set, saved for a week, for browsing (set previews, Find in a set). Storing a set and
        Check for updates use get_set."""
        key = f"{self.lang}/sets/{set_id}"
        data = DISK.get(key)
        if data is None:
            data = self.get_set(set_id)
            DISK.put(key, data)
        return data

    def set_cards(self, set_id: str) -> list[dict] | None:
        """Every card of a set with its details, in one GraphQL request; None when that doesn't work
        (the caller then asks card by card)."""
        if not re.fullmatch(r"[A-Za-z0-9._-]+", set_id or ""):
            return None
        query = f'{{ set(id: "{set_id}") {{ cards {{ {GRAPHQL_FIELDS} }} }} }}'
        try:
            resp = self.client.post(f"{BASE_URL}/graphql", params={"lang": self.lang}, json={"query": query})
            resp.raise_for_status()
            body = resp.json()
            cards = ((body.get("data") or {}).get("set") or {}).get("cards")
            if body.get("errors") or not isinstance(cards, list):
                log.info("GraphQL for set %s gave no cards: %s", set_id, str(body.get("errors"))[:200])
                return None
            return cards
        except Exception as e:
            log.info("GraphQL for set %s failed: %s", set_id, e)
            return None

    def release_date(self, set_id: str) -> str | None:
        """A set's release date ('1999-01-09'), remembered for good: it doesn't change."""
        if set_id not in self._release:
            try:
                self._release[set_id] = self._get(f"/sets/{quote(set_id, safe='')}").get("releaseDate")
            except Exception:
                return None
        return self._release[set_id]

    def get_card_cached(self, card_id: str, max_age: float = 3600) -> dict:
        """get_card, remembered for an hour, and saved for a week (DiskCache). For search filters and previews,
        where a slightly old price doesn't matter; price updates use get_card."""
        hit = self._cards.get(card_id)
        if hit and time.monotonic() - hit[0] < max_age:
            return hit[1]
        key = f"{self.lang}/cards/{card_id}"
        card = DISK.get(key)
        if card is None:
            card = self.get_card(card_id)
            DISK.put(key, card)
        if len(self._cards) > 5000:
            self._cards.clear()
        self._cards[card_id] = (time.monotonic(), card)
        return card

    def search_cards(self, local_id: str | list[str] | None = None, name: str | None = None,
                     illustrator: str | None = None) -> list[dict]:
        """Brief cards across every set. local_id (one number, or several forms
        like ['4', '04', '004']) is matched exactly, name and illustrator loosely
        (case-insensitive 'contains'), using TCGdex's query filters."""
        from . import card_index
        local = card_index.search(self, local_id, name, illustrator)  # The local catalog, once complete
        if local is not None:
            return local
        params = {}
        if local_id:
            params["localId"] = local_id_filter(local_id)
        if name:
            params["name"] = name
        if illustrator:
            params["illustrator"] = illustrator
        try:
            return without_pocket(self._get("/cards", params))
        except (httpx.TransportError, httpx.HTTPStatusError):  # 0.18.2: TCGdex didn't answer: what the catalog has so far
            kept = card_index.search(self, local_id, name, illustrator, partial=True)
            if kept is None:
                raise
            return kept


def cached_set(api, set_id: str) -> dict:
    """A set for browsing: the week-old saved copy when there is one (stand-ins without it just ask)."""
    return api.get_set_cached(set_id) if hasattr(api, "get_set_cached") else api.get_set(set_id)


def local_id_filter(local_id: str | list[str]) -> str:
    """TCGdex turns an all-digit filter value into a number ('eq:004' -> 4), which
    never equals the string localId '004', so number searches found nothing. A
    list joined with '|' stays a list of strings, so always send at least two."""
    forms = [local_id] if isinstance(local_id, str) else list(local_id)
    if len(forms) == 1:
        forms *= 2
    return "eq:" + "|".join(forms)


def image_url(base: str | None, quality: str = "low") -> str | None:
    """TCGdex returns an image base URL without extension."""
    return f"{base}/{quality}.webp" if base else None
