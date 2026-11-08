"""Chases (once called wants): cards you're looking for. A chase is for the
whole card, any printing, and shows the card's current price."""

import sqlite3

from . import chase_box
from .prices import extract


def add(conn: sqlite3.Connection, user_id: int, card_id: str, by: int | None = None) -> None:
    conn.execute("INSERT OR IGNORE INTO wants (user_id, card_id, variant, added_by) VALUES (?, ?, 'any', ?)",
                 (user_id, card_id, by))
    conn.commit()


def remove(conn: sqlite3.Connection, user_id: int, want_id: int) -> None:
    """Stop chasing; the card's ★ Chase box (links and photos) goes with it."""
    row = conn.execute("SELECT card_id FROM wants WHERE id = ? AND user_id = ?", (want_id, user_id)).fetchone()
    conn.execute("DELETE FROM wants WHERE id = ? AND user_id = ?", (want_id, user_id))
    conn.commit()
    if row is not None:
        chase_box.drop(conn, user_id, row["card_id"])


# The price is TCGplayer's cheapest printing; how many printings have one says whether it's "from".
LIST_SQL = """
SELECT w.id, w.card_id, u.username AS added_by_name, c.name, c.number, c.rarity, c.image_url, c.pricing_json, c.twin_name,
       c.set_id, s.name AS set_name, s.logo_url, s.language,
       (SELECT MIN(price) FROM prices WHERE card_id = w.card_id) AS price,
       (SELECT COUNT(*) FROM prices WHERE card_id = w.card_id) AS priced,
       (SELECT COALESCE(SUM(quantity), 0) FROM inventory i WHERE i.user_id = w.user_id AND i.card_id = w.card_id) AS owned
FROM wants w
JOIN cards c ON c.id = w.card_id
JOIN sets s ON s.set_id = c.set_id
LEFT JOIN users u ON u.id = w.added_by
WHERE w.user_id = ?
ORDER BY w.created_at DESC, w.id DESC
"""


def _live_prices(detail: dict | None) -> list[float]:
    return [p for _, source, p, _ in extract(detail or {}) if source == "tcgplayer"]


def list_wants(conn: sqlite3.Connection, user_id: int, live=None) -> list[dict]:
    """live(card_id) gives a card's TCGdex data, for chased cards you don't
    have (their prices aren't stored). 'from' is set when the card has more
    than one priced printing, so the price is the lowest of them."""
    out = []
    for r in conn.execute(LIST_SQL, (user_id,)):
        d = dict(r)
        if d["price"] is None and live is not None:
            found = _live_prices(live(d["card_id"]))
            d["price"], d["priced"] = min(found, default=None), len(found)
        d["from"] = d["priced"] > 1
        out.append(d)
    return out


def for_card(conn: sqlite3.Connection, user_id: int, card_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM wants WHERE user_id = ? AND card_id = ?", (user_id, card_id)).fetchone()
    return dict(row) if row else None


def wanted_ids(conn: sqlite3.Connection, user_id: int, set_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT w.card_id FROM wants w JOIN cards c ON c.id = w.card_id WHERE w.user_id = ? AND c.set_id = ?",
        (user_id, set_id),
    )
    return {r[0] for r in rows}
