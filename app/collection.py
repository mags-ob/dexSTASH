"""My Stash: everything in the inventory across sets, with filters, sorting,
value and what was paid."""

import math
import sqlite3

from . import binders, clock, display, extras, fx, inventory, notes, prices, spending, tags
from .inventory import CONDITION_LABELS, VARIANTS

ROWS_SQL = """
SELECT i.id AS inventory_id, i.card_id, i.variant, i.condition, i.quantity, i.acquired_price, i.acquired_currency, i.origin,
       c.name, c.number, c.sort_number, c.rarity, c.image_url, c.set_id, c.pricing_json, c.twin_name,
       s.name AS set_name, s.release_date, s.logo_url, s.language,
       p.price, p.origin AS price_mark, p.basis AS price_basis,
       (SELECT MAX(l.id) FROM inventory_log l WHERE l.collection_id = i.user_id AND l.card_id = i.card_id
          AND l.variant = i.variant AND l.condition = i.condition AND l.delta > 0) AS added_order,
       (SELECT MIN(l.at) FROM inventory_log l WHERE l.collection_id = i.user_id AND l.card_id = i.card_id
          AND l.variant = i.variant AND l.condition = i.condition AND l.delta > 0) AS first_added,
       (SELECT MAX(l.at) FROM inventory_log l WHERE l.collection_id = i.user_id AND l.card_id = i.card_id
          AND l.variant = i.variant AND l.condition = i.condition AND l.delta > 0) AS last_added
FROM inventory i
JOIN cards c ON c.id = i.card_id
JOIN sets s ON s.set_id = c.set_id
LEFT JOIN prices p ON p.card_id = i.card_id AND p.variant = i.variant
WHERE i.user_id = ?
"""

SORTS = {
    "price_desc": "Value",
    "newest": "Recently added",
    "set": "Set & number",
    "name": "Name",
    "price_asc": "Cheapest",
}

# Price filter: (label, lowest, below), the edges in dollars like stored prices. "none" is a printing with no price.
PRICE_RANGES = {
    "lt5": ("Under $5", 0, 5),
    "5": ("$5–9.99", 5, 10),
    "10": ("$10–24.99", 10, 25),
    "25": ("$25–49.99", 25, 50),
    "50": ("$50+", 50, None),
    "none": ("No price", None, None),
    "approx": ("≈ Estimated", None, None),
}


def _nice(x: float) -> float:
    """The closest of 1, 2, 2.5, 5 times a power of ten (on a log scale): 4.6 -> 5, 750 -> 1000, 1500 -> 2000."""
    steps = [m * 10 ** e for e in range(math.floor(math.log10(x)) - 1, math.floor(math.log10(x)) + 2) for m in (1, 2, 2.5, 5)]
    return min(steps, key=lambda v: abs(math.log10(v) - math.log10(x)))


def price_ranges() -> dict:
    """The price filter in the shown currency, with rounded edges (€5–9.99, ¥1,000–1,999)."""
    code = fx.shown()
    if code == "USD":
        return PRICE_RANGES
    rate = fx.from_usd(1.0)
    step = 10 ** -fx.decimals(code)
    edges = {e: _nice(e * rate) for e in (5, 10, 25, 50)}
    show = lambda v: fx.fmt(v, code)  # noqa: E731
    out = {"lt5": (f"Under {show(edges[5])}", 0, edges[5] / rate)}
    for low, below in ((5, 10), (10, 25), (25, 50)):
        out[str(low)] = (f"{show(edges[low])}–{show(edges[below] - step)}", edges[low] / rate, edges[below] / rate)
    out["50"] = (f"{show(edges[50])}+", edges[50] / rate, None)
    return {**out, "none": PRICE_RANGES["none"], "approx": PRICE_RANGES["approx"]}

# Added filter (the app's time zone): when copies were last added to the line.
ADDED = {"today": "Added today", "week": "Added this week", "month": "Added this month"}


def added_since(key: str):
    """The start of today / this week (Monday) / this month in the app's time zone, as UTC text like inventory_log.at."""
    from datetime import datetime, time, timedelta
    today = display.today()
    start = {"today": today, "week": today - timedelta(days=today.weekday()), "month": today.replace(day=1)}.get(key)
    if start is None:
        return None
    return clock.stamp(datetime.combine(start, time(), display.zone()))


# Language filter: Chinese covers both TCGdex's Chinese and the mainland sets.
LANGUAGE_FILTERS = {"en": "English", "ja": "Japanese", "zh": "Chinese", "ko": "Korean"}


def language_group(language: str) -> str:
    return "zh" if language.startswith("zh") else language


def in_price_range(price: float | None, key: str, mark: str | None = None) -> bool:
    if key not in PRICE_RANGES:
        return True
    _, low, below = price_ranges()[key]
    if key == "none":
        return price is None
    if key == "approx":  # ≈ English or ≈ another printing
        return price is not None and mark in prices.APPROX
    return price is not None and price >= low and (below is None or price < below)


_fold = display.fold  # Accents don't matter


def name_matches(q: str, name: str | None, twin: str | None = None, number: str | None = None, also=()) -> bool:
    """What was typed is in the card's name or its English name (Chinese cards), or is its number. also: the
    Pokémon's name in other languages, so "Pikachu" finds ピカチュウ and 皮卡丘."""
    wanted = _fold(q.strip())
    folded = _fold(name)
    return (wanted in folded or (twin is not None and wanted in _fold(twin)) or wanted == _fold(number)
            or any(other and other in folded for other in also))


def tag_choices(card_tags: dict, card_ids: list[str], limit: int = 40) -> list[tuple[str, str]]:
    """The Filter sheet's Tags: yours first, then the commonest automatic ones, each with how many cards."""
    counts, mine = {}, set()
    for card_id in dict.fromkeys(card_ids):
        for t, kind in card_tags.get(card_id, {"tags": []})["tags"]:
            counts[t] = counts.get(t, 0) + 1
            if kind == "mine":
                mine.add(t)
    order = sorted(counts, key=lambda t: (t not in mine, -counts[t], t.casefold()))[:limit]
    return [(t, f"{t} ({counts[t]})") for t in order]


def cards(conn: sqlite3.Connection, user_id: int, q: str = "", rarity: str = "", set_id: str = "",
          sort: str = "price_desc", method: str = "", by: int | None = None, origin: str = "",
          unpriced: bool = False, language: str = "", price: str = "", tag: str = "", binder: str = "",
          added: str = "", also=()) -> dict:
    """method ('manual', 'import', 'scan') and by (a person's id) keep lines with
    copies added that way or by that person; origin keeps lines from a pack, trade...;
    unpriced (or price 'none') keeps lines whose printing has no price; price is a
    PRICE_RANGES key; language a LANGUAGE_FILTERS key; tag a card tag; binder a binder's id or 'none' for
    Not in a binder; added an ADDED key."""
    rows = [dict(r) for r in conn.execute(ROWS_SQL, (user_id,))]
    languages = [k for k in LANGUAGE_FILTERS if any(language_group(r["language"]) == k for r in rows)]
    if language:
        rows = [r for r in rows if language_group(r["language"]) == language]
    rarities = sorted({r["rarity"] for r in rows if r["rarity"]}, key=_fold)
    sets = sorted({(r["set_id"], r["set_name"], r["release_date"] or "") for r in rows}, key=lambda s: s[2], reverse=True)

    card_tags = tags.for_cards(conn, [r["card_id"] for r in rows])
    tag_options = tag_choices(card_tags, [r["card_id"] for r in rows])
    if tag:
        rows = [r for r in rows if tag.casefold() in tags.names_of(card_tags.get(r["card_id"], {"tags": []}))]
    since = added_since(added)
    if since:
        rows = [r for r in rows if (r["last_added"] or "") >= since]
    if q.strip():
        rows = [r for r in rows if name_matches(q, r["name"], r.get("twin_name"), r["number"], also)]
    if rarity:
        rows = [r for r in rows if _fold(r["rarity"]) == _fold(rarity)]
    if set_id:
        rows = [r for r in rows if r["set_id"] == set_id]
    if origin:
        rows = [r for r in rows if r["origin"] == origin]
    if unpriced:
        price = "none"
    if price:
        rows = [r for r in rows if in_price_range(r["price"], price, r["price_mark"])]
    added = inventory.methods(conn, user_id)
    for r in rows:
        r["methods"] = added.get((r["card_id"], r["variant"], r["condition"]), [])
    if method or by:
        rows = [r for r in rows if any((not method or m["method"] == method) and (not by or m["user_id"] == by)
                                       for m in r["methods"])]

    if binder:
        if binder == "none" or binder.isdigit():
            rows = binders.rows_in(conn, user_id, rows, None if binder == "none" else int(binder))
    pulled = spending.pull_costs(conn, user_id)
    card_notes = notes.by_card(conn, user_id)
    others = prices.other_printings(conn, user_id)
    card_extras = extras.all_extras(conn)
    for r in rows:
        r["source"] = prices.source_of({"origin": r["price_mark"], "basis": r["price_basis"]} if r["price"] is not None else None,
                                       card_extras.get(r["card_id"]))
        r["variant_label"] = VARIANTS[r["variant"]]
        r["condition_label"] = CONDITION_LABELS.get(r["condition"], r["condition"])
        # The printing you have has no price, but others do: probably saved as the wrong one.
        r["other_printings"] = [dict(o, label=VARIANTS[o["variant"]]) for o in others.get((r["card_id"], r["variant"]), [])]
        r["notes"] = card_notes.get(r["card_id"], [])
        r["key"] = f"{r['card_id']}|{r['variant']}|{r['condition']}"
        r["origin_label"] = inventory.ORIGINS.get(r["origin"])
        r["counted"] = r["price"] is not None and prices.share(r["condition"]) > 0
        r["value"] = (r["price"] or 0) * r["quantity"] * prices.share(r["condition"])
        if r["acquired_price"] is not None:
            r["paid"] = fx.to_usd(r["acquired_price"] * r["quantity"], r["acquired_currency"])  # In dollars
        else:
            cost, _ = pulled.get((r["card_id"], r["variant"], r["condition"]), (None, 0))
            r["paid"] = cost

    if sort == "price_asc":
        rows.sort(key=lambda r: (r["price"] is None, r["price"] or 0, _fold(r["name"])))
    elif sort == "newest":
        rows.sort(key=lambda r: -(r["added_order"] or 0))
    elif sort == "name":
        rows.sort(key=lambda r: (_fold(r["name"]), r["release_date"] or ""))
    elif sort == "set":
        # Newest set first, number order within a set (reverse sorts keep ties in order).
        rows.sort(key=lambda r: (r["sort_number"], r["number"]))
        rows.sort(key=lambda r: (r["release_date"] or "", r["set_id"]), reverse=True)
    else:
        rows.sort(key=lambda r: (r["price"] is None, -(r["price"] or 0), _fold(r["name"])))

    return {
        "rows": rows, "rarities": rarities, "tag_options": tag_options, "languages": languages, "sets": [{"id": s[0], "name": s[1]} for s in sets],
        "count": sum(r["quantity"] for r in rows),
        "value": sum(r["value"] for r in rows),
        "paid": sum(r["paid"] or 0 for r in rows),
    }

