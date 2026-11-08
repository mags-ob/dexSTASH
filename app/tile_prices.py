"""The price a grid tile shows (round 27): the same main price as the card's own page.

The card page shows one printing's price: the first printing you have that has a price, else the first printing
in TCGplayer's order (Normal, Holo, Reverse Holo, 1st Edition...) that has one. For a card someone keeps, that is
its stored price (the average of its sources). For a card nobody keeps, it is TCGCSV's price for the card's own
product, the one with its number and English name, in that printing. Never the dearest product or printing with
that number (the old rule, which showed a 1st Edition price on an Unlimited card).

Nothing is fetched here. A card whose set has no TCGCSV prices stored yet is listed as waiting; the caller asks for
that set in the background (web._read_set_later) and the grid fills in when it's read.
"""
from __future__ import annotations

import sqlite3

from . import inventory, prices, tcgcsv


def main_printing(have: dict[str, float], owned: set[str] | None = None) -> str | None:
    """The printing whose price a tile shows, from printing -> price."""
    priced = [v for v in inventory.VARIANTS if have.get(v) is not None] + \
             [v for v in have if v not in inventory.VARIANTS and have[v] is not None]
    for v in priced:
        if owned and v in owned:
            return v
    return priced[0] if priced else None


def _stored(conn: sqlite3.Connection, ids: list[str]) -> dict[str, dict[str, float]]:
    """card id -> printing -> stored price (a kept card's), real printings first; ≈ ones only when that's all."""
    out: dict[str, dict[str, float]] = {}
    approx: dict[str, dict[str, float]] = {}
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        for r in conn.execute(f"SELECT card_id, variant, price, origin FROM prices WHERE card_id IN ({','.join('?' * len(part))}) "
                              "AND price IS NOT NULL", part):
            (approx if r["origin"] in prices.APPROX else out).setdefault(r["card_id"], {})[r["variant"]] = r["price"]
        for r in conn.execute(f"SELECT card_id, variant, market FROM tcgcsv_prices WHERE card_id IN ({','.join('?' * len(part))}) "
                              "AND market IS NOT NULL", part):
            if r["card_id"] not in out and r["card_id"] not in approx:
                out.setdefault(f"tcgcsv:{r['card_id']}", {})[r["variant"]] = r["market"]
    for key in [k for k in out if k.startswith("tcgcsv:")]:
        out.setdefault(key[7:], out.pop(key))
    for card_id, have in approx.items():
        out.setdefault(card_id, have)
    return out


def _owned(conn: sqlite3.Connection, collection_id: int | None, ids: list[str]) -> dict[str, set[str]]:
    if collection_id is None or not ids:
        return {}
    out: dict[str, set[str]] = {}
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        for r in conn.execute(f"SELECT card_id, variant FROM inventory WHERE user_id = ? AND quantity > 0 "
                              f"AND card_id IN ({','.join('?' * len(part))})", [collection_id, *part]):
            out.setdefault(r["card_id"], set()).add(r["variant"])
    return out


def _product_ids(conn: sqlite3.Connection, ids: list[str]) -> dict[str, int]:
    """card id -> the TCGplayer product TCGdex names for it (cards.tcgplayer_id), round 28."""
    out: dict[str, int] = {}
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        out.update({r[0]: r[1] for r in conn.execute(
            f"SELECT id, tcgplayer_id FROM cards WHERE tcgplayer_id IS NOT NULL AND id IN ({','.join('?' * len(part))})", part)})
    return out


def product_printings(conn: sqlite3.Connection, group_id: int | None, product_id: int) -> dict[str, float]:
    """printing -> TCGCSV market price of one product (group_id None: in whichever set it was read)."""
    out: dict[str, float] = {}
    sql = "SELECT subtype, market FROM tcgcsv_product_prices WHERE product_id = ?"
    for r in conn.execute(sql + " AND group_id = ?" if group_id is not None else sql,
                          (product_id, group_id) if group_id is not None else (product_id,)):
        variant = tcgcsv.SUBTYPES.get((r["subtype"] or "").lower()) or tcgcsv.SUBTYPES.get(r["subtype"])
        if variant and r["market"] and variant not in out:
            out[variant] = r["market"]
    return out


def for_cards(conn: sqlite3.Connection, cards: list[dict], collection_id: int | None = None
              ) -> tuple[dict[str, float], set[tuple[str, str, str | None]]]:
    """(card key -> tile price, sets waiting for TCGCSV prices as (language, TCGdex set id, set name)).

    Each card is a dict with 'key' (what the result is returned under), 'id' (our card id, stored or not), and for
    cards nobody keeps 'lang', 'set_id' (TCGdex's), 'set_name', 'number' and 'english' (the English name)."""
    ids = [c["id"] for c in cards if c.get("id")]
    stored, owned = _stored(conn, ids), _owned(conn, collection_id, ids)
    products = _product_ids(conn, ids)
    out: dict[str, float] = {}
    waiting: set[tuple[str, str, str | None]] = set()
    groups: dict[tuple, tuple | None] = {}
    for c in cards:
        have = stored.get(c.get("id") or "")
        if have:
            v = main_printing(have, owned.get(c["id"]))
            if v:
                out[c["key"]] = have[v]
            continue
        pid = c.get("product_id") or products.get(c.get("id") or "")
        if pid:  # round 28: the product TCGdex names, in whichever TCGplayer set it was read
            have = product_printings(conn, None, int(pid))
            v = main_printing(have)
            if v:
                out[c["key"]] = have[v]
                continue
        lang, set_id = c.get("lang") or "en", c.get("set_id")
        if lang not in tcgcsv.CATEGORIES or not set_id:
            continue
        gk = (lang, set_id)
        if gk not in groups:
            groups[gk] = tcgcsv.group_for(conn, lang, set_id, c.get("set_name"))
        group = groups[gk]
        if not group:
            continue
        if not tcgcsv.known(conn, group[1]):
            waiting.add((lang, set_id, c.get("set_name")))
            continue
        if pid:  # TCGdex names its product: that one or none (a number in another TCGplayer set is another card)
            product = conn.execute("SELECT * FROM tcgcsv_products WHERE group_id = ? AND product_id = ?",
                                   (group[1], int(pid))).fetchone()
            product = dict(product) if product else None
        else:
            product = tcgcsv.product_for(conn, group[1], c.get("number"), c.get("english"))
        if not product:
            continue
        have = product_printings(conn, group[1], product["product_id"])
        v = main_printing(have)
        if v:
            out[c["key"]] = have[v]
        elif product.get("market"):  # a product with no printing we know: its own price
            out[c["key"]] = product["market"]
    return out, waiting


def set_values(conn: sqlite3.Connection, group: tuple[int, int] | None) -> dict[str, float]:
    """number -> tile price of each product in a TCGCSV set (for sorting a set by value, round 27: the price shown,
    not the dearest printing). Two products with one number (two cards sharing it): the first by name."""
    if not group or not tcgcsv.known(conn, group[1]):
        return {}
    out: dict[str, float] = {}
    rows = conn.execute("SELECT product_id, number, name, market FROM tcgcsv_products WHERE group_id = ? "
                        "ORDER BY name", (group[1],)).fetchall()
    by_product: dict[int, dict[str, float]] = {}
    for r in conn.execute("SELECT product_id, subtype, market FROM tcgcsv_product_prices WHERE group_id = ?", (group[1],)):
        variant = tcgcsv.SUBTYPES.get((r["subtype"] or "").lower())
        if variant and r["market"]:
            by_product.setdefault(r["product_id"], {}).setdefault(variant, r["market"])
    for r in rows:
        if tcgcsv._patterned(r["name"]) or not r["number"] or r["number"] in out:
            continue
        have = by_product.get(r["product_id"], {})
        v = main_printing(have)
        price = have[v] if v else r["market"]
        if price:
            out[r["number"]] = price
    return out
