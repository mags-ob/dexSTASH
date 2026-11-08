"""The ★ Chase box on a chased card: links to anywhere (a listing, a video, a
shop) and photos. The short note is an ordinary card note (notes.py). The box
goes with the chase: stop chasing and its links and photos are deleted."""

import sqlite3
from urllib.parse import urlsplit

from . import handmade

MAX_LINKS = 20
MAX_PHOTOS = 12


def links(conn: sqlite3.Connection, collection_id: int, card_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT l.*, u.username AS added_by_name FROM chase_links l LEFT JOIN users u ON u.id = l.added_by "
        "WHERE l.collection_id = ? AND l.card_id = ? ORDER BY l.id", (collection_id, card_id))]


def photos(conn: sqlite3.Connection, collection_id: int, card_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM chase_photos WHERE collection_id = ? AND card_id = ? ORDER BY id", (collection_id, card_id))]


def clean_url(url: str) -> tuple[str, str]:
    """(url, label) for a pasted link; the label is the site's name. Raises ValueError."""
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or "." not in (parts.hostname or ""):
        raise ValueError("That doesn't look like a link. Paste the whole address, starting with https://.")
    host = parts.hostname.removeprefix("www.")
    return url, host


def add_link(conn: sqlite3.Connection, collection_id: int, card_id: str, url: str, by: int | None) -> None:
    url, label = clean_url(url)
    if len(links(conn, collection_id, card_id)) >= MAX_LINKS:
        raise ValueError(f"A chase can have up to {MAX_LINKS} links.")
    conn.execute("INSERT INTO chase_links (collection_id, card_id, url, label, added_by) VALUES (?, ?, ?, ?, ?)",
                 (collection_id, card_id, url, label, by))
    conn.commit()


def remove_link(conn: sqlite3.Connection, collection_id: int, link_id: int) -> None:
    conn.execute("DELETE FROM chase_links WHERE id = ? AND collection_id = ?", (link_id, collection_id))
    conn.commit()


def add_photo(conn: sqlite3.Connection, collection_id: int, card_id: str, data: bytes, by: int | None) -> None:
    if len(photos(conn, collection_id, card_id)) >= MAX_PHOTOS:
        raise ValueError(f"A chase can have up to {MAX_PHOTOS} photos.")
    photo = handmade.save_photo(data)  # same checks and folder as a card's own photo
    conn.execute("INSERT INTO chase_photos (collection_id, card_id, photo, added_by) VALUES (?, ?, ?, ?)",
                 (collection_id, card_id, photo, by))
    conn.commit()


def remove_photo(conn: sqlite3.Connection, collection_id: int, photo_id: int) -> None:
    row = conn.execute("SELECT photo FROM chase_photos WHERE id = ? AND collection_id = ?", (photo_id, collection_id)).fetchone()
    if row is None:
        return
    conn.execute("DELETE FROM chase_photos WHERE id = ?", (photo_id,))
    conn.commit()
    handmade._drop_photo(row["photo"])


def has_any(conn: sqlite3.Connection, collection_id: int, card_id: str) -> bool:
    return bool(links(conn, collection_id, card_id) or photos(conn, collection_id, card_id))


def drop(conn: sqlite3.Connection, collection_id: int, card_id: str) -> None:
    """Stop chasing: the box's links and photos go."""
    gone = photos(conn, collection_id, card_id)
    conn.execute("DELETE FROM chase_links WHERE collection_id = ? AND card_id = ?", (collection_id, card_id))
    conn.execute("DELETE FROM chase_photos WHERE collection_id = ? AND card_id = ?", (collection_id, card_id))
    conn.commit()
    for p in gone:
        handmade._drop_photo(p["photo"])
