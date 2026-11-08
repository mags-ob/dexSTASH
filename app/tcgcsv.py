"""TCGplayer prices from TCGCSV: tcgcsv.com republishes TCGplayer's own data once a day as one
file per set, free and with no account. English (category 3) and Japanese (category 85) only; no Chinese.

- Weekly: the set lists of both categories, and which of our sets is which TCGplayer set (by printed code,
  then by name). Sets that don't match are counted on Data sources, never guessed.
- Once per set (and weekly after): its products, matched to our cards by number, so every card in the set gets
  its TCGplayer product (id and picture), Japanese sets included.
- Nightly, before TCGdex's prices: one prices file per set that has cards you have, chase or follow. Its market
  price becomes the card's main price for each printing, with the low and mid as the range.

Only tcgcsv.com is contacted, with a clear User-Agent and a pause between requests, as its FAQ asks.
"""

import json
import logging
import re
import sqlite3
import time
import unicodedata
from datetime import timedelta
from urllib.parse import urlparse

from . import clock

log = logging.getLogger("dexstash.tcgcsv")

BASE = "https://tcgcsv.com/tcgplayer"
HOSTS = {"tcgcsv.com"}
PAUSE = 0.25
CATEGORIES = {"en": 3, "ja": 85}
GROUPS_EVERY = timedelta(days=7)
PRODUCTS_EVERY = timedelta(days=7)
SUBTYPES = {"normal": "normal", "unlimited": "normal", "holofoil": "holo", "unlimited holofoil": "holo",
            "reverse holofoil": "reverse", "1st edition": "first_ed", "1st edition normal": "first_ed",
            "1st edition holofoil": "first_ed"}


def _now() -> str:
    return clock.now()


def fetch(url: str) -> dict:
    """One tcgcsv.com file as JSON. Never another host, redirects included."""
    from . import sites
    parts = urlparse(url)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in HOSTS:
        raise ValueError(f"Not a TCGCSV address: {url}")
    with sites.client(timeout=30, follow_redirects=False) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()


def _results(body) -> list[dict]:
    if isinstance(body, dict):
        return [r for r in body.get("results") or [] if isinstance(r, dict)]
    return [r for r in body or [] if isinstance(r, dict)]


def _norm(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", text)


def _plain_name(name: str | None) -> str:
    """'SV08: Surging Sparks' -> 'surgingsparks'; 'Scarlet & Violet 151' stays."""
    return _norm(re.sub(r"^[A-Za-z0-9.\- ]{1,8}:\s*", "", name or ""))


def _number(text: str | None) -> str:
    """'001/083' -> '1'; 'TG05/TG30' -> 'TG5'; '177a' -> '177A'."""
    head = (text or "").split("/")[0].strip().upper()
    m = re.fullmatch(r"([A-Z\-]*)0*(\d+)([A-Z]?)", head)
    return f"{m.group(1)}{m.group(2)}{m.group(3)}" if m else head


def _meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO app_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                 (key, value))


def _older(stamp: str | None, age: timedelta) -> bool:
    return not stamp or stamp < clock.since(age)


# --- sets -------------------------------------------------------------------------------

def load_groups(conn: sqlite3.Connection, get=fetch, sleep=time.sleep) -> int:
    """Both categories' set lists. Returns how many sets were listed."""
    count = 0
    for lang, category in CATEGORIES.items():
        groups = _results(get(f"{BASE}/{category}/groups"))
        sleep(PAUSE)
        conn.execute("DELETE FROM tcgcsv_groups WHERE category = ?", (category,))
        conn.executemany("INSERT OR REPLACE INTO tcgcsv_groups (category, group_id, name, abbreviation, published_on) "
                         "VALUES (?, ?, ?, ?, ?)",
                         [(category, int(g["groupId"]), g.get("name") or "", g.get("abbreviation") or "", g.get("publishedOn"))
                          for g in groups if str(g.get("groupId", "")).isdigit()])
        count += len(groups)
    _set_meta(conn, "tcgcsv_groups_at", _now())
    conn.commit()
    return count


def _code(text: str | None) -> str:
    """A set code to compare: _norm, with zeros in front of a number dropped ('SWSH05' and 'swsh5' are the same)."""
    return re.sub(r"(?<![0-9])0+(?=[0-9])", "", _norm(text))


def _choose(groups: list[dict], tcgdex_id: str, codes: set[str], set_name: str | None) -> tuple[dict | None, str | None]:
    """(group, how) for one of our sets among a category's groups, or (None, None). Its TCGdex id as the group's
    abbreviation first; else a printed code (from the scanner's list) or the same name, one candidate only. Round 28:
    when a printed code and the name point at two different groups, the name wins (Battle Styles' code BST is
    another TCGplayer set's abbreviation)."""
    by_id = [g for g in groups if _code(g["abbreviation"]) == _code(tcgdex_id)]
    if len(by_id) == 1:
        return by_id[0], "code"
    by_code = [g for g in groups if _code(g["abbreviation"]) in {_code(c) for c in codes}]
    by_name = [g for g in groups if set_name and _plain_name(g["name"]) == _plain_name(set_name)]
    if len(by_name) == 1 and (len(by_code) != 1 or by_code[0]["group_id"] != by_name[0]["group_id"]):
        return by_name[0], "name"
    if len(by_code) == 1:
        return by_code[0], "code"
    return None, None


def _codes(set_id: str) -> set[str]:
    from .scanner import SET_CODES
    return {code for code, ids in SET_CODES.items() if set_id in ids}


def match_sets(conn: sqlite3.Connection, set_ids: list[str] | None = None) -> dict:
    """Which TCGplayer set each of our English and Japanese sets is (_choose; never a guess). A match fixed by the
    cards' own product ids (matched_by 'product', round 28) is kept while its group is listed. set_ids: only
    those sets (a set just stored). Returns counts."""
    groups = {cat: [dict(r) for r in conn.execute("SELECT * FROM tcgcsv_groups WHERE category = ?", (cat,))]
              for cat in CATEGORIES.values()}
    fixed = {r[0] for r in conn.execute("SELECT t.set_id FROM tcgcsv_sets t JOIN tcgcsv_groups g ON g.group_id = t.group_id "
                                        "AND g.category = t.category WHERE t.matched_by IN ('product', 'you')")}
    sql = "SELECT set_id, language, name FROM sets WHERE language IN ('en', 'ja')"
    rows = conn.execute(sql).fetchall() if set_ids is None else \
        conn.execute(sql + f" AND set_id IN ({','.join('?' * len(set_ids))})", set_ids).fetchall()
    matched = unmatched = 0
    changed: list[str] = []
    for s in rows:
        if s["set_id"] in fixed:
            matched += 1
            continue
        category = CATEGORIES[s["language"]]
        tcgdex_id = s["set_id"].split(":", 1)[1] if ":" in s["set_id"] else s["set_id"]
        found, how = _choose(groups[category], tcgdex_id, _codes(s["set_id"]), s["name"])
        old = conn.execute("SELECT group_id FROM tcgcsv_sets WHERE set_id = ?", (s["set_id"],)).fetchone()
        if found:
            conn.execute("INSERT INTO tcgcsv_sets (set_id, category, group_id, matched_by) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT (set_id) DO UPDATE SET category = excluded.category, group_id = excluded.group_id, "
                         "matched_by = excluded.matched_by", (s["set_id"], category, found["group_id"], how))
            if old and old[0] != found["group_id"]:  # another set now: its products are read again
                _rematched(conn, s["set_id"])
                changed.append(s["set_id"])
            matched += 1
        else:
            conn.execute("DELETE FROM tcgcsv_sets WHERE set_id = ?", (s["set_id"],))
            unmatched += 1
    conn.commit()
    return {"matched": matched, "unmatched": unmatched, "changed": changed}


def _rematched(conn: sqlite3.Connection, set_id: str) -> None:
    """A set matched to another TCGplayer set: its products and prices are read again at the next read, and the
    products the old match gave its cards are dropped (their TCGplayer price and link)."""
    from . import prices
    conn.execute("UPDATE tcgcsv_sets SET products_at = NULL, prices_at = NULL WHERE set_id = ?", (set_id,))
    for r in conn.execute("SELECT c.id, c.pricing_json FROM cards c JOIN tcgcsv_cards t ON t.card_id = c.id AND t.variant = '' "
                          "AND t.product_id = c.tcgplayer_id WHERE c.set_id = ?", (set_id,)).fetchall():
        if not prices.product_ids(r["pricing_json"]):  # the old match's product (G3), not TCGdex's
            conn.execute("UPDATE cards SET tcgplayer_id = NULL WHERE id = ?", (r["id"],))
    ids = [r[0] for r in conn.execute("SELECT id FROM cards WHERE set_id = ?", (set_id,))]
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        marks = ",".join("?" * len(part))
        conn.execute(f"DELETE FROM tcgcsv_cards WHERE card_id IN ({marks})", part)
        conn.execute(f"DELETE FROM tcgcsv_prices WHERE card_id IN ({marks})", part)
        conn.execute(f"DELETE FROM price_quotes WHERE source = 'tcgplayer' AND basis = 'tcgcsv' AND card_id IN ({marks})", part)


def unmatched_sets(conn: sqlite3.Connection, kept_only: bool = False) -> list[dict]:
    """Our English and Japanese sets with no TCGplayer set, for Data sources (kept_only: sets you keep cards from)."""
    from . import prices
    kept = f" AND set_id IN (SELECT set_id FROM cards WHERE id IN ({prices.KEPT_SQL}))" if kept_only else ""
    return [dict(r) for r in conn.execute(
        "SELECT set_id, language, name FROM sets WHERE language IN ('en', 'ja') "
        f"AND set_id NOT IN (SELECT set_id FROM tcgcsv_sets){kept} ORDER BY language, name")]


def groups(conn: sqlite3.Connection, lang: str) -> list[dict]:
    """A language's TCGplayer sets, newest first, for picking a set's match by hand."""
    return [dict(r) for r in conn.execute("SELECT group_id, name, abbreviation FROM tcgcsv_groups WHERE category = ? "
                                          "ORDER BY published_on DESC, name", (CATEGORIES.get(lang, 0),))]


def pick(conn: sqlite3.Connection, set_id: str, group_id: int) -> bool:
    """Round 28: a set's TCGplayer set picked by hand (matched_by 'you', kept by match_sets). False when either is
    unknown."""
    row = conn.execute("SELECT language FROM sets WHERE set_id = ?", (set_id,)).fetchone()
    category = CATEGORIES.get(row[0]) if row else None
    if category is None or not conn.execute("SELECT 1 FROM tcgcsv_groups WHERE category = ? AND group_id = ?",
                                            (category, group_id)).fetchone():
        return False
    old = conn.execute("SELECT group_id FROM tcgcsv_sets WHERE set_id = ?", (set_id,)).fetchone()
    if old and old[0] != group_id:
        _rematched(conn, set_id)
    conn.execute("INSERT INTO tcgcsv_sets (set_id, category, group_id, matched_by) VALUES (?, ?, ?, 'you') "
                 "ON CONFLICT (set_id) DO UPDATE SET category = excluded.category, group_id = excluded.group_id, "
                 "matched_by = 'you', products_at = NULL, prices_at = NULL", (set_id, category, group_id))
    conn.commit()
    return True


def match_by_products(conn: sqlite3.Connection, get=fetch, sleep=time.sleep) -> int:
    """Round 28: sets you keep cards from that matched no TCGplayer set by code or name, tried against TCGplayer sets
    whose name holds theirs (or the other way round): the one listing most of the products TCGdex names for the
    set's cards is the match (matched_by 'product'). At most 4 sets read for each. Returns sets matched."""
    found = 0
    for s in unmatched_sets(conn, kept_only=True):
        known = known_products(conn, s["set_id"])
        mine = _plain_name(s["name"])
        if not known or not mine:
            continue
        category = CATEGORIES[s["language"]]
        candidates = [dict(g) for g in conn.execute("SELECT group_id, name FROM tcgcsv_groups WHERE category = ?", (category,))
                      if _plain_name(g["name"]) and (mine in _plain_name(g["name"]) or _plain_name(g["name"]) in mine)]
        for g in candidates[:4]:
            try:
                theirs = _results(get(f"{BASE}/{category}/{g['group_id']}/products"))
            except Exception as e:
                log.info("TCGCSV set %s not read: %s", g["group_id"], e)
                break
            sleep(PAUSE)
            ids = {int(p["productId"]) for p in theirs if str(p.get("productId", "")).isdigit()}
            if len(known.keys() & ids) * 2 >= len(known):
                conn.execute("INSERT OR REPLACE INTO tcgcsv_sets (set_id, category, group_id, matched_by) VALUES (?, ?, ?, 'product')",
                             (s["set_id"], category, g["group_id"]))
                found += 1
                break
    conn.commit()
    return found


# --- products: our cards' TCGplayer products -------------------------------------------------

def _extended(product: dict, key: str) -> str | None:
    for e in product.get("extendedData") or []:
        if isinstance(e, dict) and (e.get("name") or "").lower() == key.lower():
            return e.get("value")
    return None


# A product listed apart for one printing, by the words in its name: TCGplayer's "(Poke Ball Pattern)". Other
# patterns ("(Quick Ball Pattern)", "(Energy Symbol Pattern)" in Japanese sets) are printings we don't track.
PATTERNS = {"pokeballpattern": "pokeball", "masterballpattern": "masterball"}
UNKNOWN_SUBTYPES: dict[str, int] = {}  # TCGplayer printing names not in SUBTYPES, skipped and counted


def _pattern(name: str | None) -> str | None:
    plain = _norm(name)
    return next((v for words, v in PATTERNS.items() if words in plain), None)


def _patterned(name: str | None) -> bool:
    """Any "(... Pattern)" product, known or not."""
    return bool(re.search(r"\(\s*[^()]*\bpattern\s*\)", name or "", re.I))


def _url(p: dict) -> str | None:
    url = p.get("url")
    return url if isinstance(url, str) and url.startswith("https://") else None


def known_products(conn: sqlite3.Connection, set_id: str) -> dict[int, str]:
    """product id -> our card, for a set's cards whose TCGplayer product TCGdex names (round 28)."""
    from . import prices
    out: dict[int, str] = {}
    for r in conn.execute("SELECT id, tcgplayer_id, pricing_json FROM cards WHERE set_id = ?", (set_id,)):
        for pid in {r["tcgplayer_id"], *prices.product_ids(r["pricing_json"])}:
            if pid:
                out.setdefault(int(pid), r["id"])
    return out


def _check_group(conn: sqlite3.Connection, set_id: str, products: list[dict], get) -> list[dict]:
    """Round 28: a set's match checked against the products TCGdex names for its cards. When none of them is in
    the matched TCGplayer set but another set of the same code or name has them, that one is the match from now
    on (matched_by 'product') and its products are returned. Otherwise the products as they are."""
    known = known_products(conn, set_id)
    ids = {int(p["productId"]) for p in products if str(p.get("productId", "")).isdigit()}
    if not known or len(known.keys() & ids) * 2 >= len(known):
        return products
    row = conn.execute("SELECT s.language, s.name, t.category, t.group_id FROM sets s JOIN tcgcsv_sets t ON t.set_id = s.set_id "
                       "WHERE s.set_id = ?", (set_id,)).fetchone()
    if row is None:
        return products
    tcgdex_id = set_id.split(":", 1)[1] if ":" in set_id else set_id
    codes = {_code(c) for c in _codes(set_id)} | {_code(tcgdex_id)}
    others = [dict(g) for g in conn.execute("SELECT group_id, name, abbreviation FROM tcgcsv_groups WHERE category = ? "
                                            "AND group_id != ?", (row["category"], row["group_id"]))
              if _code(g["abbreviation"]) in codes or _plain_name(g["name"]) == _plain_name(row["name"])
              or _plain_name(row["name"]) and _plain_name(row["name"]) in _plain_name(g["name"])]
    best, best_hits = None, len(known.keys() & ids)
    for g in others[:4]:
        theirs = _results(get(f"{BASE}/{row['category']}/{g['group_id']}/products"))
        hits = len(known.keys() & {int(p["productId"]) for p in theirs if str(p.get("productId", "")).isdigit()})
        if hits > best_hits:
            best, best_hits, best_products = g, hits, theirs
    if best is None:
        return products
    log.info("TCGCSV: %s matched to %s (%s) by its cards' products, not %s", set_id, best["group_id"], best["name"], row["group_id"])
    _rematched(conn, set_id)
    conn.execute("UPDATE tcgcsv_sets SET group_id = ?, matched_by = 'product' WHERE set_id = ?", (best["group_id"], set_id))
    theirs = {int(p["productId"]) for p in best_products if str(p.get("productId", "")).isdigit()}
    from . import prices
    for r in conn.execute("SELECT id, tcgplayer_id, pricing_json FROM cards WHERE set_id = ? AND tcgplayer_id IS NOT NULL",
                          (set_id,)).fetchall():  # a product the old match gave a card (G3), not TCGdex's: dropped
        if r["tcgplayer_id"] not in theirs and r["tcgplayer_id"] not in prices.product_ids(r["pricing_json"]):
            conn.execute("UPDATE cards SET tcgplayer_id = NULL WHERE id = ?", (r["id"],))
    return best_products


def load_products(conn: sqlite3.Connection, set_id: str, get=fetch) -> int:
    """Match a set's TCGplayer products to our cards. Round 28: the product TCGdex names for a card first; else by
    number. A Poké Ball or Master Ball pattern product is that printing's (variant pokeball / masterball); of the
    rest, a number with two products (a card and its variant listed apart) matches the one with the card's English
    name, else neither. The set's match is checked against those product ids first (_check_group). Returns cards
    matched."""
    row = conn.execute("SELECT category, group_id FROM tcgcsv_sets WHERE set_id = ?", (set_id,)).fetchone()
    if row is None:
        return 0
    products = _check_group(conn, set_id, _results(get(f"{BASE}/{row['category']}/{row['group_id']}/products")), get)
    row = conn.execute("SELECT category, group_id FROM tcgcsv_sets WHERE set_id = ?", (set_id,)).fetchone()
    by_id = {int(p["productId"]): p for p in products if str(p.get("productId", "")).isdigit()}
    known = known_products(conn, set_id)
    by_number: dict[str, list[dict]] = {}
    for p in products:
        number = _extended(p, "Number")
        if number and str(p.get("productId", "")).isdigit():
            by_number.setdefault(_number(number), []).append(p)
    named = {}
    for pid, card_id in known.items():
        if pid in by_id and not _patterned(by_id[pid].get("name")):
            named.setdefault(card_id, by_id[pid])
    matched = 0
    for card in conn.execute("SELECT id, number, name, english_name FROM cards WHERE set_id = ?", (set_id,)).fetchall():
        conn.execute("DELETE FROM tcgcsv_cards WHERE card_id = ?", (card["id"],))
        everything = by_number.get(_number(card["number"])) or []
        patterns = [p for p in everything if _pattern(p.get("name"))]
        options = [p for p in everything if not _patterned(p.get("name"))]
        for p in patterns:
            conn.execute("INSERT OR REPLACE INTO tcgcsv_cards (card_id, variant, product_id, group_id, image_url, url, modified_on) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", (card["id"], _pattern(p.get("name")), int(p["productId"]), row["group_id"],
                                                          p.get("imageUrl"), _url(p), p.get("modifiedOn")))
        if card["id"] in named:
            options = [named[card["id"]]]
        elif len(options) > 1:
            english = _norm(card["english_name"] or card["name"])
            exact = [p for p in options if _norm(p.get("name")) == english]
            options = exact or [p for p in options if _norm(re.sub(r"\s*[-(].*$", "", p.get("name") or "")) == english]
        if len(options) != 1:
            matched += bool(patterns)
            continue
        p = options[0]
        conn.execute("INSERT OR REPLACE INTO tcgcsv_cards (card_id, variant, product_id, group_id, image_url, url, modified_on) "
                     "VALUES (?, '', ?, ?, ?, ?, ?)",
                     (card["id"], int(p["productId"]), row["group_id"], p.get("imageUrl"), _url(p), p.get("modifiedOn")))
        # G3: the card's TCGplayer product (its picture and link) when TCGdex had none
        conn.execute("UPDATE cards SET tcgplayer_id = ?, picture_checked_at = CASE WHEN image_url IS NULL AND saved_picture IS NULL "
                     "THEN NULL ELSE picture_checked_at END WHERE id = ? AND tcgplayer_id IS NULL", (int(p["productId"]), card["id"]))
        matched += 1
    conn.execute("UPDATE tcgcsv_sets SET products_at = ? WHERE set_id = ?", (_now(), set_id))
    conn.commit()
    return matched


# --- prices --------------------------------------------------------------------------

def _price(value) -> float | None:
    return round(float(value), 2) if isinstance(value, (int, float)) and value > 0 else None


def load_prices(conn: sqlite3.Connection, set_id: str, get=fetch) -> int:
    """A set's TCGplayer prices for every matched card, per printing. A good read replaces the set's prices, so a
    printing TCGplayer stopped listing loses its price; a failed (or empty) read keeps the last ones. Kept cards
    get them as TCGplayer quotes (basis 'tcgcsv'): the market price, else the mid. Returns cards priced."""
    from . import prices
    row = conn.execute("SELECT category, group_id FROM tcgcsv_sets WHERE set_id = ?", (set_id,)).fetchone()
    if row is None:
        return 0
    products = {r["product_id"]: dict(r) for r in conn.execute(
        "SELECT card_id, variant, product_id, url, modified_on FROM tcgcsv_cards "
        "WHERE card_id IN (SELECT id FROM cards WHERE set_id = ?)", (set_id,))}
    results = _results(get(f"{BASE}/{row['category']}/{row['group_id']}/prices"))  # raises on a failed read
    stamp = _now()
    found: dict[tuple[str, str], tuple] = {}
    for p in results:
        product = products.get(p.get("productId"))
        if product is None:
            continue
        subtype = (p.get("subTypeName") or "").strip().lower()
        variant = product["variant"] or SUBTYPES.get(subtype)
        if not variant:
            UNKNOWN_SUBTYPES[subtype] = UNKNOWN_SUBTYPES.get(subtype, 0) + 1
            continue
        market = _price(p.get("marketPrice")) or _price(p.get("midPrice")) or _price(p.get("lowPrice"))
        if market is None:
            continue
        key = (product["card_id"], variant)
        if key in found and found[key][0] >= market:  # a pattern product with two printings: the dearer
            continue
        found[key] = (market, _price(p.get("lowPrice")), _price(p.get("midPrice")), product)
    if not found:
        return 0
    cards = sorted({k[0] for k in found} | {p["card_id"] for p in products.values()})
    marks = ",".join("?" * len(cards))
    kept = {r[0] for r in conn.execute(f"SELECT card_id FROM ({prices.KEPT_SQL}) WHERE card_id IN ({marks})", cards)}
    conn.execute(f"DELETE FROM tcgcsv_prices WHERE card_id IN ({marks})", cards)
    conn.execute(f"DELETE FROM price_quotes WHERE source = 'tcgplayer' AND basis = 'tcgcsv' AND card_id IN ({marks})", cards)
    for (card_id, variant), (market, low, mid, product) in found.items():
        conn.execute("INSERT INTO tcgcsv_prices (card_id, variant, market, low, mid, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (card_id, variant, market, low, mid, stamp))
        if card_id in kept:
            conn.execute("INSERT OR REPLACE INTO price_quotes (card_id, variant, source, price, currency, low, mid, basis, ref, "
                         "updated_at, source_updated_at, url) VALUES (?, ?, 'tcgplayer', ?, 'USD', ?, ?, 'tcgcsv', ?, ?, ?, ?)",
                         (card_id, variant, market, low, mid, product["product_id"], stamp, product["modified_on"], product["url"]))
    conn.execute("UPDATE tcgcsv_sets SET prices_at = ? WHERE set_id = ?", (stamp, set_id))
    conn.commit()
    return len({k[0] for k in found})


def for_card(conn: sqlite3.Connection, card_id: str) -> dict[str, dict]:
    """printing -> {market, low, mid, updated_at} from the last run."""
    return {r["variant"]: dict(r) for r in conn.execute(
        "SELECT variant, market, low, mid, updated_at FROM tcgcsv_prices WHERE card_id = ?", (card_id,))}


def covered(conn: sqlite3.Connection, card_id: str) -> bool:
    return conn.execute("SELECT 1 FROM tcgcsv_prices WHERE card_id = ? LIMIT 1", (card_id,)).fetchone() is not None


# --- any set, by its TCGdex id ------------------------------------------------------

PRICES_EVERY = timedelta(hours=20)


def ensure_groups(conn: sqlite3.Connection, get=fetch, sleep=time.sleep) -> None:
    """The set lists, when they're a week old or missing (the nightly run reads them too)."""
    if _older(_meta(conn, "tcgcsv_groups_at"), GROUPS_EVERY):
        load_groups(conn, get, sleep)
        match_sets(conn)


def group_for(conn: sqlite3.Connection, lang: str, tcgdex_set_id: str, set_name: str | None = None) -> tuple[int, int] | None:
    """(category, group id) of a TCGdex set, stored or not: the stored match, else _choose. Never a guess."""
    category = CATEGORIES.get(lang)
    if category is None or not tcgdex_set_id:
        return None
    ours = tcgdex_set_id if lang == "en" else f"{lang}:{tcgdex_set_id}"
    row = conn.execute("SELECT category, group_id FROM tcgcsv_sets WHERE set_id = ?", (ours,)).fetchone()
    if row:
        return row["category"], row["group_id"]
    groups = [dict(r) for r in conn.execute("SELECT group_id, name, abbreviation FROM tcgcsv_groups WHERE category = ?", (category,))]
    found, _ = _choose(groups, tcgdex_set_id, _codes(ours), set_name)
    return (category, found["group_id"]) if found else None


def read_group(conn: sqlite3.Connection, category: int, group_id: int, get=fetch, sleep=time.sleep) -> bool:
    """A set's products (weekly) and prices (daily) into tcgcsv_products. Returns whether anything was read."""
    row = conn.execute("SELECT products_at, prices_at FROM tcgcsv_group_reads WHERE group_id = ?", (group_id,)).fetchone()
    products_due = _older(row["products_at"] if row else None, PRODUCTS_EVERY)
    prices_due = _older(row["prices_at"] if row else None, PRICES_EVERY)
    if not products_due and not prices_due:
        return False
    conn.execute("INSERT OR IGNORE INTO tcgcsv_group_reads (group_id, category) VALUES (?, ?)", (group_id, category))
    if products_due:
        products = _results(get(f"{BASE}/{category}/{group_id}/products"))
        conn.execute("DELETE FROM tcgcsv_products WHERE group_id = ?", (group_id,))
        conn.executemany("INSERT OR REPLACE INTO tcgcsv_products (category, group_id, product_id, number, name, image_url) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         [(category, group_id, int(p["productId"]), _number(_extended(p, "Number")), p.get("name"), p.get("imageUrl"))
                          for p in products if str(p.get("productId", "")).isdigit() and _extended(p, "Number")])
        conn.execute("UPDATE tcgcsv_group_reads SET products_at = ? WHERE group_id = ?", (_now(), group_id))
        sleep(PAUSE)
    best: dict[int, float] = {}
    each = []  # every printing's price, kept from 0.20.0 for showing per printing later
    for p in _results(get(f"{BASE}/{category}/{group_id}/prices")):
        price = _price(p.get("marketPrice")) or _price(p.get("midPrice")) or _price(p.get("lowPrice"))
        if price is not None and isinstance(p.get("productId"), int):
            best[p["productId"]] = max(best.get(p["productId"], 0), price)
            each.append((group_id, p["productId"], (p.get("subTypeName") or "").strip().lower(), price, _price(p.get("lowPrice"))))
    conn.executemany("UPDATE tcgcsv_products SET market = ? WHERE group_id = ? AND product_id = ?",
                     [(price, group_id, pid) for pid, price in best.items()])
    if each:
        conn.execute("DELETE FROM tcgcsv_product_prices WHERE group_id = ?", (group_id,))
        conn.executemany("INSERT OR REPLACE INTO tcgcsv_product_prices (group_id, product_id, subtype, market, low) "
                         "VALUES (?, ?, ?, ?, ?)", each)
    conn.execute("UPDATE tcgcsv_group_reads SET prices_at = ? WHERE group_id = ?", (_now(), group_id))
    conn.commit()
    return True


def product_for(conn: sqlite3.Connection, group_id: int, number: str | None, english: str | None = None) -> dict | None:
    """The one product with this number (the one with the English name when there are two), else None."""
    options = [dict(r) for r in conn.execute("SELECT * FROM tcgcsv_products WHERE group_id = ? AND number = ?",
                                             (group_id, _number(number))) if not _patterned(r["name"])]
    if len(options) > 1 and english:
        exact = [p for p in options if _norm(p["name"]) == _norm(english)]
        options = exact or [p for p in options if _norm(re.sub(r"\s*[-(].*$", "", p["name"] or "")) == _norm(english)]
    return options[0] if len(options) == 1 else None


def known(conn: sqlite3.Connection, group_id: int) -> bool:
    row = conn.execute("SELECT prices_at FROM tcgcsv_group_reads WHERE group_id = ?", (group_id,)).fetchone()
    return bool(row and row["prices_at"])


# --- the nightly run -------------------------------------------------------------------------

def run(conn: sqlite3.Connection, get=fetch, sleep=time.sleep, resolve: bool = True) -> tuple[int, list[str]]:
    """Before TCGdex's prices: set lists (weekly), products (new sets, then weekly) and prices of every set that
    has a kept card (read_sets). Returns (sets read, errors)."""
    from . import prices
    errors: list[str] = []
    try:
        if _older(_meta(conn, "tcgcsv_groups_at"), GROUPS_EVERY):
            load_groups(conn, get, sleep)
            match_sets(conn)
        if _older(_meta(conn, "tcgcsv_product_match_at"), GROUPS_EVERY):  # weekly, with the set lists
            match_by_products(conn, get, sleep)
            _set_meta(conn, "tcgcsv_product_match_at", _now())
    except Exception as e:
        log.warning("TCGCSV set lists not loaded: %s", e)
        errors.append(f"TCGCSV set lists: {e}")
        if not conn.execute("SELECT 1 FROM tcgcsv_sets LIMIT 1").fetchone():
            return 0, errors
    sets = [r[0] for r in conn.execute(
        f"""SELECT DISTINCT c.set_id FROM cards c JOIN tcgcsv_sets t ON t.set_id = c.set_id
            WHERE c.id IN ({prices.KEPT_SQL}) ORDER BY c.set_id""")]
    done, errors_sets = read_sets(conn, sets, get, sleep, resolve)
    errors += errors_sets
    _set_meta(conn, "tcgcsv_last_run", json.dumps({"at": _now(), "sets": done, "errors": len(errors)}))
    conn.commit()
    log.info("TCGCSV: %d sets read, %d problems", done, len(errors))
    return done, errors


def read_sets(conn: sqlite3.Connection, sets: list[str], get=fetch, sleep=time.sleep,
              resolve: bool = True) -> tuple[int, list[str]]:
    """Read these sets (matched ones only): products when new or a week old, then prices. Then the kept cards
    of every set read get their history rows, and their prices worked out again unless resolve is False (the
    caller does that once, at its end). Returns (sets read, errors)."""
    from . import db, prices
    done, errors, read = 0, [], []
    for set_id in sets:
        when = conn.execute("SELECT products_at FROM tcgcsv_sets WHERE set_id = ?", (set_id,)).fetchone()
        if when is None:
            continue
        try:
            if _older(when[0], PRODUCTS_EVERY):
                load_products(conn, set_id, get)
                sleep(PAUSE)
            load_prices(conn, set_id, get)
            sleep(PAUSE)
            done += 1
            read.append(set_id)
        except Exception as e:
            log.warning("TCGCSV %s not read: %s", set_id, e)
            errors.append(f"TCGCSV {set_id}: {e}")
    if UNKNOWN_SUBTYPES:
        log.info("TCGCSV printings not known, skipped: %s", ", ".join(f"{k or '(none)'} ×{n}" for k, n in UNKNOWN_SUBTYPES.items()))
        UNKNOWN_SUBTYPES.clear()
    if read:
        marks = ",".join("?" * len(read))
        with db.transaction(conn):
            for (card_id,) in conn.execute(f"SELECT id FROM cards WHERE set_id IN ({marks}) AND id IN ({prices.KEPT_SQL})",
                                           read).fetchall():
                prices.apply_tcgcsv(conn, card_id, resolve)
    return done, errors


def status(conn: sqlite3.Connection) -> dict:
    """For Data sources: sets matched, unmatched, cards priced, last run."""
    last = _meta(conn, "tcgcsv_last_run")
    return {"matched": conn.execute("SELECT COUNT(*) FROM tcgcsv_sets").fetchone()[0],
            "unmatched": len(unmatched_sets(conn)),
            "priced": conn.execute("SELECT COUNT(DISTINCT card_id) FROM tcgcsv_prices").fetchone()[0],
            "last": json.loads(last) if last else None, "groups_at": _meta(conn, "tcgcsv_groups_at")}
