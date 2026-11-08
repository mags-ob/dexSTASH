"""Card prices from TCGdex, which passes on TCGplayer (USD, market price,
near mint).

TCGdex card responses carry a 'pricing' object:
  tcgplayer:  {unit, updated, 'normal': {marketPrice, midPrice, ...}, 'holofoil': {...}, 'reverse-holofoil': {...}}
  cardmarket: EUR, not used
and 'variants_detailed', where special printings such as Poké Ball and Master
Ball reverses can carry their own 'pricing'.
"""

import json
from datetime import timedelta
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote_plus

from . import clock

# TCGplayer sub-type keys (TCGdex lower-cases and dashes them) -> our variants.
_TCGPLAYER_VARIANTS = {
    "normal": "normal",
    "unlimited": "normal",
    "holofoil": "holo",
    "unlimited-holofoil": "holo",
    "reverse-holofoil": "reverse",
    "1st-edition": "first_ed",
    "1st-edition-normal": "first_ed",
    "1st-edition-holofoil": "first_ed",
}
_FOIL_VARIANTS = {"pokeball": "pokeball", "masterball": "masterball"}


def _tcgplayer_price(entry: dict) -> float | None:
    for key in ("marketPrice", "midPrice"):
        value = entry.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return round(float(value), 2)
    return None


def extract(detail: dict) -> list[tuple[str, str, float, str]]:
    """(variant, source, price, currency) rows for one TCGdex card."""
    rows: dict[tuple[str, str], tuple[float, str]] = {}
    pricing = detail.get("pricing") or {}

    tcg = pricing.get("tcgplayer") or {}
    for key, entry in tcg.items():
        variant = _TCGPLAYER_VARIANTS.get(key)
        if variant and isinstance(entry, dict) and (price := _tcgplayer_price(entry)) is not None:
            rows.setdefault((variant, "tcgplayer"), (price, tcg.get("unit", "USD")))


    for v in detail.get("variants_detailed") or []:
        variant = _FOIL_VARIANTS.get(v.get("foil") or "")
        vp = v.get("pricing") or {}
        if not variant or not vp:
            continue
        vtcg = vp.get("tcgplayer") or {}
        for key in ("reverse-holofoil", "holofoil", "normal"):
            if isinstance(vtcg.get(key), dict) and (price := _tcgplayer_price(vtcg[key])) is not None:
                rows[(variant, "tcgplayer")] = (price, vtcg.get("unit", "USD"))
                break

    return [(variant, source, price, unit) for (variant, source), (price, unit) in rows.items()]


def ranges(detail: dict) -> dict[str, tuple[float | None, float | None]]:
    """TCGplayer's low and mid per printing, from TCGdex's pricing (the same numbers TCGCSV has)."""
    out = {}
    for key, entry in ((detail.get("pricing") or {}).get("tcgplayer") or {}).items():
        variant = _TCGPLAYER_VARIANTS.get(key)
        if variant and isinstance(entry, dict) and variant not in out:
            low, mid = entry.get("lowPrice"), entry.get("midPrice")
            out[variant] = (round(float(low), 2) if isinstance(low, (int, float)) and low > 0 else None,
                            round(float(mid), 2) if isinstance(mid, (int, float)) and mid > 0 else None)
    return out


def apply_tcgcsv(conn: sqlite3.Connection, card_id: str, resolve_now: bool = True) -> None:
    """After a TCGCSV run (which wrote its quotes, tcgcsv.load_prices): a history row for each of its prices that
    changed, then the card's prices worked out again (unless resolve_now is False: the full update works every card
    out once, at its end). Call inside a transaction."""
    now = clock.now()
    if conn.execute(f"SELECT 1 FROM ({HISTORY_SQL}) WHERE card_id = ?", (card_id,)).fetchone():
        for q in conn.execute("SELECT variant, price FROM price_quotes WHERE card_id = ? AND source = 'tcgplayer' "
                              "AND basis = 'tcgcsv'", (card_id,)).fetchall():
            last = conn.execute("SELECT price FROM price_snapshots WHERE card_id = ? AND variant = ? AND source = 'tcgplayer' "
                                "ORDER BY captured_at DESC LIMIT 1", (card_id, q["variant"])).fetchone()
            if last is None or last[0] != q["price"]:
                conn.execute("INSERT INTO price_snapshots (card_id, variant, source, price, captured_at, basis) "
                             "VALUES (?, ?, 'tcgplayer', ?, ?, 'tcgcsv')", (card_id, q["variant"], q["price"], now))
    if resolve_now:
        resolve(conn, card_id)


def store(conn: sqlite3.Connection, card_id: str, detail: dict, resolve_now: bool = True) -> None:
    """Save TCGdex's TCGplayer prices for the printings TCGCSV has no price for (TCGCSV's own are never touched),
    and add a history row for each that changed (or is new). Then the card's prices are worked out again, unless
    resolve_now is False (E3: a price run works every card out once, at its end). Call inside a transaction."""
    now = clock.now()
    from_tcgcsv = {r[0] for r in conn.execute("SELECT variant FROM price_quotes WHERE card_id = ? AND source = 'tcgplayer' "
                                              "AND basis = 'tcgcsv'", (card_id,))}
    spans = ranges(detail)
    rows = [r for r in extract(detail) if r[0] not in from_tcgcsv]
    last = {r["variant"]: r["price"] for r in conn.execute(
        """SELECT ps.variant, ps.price FROM price_snapshots ps WHERE ps.card_id = ? AND ps.source = 'tcgplayer'
             AND ps.captured_at = (SELECT MAX(p2.captured_at) FROM price_snapshots p2 WHERE p2.card_id = ps.card_id
                AND p2.variant = ps.variant AND p2.source = ps.source)""", (card_id,))}
    conn.execute("DELETE FROM price_quotes WHERE card_id = ? AND source = 'tcgplayer' AND basis IS NULL", (card_id,))
    conn.executemany(
        "INSERT INTO price_quotes (card_id, variant, source, price, currency, updated_at, low, mid) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(card_id, variant, source, price, unit, now, *(spans.get(variant) or (None, None))) for variant, source, price, unit in rows])
    if conn.execute(f"SELECT 1 FROM ({HISTORY_SQL}) WHERE card_id = ?", (card_id,)).fetchone():
        conn.executemany(
            "INSERT INTO price_snapshots (card_id, variant, source, price, captured_at) VALUES (?, ?, ?, ?, ?)",
            [(card_id, variant, source, price, now) for variant, source, price, _ in rows if last.get(variant) != price])
    conn.execute("UPDATE cards SET pricing_json = ?, tcgplayer_id = COALESCE(?, tcgplayer_id) WHERE id = ?",
                 (json.dumps(detail.get("pricing")), product_id(detail.get("pricing")), card_id))
    if detail.get("image"):  # a picture TCGdex added since the set was stored
        from .tcgdex import image_url
        conn.execute("UPDATE cards SET image_url = ?, picture_checked_at = NULL WHERE id = ? AND image_url IS NULL",
                     (image_url(detail["image"]), card_id))
    if resolve_now:
        resolve(conn, card_id)


# Cards someone has, cards with a link or price you added, and cards of
# Pokémon you follow keep stored prices; everything else is looked up live when it's shown (search, previews,
# wants, other cards).
OWNED_SQL = "SELECT DISTINCT card_id FROM inventory"
FOLLOWED_SQL = ("SELECT card_id FROM pokemon_cards WHERE how <> 'removed' "
                "AND dex_id IN (SELECT dex_id FROM followed_pokemon)")  # cards of Pokémon you follow
# Single-card chases keep prices too (their found links and value over time need them).
# Round 27: a matched photo's card too, while the photo waits to be added (its prices show in scan review).
SCANNED_SQL = ("SELECT m.card_id FROM scan_matches m JOIN scans s ON s.id = m.scan_id "
               "WHERE s.status = 'ready' AND s.card_id IS NULL")
KEPT_SQL = (f"{OWNED_SQL} UNION SELECT card_id FROM card_extras UNION SELECT card_id FROM typed_prices "
            f"UNION {FOLLOWED_SQL} UNION SELECT card_id FROM wants UNION {SCANNED_SQL}")


def owned_ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(OWNED_SQL)}


def kept_ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(KEPT_SQL)}


def prune(conn: sqlite3.Connection) -> int:
    """Forget stored prices (and price history) of cards that aren't kept any
    more. Returns how many cards lost their prices."""
    gone = conn.execute(f"SELECT COUNT(DISTINCT card_id) FROM prices WHERE card_id NOT IN ({KEPT_SQL})").fetchone()[0]
    conn.execute(f"DELETE FROM prices WHERE card_id NOT IN ({KEPT_SQL})")
    conn.execute(f"DELETE FROM price_quotes WHERE card_id NOT IN ({KEPT_SQL})")
    # History: only for cards you have and single chases, and only the sources above.
    conn.execute(f"DELETE FROM price_snapshots WHERE card_id NOT IN ({HISTORY_SQL})")
    conn.execute(f"DELETE FROM price_snapshots WHERE source NOT IN ({', '.join(repr(x) for x in HISTORY_SOURCES)})")
    conn.execute(f"UPDATE cards SET pricing_json = NULL WHERE pricing_json IS NOT NULL AND id NOT IN ({KEPT_SQL})")
    conn.commit()
    return gone


# --- which price a card shows -----------------------------------------------------------
# Every downloaded price is a quote (catalog.price_quotes), from the sources in SOURCES; what you typed is in
# typed_prices. resolve works out each printing's prices into prices, which value and every list read. Every source
# with a price for that printing counts equally. The row's price is their average (what lists, sorts, worth and
# history use), range_low and range_high the lowest and highest, sources each one. Its origin is the first of them in
# source_order(). A downloaded price far above the others (FAR_TIMES and FAR_DOLLARS over TCGplayer's, else over the
# lowest) is left out; what you typed always counts. A printing you have or chase with none of its own takes the
# closest printing's (≈ another printing).
TCGPLAYER, CARDTRADER, MANUAL, PRICECHARTING, PRINTING = "tcgplayer", "cardtrader", "manual", "pricecharting", "printing"
AVERAGE = "average"  # The history of each printing's average


def _fetch_tcgcsv(conn, api_for, run: dict) -> list[str]:
    from . import settings, tcgcsv
    if not settings.on("TCGCSV"):
        return []
    done, errors = tcgcsv.run(conn, resolve=False)
    run["counts"]["tcgcsv"] = {"sets": done}
    return errors


def _fetch_tcgdex(conn, api_for, run: dict) -> list[str]:
    from . import sync
    sync.reload_hand_sets(conn, api_for)
    rows, everything = sync.tcgdex_due(conn)
    updated, errors = sync.refresh_cards(conn, api_for, rows, run.setdefault("seen", {}), resolve=False)
    if everything and not errors:
        sync.tcgdex_all_done(conn)
    kept = len(sync._kept_cards(conn))
    run["updated"] = kept - len(errors)  # every kept card's prices are worked out again at the end
    run["counts"]["tcgdex"] = {"cards": updated, "skipped": kept - len(rows)}
    return errors


def _fetch_pricecharting(conn, api_for, run: dict) -> list[str]:
    from . import pricecharting
    priced, errors = pricecharting.run(conn)
    if pricecharting.token():
        run["counts"]["pricecharting"] = {"cards": priced, **pricecharting.LAST_RUN}
    return errors


def _fetch_cardtrader(conn, api_for, run: dict) -> list[str]:
    from . import cardtrader
    priced, errors = cardtrader.run(conn)
    if cardtrader.token():
        run["counts"]["cardtrader"] = {"cards": priced, **cardtrader.LAST_RUN}
    return errors


@dataclass(frozen=True)
class Source:
    """A price source. quotes: the price_quotes.source its prices are stored under (TCGCSV's and TCGdex's are both
    TCGplayer's, told apart by basis 'tcgcsv'). tied_to: 'printing', a price only for the printing it names; 'base',
    a price for the whole card ('' printing) goes on the card's one base printing when there's exactly one.
    fetch(conn, api_for, run) -> errors, run by the price update in this order (it puts what it did in
    run['counts'][name] for the run summary); None for the ones you type."""
    name: str
    label: str
    quotes: str
    tied_to: str = "printing"
    fetch: Callable | None = None


SOURCES: tuple[Source, ...] = (
    Source("tcgcsv", "TCGplayer", TCGPLAYER, fetch=_fetch_tcgcsv),
    Source("tcgdex", "TCGplayer", TCGPLAYER, fetch=_fetch_tcgdex),  # only the printings TCGCSV has no price for
    Source("cardtrader", "CardTrader", CARDTRADER, tied_to="base", fetch=_fetch_cardtrader),
    Source("manual", "Your price", MANUAL),
    # Round 28: PriceCharting's API with the host's token (pricecharting.py); a price you type wins over it.
    Source("pricecharting", "PriceCharting", PRICECHARTING, tied_to="base", fetch=_fetch_pricecharting),
)


def quote_sources() -> tuple[str, ...]:
    """The price_quotes.source names of the downloaded sources, in list order."""
    return tuple(dict.fromkeys(src.quotes for src in SOURCES if src.fetch is not None))


def _tied_to(quote_source: str) -> str:
    return next((src.tied_to for src in SOURCES if src.quotes == quote_source), "printing")


def source_label(origin: str) -> str:
    return next((src.label for src in SOURCES if src.quotes == origin), origin)


def source_order() -> tuple[str, ...]:
    return tuple(dict.fromkeys(src.quotes for src in SOURCES))


FAR_TIMES, FAR_DOLLARS = 20, 20.0
TYPED = (MANUAL, PRICECHARTING)
APPROX = (PRINTING,)
OWN_PRICE = "origin = 'tcgplayer'"  # TCGplayer's price for the printing itself
# The printings to borrow a price from, closest first.
NEAREST = {
    "normal": ("reverse", "holo"), "holo": ("reverse", "normal"), "reverse": ("normal", "holo"),
    "first_ed": ("holo", "normal", "reverse"), "star": ("holo", "reverse", "normal"),
    "star2": ("holo", "reverse", "normal"), "pokeball": ("reverse", "normal", "holo"),
    "masterball": ("pokeball", "reverse", "normal", "holo"), "other": ("normal", "holo", "reverse"),
}


def nearest(variant: str, have: dict[str, float]) -> str | None:
    """The closest printing to variant that has a price in have."""
    for v in NEAREST.get(variant, ()):
        if v in have:
            return v
    return next(iter(have), None)


def keep_close(found: list[dict]) -> list[dict]:
    """The sources to count, without any far above the rest (a placeholder listing, say)."""
    if len(found) < 2:
        return found
    ref = next((f["price"] for f in found if f["origin"] == TCGPLAYER), min(f["price"] for f in found))
    kept = [f for f in found if f["origin"] in TYPED  # what you typed always counts
            or not (f["price"] > ref * FAR_TIMES and f["price"] - ref > FAR_DOLLARS)]
    return kept or found


SOURCE_KEYS = ("origin", "price", "basis", "updated_at", "paid", "low", "mid", "url")


def combine(found: list[dict]) -> dict | None:
    """One printing's sources ({origin, price, basis, updated_at, low?, mid?, paid?}) as its prices row: the
    average, the range and the sources counted, in source_order(), then any left out. None without any."""
    order = source_order()
    found = sorted(found, key=lambda f: order.index(f["origin"]) if f["origin"] in order else 99)
    kept = keep_close(found)
    if not kept:
        return None
    each = [f["price"] for f in kept]
    first, tcg = kept[0], next((f for f in kept if f["origin"] == TCGPLAYER), {})
    sources = [{k: f[k] for k in SOURCE_KEYS if f.get(k) is not None} for f in kept]
    sources += [dict({k: f[k] for k in SOURCE_KEYS if f.get(k) is not None}, left_out=True)
                for f in found if not any(f is k for k in kept)]
    return {"price": round(sum(each) / len(each), 2), "origin": first["origin"], "basis": first.get("basis"),
            "low": tcg.get("low"), "mid": tcg.get("mid"), "range_low": min(each), "range_high": max(each),
            "updated_at": max((f.get("updated_at") or "") for f in kept) or None, "sources": sources}


def counted(row: dict) -> list[dict]:
    """The sources a prices row's average and range were worked out from (not the ones left out)."""
    return [f for f in row.get("sources") or () if not f.get("left_out")]


def localize(row: dict, code: str, rate: float) -> dict:
    """A prices row with its amounts also in the install's currency (code, rate = code per dollar)."""
    def loc(v):
        return None if v is None else round(v * rate, 2)
    return dict(row, local_currency=code, local_rate=rate, price_local=loc(row["price"]),
                range_low_local=loc(row.get("range_low")), range_high_local=loc(row.get("range_high")),
                sources=[dict(f, local=loc(f["price"]), **{f"{k}_local": loc(f[k]) for k in ("low", "mid") if f.get(k) is not None})
                         for f in row.get("sources") or ()])


def resolve(conn: sqlite3.Connection, card_id: str) -> None:
    """Work out each printing's prices from its quotes and what you typed (every source, averaged), and write them
    to prices, in dollars and in the install's currency. A price in another currency is converted here, at the
    rate saved with the day's exchange rates, and the amount, currency and rate are kept with it; one that can't be
    converted yet isn't counted. Call inside a transaction, whenever a quote or a typed price changes."""
    from . import extras, fx
    if conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone() is None:
        conn.execute("DELETE FROM prices WHERE card_id = ?", (card_id,))
        return  # your link or price for a card whose set was removed
    now = clock.now()
    quotes = [dict(r) for r in conn.execute(
        "SELECT * FROM price_quotes WHERE card_id = ? ORDER BY source, variant <> '' DESC", (card_id,))]
    found: dict[str, list[dict]] = {}

    def add(v: str, origin: str, price, **more) -> None:
        if price and not any(f["origin"] == origin for f in found.get(v, ())):  # one of each source a printing
            found.setdefault(v, []).append({"origin": origin, "price": round(price, 2), **more})

    def paid(amount, currency) -> tuple[float | None, dict | None]:
        """amount in currency as dollars, and what it was converted from (None for dollars)."""
        usd, each = fx.in_usd(amount, currency)
        code = (currency or "USD").upper()
        return usd, (None if code == "USD" or usd is None else {"amount": amount, "currency": code, "rate": each})

    owned = [r[0] for r in conn.execute("SELECT DISTINCT variant FROM inventory WHERE card_id = ? ORDER BY variant", (card_id,))]
    chased = [r[0] for r in conn.execute("SELECT DISTINCT variant FROM wants WHERE card_id = ? AND variant <> 'any' "
                                         "ORDER BY variant", (card_id,))]
    tracked = owned + [v for v in chased if v not in owned]
    card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    on_tcgplayer = {q["variant"] for q in quotes if q["source"] == TCGPLAYER and q["variant"]}
    swap = printing_swap(on_tcgplayer, tracked)
    base = base_printing(card, tracked, {q["variant"] for q in quotes if q["variant"]})
    # Typed prices first, so a PriceCharting price you type wins over its API's (round 28); each for the printing it
    # was typed for, in its own currency.
    for t in extras.typed_rows(conn, card_id):
        usd, was = paid(t["price"], t["currency"])
        add(t["variant"], t["kind"], usd, updated_at=t["typed_at"] or now, paid=was, basis="read" if t["was_read"] else None)
    for q in quotes:
        usd, was = paid(q["price"], q["currency"])
        if q["variant"]:
            onto = [q["variant"]] + ([v for v, t in swap.items() if t == q["variant"]] if q["source"] == TCGPLAYER else [])
        elif _tied_to(q["source"]) == "base" and base:
            onto = [base]
        else:
            continue  # a price for no one printing isn't spread over them
        for v in onto:
            add(v, q["source"], usd, basis=q["basis"], low=q["low"], mid=q["mid"], updated_at=q["updated_at"], paid=was,
                url=q["url"] if "url" in q else None)
    shown = {v: row for v, f in found.items() if (row := combine(f))}
    if shown:
        for v in tracked:
            if v not in shown and (basis := nearest(v, shown)):
                shown[v] = dict(shown[basis], origin=PRINTING, basis=basis, sources=[])
    code, rate = fx.local_rate()
    shown = {v: localize(r, code, rate) for v, r in shown.items()}
    conn.execute("DELETE FROM prices WHERE card_id = ?", (card_id,))
    conn.executemany(f"INSERT INTO prices ({', '.join(PRICE_COLUMNS)}) VALUES ({', '.join('?' * len(PRICE_COLUMNS))})",
                     [_price_values(card_id, v, r, now) for v, r in shown.items()])
    # History: each source that isn't downloaded with its own (TCGplayer's is kept by store and apply_tcgcsv),
    # ≈ another printing, and each printing's average.
    rows = [(v, f["price"], f["origin"], None) for v, r in shown.items() for f in counted(r) if f["origin"] != TCGPLAYER]
    rows += [(v, r["price"], PRINTING, None) if r["origin"] == PRINTING else (v, r["price"], AVERAGE, _spread(r))
             for v, r in shown.items()]
    _history(conn, card_id, rows, now)


BASE_PRINTINGS = ("normal", "holo")


def printing_swap(on_tcgplayer: set[str], tracked: list[str]) -> dict[str, str]:
    """TCGdex and TCGplayer can name a card's one printing differently (an ex card is Normal on TCGdex, Holofoil on
    TCGplayer). When TCGplayer lists exactly one printing, Normal or Holo, and you have or chase only the other of
    the two, yours is priced as that one: {your printing: TCGplayer's}. With both tracked, each keeps its own."""
    if len(on_tcgplayer) != 1:
        return {}
    theirs = next(iter(on_tcgplayer))
    mine = [v for v in tracked if v in BASE_PRINTINGS]
    if theirs not in BASE_PRINTINGS or len(mine) != 1 or mine[0] == theirs:
        return {}
    return {mine[0]: theirs}


def base_printing(card, tracked: list[str], quoted: set[str]) -> str | None:
    """The card's one base printing (Normal or Holo), for a price that's for the whole card: the one you have or
    chase, else the one the card comes in. None when there are two (or none)."""
    from .inventory import offered_variants
    mine = [v for v in tracked if v in BASE_PRINTINGS]
    known = mine or [v for v in offered_variants(card, quoted) if v in BASE_PRINTINGS]
    return known[0] if len(known) == 1 else None


PRICE_COLUMNS = ("card_id", "variant", "price", "origin", "basis", "low", "mid", "updated_at", "range_low", "range_high",
                 "sources", "local_currency", "local_rate", "price_local", "range_low_local", "range_high_local")


def _price_values(card_id: str, v: str, r: dict, now: str) -> tuple:
    return (card_id, v, r["price"], r["origin"], r.get("basis"), r.get("low"), r.get("mid"), r["updated_at"] or now,
            r["range_low"], r["range_high"], json.dumps(r["sources"]), r["local_currency"], r["local_rate"],
            r["price_local"], r["range_low_local"], r["range_high_local"])


def localize_all(conn: sqlite3.Connection) -> int:
    """Save every stored price again in the install's currency at the last saved rates (each night after the
    exchange rates, and when the currency changes), and today's history with it. Returns how many rows changed."""
    from . import fx
    code, rate = fx.local_rate()
    changed = 0
    for r in conn.execute("SELECT * FROM prices WHERE local_currency IS NOT ? OR local_rate IS NOT ?",
                          (code, rate)).fetchall():
        row = localize(dict(r, sources=json.loads(r["sources"] or "[]")), code, rate)
        conn.execute("UPDATE prices SET local_currency = ?, local_rate = ?, price_local = ?, range_low_local = ?, "
                     "range_high_local = ?, sources = ? WHERE card_id = ? AND variant = ?",
                     (code, rate, row["price_local"], row["range_low_local"], row["range_high_local"],
                      json.dumps(row["sources"]), r["card_id"], r["variant"]))
        changed += 1
    today = datetime.now(timezone.utc).date().isoformat()
    # E8: today's history points only where their copy in your currency is missing or differs (the rate changed)
    conn.execute(f"UPDATE price_snapshots SET local = ROUND(price * ?, 2), local_currency = ? "
                 f"WHERE captured_at >= ? AND source IN ({', '.join('?' * len(DOLLAR_HISTORY))}) "
                 f"AND (local IS NULL OR local_currency IS NOT ? OR local IS NOT ROUND(price * ?, 2))",
                 (rate, code, today, *DOLLAR_HISTORY, code, rate))
    return changed


UNCONVERTED = "history_unconverted"  # history points older than every kept rate, after the last re-save


def localize_history(conn: sqlite3.Connection) -> tuple[int, int]:
    """Save every history point again in the install's currency at its own day's rate (fx_rates: the newest day on
    or before it; a week or month point is stored under its first day, so it takes that day's). A point older than
    every kept rate keeps what it had. After a currency change, and once when past rates are first read. Returns
    (points re-saved, points kept as they were). Call inside a transaction."""
    from . import fx
    code = fx.local_rate()[0]
    done = kept = 0
    for (day, n) in conn.execute("SELECT substr(captured_at, 1, 10), COUNT(*) FROM price_snapshots GROUP BY 1").fetchall():
        rate = 1.0 if code == "USD" else fx.usd_rate_on(conn, day, code)
        if rate is None:
            kept += n
            continue
        conn.execute("UPDATE price_snapshots SET local = ROUND(price * ?, 2), local_currency = ? "
                     "WHERE substr(captured_at, 1, 10) = ?", (rate, code, day))
        done += n
    conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)", (UNCONVERTED, str(kept)))
    return done, kept


# Price history also covers prices that aren't TCGdex's, each under its own source.
# History kept: each downloaded source's, what you typed ('link' is the PriceCharting price), ≈ another printing and
# each printing's average. Older versions' names stay so their history is never tidied away.
OLD_HISTORY = ("found_tcgplayer", "found_pricecharting", "english")
HISTORY_SOURCES = tuple(dict.fromkeys(quote_sources() + ("link", MANUAL, PRINTING, AVERAGE) + OLD_HISTORY))
DOLLAR_HISTORY = HISTORY_SOURCES  # every one is kept in dollars
HISTORY_SQL = f"{OWNED_SQL} UNION SELECT card_id FROM wants"  # history is kept for cards you have and single chases


def history_source(origin: str) -> str:
    """The price history's source for a shown price's origin. The PriceCharting price keeps 'link', its name from
    when it was read from your link, so its history carries on."""
    return "link" if origin == PRICECHARTING else origin


def _spread(row: dict) -> dict:
    """An average's low and high, each with its source, for its history point (0.20.1)."""
    found = counted(row)
    if not found:
        return {}
    lo, hi = min(found, key=lambda f: f["price"]), max(found, key=lambda f: f["price"])
    return {"low": lo["price"], "low_source": lo["origin"], "high": hi["price"], "high_source": hi["origin"]}


def _history(conn: sqlite3.Connection, card_id: str, rows, now: str) -> None:
    """A history row for each price that isn't TCGplayer's and changed (or is new), like store does for TCGplayer's,
    with the price in the install's currency on its day. rows: (printing, price, source, spread); an average's
    spread is its low and high with their sources, and a change in them is a new row too."""
    if not rows or not conn.execute(f"SELECT 1 FROM ({HISTORY_SQL}) WHERE card_id = ?", (card_id,)).fetchone():
        return
    from . import fx
    code, rate = fx.local_rate()
    for variant, price, origin, spread in rows:
        spread = spread or {}
        source = history_source(origin)
        last = conn.execute("SELECT rowid, price, captured_at, local_currency, low, high FROM price_snapshots "
                            "WHERE card_id = ? AND variant = ? AND source = ? ORDER BY captured_at DESC LIMIT 1",
                            (card_id, variant, source)).fetchone()
        if last is not None and last[1] == price and last[2][:10] == now[:10] and last[3] != code:
            conn.execute("UPDATE price_snapshots SET local = ?, local_currency = ? WHERE rowid = ?",
                         (round(price * rate, 2), code, last[0]))  # today's, in a currency picked today
        moved = last is not None and spread and (last[4], last[5]) != (spread.get("low"), spread.get("high"))
        if last is None or last[1] != price or moved:
            conn.execute("INSERT INTO price_snapshots (card_id, variant, source, price, captured_at, local, local_currency, "
                         "low, low_source, high, high_source, samples) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                         (card_id, variant, source, price, now, round(price * rate, 2), code, spread.get("low"),
                          spread.get("low_source"), spread.get("high"), spread.get("high_source")))


def apply_all(conn: sqlite3.Connection) -> None:
    for (card_id,) in conn.execute(f"SELECT card_id FROM ({KEPT_SQL})").fetchall():
        resolve(conn, card_id)


def real(rows: list[dict]) -> list[dict]:
    """The rows that are a price for that very printing (not ≈ another printing)."""
    return [r for r in rows if r.get("origin") not in APPROX]


def real_variants(rows: list[dict]) -> set[str]:
    return {r["variant"] for r in real(rows)}


def source_of(row: dict | None, extra: dict | None = None, tcg_url: str | None = None,
              rows: list[dict] | None = None) -> dict | None:
    """Where a price came from: {'label', 'short', 'url'}."""
    if not row:
        return None
    origin = row.get("origin")
    if origin in TYPED:
        extra = extra or {}
        if origin == PRICECHARTING:  # the date it was typed (or last read), from its line in sources
            from . import display
            f = next((f for f in row.get("sources") or () if f.get("origin") == PRICECHARTING), row)
            if f.get("basis") == "api":  # round 28: PriceCharting's API, with your token
                return {"label": "PriceCharting · ungraded", "short": "PriceCharting", "url": extra.get("pricecharting_url")}
            when = display.short_date(f.get("updated_at")) if f.get("updated_at") else ""
            how = "last read" if f.get("basis") == "read" else "you"
            return {"label": f"PriceCharting · {how}{', ' + when if when and how == 'you' else (' ' + when if when else '')}",
                    "short": "PriceCharting", "url": extra.get("pricecharting_url")}
        return {"label": "your price", "short": "Manual", "url": None}
    if origin == CARDTRADER:
        any_condition = row.get("basis") == "any"
        return {"label": "CardTrader · lowest listing (any condition)" if any_condition else "CardTrader · Near Mint median",
                "short": "CardTrader", "url": None}
    if origin == PRINTING:
        from .inventory import VARIANTS
        name = VARIANTS.get(row.get("basis") or "", row.get("basis") or "")
        return {"label": f"≈ {name}", "short": f"≈ {name}", "url": None}
    if row.get("mid") is None and row.get("low") is not None and row.get("price") == row.get("low"):
        return {"label": "TCGplayer · lowest listing", "short": "TCGplayer", "url": tcg_url}  # round 28: no sales or mid
    return {"label": "TCGplayer", "short": "TCGplayer", "url": tcg_url}


def sources(rows: list[dict], extra: dict | None = None, tcg_url: str | None = None) -> dict[str, dict]:
    """Printing -> where its shown price came from."""
    return {r["variant"]: source_of(r, extra, tcg_url, rows) for r in rows}


def missing(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Cards someone has that have no stored price yet (cards added by hand never get one)."""
    return conn.execute(
        f"""SELECT c.id, s.language FROM cards c JOIN sets s ON s.set_id = c.set_id
            WHERE c.id IN ({OWNED_SQL}) AND c.hand_added_by IS NULL AND c.source IS NULL
              AND c.id NOT IN (SELECT card_id FROM prices) AND c.pricing_json IS NULL""").fetchall()


def as_rows(detail: dict) -> list[dict]:
    """A live TCGdex card's TCGplayer prices shaped like card_prices rows (no update time)."""
    spans = ranges(detail)
    found: dict[str, list[dict]] = {}
    for v, src, p, cur in extract(detail):
        if src == "tcgplayer" and v not in found:
            found[v] = [{"origin": TCGPLAYER, "price": p, "low": (spans.get(v) or (None, None))[0],
                         "mid": (spans.get(v) or (None, None))[1]}]
    return [dict(row, variant=v) for v, f in found.items() if (row := combine(f))]


def listed(pricing) -> list[str]:
    """The printings TCGplayer lists for a card, in TCGdex's order, from the TCGdex pricing already stored or in hand
    (no request). Only for picking the printing an Add starts on; its amounts aren't shown (0.20.1)."""
    if isinstance(pricing, str):
        try:
            pricing = json.loads(pricing)
        except ValueError:
            return []
    return [r["variant"] for r in as_rows({"pricing": pricing or {}})]


# --- flags and coverage (0.20.1): the printings you have or chase as single cards ---------------------------------

STALE_KEY, STALE_DEFAULT = "stale_days", 7  # a printing whose newest downloaded price is older than this is stale
COVERAGE = (("tcgplayer", "TCGplayer"), ("cardtrader", "CardTrader"), ("typed", "typed"), ("approx", "≈ another printing"),
            ("no_price", "no price"), ("stale", "stale"))


def stale_days(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (STALE_KEY,)).fetchone()
    return int(row[0]) if row and str(row[0]).isdigit() and int(row[0]) > 0 else STALE_DEFAULT


def printing_states(conn: sqlite3.Connection, collection_id: int) -> dict[tuple[str, str], set[str]]:
    """(card, printing) -> what's true of its price: the COVERAGE keys, for each printing you have or chase as a
    specific printing; (card, '') -> {'no_price'} for a card chased in any printing with no priced printing at all.
    Cards you don't have or chase never appear."""
    tracked = {(r[0], r[1]) for r in conn.execute(
        """SELECT card_id, variant FROM inventory WHERE user_id = ?
           UNION SELECT card_id, variant FROM wants WHERE user_id = ? AND variant <> 'any'""", (collection_id, collection_id))}
    rows = {(r["card_id"], r["variant"]): r for r in conn.execute(
        "SELECT card_id, variant, origin, sources FROM prices WHERE card_id IN "
        "(SELECT card_id FROM inventory WHERE user_id = ? UNION SELECT card_id FROM wants WHERE user_id = ?)",
        (collection_id, collection_id))}
    cutoff = clock.since(timedelta(days=stale_days(conn)))
    out: dict[tuple[str, str], set[str]] = {}
    for key in tracked:
        row = rows.get(key)
        if row is None:
            out[key] = {"no_price"}
            continue
        if row["origin"] == PRINTING:
            out[key] = {"approx"}
            continue
        found = [f for f in json.loads(row["sources"] or "[]") if not f.get("left_out")]
        kinds = {"typed" if f["origin"] in TYPED else f["origin"] for f in found}
        downloaded = [f.get("updated_at") or "" for f in found if f["origin"] not in TYPED]
        if downloaded and max(downloaded) < cutoff:
            kinds.add("stale")
        out[key] = kinds
    priced = {k[0] for k in rows}
    for (card_id,) in conn.execute("SELECT card_id FROM wants WHERE user_id = ? AND variant = 'any'", (collection_id,)):
        if card_id not in priced:
            out[(card_id, "")] = {"no_price"}
    return out


def coverage(conn: sqlite3.Connection, collection_id: int) -> dict | None:
    """For Data sources: how many printings you have or chase each source prices, and how many have none or a
    stale one. None with nothing tracked."""
    states = printing_states(conn, collection_id)
    if not states:
        return None
    return {"total": len(states), "parts": [(key, label, sum(1 for s in states.values() if key in s)) for key, label in COVERAGE]}


def card_prices(conn: sqlite3.Connection, card_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT variant, price, origin, updated_at, basis, low, mid, range_low, range_high, sources, local_currency, "
        "local_rate, price_local, range_low_local, range_high_local FROM prices "
        "WHERE card_id = ? ORDER BY variant", (card_id,),
    )
    return [dict(r, sources=json.loads(r["sources"] or "[]")) for r in rows]


# Value uses TCGplayer market prices (USD, near mint) for the printing you
# have. Every condition counts at that price except Damaged, which counts at
# DAMAGED_SHARE of it. A printing with no price of its own counts at your
# link's or your price, else ≈ the English version's or the closest printing's
# (marked ≈ wherever it shows).
DAMAGED_SHARE = 0.0


def share(condition: str) -> float:
    return DAMAGED_SHARE if condition == "DMG" else 1.0


_SHARE_SQL = "(CASE WHEN i.condition = 'DMG' THEN :dmg ELSE 1.0 END)"

COLLECTION_VALUE_SQL = f"""
SELECT COALESCE(SUM(i.quantity * p.price * {_SHARE_SQL}), 0) AS value,
       COALESCE(SUM(CASE WHEN p.price IS NULL THEN i.quantity ELSE 0 END), 0) AS unpriced,
       COALESCE(SUM(CASE WHEN p.price IS NOT NULL AND i.condition = 'DMG' THEN i.quantity ELSE 0 END), 0) AS damaged,
       COALESCE(SUM(CASE WHEN p.origin = 'printing' THEN i.quantity * p.price * {_SHARE_SQL} ELSE 0 END), 0) AS estimated,
       COALESCE(SUM(i.quantity), 0) AS cards
FROM inventory i
LEFT JOIN prices p ON p.card_id = i.card_id AND p.variant = i.variant
WHERE i.user_id = :user_id
"""

# Per set: what you own is worth.
SET_VALUES_SQL = f"""
SELECT c.set_id, COALESCE(SUM(i.quantity * p.price * {_SHARE_SQL}), 0) AS owned_value
FROM inventory i
JOIN cards c ON c.id = i.card_id
JOIN prices p ON p.card_id = i.card_id AND p.variant = i.variant
WHERE i.user_id = :user_id
GROUP BY c.set_id
"""


def collection_value(conn: sqlite3.Connection, user_id: int) -> dict:
    return dict(conn.execute(COLLECTION_VALUE_SQL, {"user_id": user_id, "dmg": DAMAGED_SHARE}).fetchone())


def set_values(conn: sqlite3.Connection, user_id: int) -> dict[str, dict]:
    return {r["set_id"]: dict(r) for r in conn.execute(SET_VALUES_SQL, {"user_id": user_id, "dmg": DAMAGED_SHARE})}


def other_printings(conn: sqlite3.Connection, user_id: int) -> dict[tuple[str, str], list[dict]]:
    """(card_id, variant) -> the TCGplayer prices of the card's other printings,
    for lines whose own printing has no price (most likely saved as the wrong
    printing). Cheapest first. ≈ prices don't count as the printing's own."""
    out: dict[tuple[str, str], list[dict]] = {}
    for r in conn.execute(
        """SELECT DISTINCT i.card_id, i.variant AS have, p.variant, p.price FROM inventory i
           JOIN prices p ON p.card_id = i.card_id AND p.variant <> i.variant
                AND p.origin <> ?
           WHERE i.user_id = ? AND NOT EXISTS (SELECT 1 FROM prices o WHERE o.card_id = i.card_id
                 AND o.variant = i.variant AND o.origin <> ?)
           ORDER BY p.price""", (PRINTING, user_id, PRINTING)):
        out.setdefault((r["card_id"], r["have"]), []).append({"variant": r["variant"], "price": r["price"]})
    return out


def last_updated(conn: sqlite3.Connection) -> str | None:
    return conn.execute("SELECT MAX(updated_at) FROM prices").fetchone()[0]


_LANGUAGE_WORDS = {"ja": "Japanese", "zh-tw": "Chinese", "zh-cn": "Chinese", "ko": "Korean"}


def latin(text: str | None) -> bool:
    """No Japanese, Chinese or Korean letters (accents like Flabébé's are fine)."""
    return not any(ord(ch) >= 0x2E80 for ch in text or "")


def _search_words(name: str | None, number: str, set_name: str = "", language: str = "en") -> str:
    """name is the English name; with none, the search is by number (and set) only. Japanese, Chinese
    and Korean text never goes into a search: it finds nothing on any site."""
    return _words(name, number, set_name, _LANGUAGE_WORDS.get(language, ""))


def _words(*parts: str | None) -> str:
    """The parts in this order, leaving out empty ones and any with Japanese, Chinese or Korean text
    (a set name must be plain ASCII: the English one)."""
    return " ".join(p for i, p in enumerate(parts) if p and latin(p) and (p.isascii() or i == 0))


def printed_number(number: str, printed_total: int | None = None) -> str:
    """The number as the card prints it, without the total: '77' of 167 -> '077'."""
    return full_number(number, printed_total).split("/")[0] if number else ""


def bare_number(number: str) -> str:
    """'077' -> '77'; 'TG05' and 'SV001' stay as they are (PriceCharting's titles read 'Pikachu ex #77')."""
    n = (number or "").split("/")[0]
    return str(int(n)) if n.isdigit() else n


def tcgplayer_link(pricing: dict | str | None, name: str, number: str, set_name: str = "",
                   language: str = "en", product_id: int | None = None, printed_total: int | None = None) -> tuple[str, bool]:
    """(url, exact) for a card on TCGplayer. exact is True when the URL is the
    card's own product page, from the product id TCGdex passes on or,
    the one TCGCSV's open product list gives (product_id); no request to TCGplayer
    either way. Otherwise it's a TCGplayer search (round 27): English name, number as printed without the total, and
    the English set name ("Pikachu ex 077 Twilight Masquerade"); Japanese cards in TCGplayer's Japanese product line."""
    if isinstance(pricing, str):
        pricing = json.loads(pricing)
    tcg = (pricing or {}).get("tcgplayer") or {}
    for key in ("normal", "holofoil", "reverse-holofoil", *tcg):
        entry = tcg.get(key)
        if isinstance(entry, dict) and entry.get("productId"):
            return f"https://www.tcgplayer.com/product/{int(entry['productId'])}", True
    if product_id:
        return f"https://www.tcgplayer.com/product/{int(product_id)}", True
    line = "pokemon-japan" if language == "ja" else "pokemon"
    q = quote_plus(_words(name, printed_number(number, printed_total), set_name))
    return f"https://www.tcgplayer.com/search/{line}/product?productLineName={line}&q={q}", False


def pricecharting_link(name: str, number: str, set_name: str = "", language: str = "en") -> str:
    """A PriceCharting search (round 27): English name, English set name, the number without leading zeros, and the
    language for Japanese, Chinese and Korean cards ("Charizard ex 151 199"; its titles read "Charizard ex #199")."""
    q = quote_plus(_words(name, set_name, bare_number(number), _LANGUAGE_WORDS.get(language, "")))
    return f"https://www.pricecharting.com/search-products?type=prices&q={q}"


def cardtrader_search_link(name: str, set_name: str = "") -> str:
    """A CardTrader search by English name and set name, for a card with no CardTrader page found yet."""
    return f"https://www.cardtrader.com/en/search?q={quote_plus(_words(name, set_name))}"


# eBay's sold listings: the person picks their eBay site on Data sources › Prices (only these are accepted).
EBAY_SITES = ("ebay.com", "ebay.ca", "ebay.co.uk", "ebay.com.au", "ebay.de", "ebay.fr", "ebay.it", "ebay.es", "ebay.ie")
EBAY_KEY = "ebay_site"


def ebay_site(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (EBAY_KEY,)).fetchone()
    return row[0] if row and row[0] in EBAY_SITES else EBAY_SITES[0]


def set_ebay_site(conn: sqlite3.Connection, site: str) -> None:
    if site not in EBAY_SITES:
        raise ValueError("Pick one of the eBay sites listed.")
    conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)", (EBAY_KEY, site))
    conn.commit()


def full_number(number: str, printed_total: int | None) -> str:
    """'77' and 167 -> '077/167', as printed on the card. A number with letters (TG05, SV001) stays as it is."""
    if not number or not printed_total or not number.isdigit():
        return number or ""
    return f"{number.zfill(len(str(printed_total)))}/{printed_total}"


def ebay_link(name: str, number: str, set_name: str = "", language: str = "en", site: str = EBAY_SITES[0],
              printed_total: int | None = None) -> str:
    """An eBay search of sold, ungraded, near mint listings of the card, most recently ended first, in eBay's
    trading card category (a public page; nothing is read from it, no tracking). English cards: English name, full
    number and set name; other languages: name, number and the language instead of the set."""
    if site not in EBAY_SITES:
        site = EBAY_SITES[0]
    words = _search_words(name, full_number(number, printed_total), set_name if language == "en" else "", language)
    return (f"https://www.{site}/sch/183454/i.html?_nkw={quote_plus(words)}&_dcat=183454&LH_Sold=1&LH_Complete=1"
            "&Graded=NO&Card%20Condition=Near%20Mint%20or%20Better&_sop=13&_ipg=60")


def cardmarket_link(name: str) -> str:
    """A Cardmarket search for the card by English name (a public page; nothing is read from it)."""
    return f"https://www.cardmarket.com/en/Pokemon/Products/Search?searchString={quote_plus(name)}"


def by_variant(rows: list[dict]) -> dict[str, float]:
    """The shown price per printing, from card_prices / as_rows rows."""
    return {r["variant"]: r["price"] for r in rows}


def product_ids(pricing) -> set[int]:
    """Every TCGplayer product number in TCGdex's pricing (a dict or its JSON), round 28."""
    if isinstance(pricing, str):
        try:
            pricing = json.loads(pricing)
        except ValueError:
            return set()
    tcg = (pricing or {}).get("tcgplayer") if isinstance(pricing, dict) else None
    return {int(e["productId"]) for e in (tcg or {}).values()
            if isinstance(e, dict) and str(e.get("productId") or "").isdigit()}


def product_id(pricing) -> int | None:
    """TCGplayer's product number from TCGdex's pricing, kept on the card for good."""
    for entry in ((pricing or {}).get("tcgplayer") or {}).values() if isinstance(pricing, dict) else []:
        if isinstance(entry, dict) and str(entry.get("productId") or "").isdigit():
            return int(entry["productId"])
    return None
