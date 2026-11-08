"""Recently viewed cards: the last cards anyone opened, shared by everyone on this server,
shown on an empty Find. Only the last KEEP are kept."""

import sqlite3

from . import clock

KEEP = 50
SHOWN = 9  # Three rows of three


def record(conn: sqlite3.Connection, card_id: str, name: str | None, number: str | None,
           image_url: str | None, href: str) -> None:
    """A card page or preview was opened: it moves to the front."""
    conn.execute("INSERT OR REPLACE INTO card_views (card_id, name, number, image_url, href, viewed_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (card_id, name or "", number or "", image_url, href, clock.now()))
    conn.execute("DELETE FROM card_views WHERE card_id NOT IN "
                 "(SELECT card_id FROM card_views ORDER BY viewed_at DESC, rowid DESC LIMIT ?)", (KEEP,))
    conn.commit()


def latest(conn: sqlite3.Connection, limit: int = SHOWN) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT card_id, name, number, image_url, href FROM card_views ORDER BY viewed_at DESC, rowid DESC LIMIT ?", (limit,))]
