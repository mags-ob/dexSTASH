"""Adding and removing cards, and parsing set-mode typed entries."""

import re
import sqlite3
from dataclasses import dataclass

from . import celebrate

VARIANTS = {
    "normal": "Normal",
    "holo": "Holo",
    "reverse": "Reverse Holo",
    "first_ed": "1st Edition",
    "star": "★",            # mainland Chinese prints
    "star2": "★★",
    "pokeball": "Poké Ball Reverse",
    "masterball": "Master Ball Reverse",
    "other": "Other",
}
CONDITIONS = ["NM", "LP", "MP", "HP", "DMG"]
# The TCGplayer / Collectr condition scale. Picking one is optional: copies are Near Mint unless you say otherwise.
CONDITION_LABELS = {"NM": "Near Mint", "LP": "Lightly Played", "MP": "Moderately Played", "HP": "Heavily Played",
                    "DMG": "Damaged"}

# Where a copy came from, picked by hand (optional).
ORIGINS = {"pack": "Pack", "single": "Single", "trade": "Trade", "gift": "Gift", "other": "Other"}

# How a copy got into the app, from inventory_log.source. Cards logged on the
# Spending page ('pull') were added by hand too.
METHODS = {"manual": "Added by hand", "pull": "Added by hand", "import": "Imported", "scan": "Scanned"}
METHOD_FILTERS = {"manual": "Added by hand", "import": "Imported", "scan": "Scanned"}

# Which cards.has_* column says a variant exists. Ball patterns and 'other'
# are not in the card data, so they are always allowed.
_VARIANT_FLAG = {"normal": "has_normal", "holo": "has_holo", "reverse": "has_reverse", "first_ed": "has_first_ed",
                 "star": "has_star", "star2": "has_star2"}

# '123', '045 x3', 'TG05', '177a x2', '-123' (remove one)
_ENTRY = re.compile(r"^\s*(-)?\s*([A-Za-z]*\d+[A-Za-z]?)\s*(?:[x*×]\s*(\d+))?\s*$")


@dataclass
class Entry:
    number: str
    quantity: int
    remove: bool


def parse_entry(text: str) -> Entry | None:
    m = _ENTRY.match(text)
    if not m:
        return None
    quantity = int(m.group(3) or 1)
    if quantity < 1 or quantity > 99:
        return None
    return Entry(number=m.group(2), quantity=quantity, remove=bool(m.group(1)))


def find_card(conn: sqlite3.Connection, set_id: str, number: str) -> sqlite3.Row | None:
    """Match a typed number to a card in the set. '45' finds '045', and
    letters are case-insensitive ('tg05' finds 'TG05')."""
    row = conn.execute(
        "SELECT * FROM cards WHERE set_id = ? AND number = ? COLLATE NOCASE", (set_id, number)
    ).fetchone()
    if row is None and number.isdigit():
        row = conn.execute(
            "SELECT * FROM cards WHERE set_id = ? AND number GLOB '[0-9]*' "
            "AND number NOT GLOB '*[^0-9]*' AND CAST(number AS INTEGER) = ?",
            (set_id, int(number)),
        ).fetchone()
    return row


def variant_exists(card: sqlite3.Row, variant: str) -> bool:
    flag = _VARIANT_FLAG.get(variant)
    return True if flag is None else _has(card, flag)


def adjust(conn: sqlite3.Connection, user_id: int, card_id: str, variant: str, condition: str, delta: int,
           by: int | None = None, source: str = "manual", origin: str | None = None) -> int:
    """Add (delta > 0) or remove (delta < 0) copies. user_id is the collection;
    by is the person, for the history. Returns the new quantity for that
    variant and condition; the row is deleted when it reaches 0."""
    key = (user_id, card_id, variant, condition)
    row = conn.execute(
        "SELECT quantity FROM inventory WHERE user_id = ? AND card_id = ? AND variant = ? AND condition = ?", key
    ).fetchone() if variant in VARIANTS and condition in CONDITIONS else None
    new_qty = set_quantity(conn, user_id, card_id, variant, condition,
                           max((row["quantity"] if row else 0) + delta, 0), by=by, source=source, origin=origin)
    conn.commit()
    return new_qty


def set_quantity(conn: sqlite3.Connection, user_id: int, card_id: str, variant: str, condition: str, quantity: int,
                 by: int | None = None, source: str = "manual", origin: str | None = None, cheer: bool = True) -> int:
    """Make the collection hold exactly `quantity` of this printing and condition,
    and log the change. origin (pack, trade...) is where added copies came from;
    the line keeps the latest one given. Doesn't commit."""
    if variant not in VARIANTS or condition not in CONDITIONS:
        raise ValueError("Unknown variant or condition.")
    origin = origin if origin in ORIGINS else None
    key = (user_id, card_id, variant, condition)
    row = conn.execute(
        "SELECT quantity FROM inventory WHERE user_id = ? AND card_id = ? AND variant = ? AND condition = ?", key
    ).fetchone()
    current = row["quantity"] if row else 0
    quantity = max(quantity, 0)
    if quantity == current:
        return quantity
    if cheer and quantity > current and by is not None and not conn.execute(  # The first copy of a chase
            "SELECT 1 FROM inventory WHERE user_id = ? AND card_id = ?", (user_id, card_id)).fetchone() and conn.execute(
            "SELECT 1 FROM wants WHERE user_id = ? AND card_id = ?", (user_id, card_id)).fetchone():
        celebrate.noted(by, card_id)
    if quantity == 0:
        conn.execute("DELETE FROM inventory WHERE user_id = ? AND card_id = ? AND variant = ? AND condition = ?", key)
    elif row:
        conn.execute(
            "UPDATE inventory SET quantity = ? WHERE user_id = ? AND card_id = ? AND variant = ? AND condition = ?",
            (quantity, *key),
        )
    else:
        conn.execute(
            "INSERT INTO inventory (user_id, card_id, variant, condition, quantity, acquired_date) "
            "VALUES (?, ?, ?, ?, ?, date('now'))",
            (*key, quantity),
        )
    added = quantity > current
    if added and origin:
        conn.execute("UPDATE inventory SET origin = ? WHERE user_id = ? AND card_id = ? AND variant = ? AND condition = ?",
                     (origin, *key))
    conn.execute(
        "INSERT INTO inventory_log (collection_id, card_id, variant, condition, delta, user_id, source, origin) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, card_id, variant, condition, quantity - current, by, source, origin if added else None),
    )
    return quantity


def move(conn: sqlite3.Connection, user_id: int, card_id: str, variant: str, condition: str,
         to_variant: str, to_condition: str, by: int | None = None) -> int:
    """Change the printing and/or condition of every copy on one line (all of
    them, or the line keeps none). Paid price, origin, notes and pulls go with
    them. Returns how many copies moved."""
    if to_variant not in VARIANTS or to_condition not in CONDITIONS:
        raise ValueError("Unknown variant or condition.")
    key = (user_id, card_id, variant, condition)
    row = conn.execute(
        "SELECT * FROM inventory WHERE user_id = ? AND card_id = ? AND variant = ? AND condition = ?", key
    ).fetchone()
    if row is None or (variant, condition) == (to_variant, to_condition):
        return 0
    target = conn.execute(
        "SELECT * FROM inventory WHERE user_id = ? AND card_id = ? AND variant = ? AND condition = ?",
        (user_id, card_id, to_variant, to_condition),
    ).fetchone()
    qty = row["quantity"]
    set_quantity(conn, user_id, card_id, variant, condition, 0, by=by, cheer=False)
    set_quantity(conn, user_id, card_id, to_variant, to_condition, qty + (target["quantity"] if target else 0), by=by,
                 cheer=False)
    if target is None:  # a new line keeps what the old one knew
        conn.execute(
            "UPDATE inventory SET acquired_price = ?, acquired_currency = ?, acquired_date = ?, origin = ?, "
            "storage_location = ?, note = ? WHERE user_id = ? AND card_id = ? AND variant = ? AND condition = ?",
            (row["acquired_price"], row["acquired_currency"], row["acquired_date"], row["origin"], row["storage_location"], row["note"],
             user_id, card_id, to_variant, to_condition),
        )
    conn.execute("UPDATE notes SET variant = ?, condition = ? WHERE collection_id = ? AND card_id = ? "
                 "AND variant = ? AND condition = ?", (to_variant, to_condition, *key))
    # Pulls from your purchases follow, merging into a pull of the new printing from the same purchase.
    for p in conn.execute(
        "SELECT pl.* FROM pulls pl JOIN purchases pu ON pu.id = pl.purchase_id "
        "WHERE pu.user_id = ? AND pl.card_id = ? AND pl.variant = ? AND pl.condition = ?", key
    ).fetchall():
        other = conn.execute("SELECT id FROM pulls WHERE purchase_id = ? AND card_id = ? AND variant = ? AND condition = ?",
                             (p["purchase_id"], card_id, to_variant, to_condition)).fetchone()
        if other:
            conn.execute("UPDATE pulls SET quantity = quantity + ? WHERE id = ?", (p["quantity"], other["id"]))
            conn.execute("DELETE FROM pulls WHERE id = ?", (p["id"],))
        else:
            conn.execute("UPDATE pulls SET variant = ?, condition = ? WHERE id = ?", (to_variant, to_condition, p["id"]))
    conn.commit()
    return qty


def set_origin(conn: sqlite3.Connection, user_id: int, card_id: str, variant: str, condition: str,
               origin: str | None) -> None:
    """Change where a line's copies came from ('' or None clears it)."""
    conn.execute("UPDATE inventory SET origin = ? WHERE user_id = ? AND card_id = ? AND variant = ? AND condition = ?",
                 (origin if origin in ORIGINS else None, user_id, card_id, variant, condition))
    conn.commit()


def set_note(conn: sqlite3.Connection, line_id: int, note: str) -> None:
    """The copy note, one per printing + condition line (200 characters)."""
    conn.execute("UPDATE inventory SET note = ? WHERE id = ?", (" ".join((note or "").split())[:200] or None, line_id))
    conn.commit()


def holdings(conn: sqlite3.Connection, user_id: int, card_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT i.id, variant, condition, quantity, acquired_price, acquired_currency, origin, note, "
        "(SELECT MIN(l.at) FROM inventory_log l WHERE l.collection_id = i.user_id AND l.card_id = i.card_id "
        " AND l.variant = i.variant AND l.condition = i.condition AND l.delta > 0) AS first_added, "
        "(SELECT MAX(l.at) FROM inventory_log l WHERE l.collection_id = i.user_id AND l.card_id = i.card_id "
        " AND l.variant = i.variant AND l.condition = i.condition AND l.delta > 0) AS last_added "
        "FROM inventory i WHERE user_id = ? AND card_id = ? ORDER BY variant, condition",
        (user_id, card_id),
    )
    added = methods(conn, user_id, card_id)
    return [dict(r, variant_label=VARIANTS[r["variant"]], origin_label=ORIGINS.get(r["origin"]),
                 methods=added.get((r["variant"], r["condition"]), [])) for r in rows]


def methods(conn: sqlite3.Connection, user_id: int, card_id: str | None = None) -> dict:
    """(variant, condition) -> how and by whom copies were added, from the history:
    [{'method': 'scan', 'user_id': 2, 'label': 'Scanned by Sam'}, ...], oldest first.
    With no card_id, keyed (card_id, variant, condition) for the whole collection."""
    sql = ("SELECT l.card_id, l.variant, l.condition, l.source, l.user_id, "
           "COALESCE(u.username, CASE WHEN l.user_id IS NULL THEN NULL ELSE 'a removed user' END) AS name "
           "FROM inventory_log l LEFT JOIN users u ON u.id = l.user_id "
           "WHERE l.collection_id = ? AND l.delta > 0")
    params: tuple = (user_id,)
    if card_id is not None:
        sql += " AND l.card_id = ?"
        params += (card_id,)
    out: dict = {}
    for r in conn.execute(sql + " ORDER BY l.id", params):
        key = (r["variant"], r["condition"]) if card_id is not None else (r["card_id"], r["variant"], r["condition"])
        method = "manual" if r["source"] == "pull" else r["source"]
        label = METHODS.get(r["source"], r["source"]) + (f" by {r['name']}" if r["name"] else "")
        entries = out.setdefault(key, [])
        if not any(e["label"] == label for e in entries):
            entries.append({"method": method, "user_id": r["user_id"], "label": label})
    return out


SOURCES = {"manual": "by hand", "pull": "from a purchase", "import": "from an import", "scan": "from a scan",
           "bulk": "with Select › Remove"}


def history(conn: sqlite3.Connection, user_id: int, card_id: str, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT l.*, COALESCE(u.username, CASE WHEN l.user_id IS NULL THEN NULL ELSE 'a removed user' END) AS name "
        "FROM inventory_log l LEFT JOIN users u ON u.id = l.user_id "
        "WHERE l.collection_id = ? AND l.card_id = ? ORDER BY l.id DESC LIMIT ?",
        (user_id, card_id, limit),
    )
    return [dict(r, variant_label=VARIANTS.get(r["variant"], r["variant"]), source_label=SOURCES.get(r["source"], r["source"]),
                 origin_label=ORIGINS.get(r["origin"])) for r in rows]


def _has(card, flag: str) -> bool:
    """A printing flag of a cards row, or of a dict with only some of them."""
    try:
        return bool(card[flag])
    except (KeyError, IndexError):
        return False


def offered_variants(card, priced: set[str]) -> list[str]:
    """The printings to offer when adding this card: the ones with a price,
    plus any the card data lists. Every printing if nothing is known.
    card is a cards row, or None for a card that isn't stored yet."""
    known = set(priced)
    if card is not None:
        known |= {v for v, flag in _VARIANT_FLAG.items() if _has(card, flag)}
    return [v for v in VARIANTS if v in known] or list(VARIANTS)


def default_variant(offered: list[str], priced: list[str], remembered: str | None = None) -> str:
    """The printing to pick first: the last one used if this card has a price
    for it, else the first printing TCGplayer lists, else the first offered.
    So a card with no Normal printing never defaults to Normal."""
    if remembered in priced:
        return remembered
    for v in VARIANTS:  # TCGplayer's own order: Normal, Holo, Reverse Holo...
        if v in priced:
            return v
    return offered[0] if offered else "normal"

