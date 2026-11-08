"""A local card catalog for Find, search and close matches.

Every card of every TCGdex set in a language, fetched once per set with one GraphQL request and kept in the
catalog file (dexstash-catalog.db, index_sets and index_cards), with a full-text index of names and artists
(index_names, SQLite FTS5 with trigrams, so any part of a name matches as TCGdex's own filter does). Once a
language has every set, TCGdex.search_cards answers from here instead of asking TCGdex, and Find's rarity and
kind filters read the stored rarity instead of fetching cards one by one.

A language is fetched the first time someone searches in it, in the background at low priority (searches and
pages come first). The price update looks again each night: a set TCGdex lists more cards for, and any set not
fetched for INDEX_DAYS, is fetched again. Until a language is complete, searches ask TCGdex as before.
Nothing here has prices; those come from the price tables (stored prices, then TCGCSV's).
"""

import json
import logging
import re
import threading
import time
from datetime import timedelta

from . import clock, db, settings
from .display import fold

log = logging.getLogger("dexstash.index")

PAUSE = 1.0          # seconds between sets, so a first fetch never crowds TCGdex or the NAS
INDEX_DAYS = 60      # a set is fetched again after this, in case TCGdex corrected it
MAX_CARDS = 5000     # one search returns at most this many briefs (TCGdex's own lists are long too)
# More than tcgdex.GRAPHQL_FIELDS: what tells a Pokémon from a Trainer or an Energy. If TCGdex refuses these,
# the set is asked again with the fields it's known to take.
FIELDS = ("id localId name image rarity illustrator dexId types trainerType energyType category hp")
BASIC_FIELDS = "id localId name image rarity illustrator dexId types trainerType"


def enabled() -> bool:
    return settings.on("CARD_INDEX")


def _now() -> str:
    return clock.now()


_conn = None
_conn_lock = threading.Lock()


def _db():
    """The index's own connection (searches run on many threads, so statements take turns)."""
    global _conn
    with _conn_lock:
        if _conn is None:
            _conn = db.SharedConnection(db.connect())
            make_names(_conn)
        return _conn


def forget() -> None:
    """After Rebuild downloaded data: ask again which languages are complete."""
    _complete.clear()


def reset() -> None:
    """Forget the connection and what's known to be complete (tests, Rebuild downloaded data)."""
    global _conn
    with _conn_lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
        _conn = None
    _complete.clear()


_FTS = None


def make_names(conn) -> bool:
    """The full-text index of names, when this SQLite has FTS5 with trigrams (3.34 and later); else names are
    matched with LIKE, which finds the same cards a little slower."""
    global _FTS
    try:
        conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS catalog.index_names USING fts5(
                          folded, artist, lang UNINDEXED, id UNINDEXED, set_id UNINDEXED, tokenize = 'trigram')""")
        _FTS = True
    except Exception as e:
        log.info("no full-text index (%s): names are matched with LIKE", e)
        _FTS = False
    return _FTS


# --- what a card is ---------------------------------------------------------------------------------------

def kind_of(card: dict) -> str | None:
    """'pokemon', 'trainer' or 'energy', from the fields only one kind has (as search.card_kind does)."""
    if card.get("energyType"):
        return "energy"
    if card.get("trainerType"):
        return "trainer"
    if card.get("hp") or card.get("types") or card.get("dexId"):
        return "pokemon"
    from .search import card_kind
    if card.get("category"):
        return card_kind(card)
    return "energy" if card.get("name") and "energy" in fold(card["name"]) else None


def _row(lang: str, set_id: str, c: dict) -> tuple:
    return (lang, c["id"], set_id, str(c.get("localId") or ""), c.get("name") or "", fold(c.get("name")),
            c.get("image"), c.get("rarity"), c.get("illustrator"), kind_of(c),
            json.dumps(c.get("dexId")) if c.get("dexId") else None)


def store_set(conn, lang: str, set_id: str, cards: list[dict], listed: int | None = None) -> int:
    """Keep one set's cards (replacing what was kept). listed is how many cards TCGdex's set list says it has."""
    rows = [_row(lang, set_id, c) for c in cards if c.get("id")]
    with db.transaction(conn):
        conn.execute("DELETE FROM index_cards WHERE lang = ? AND set_id = ?", (lang, set_id))
        conn.executemany("""INSERT OR REPLACE INTO index_cards (lang, id, set_id, local_id, name, folded, image, rarity,
                              illustrator, kind, dex_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
        if _FTS:
            conn.execute("DELETE FROM index_names WHERE lang = ? AND set_id = ?", (lang, set_id))
            conn.executemany("INSERT INTO index_names (folded, artist, lang, id, set_id) VALUES (?, ?, ?, ?, ?)",
                             [(r[5], fold(r[8]), lang, r[1], set_id) for r in rows])
        conn.execute("INSERT OR REPLACE INTO index_sets (lang, set_id, cards, listed, fetched_at) VALUES (?, ?, ?, ?, ?)",
                     (lang, set_id, len(rows), listed, _now()))
    _complete.pop(lang, None)
    return len(rows)


def _fetch_set(api, set_id: str) -> list[dict] | None:
    """A set's cards with their details, by GraphQL; with the basic fields if TCGdex refuses the others; else
    the set's REST reply (names and numbers only, no rarity)."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", set_id or ""):
        return None
    from .tcgdex import BASE_URL
    for fields in (FIELDS, BASIC_FIELDS):
        try:
            resp = api.client.post(f"{BASE_URL}/graphql", params={"lang": api.lang},
                                   json={"query": f'{{ set(id: "{set_id}") {{ cards {{ {fields} }} }} }}'})
            resp.raise_for_status()
            body = resp.json()
            cards = ((body.get("data") or {}).get("set") or {}).get("cards")
            if not body.get("errors") and isinstance(cards, list):
                return cards
        except Exception as e:
            log.info("index: GraphQL for %s %s failed: %s", api.lang, set_id, e)
    try:
        return api.get_set(set_id).get("cards") or []
    except Exception as e:
        log.info("index: set %s %s not fetched: %s", api.lang, set_id, e)
        return None


def due(conn, lang: str, sets: list[dict]) -> list[dict]:
    """The sets of a language to fetch: never fetched, TCGdex lists more cards than are kept, or older than
    INDEX_DAYS. New sets first. TCG Pocket sets never are."""
    from .tcgdex import is_pocket
    sets = [s for s in sets if not is_pocket(s.get("id"))]
    have = {r["set_id"]: dict(r) for r in conn.execute("SELECT * FROM index_sets WHERE lang = ?", (lang,))}
    old = clock.since(timedelta(days=INDEX_DAYS))
    out = []
    for s in reversed(sets):  # TCGdex lists oldest first
        kept = have.get(s["id"])
        listed = (s.get("cardCount") or {}).get("total")
        if kept is None or (listed and listed > (kept["cards"] or 0) and listed != kept["listed"]) or kept["fetched_at"] < old:
            out.append(s)
    return out


FAILED_FOR = timedelta(days=2)  # 0.18.2: a set TCGdex wouldn't send doesn't keep a language "incomplete" this long


def _failed_key(lang: str) -> str:
    return f"card_index_failed:{lang}"


def failed(conn, lang: str) -> set[str]:
    """0.18.2: the sets of a language TCGdex wouldn't send in the last FAILED_FOR (tried again each fetch)."""
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (_failed_key(lang),)).fetchone()
    try:
        seen = json.loads(row[0]) if row else {}
    except ValueError:
        seen = {}
    since = clock.since(FAILED_FOR)
    return {set_id for set_id, at in seen.items() if at >= since}


def _note_failed(conn, lang: str, tried: set[str], bad: set[str]) -> None:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (_failed_key(lang),)).fetchone()
    try:
        seen = json.loads(row[0]) if row else {}
    except ValueError:
        seen = {}
    now = clock.now()
    seen = {k: v for k, v in seen.items() if k not in tried} | {k: seen.get(k, now) for k in bad}
    conn.execute("INSERT INTO app_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                 (_failed_key(lang), json.dumps(seen)))


def build(api, conn=None, sleep=time.sleep, limit: int | None = None) -> tuple[int, list[str]]:
    """Fetch the sets of api's language that are due. Returns (sets fetched, problems)."""
    from .tcgdex import is_pocket
    conn = conn or _db()
    make_names(conn)
    sets = [s for s in api.list_sets() if not is_pocket(s.get("id"))]
    fetched, errors, tried, bad = 0, [], set(), set()
    for s in due(conn, api.lang, sets)[:limit]:
        cards = _fetch_set(api, s["id"])
        tried.add(s["id"])
        if cards is None:
            errors.append(f"{api.lang} {s['id']}")
            bad.add(s["id"])
        else:
            store_set(conn, api.lang, s["id"], cards, (s.get("cardCount") or {}).get("total"))
            fetched += 1
        sleep(PAUSE)
    with db.transaction(conn):  # sets TCGdex no longer lists
        _note_failed(conn, api.lang, tried, bad)
        listed = {s["id"] for s in sets}
        for r in conn.execute("SELECT set_id FROM index_sets WHERE lang = ?", (api.lang,)).fetchall():
            if r["set_id"] not in listed:
                conn.execute("DELETE FROM index_cards WHERE lang = ? AND set_id = ?", (api.lang, r["set_id"]))
                conn.execute("DELETE FROM index_sets WHERE lang = ? AND set_id = ?", (api.lang, r["set_id"]))
                if _FTS:
                    conn.execute("DELETE FROM index_names WHERE lang = ? AND set_id = ?", (api.lang, r["set_id"]))
    _complete.pop(api.lang, None)
    if fetched or errors:
        log.info("card index %s: %d sets fetched, %d problems", api.lang, fetched, len(errors))
    return fetched, errors


def languages(conn) -> list[str]:
    """The languages the index has started, for the nightly look."""
    return [r[0] for r in conn.execute("SELECT DISTINCT lang FROM index_sets")]


def refresh(conn, api_for) -> list[str]:
    """The price update's turn: every language already in the index. Returns problems."""
    if not enabled():
        return []
    errors = []
    for lang in languages(conn):
        try:
            errors += build(api_for(lang), conn)[1]
        except Exception as e:
            log.warning("card index %s: %s", lang, e)
            errors.append(f"card index {lang}: {e}")
    if not errors:
        return []
    return [f"Card catalog: {len(errors)} set{'s' if len(errors) != 1 else ''} not fetched; tried again next time."]


# --- background fetching --------------------------------------------------------------------------------

runner = None  # the app's jobs.Jobs (web.py sets it)


def _key(lang: str) -> str:
    return f"card-index:{lang}"


def building() -> set[str]:
    """The languages being fetched now, or waiting their turn."""
    if runner is None:
        return set()
    return {r[0].partition(":")[2] for r in runner.conn.execute(
        "SELECT key FROM jobs WHERE key LIKE 'card-index:%' AND status IN ('waiting', 'running')").fetchall()}


def start(api, priority: int = 0, wait: bool = False) -> bool:
    """Fetch a language as a job ('card-index:<lang>'), one language at a time with the rest of the jobs."""
    if not enabled() or runner is None:
        return False
    from .card_pictures import LANG_NAMES

    def run(conn, job):
        fetched, errors = build(api, sleep=lambda s: (job.progress(job.done + 1), time.sleep(s)))
        return (f"{fetched} set{'' if fetched == 1 else 's'} added to Find's card catalog"
                + (f"; {len(errors)} not fetched, tried again next time." if errors else "."))
    name = LANG_NAMES.get(api.lang, api.lang)
    return runner.start(_key(api.lang), f"Downloading Find's card catalog ({name})", run, priority=priority, wait=wait)


# --- searching -------------------------------------------------------------------------------------------

_complete: dict[str, tuple[float, bool]] = {}
COMPLETE_FOR = 300  # seconds a "this language is complete" answer is reused


def complete(conn, api) -> bool:
    """Every set TCGdex lists in api's language is in the index (0.18.2: or wouldn't send lately, so one broken
    set doesn't send every search to TCGdex). Starts fetching what's missing when not."""
    hit = _complete.get(api.lang)
    if hit and time.monotonic() - hit[0] < COMPLETE_FOR:
        return hit[1]
    from .tcgdex import is_pocket
    have = {r[0] for r in conn.execute("SELECT set_id FROM index_sets WHERE lang = ?", (api.lang,))}
    wanted = {s["id"] for s in api.list_sets() if not is_pocket(s.get("id"))}
    ok = bool(wanted) and bool(have) and wanted - have <= failed(conn, api.lang)
    _complete[api.lang] = (time.monotonic(), ok)
    if not ok:
        start(api)
    return ok


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search(api, local_id: str | list[str] | None = None, name: str | None = None,
           illustrator: str | None = None, partial: bool = False) -> list[dict] | None:
    """Brief cards like TCGdex.search_cards gives (id, localId, name, image), plus rarity and kind, from the
    index. None when the index can't answer yet for this language (then TCGdex is asked). partial (0.18.2): answer
    from whatever is kept, for when TCGdex didn't answer; None only when nothing of the language is kept."""
    if not enabled() or not (local_id or name or illustrator):
        return None
    try:
        conn = _db()
        if partial:
            if not conn.execute("SELECT 1 FROM index_sets WHERE lang = ? LIMIT 1", (api.lang,)).fetchone():
                return None
        elif not complete(conn, api):
            return None
        where, args = ["c.lang = ?"], [api.lang]
        if local_id:
            forms = [local_id] if isinstance(local_id, str) else list(local_id)
            where.append(f"c.local_id IN ({', '.join('?' * len(forms))})")
            args += forms
        for column, text in (("folded", name), ("artist", illustrator)):
            if not text:
                continue
            wanted = fold(text)
            if _FTS and len(wanted) >= 3:
                where.append(f"c.id IN (SELECT id FROM index_names WHERE lang = ? AND {column} LIKE ? ESCAPE '\\')")
                args += [api.lang, _like(wanted)]
            else:
                where.append(f"{'c.folded' if column == 'folded' else 'lower(c.illustrator)'} LIKE ? ESCAPE '\\'")
                args.append(_like(wanted))
        rows = conn.execute(f"""SELECT c.id, c.local_id, c.name, c.image, c.rarity, c.kind FROM index_cards c
                                WHERE {' AND '.join(where)} LIMIT {MAX_CARDS}""", args)
        return [{"id": r["id"], "localId": r["local_id"], "name": r["name"], "image": r["image"],
                 "rarity": r["rarity"], "_kind": r["kind"]} for r in rows]
    except Exception as e:  # never in the way of a search: TCGdex answers instead
        log.info("card index not used (%s): %s", api.lang, e)
        return None


def status(conn) -> list[dict]:
    """For Data sources: per language, sets and cards kept and when last fetched."""
    return [dict(r) for r in conn.execute("""SELECT lang, COUNT(*) AS sets, SUM(cards) AS cards, MAX(fetched_at) AS at
                                             FROM index_sets GROUP BY lang ORDER BY lang""")]
