"""Cards added by hand, for sets TCGdex lists without cards (mainland Chinese
sets such as CSV7C and the CBB Gem Packs) or doesn't list at all.

They go in the same tables as TCGdex cards, with ids of the same shape
('zh-cn:CSV7C-141'), so they work everywhere a card does. If TCGdex adds the
card later, the next "Update prices" fills in its details and it stops
counting as added by hand.
"""

import io
import re
import sqlite3
import uuid
from pathlib import Path

from . import catalog, db
from .db import transaction
from .numbers import parse_number

FOLDER = db.DB_PATH.parent / "card-photos"
PHOTO_SIDE = 800
MAX_BYTES = 20_000_000

_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,15}$")
_NUMBER = re.compile(r"^\s*([A-Za-z]{0,4}\d{1,4}[A-Za-z]?)\s*(?:/\s*([A-Za-z]{0,4}\d{1,4}))?\s*$")


def split_number(text: str) -> tuple[str, int | None]:
    """'141/204' -> ('141', 204), '0602/07' -> ('0602', 7), 'TG05' -> ('TG05', None)."""
    m = _NUMBER.match(text or "")
    if not m:
        raise ValueError(f"'{text}' isn't a card number. Type it as printed, like 141/204.")
    total = m.group(2)
    return m.group(1).upper(), int(total) if total and total.isdigit() else None


def find_set(conn: sqlite3.Connection, lang: str, code: str) -> sqlite3.Row | None:
    """A set already stored for the language, by its printed code, ignoring case."""
    return conn.execute("SELECT * FROM sets WHERE language = ? AND set_id = ? COLLATE NOCASE",
                        (lang, catalog.local_id(lang, code))).fetchone()


def save_photo(data: bytes, folder: Path | None = None) -> str:
    """Store a picture of the card and return the address it's shown from."""
    if len(data) > MAX_BYTES:
        raise ValueError("That photo is too big.")
    from PIL import Image, ImageOps
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    except Exception:
        raise ValueError("That file isn't a photo.")
    image.thumbnail((PHOTO_SIDE, PHOTO_SIDE))
    folder = folder or FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.jpg"
    image.convert("RGB").save(folder / name, "JPEG", quality=85)
    return f"/card-photos/{name}"


def photo_path(name: str, folder: Path | None = None) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{32}\.jpg", name):
        return None
    path = (folder or FOLDER) / name
    return path if path.exists() else None


def _drop_photo(image_url: str | None, folder: Path | None = None) -> None:
    if image_url and image_url.startswith("/card-photos/"):
        path = photo_path(image_url.rsplit("/", 1)[1], folder)
        if path:
            path.unlink(missing_ok=True)


def add(conn: sqlite3.Connection, lang: str, code: str, number_text: str, name: str, user_id: int,
        rarity: str = "", set_name: str = "", tcgdex_set: dict | None = None, image_url: str | None = None) -> str:
    """Add a card by hand and return its id. The set is made too if it isn't
    stored yet, from tcgdex_set (TCGdex's entry for it) when there is one.
    Raises ValueError for bad input or a card that's already there."""
    code, name = code.strip(), name.strip()
    if not _CODE.match(code):
        raise ValueError("Type the set code as printed at the bottom of the card, like CSV7C.")
    if not name:
        raise ValueError("Type the card's name.")
    number, total = split_number(number_text)

    with transaction(conn):
        row = find_set(conn, lang, code)
        if row is None:
            if tcgdex_set:
                code = tcgdex_set.get("id") or code
                counts = tcgdex_set.get("cardCount") or {}
                printed = counts.get("official") or total or 0
                set_name = set_name.strip() or tcgdex_set.get("name") or code
                release = tcgdex_set.get("releaseDate")
            else:
                code, printed, set_name, release = code.upper(), total or 0, set_name.strip() or code.upper(), None
            set_id = catalog.local_id(lang, code)
            conn.execute("INSERT INTO sets (set_id, language, name, release_date, printed_total, total) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (set_id, lang, set_name, release, printed, printed))
        else:
            set_id, printed = row["set_id"], row["printed_total"]
            code = catalog.tcgdex_set_id(set_id)
            if not printed and total:
                printed = total
                conn.execute("UPDATE sets SET printed_total = ?, total = MAX(total, ?) WHERE set_id = ?",
                             (total, total, set_id))
        card_id = catalog.local_id(lang, f"{code}-{number}")
        if conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone():
            raise ValueError(f"#{number} is already in this set.")
        sort_number, is_main = parse_number(number, printed)
        conn.execute(
            "INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name, rarity, image_url, hand_added_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (card_id, set_id, number, sort_number, int(is_main), name, rarity.strip() or None, image_url, user_id))
    return card_id


def update(conn: sqlite3.Connection, card_id: str, name: str, rarity: str, image_url: str | None = None,
           folder: Path | None = None) -> None:
    """Change a hand-added card's name and rarity, and its picture when image_url is given."""
    card = conn.execute("SELECT * FROM cards WHERE id = ? AND hand_added_by IS NOT NULL", (card_id,)).fetchone()
    if card is None:
        raise ValueError("Only cards added by hand can be changed.")
    if not name.strip():
        raise ValueError("Type the card's name.")
    with transaction(conn):
        conn.execute("UPDATE cards SET name = ?, rarity = ? WHERE id = ?", (name.strip(), rarity.strip() or None, card_id))
        if image_url:
            conn.execute("UPDATE cards SET image_url = ? WHERE id = ?", (image_url, card_id))
    if image_url:
        _drop_photo(card["image_url"], folder)


def delete(conn: sqlite3.Connection, card_id: str, folder: Path | None = None) -> str:
    """Delete a hand-added card nobody has, wants or pulled. Returns the set
    id, or '' when the set went too because nothing was left in it."""
    card = conn.execute("SELECT * FROM cards WHERE id = ? AND hand_added_by IS NOT NULL", (card_id,)).fetchone()
    if card is None:
        raise ValueError("Only cards added by hand can be deleted.")
    in_use = conn.execute(
        "SELECT 1 FROM inventory WHERE card_id = ? UNION ALL SELECT 1 FROM wants WHERE card_id = ? "
        "UNION ALL SELECT 1 FROM pulls WHERE card_id = ? LIMIT 1", (card_id, card_id, card_id)).fetchone()
    if in_use:
        raise ValueError("Someone still has or wants this card. Remove those copies first.")
    set_id = card["set_id"]
    with transaction(conn):
        for table in ("notes", "prices", "price_snapshots"):
            conn.execute(f"DELETE FROM {table} WHERE card_id = ?", (card_id,))
        conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        empty = conn.execute("SELECT 1 FROM cards WHERE set_id = ? LIMIT 1", (set_id,)).fetchone() is None
        used = conn.execute("SELECT 1 FROM purchases WHERE set_id = ? LIMIT 1", (set_id,)).fetchone()
        if empty and not used:
            conn.execute("DELETE FROM sets WHERE set_id = ?", (set_id,))
            set_id = ""
    _drop_photo(card["image_url"], folder)
    return set_id


def for_language(conn: sqlite3.Connection, lang: str, number: str | None = None, name: str | None = None,
                 limit: int = 30) -> list[dict]:
    """Hand-added cards in a language matching a number and/or part of a name, for Find a card."""
    sql = ("SELECT c.*, s.name AS set_name, s.printed_total FROM cards c JOIN sets s ON s.set_id = c.set_id "
           "WHERE s.language = ? AND c.hand_added_by IS NOT NULL")
    args: list = [lang]
    if number:
        sql += " AND (c.number = ? COLLATE NOCASE OR (c.number GLOB '[0-9]*' AND c.number NOT GLOB '*[^0-9]*' " \
               "AND ? GLOB '[0-9]*' AND ? NOT GLOB '*[^0-9]*' AND CAST(c.number AS INTEGER) = CAST(? AS INTEGER)))"
        args += [number, number, number, number]
    if name:
        sql += " AND c.name LIKE ?"
        args.append(f"%{name}%")
    sql += " ORDER BY s.release_date DESC, c.sort_number LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]
