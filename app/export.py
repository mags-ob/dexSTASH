"""Download your stash or chases as a CSV file: dexSTASH's own columns, or Collectr's so the file can go back into
Collectr (or be imported here again). Written with a byte order mark so Excel and Numbers read Japanese and
Chinese names correctly."""

import csv
import io
import sqlite3
from datetime import date

from . import binders, display
from .inventory import CONDITION_LABELS, ORIGINS, VARIANTS

COLUMNS = ["Name", "English name", "Set", "Set code", "Number", "Language", "Printing", "Condition", "Quantity",
           "Binder", "Got it from", "Paid each", "Paid currency", "Copy note", "Price", "Price currency",
           "Price date", "Added by", "Added on"]
CHASE_COLUMNS = ["Name", "English name", "Set", "Set code", "Number", "Language", "Printing", "Price",
                 "Price currency", "Price date", "Chasing since", "Added by", "Shop links"]
# Collectr's export, in its order (the importer reads the same names).
COLLECTR_COLUMNS = ["Portfolio Name", "Category", "Set", "Product Name", "Card Number", "Rarity", "Variance", "Grade",
                    "Card Condition", "Average Cost Paid", "Quantity", "Market Price", "Price Override", "Watchlist",
                    "Date Added", "Notes"]
COLLECTR_VARIANCE = {"normal": "Normal", "holo": "Holofoil", "reverse": "Reverse Holofoil", "first_ed": "1st Edition",
                     "pokeball": "Reverse Holofoil", "masterball": "Reverse Holofoil"}
COLLECTR_NAME_MARK = {"pokeball": " (Poke Ball Pattern)", "masterball": " (Master Ball Pattern)"}
LANGUAGE_NAMES = {"en": "English", "ja": "Japanese", "zh-tw": "Chinese (Taiwan)", "zh-cn": "Chinese (mainland)",
                  "ko": "Korean"}

_LINES_SQL = """
SELECT i.id, i.card_id, i.variant, i.condition, i.quantity, i.acquired_price, i.acquired_currency, i.origin, i.note,
       c.name, c.number, c.rarity, c.set_id, c.twin_name, s.name AS set_name, s.language,
       p.price, p.price_local, p.local_currency, p.updated_at AS price_at,
       (SELECT MIN(l.at) FROM inventory_log l WHERE l.collection_id = i.user_id AND l.card_id = i.card_id
          AND l.variant = i.variant AND l.condition = i.condition AND l.delta > 0) AS added_on,
       (SELECT u.username FROM inventory_log l JOIN users u ON u.id = l.user_id WHERE l.collection_id = i.user_id
          AND l.card_id = i.card_id AND l.variant = i.variant AND l.condition = i.condition AND l.delta > 0
          ORDER BY l.id LIMIT 1) AS added_by
FROM inventory i JOIN cards c ON c.id = i.card_id JOIN sets s ON s.set_id = c.set_id
LEFT JOIN prices p ON p.card_id = i.card_id AND p.variant = i.variant
WHERE i.user_id = ?
ORDER BY s.release_date, c.set_id, c.sort_number, c.number, i.variant, i.condition
"""


def filename(what: str, today: date | None = None) -> str:
    """'dexstash-stash-2026-10-28.csv'."""
    return f"dexstash-{what}-{(today or display.today()).isoformat()}.csv"


def _csv(header: list[str], rows: list[list]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return ("﻿" + out.getvalue()).encode("utf-8")


def _set_code(set_id: str) -> str:
    """The code printed on the card (SVI, PAF…) for an English set, else the set's own code."""
    from .scanner import SET_CODES
    if ":" not in set_id:
        code = next((c for c, ids in SET_CODES.items() if ":" not in c and set_id in ids), None)
        if code:
            return code
    return display.set_code(set_id)


def _english(r) -> str:
    if (r["language"] or "en") == "en":
        return ""
    from . import web
    shown = web._en_name(r["name"], r["card_id"], r["twin_name"])
    return shown["en"] if shown["translated"] else ""


def _price(r) -> tuple[str, str]:
    """The shown price (the average) as saved in the install's currency, with its code."""
    if r["price"] is None:
        return "", ""
    if r["price_local"] is not None and r["local_currency"]:
        return f"{r['price_local']:.2f}", r["local_currency"]
    return f"{r['price']:.2f}", "USD"


def _binder_names(conn: sqlite3.Connection, collection_id: int) -> tuple[dict, dict]:
    names = {b["id"]: b["name"] for b in conn.execute("SELECT id, name FROM binders WHERE collection_id = ?",
                                                       (collection_id,))}
    return names, binders.placement(conn, collection_id)


def _lines(conn: sqlite3.Connection, collection_id: int, binder_id: int | None | str = "all") -> list[dict]:
    """Your stash's lines (a printing and condition each), or one binder's ('none': Not in a binder), with how
    many copies are in it and where the rest are."""
    names, spots = _binder_names(conn, collection_id)
    out = []
    for r in conn.execute(_LINES_SQL, (collection_id,)).fetchall():
        where = {b: q for b, q in (spots.get(r["id"]) or {None: r["quantity"]}).items() if q > 0}  # None: loose
        if binder_id != "all":
            want = None if binder_id == "none" else binder_id
            if not where.get(want):
                continue
            quantity, where = where[want], {want: where[want]}
        else:
            quantity = r["quantity"]
        label = [(names.get(b, "") if b is not None else "Not in a binder") + (f" ({q})" if len(where) > 1 else "")
                 for b, q in where.items()]
        out.append(dict(r, quantity=quantity, binder="; ".join(label)))
    return out


def stash(conn: sqlite3.Connection, collection_id: int, binder_id: int | None | str = "all") -> bytes:
    """Your stash (or one binder) in dexSTASH's columns: one row per printing and condition."""
    rows = []
    for r in _lines(conn, collection_id, binder_id):
        price, currency = _price(r)
        rows.append([
            r["name"], _english(r), r["set_name"], _set_code(r["set_id"]), r["number"],
            LANGUAGE_NAMES.get(r["language"], r["language"] or ""), VARIANTS.get(r["variant"], r["variant"]),
            CONDITION_LABELS.get(r["condition"], r["condition"]), r["quantity"], r["binder"],
            ORIGINS.get(r["origin"] or "", ""),
            f"{r['acquired_price']:.2f}" if r["acquired_price"] is not None else "",
            (r["acquired_currency"] or "USD") if r["acquired_price"] is not None else "",
            r["note"] or "", price, currency, (r["price_at"] or "")[:10], r["added_by"] or "",
            (r["added_on"] or "")[:10]])
    return _csv(COLUMNS, rows)


def collectr(conn: sqlite3.Connection, collection_id: int, binder_id: int | None | str = "all",
             portfolio: str = "dexSTASH") -> bytes:
    """The same lines in Collectr's export layout, so the file can be imported into Collectr or back here."""
    rows = []
    for r in _lines(conn, collection_id, binder_id):
        price, _ = _price(r)
        name = (_english(r) or r["name"]) + COLLECTR_NAME_MARK.get(r["variant"], "")
        if r["language"] == "ja":
            name += " (JP)"
        elif (r["language"] or "").startswith("zh"):
            name += " (CN)"
        rows.append([
            portfolio, "Pokemon", r["set_name"], name, r["number"], r["rarity"] or "",
            COLLECTR_VARIANCE.get(r["variant"], VARIANTS.get(r["variant"], "")), "Ungraded",
            CONDITION_LABELS.get(r["condition"], r["condition"]),
            f"{r['acquired_price']:.2f}" if r["acquired_price"] is not None else "", r["quantity"], price, "", "No",
            (r["added_on"] or "")[:10], r["note"] or ""])
    return _csv(COLLECTR_COLUMNS, rows)


def chases(conn: sqlite3.Connection, collection_id: int) -> bytes:
    """Your single-card chases, without prices paid: the card's price, since when, who added it and shop links."""
    rows = []
    for r in conn.execute(
            """SELECT w.card_id, w.variant, w.created_at, c.name, c.number, c.set_id, c.twin_name, s.name AS set_name,
                      s.language, u.username AS added_by,
                      (SELECT MIN(p.price) FROM prices p WHERE p.card_id = w.card_id) AS price,
                      (SELECT p.price_local FROM prices p WHERE p.card_id = w.card_id ORDER BY p.price LIMIT 1) AS price_local,
                      (SELECT p.local_currency FROM prices p WHERE p.card_id = w.card_id ORDER BY p.price LIMIT 1) AS local_currency,
                      (SELECT MAX(p.updated_at) FROM prices p WHERE p.card_id = w.card_id) AS price_at
               FROM wants w JOIN cards c ON c.id = w.card_id JOIN sets s ON s.set_id = c.set_id
               LEFT JOIN users u ON u.id = w.added_by
               WHERE w.user_id = ? ORDER BY w.created_at""", (collection_id,)).fetchall():
        price, currency = _price(r)
        links = [x[0] for x in conn.execute("SELECT url FROM chase_links WHERE collection_id = ? AND card_id = ? ORDER BY id",
                                            (collection_id, r["card_id"]))]
        rows.append([
            r["name"], _english(r), r["set_name"], _set_code(r["set_id"]), r["number"],
            LANGUAGE_NAMES.get(r["language"], r["language"] or ""),
            "Any" if (r["variant"] or "any") == "any" else VARIANTS.get(r["variant"], r["variant"]),
            price, currency, (r["price_at"] or "")[:10], (r["created_at"] or "")[:10], r["added_by"] or "",
            " ".join(links)])
    return _csv(CHASE_COLUMNS, rows)


def known_binder(conn: sqlite3.Connection, collection_id: int, binder_id: str) -> int | None | str:
    """A binder id from the address ('none': Not in a binder); raises LookupError when it isn't yours."""
    if binder_id == "none":
        return "none"
    if binder_id.isdigit() and conn.execute(
            "SELECT 1 FROM binders WHERE id = ? AND collection_id = ?", (int(binder_id), collection_id)).fetchone():
        return int(binder_id)
    raise LookupError(binder_id)
