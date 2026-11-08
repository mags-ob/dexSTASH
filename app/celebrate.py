""""You got it!" when the first copy of a card you're chasing goes into your stash, by hand, from a
scan, an import or Find. Each person's new ones wait here (in memory: a restart only skips a pop-up) until their
next page asks for them, so a batch of scans or an import shows one pop-up listing them all."""

import sqlite3
import threading

from . import chase_box

_lock = threading.Lock()
_waiting: dict[int, list[str]] = {}  # person (users.id) -> card ids, oldest first
MOST = 20  # an import that finds more shows the first 20 and "and N more"


def noted(by: int | None, card_id: str) -> None:
    """inventory.set_quantity: the first copy of a chased card was just added by this person."""
    if by is None:
        return
    with _lock:
        cards = _waiting.setdefault(by, [])
        if card_id not in cards:
            cards.append(card_id)


def waiting(user) -> bool:
    """Whether this person has one waiting; the page then asks for it (/got), so a page drawn twice
    (the back-button save loads it once to check where it went) doesn't use it up."""
    return bool(user) and bool(_waiting.get(user["id"]))


def take(conn: sqlite3.Connection, user) -> list[dict]:
    """This person's waiting cards that are still chased and owned, and forget them."""
    with _lock:
        card_ids = _waiting.pop(user["id"], [])
    out = []
    for card_id in card_ids:
        row = conn.execute(
            "SELECT w.id AS want_id, c.id AS card_id, c.name, c.number, c.image_url, s.name AS set_name "
            "FROM wants w JOIN cards c ON c.id = w.card_id JOIN sets s ON s.set_id = c.set_id "
            "WHERE w.user_id = ? AND w.card_id = ? AND EXISTS (SELECT 1 FROM inventory i "
            "WHERE i.user_id = w.user_id AND i.card_id = w.card_id)",
            (user["collection_id"], card_id)).fetchone()
        if row is None:
            continue
        d = dict(row)
        d["links"] = len(chase_box.links(conn, user["collection_id"], card_id))
        d["photos"] = len(chase_box.photos(conn, user["collection_id"], card_id))
        out.append(d)
    return out


def clear() -> None:
    with _lock:
        _waiting.clear()
