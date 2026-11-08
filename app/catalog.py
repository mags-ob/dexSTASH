"""Caching sets and cards from TCGdex into SQLite, and set completion."""

import logging
import sqlite3

from . import clock, jobs, prices, tags
from .db import transaction
from .numbers import parse_number
from .tcgdex import TCGdex, image_url

log = logging.getLogger("dexstash.catalog")


def local_id(lang: str, tcgdex_id: str) -> str:
    """Our id for a TCGdex set or card. English keeps the plain TCGdex id; other
    languages get a prefix ('fr:sv03-125'), because some languages reuse the
    same ids for different printings."""
    return tcgdex_id if lang == "en" else f"{lang}:{tcgdex_id}"


def cache_set(conn: sqlite3.Connection, api: TCGdex, set_id: str, data: dict | None = None) -> int:
    """Store a set (by its TCGdex id, in api.lang) and its full card list.
    Returns the number of cards cached.

    The whole list is cached (not just owned cards) so missing cards can be
    shown. Each card is fetched individually for its rarity, variants and
    prices, a few at a time, so a 200-card set takes seconds rather than
    minutes. Running it again on a cached set refreshes everything. Prices are
    stored only for cards someone has. data: the set, when it was just fetched.
    """
    lang = getattr(api, "lang", "en")
    our_set_id = local_id(lang, set_id)
    data = data or api.get_set(set_id)
    from .tcgdex import DISK
    DISK.put(f"{lang}/sets/{set_id}", data)  # browsing sees the fresh list too
    counts = data.get("cardCount", {})
    printed_total = counts.get("official") or counts.get("total") or 0
    total = counts.get("total") or len(data.get("cards", []))
    logo = data.get("logo")
    symbol = data.get("symbol")

    briefs = data.get("cards", [])
    kept = prices.kept_ids(conn)
    details = _graphql_details(api, set_id, briefs)
    with jobs.pool(8) as pool:
        if details is None:  # card by card
            details = list(pool.map(lambda b: api.get_card(b["id"]), briefs))
        else:  # GraphQL has no prices: kept cards still get their full detail, for prices and the product number
            todo = [i for i, b in enumerate(briefs) if local_id(lang, b["id"]) in kept]
            for i, detail in zip(todo, pool.map(lambda i: api.get_card(briefs[i]["id"]), todo)):
                details[i] = detail

    cards, auto = [], {}
    for brief, detail in zip(briefs, details):
        auto[local_id(lang, brief["id"])] = tags.detail_tags(detail, brief["name"])
        variants = detail.get("variants") or {}
        sort_number, is_main = parse_number(brief["localId"], printed_total)
        cards.append((
            local_id(lang, brief["id"]), our_set_id, brief["localId"], sort_number, int(is_main),
            brief["name"], detail.get("rarity"), image_url(brief.get("image")),
            int(bool(variants.get("normal"))), int(bool(variants.get("reverse"))),
            int(bool(variants.get("holo"))), int(bool(variants.get("firstEdition"))), types_of(detail),
            prices.product_id(detail.get("pricing")),
        ))

    with transaction(conn):
        conn.execute(
            """INSERT INTO sets (set_id, language, name, series, release_date, printed_total, total,
                                 logo_url, symbol_url, cached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(set_id) DO UPDATE SET
                 language=excluded.language,
                 name=excluded.name, series=excluded.series, release_date=excluded.release_date,
                 printed_total=excluded.printed_total, total=excluded.total,
                 logo_url=excluded.logo_url, symbol_url=excluded.symbol_url,
                 cached_at=excluded.cached_at""",
            (our_set_id, lang, data["name"], (data.get("serie") or {}).get("name"), data.get("releaseDate"),
             printed_total, total, f"{logo}.webp" if logo else None,
             f"{symbol}.webp" if symbol else None, clock.now()),
        )
        if lang in ("ja", "zh-tw", "zh-cn"):  # round 27: its English name, from the set lists already here
            from . import english
            conn.execute("UPDATE sets SET english_name = ? WHERE set_id = ?", (english.work_out(conn, our_set_id), our_set_id))
            english.changed()
        conn.executemany(
            """INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name, rarity,
                                  image_url, has_normal, has_reverse, has_holo, has_first_ed, types, tcgplayer_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 number=excluded.number, sort_number=excluded.sort_number,
                 is_main_set=excluded.is_main_set, name=excluded.name, rarity=excluded.rarity,
                 image_url=excluded.image_url, has_normal=excluded.has_normal,
                 has_reverse=excluded.has_reverse, has_holo=excluded.has_holo,
                 has_first_ed=excluded.has_first_ed, types=excluded.types, hand_added_by=NULL,
                 tcgplayer_id=COALESCE(excluded.tcgplayer_id, cards.tcgplayer_id),
                 picture_checked_at=CASE WHEN cards.image_url IS NULL AND excluded.image_url IS NOT NULL THEN NULL
                                         ELSE cards.picture_checked_at END""",
            cards,
        )
        tags.save_auto(conn, auto)
        for brief, detail in zip(briefs, details):  # only kept cards (yours, chased, with your link...) keep prices
            if local_id(lang, brief["id"]) in kept:
                prices.store(conn, local_id(lang, brief["id"]), detail)
    if lang in ("en", "ja"):  # round 28: matched to its TCGplayer set now, not at the next weekly set-list read
        from . import tcgcsv
        try:
            if conn.execute("SELECT 1 FROM tcgcsv_groups LIMIT 1").fetchone():
                tcgcsv.match_sets(conn, [our_set_id])
                if lang == "ja":
                    from . import english
                    conn.execute("UPDATE sets SET english_name = ? WHERE set_id = ?", (english.work_out(conn, our_set_id), our_set_id))
                    conn.commit()
                    english.changed()
        except sqlite3.Error as e:
            log.info("TCGplayer set match for %s: %s", our_set_id, e)
    return len(cards)


def _graphql_details(api, set_id: str, briefs: list[dict]) -> list[dict] | None:
    """The set's card details from one GraphQL request, in the order of briefs. None when
    GraphQL isn't there or doesn't agree with the set's own card list (same ids and names), so a wrong
    language or a changed schema can never store the wrong cards."""
    if not briefs or not hasattr(api, "set_cards"):
        return None
    cards = api.set_cards(set_id)
    if not cards:
        return None
    by_id = {c.get("id"): c for c in cards if isinstance(c, dict)}
    out = []
    for b in briefs:
        c = by_id.get(b["id"])
        if c is None or c.get("name") != b.get("name"):
            log.info("GraphQL for set %s doesn't match its card list; asking card by card", set_id)
            return None
        out.append(c)
    return out


def types_of(detail: dict) -> str | None:
    """TCGdex's 'types' (['Fire']) as stored: 'Fire'. None for Trainers and Energy."""
    return ",".join(t for t in detail.get("types") or [] if isinstance(t, str)) or None


def store_types(conn: sqlite3.Connection, card_id: str, detail: dict) -> None:
    """Keep a stored card's type up to date from a TCGdex fetch made for something else (its price)."""
    types = types_of(detail)
    if types:
        conn.execute("UPDATE cards SET types = ? WHERE id = ? AND (types IS NULL OR types <> ?)", (types, card_id, types))


# Completion counts a card once, whatever its quantity, condition or variant.
# Master-set completion (every variant) comes with the dashboard step.
COMPLETION_SQL = """
SELECT s.set_id, s.language, s.name, s.series, s.release_date, s.printed_total, s.total, s.logo_url, s.symbol_url,
       COUNT(DISTINCT CASE WHEN c.is_main_set = 1 AND i.id IS NOT NULL THEN c.id END) AS owned_main,
       COUNT(DISTINCT CASE WHEN c.is_main_set = 0 AND i.id IS NOT NULL THEN c.id END) AS owned_extra,
       SUM(CASE WHEN c.is_main_set = 0 THEN 1 ELSE 0 END) AS extra_total
FROM sets s
JOIN cards c ON c.set_id = s.set_id
LEFT JOIN inventory i ON i.card_id = c.id AND i.user_id = :user_id
GROUP BY s.set_id
HAVING owned_main + owned_extra > 0 OR :include_empty
ORDER BY CAST(owned_main AS REAL) / MAX(s.printed_total, 1) DESC, s.release_date DESC
"""


def set_completion(conn: sqlite3.Connection, user_id: int, include_empty: bool = True) -> list[dict]:
    rows = conn.execute(COMPLETION_SQL, {"user_id": user_id, "include_empty": int(include_empty)})
    out = []
    for r in rows:
        d = dict(r)
        d["extra_total"] = d["extra_total"] or 0
        d["percent"] = round(100 * d["owned_main"] / d["printed_total"]) if d["printed_total"] else 0
        out.append(d)
    return out


def tcgdex_set_id(set_id: str) -> str:
    """The TCGdex id behind one of our set ids ('fr:sv03' -> 'sv03')."""
    return set_id.split(":", 1)[1] if ":" in set_id else set_id


def set_cards(conn: sqlite3.Connection, set_id: str, user_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT c.*, COALESCE(SUM(i.quantity), 0) AS owned_qty
           FROM cards c
           LEFT JOIN inventory i ON i.card_id = c.id AND i.user_id = ?
           WHERE c.set_id = ?
           GROUP BY c.id
           ORDER BY c.sort_number, c.number""",
        (user_id, set_id),
    )
    return [dict(r) for r in rows]


def remove_set(conn: sqlite3.Connection, set_id: str, user_id: int) -> None:
    """Take a set out of your collection: your copies, wants, notes and pulled cards
    from it (the history of who added what stays). The set's card data is deleted too once no one else has any of it."""
    cards = "SELECT id FROM cards WHERE set_id = ?"
    with transaction(conn):
        conn.execute(f"DELETE FROM inventory WHERE user_id = ? AND card_id IN ({cards})", (user_id, set_id))
        conn.execute(f"DELETE FROM wants WHERE user_id = ? AND card_id IN ({cards})", (user_id, set_id))
        conn.execute(f"DELETE FROM chase_links WHERE collection_id = ? AND card_id IN ({cards})", (user_id, set_id))
        gone_photos = [r[0] for r in conn.execute(
            f"SELECT photo FROM chase_photos WHERE collection_id = ? AND card_id IN ({cards})", (user_id, set_id))]
        conn.execute(f"DELETE FROM chase_photos WHERE collection_id = ? AND card_id IN ({cards})", (user_id, set_id))
        conn.execute(f"DELETE FROM notes WHERE collection_id = ? AND card_id IN ({cards})", (user_id, set_id))
        conn.execute(
            f"DELETE FROM pulls WHERE card_id IN ({cards}) "
            "AND purchase_id IN (SELECT id FROM purchases WHERE user_id = ?)", (set_id, user_id))
        conn.execute("UPDATE purchases SET set_id = NULL WHERE user_id = ? AND set_id = ?", (user_id, set_id))
        in_use = conn.execute(
            f"SELECT 1 FROM inventory WHERE card_id IN ({cards}) UNION ALL "
            f"SELECT 1 FROM wants WHERE card_id IN ({cards}) UNION ALL "
            f"SELECT 1 FROM pulls WHERE card_id IN ({cards}) UNION ALL "
            f"SELECT 1 FROM card_extras WHERE card_id IN ({cards}) UNION ALL "  # your link or price stays with the card
            "SELECT 1 FROM purchases WHERE set_id = ? LIMIT 1",
            (set_id, set_id, set_id, set_id, set_id),
        ).fetchone()
        if not in_use:
            for table in ("prices", "price_snapshots"):
                conn.execute(f"DELETE FROM {table} WHERE card_id IN ({cards})", (set_id,))
            conn.execute("DELETE FROM cards WHERE set_id = ?", (set_id,))
            conn.execute("DELETE FROM sets WHERE set_id = ?", (set_id,))
    from .handmade import _drop_photo
    for photo in gone_photos:
        _drop_photo(photo)


def drop_pocket(conn) -> int:
    """TCG Pocket sets are digital only. Stored Pocket sets nobody owns a card from are deleted,
    with everything about their cards (chases, notes, prices, followed-Pokémon lists); a set someone owns
    cards from is left and logged. Returns how many sets went."""
    import logging
    from .tcgdex import is_pocket
    log = logging.getLogger("dexstash")
    sets = [r[0] for r in conn.execute("SELECT set_id FROM sets") if is_pocket(r[0])]
    tables = [f"{schema}.{r[0]}" for schema in ("main", "catalog")  # Downloaded prices are in the catalog
              for r in conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table'")
              if r[0] not in ("cards", "inventory", "pulls")
              and any(c[1] == "card_id" for c in conn.execute(f"PRAGMA {schema}.table_info({r[0]})"))]
    gone = 0
    for set_id in sets:
        cards = "SELECT id FROM cards WHERE set_id = ?"
        owned = conn.execute(f"SELECT 1 FROM inventory WHERE card_id IN ({cards}) UNION ALL "
                             f"SELECT 1 FROM pulls WHERE card_id IN ({cards}) LIMIT 1", (set_id, set_id)).fetchone()
        if owned:
            log.warning("TCG Pocket set %s kept: someone has cards from it", set_id)
            continue
        with transaction(conn):
            for table in tables:
                conn.execute(f"DELETE FROM {table} WHERE card_id IN ({cards})", (set_id,))
            conn.execute("UPDATE purchases SET set_id = NULL WHERE set_id = ?", (set_id,))
            conn.execute("DELETE FROM cards WHERE set_id = ?", (set_id,))
            conn.execute("DELETE FROM sets WHERE set_id = ?", (set_id,))
        gone += 1
    # Followed Pokémon lists can hold Pocket cards from sets that were never stored.
    pocket_rows = [r[0] for r in conn.execute("SELECT DISTINCT set_id FROM pokemon_cards") if is_pocket(r[0])]
    for set_id in pocket_rows:
        conn.execute("DELETE FROM pokemon_cards WHERE set_id = ?", (set_id,))
    conn.commit()
    if gone or pocket_rows:
        log.info("TCG Pocket: %d stored sets and %d followed-Pokémon set lists removed", gone, len(pocket_rows))
    return gone
