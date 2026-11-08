"""Scanning cards in batches: photos are saved, read one at a time in the
background, and wait for you to confirm them, even after you close the app.

Confirming adds the card (history source 'scan') and deletes the photo and
the text read from it; skipping deletes them too. Photos nobody got to stay
until you add, remove or delete them. Each photo has its own language,
worked out from the text read on it; batches from older versions give their rows the batch's language.
"""

import io
import json
import logging
import re
import sqlite3
import threading
import uuid
from pathlib import Path

from . import catalog, db, fx, inventory, pictures, scanner, search

log = logging.getLogger("dexstash.scan")
GUESSES = 5  # guesses kept per photo, best first

MAX_BYTES = 20_000_000
MAX_SIDE = 1600       # photos are shrunk to this; plenty for the small print at the bottom
PICTURE = ("picture_waiting", "picture_searching", "picture_ready")  # in the Picture search queue
PENDING = ("waiting", "reading", "ready", "nomatch", "failed") + PICTURE
OPEN = ("ready", "nomatch", "failed", "picture_ready")  # a card can be picked for these
FOLDER = db.DB_PATH.parent / "scans"

_IMAGE_STARTS = (b"\xff\xd8\xff", b"\x89PNG", b"RIFF", b"GIF8")


def _looks_like_image(data: bytes) -> bool:
    return data.startswith(_IMAGE_STARTS) or data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftyphevc")


def _shrink(data: bytes) -> bytes:
    """Upright and at most MAX_SIDE, as JPEG. Unchanged if Pillow isn't installed
    or can't open it (the reader then says so)."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return data
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        image.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        image.convert("RGB").save(out, "JPEG", quality=85)
        return out.getvalue()
    except Exception:
        return data


# --- batches and photos -----------------------------------------------------

def create_batch(conn: sqlite3.Connection, collection_id: int, user_id: int, origin: str | None = None,
                 language: str = "en") -> int:
    origin = origin if origin in inventory.ORIGINS else None
    batch_id = conn.execute("INSERT INTO scan_batches (collection_id, created_by, origin, language) VALUES (?, ?, ?, ?)",
                            (collection_id, user_id, origin, language)).lastrowid
    conn.commit()
    return batch_id


def get_batch(conn: sqlite3.Connection, collection_id: int, batch_id: int) -> dict | None:
    row = conn.execute("SELECT b.*, u.username AS created_by_name FROM scan_batches b "
                       "LEFT JOIN users u ON u.id = b.created_by WHERE b.id = ? AND b.collection_id = ?",
                       (batch_id, collection_id)).fetchone()
    return dict(row) if row else None


BATCH_NAME_MAX = 60


def set_batch_name(conn: sqlite3.Connection, batch_id: int, name: str) -> str | None:
    """A batch's name (60 characters at most); empty goes back to showing the date. Commits."""
    name = " ".join((name or "").split())[:BATCH_NAME_MAX] or None
    conn.execute("UPDATE scan_batches SET name = ? WHERE id = ?", (name, batch_id))
    conn.commit()
    return name


def set_batch_origin(conn: sqlite3.Connection, batch_id: int, origin: str | None) -> None:
    conn.execute("UPDATE scan_batches SET origin = ? WHERE id = ?",
                 (origin if origin in inventory.ORIGINS else None, batch_id))
    conn.commit()


def sent_before(conn: sqlite3.Connection, batch_id: int, client_id: str | None) -> int | None:
    """A photo the phone sent already (a resumed upload): its scan, so a resend is ignored."""
    if not client_id:
        return None
    row = conn.execute("SELECT id FROM scans WHERE batch_id = ? AND client_id = ?", (batch_id, client_id)).fetchone()
    return row["id"] if row else None


class Repeat(Exception):
    """The phone sent a photo the batch already has (the same client id, twice at once). args[0]: its scan id."""


def add_photo(conn: sqlite3.Connection, batch: dict, user_id: int, data: bytes, folder: Path | None = None,
              client_id: str | None = None) -> int:
    if len(data) > MAX_BYTES:
        raise ValueError("That photo is too big.")
    if not _looks_like_image(data):
        raise ValueError("That file isn't a photo.")
    folder = folder or FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.jpg"
    (folder / name).write_bytes(_shrink(data))
    try:
        scan_id = conn.execute(
            "INSERT INTO scans (batch_id, collection_id, created_by, photo, status, client_id) VALUES (?, ?, ?, ?, 'waiting', ?)",
            (batch["id"], batch["collection_id"], user_id, name, client_id or None)).lastrowid
    except sqlite3.IntegrityError:  # the same photo arrived twice at once
        (folder / name).unlink(missing_ok=True)
        conn.commit()  # The failed insert's transaction mustn't stay open
        raise Repeat(sent_before(conn, batch["id"], client_id))
    except sqlite3.OperationalError:  # busy too long: the phone sends it again later
        (folder / name).unlink(missing_ok=True)
        raise
    conn.commit()
    return scan_id


def photo_path(scan: dict, folder: Path | None = None) -> Path | None:
    if not scan.get("photo"):
        return None
    path = (folder or FOLDER) / scan["photo"]
    return path if path.exists() else None


def _drop_photo(conn: sqlite3.Connection, scan: dict, folder: Path | None = None) -> None:
    path = photo_path(scan, folder)
    if path:
        path.unlink(missing_ok=True)
    conn.execute("UPDATE scans SET photo = NULL, read_json = NULL, guesses_json = NULL, picture_json = NULL, "
                 "picture_vector = NULL WHERE id = ?",
                 (scan["id"],))


def get_scan(conn: sqlite3.Connection, collection_id: int, scan_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM scans WHERE id = ? AND collection_id = ?", (scan_id, collection_id)).fetchone()
    return _with_details(conn, dict(row)) if row else None


def _with_details(conn: sqlite3.Connection, scan: dict) -> dict:
    scan["reading"] = scanner.Reading.from_json(scan["read_json"])
    scan["guesses"] = json.loads(scan["guesses_json"]) if scan["guesses_json"] else []
    scan["matches"] = json.loads(scan["picture_json"]) if scan.get("picture_json") else []
    scan["card"] = None
    if scan["card_id"]:
        card = conn.execute("SELECT c.id, c.name, c.number, c.image_url, s.name AS set_name FROM cards c "
                            "JOIN sets s ON s.set_id = c.set_id WHERE c.id = ?", (scan["card_id"],)).fetchone()
        scan["card"] = dict(card) if card else {"id": scan["card_id"], "name": scan["card_id"], "number": "",
                                                "image_url": None, "set_name": ""}
    scan["variant_label"] = inventory.VARIANTS.get(scan["variant"] or "")
    return scan


def batch_scans(conn: sqlite3.Connection, batch_id: int, since: int = 0) -> list[dict]:
    """In the order the photos were taken (the grid's tiles never move).
    With since, only the rows that changed after that rev."""
    rows = conn.execute("SELECT * FROM scans WHERE batch_id = ? AND rev > ? ORDER BY id",
                        (batch_id, since)).fetchall()
    return [_with_details(conn, dict(r)) for r in rows]


def batch_rev(conn: sqlite3.Connection, batch_id: int) -> int:
    return conn.execute("SELECT COALESCE(MAX(rev), 0) FROM scans WHERE batch_id = ?", (batch_id,)).fetchone()[0]


def batches(conn: sqlite3.Connection, collection_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT b.*, u.username AS created_by_name,
                  COUNT(s.id) AS photos,
                  SUM(s.status IN ('waiting', 'reading', 'picture_waiting', 'picture_searching')) AS reading,
                  SUM(s.status IN ('ready', 'nomatch', 'failed', 'picture_ready')) AS to_confirm,
                  SUM(s.status = 'added') AS added,
                  SUM(s.status = 'skipped') AS skipped
           FROM scan_batches b LEFT JOIN scans s ON s.batch_id = b.id LEFT JOIN users u ON u.id = b.created_by
           WHERE b.collection_id = ? GROUP BY b.id ORDER BY b.id DESC""", (collection_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_batch(conn: sqlite3.Connection, collection_id: int, batch_id: int, folder: Path | None = None) -> None:
    """Deletes the batch's leftover photos and the batch. Cards already added stay."""
    for scan in conn.execute("SELECT * FROM scans WHERE batch_id = ? AND collection_id = ?",
                             (batch_id, collection_id)).fetchall():
        _drop_photo(conn, dict(scan), folder)
    conn.execute("DELETE FROM scans WHERE batch_id = ? AND collection_id = ?", (batch_id, collection_id))
    conn.execute("DELETE FROM scan_batches WHERE id = ? AND collection_id = ?", (batch_id, collection_id))
    conn.commit()


# --- confirming -------------------------------------------------------------

def add_card(conn: sqlite3.Connection, scan: dict, user_id: int, card_id: str, variant: str, condition: str,
             quantity: int, origin: str | None, folder: Path | None = None) -> int:
    """Add the card for a scan, then forget the photo and what was read from it.
    card_id is our id (the set must be loaded). Returns the new quantity."""
    if scan["status"] not in OPEN:
        raise ValueError("This photo was already dealt with.")
    if variant not in inventory.VARIANTS or condition not in inventory.CONDITIONS:
        raise ValueError("Unknown variant or condition.")
    quantity = max(1, min(int(quantity), 99))
    with db.transaction(conn):
        current = conn.execute(
            "SELECT quantity FROM inventory WHERE user_id = ? AND card_id = ? AND variant = ? AND condition = ?",
            (scan["collection_id"], card_id, variant, condition)).fetchone()
        qty = inventory.set_quantity(conn, scan["collection_id"], card_id, variant, condition,
                                     (current["quantity"] if current else 0) + quantity, by=user_id,
                                     source="scan", origin=origin)
        _keep_scan_picture(conn, scan, card_id, folder)
        pictures.learn(conn, scan, card_id)  # Enhanced Search's score, and what it learns
        _drop_photo(conn, scan, folder)
        conn.execute("UPDATE scans SET status = 'added', card_id = ?, variant = ?, condition = ?, quantity = ?, "
                     "added_by = ?, message = NULL, done_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                     (card_id, variant, condition, quantity, user_id, scan["id"]))
    return qty


def _keep_scan_picture(conn: sqlite3.Connection, scan: dict, card_id: str, folder: Path | None = None) -> None:
    """P6: a card with no picture from anywhere keeps the card cut out of this scan."""
    from . import card_pictures
    path = photo_path(scan, folder)
    if path is None or not card_pictures.needs_scan(conn, card_id):
        return
    try:
        from PIL import Image
        with Image.open(path) as image:
            image.load()
            crop = pictures.background_crop(image) or pictures.text_crop(image, scanner.Reading.from_json(scan["read_json"]).box)
            card_pictures.save_scan(conn, card_id, crop)
    except Exception as e:
        log.info("scan %s not kept as a picture: %s", scan["id"], e)


# A result that arrives for a deleted scan keeps it deleted; the result waits in prev_status for Put back.
_KEEP_DELETED = ("status = CASE status WHEN 'skipped' THEN 'skipped' ELSE ? END, "
                 "prev_status = CASE status WHEN 'skipped' THEN ? ELSE prev_status END")
_PUT_BACK = {"reading": "waiting", "picture_searching": "picture_waiting"}


def skip(conn: sqlite3.Connection, scan: dict, folder: Path | None = None) -> None:
    """Delete Scan: it moves to Deleted scans with its photo kept, so it can be put back.
    The photo goes when the batch is done: Add all, Delete Batch or Clear finished batches."""
    if scan["status"] in ("added", "skipped", "expired"):
        return
    conn.execute("UPDATE scans SET prev_status = status, status = 'skipped', done_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                 (scan["id"],))
    pictures.unlearn(conn, scan["id"])
    conn.commit()


def put_back(conn: sqlite3.Connection, scan: dict) -> bool:
    """Put back: a deleted scan returns to where it was (one that was being read waits to be read again)."""
    if scan["status"] != "skipped" or not scan["photo"]:
        return False
    before = scan.get("prev_status") or "waiting"
    conn.execute("UPDATE scans SET status = ?, prev_status = NULL, done_at = NULL WHERE id = ?",
                 (_PUT_BACK.get(before, before), scan["id"]))
    pictures.unlearn(conn, scan["id"])
    conn.commit()
    return True


def drop_deleted(conn: sqlite3.Connection, batch_id: int, folder: Path | None = None) -> int:
    """The batch is done: deleted scans lose their photos for good."""
    rows = conn.execute("SELECT * FROM scans WHERE batch_id = ? AND status = 'skipped' AND photo IS NOT NULL",
                        (batch_id,)).fetchall()
    for scan in rows:
        _drop_photo(conn, dict(scan), folder)
    conn.commit()
    return len(rows)


def retry(conn: sqlite3.Connection, scan: dict) -> bool:
    """Read the photo again (after a failure, or if the guesses were off)."""
    if scan["status"] not in ("failed", "nomatch", "ready") or not scan["photo"]:
        return False
    conn.execute("UPDATE scans SET status = 'waiting', message = NULL, checked = 0, undo_json = NULL WHERE id = ?",
                 (scan["id"],))
    conn.commit()
    return True


def use_card(conn: sqlite3.Connection, scan: dict, guess: dict, variant: str | None = None,
             condition: str | None = None, quantity: int | None = None) -> None:
    """A card picked by hand becomes the scan's first guess, with the printing,
    condition and quantity to add it as when they were picked too."""
    if variant is not None and variant not in inventory.VARIANTS:
        raise ValueError("Unknown printing.")
    if condition is not None and condition not in inventory.CONDITIONS:
        raise ValueError("Unknown condition.")
    if quantity is not None:
        quantity = max(1, min(int(quantity), 99))
    guesses = [guess] + [g for g in scan["guesses"] if g["id"] != guess["id"] or g.get("lang") != guess.get("lang")]
    conn.execute("UPDATE scans SET guesses_json = ?, status = 'ready', message = NULL, variant = ?, condition = ?, "
                 "quantity = ?, checked = 0 WHERE id = ?",
                 (json.dumps(guesses[:GUESSES]), variant, condition, quantity, scan["id"]))
    pictures.unlearn(conn, scan["id"])
    conn.commit()


def _paid_currency(scan: dict, paid: float | None) -> str:
    """A price paid keeps its currency while it's the same amount, else it's in the chosen one."""
    if paid is not None and scan.get("paid") is not None and round(paid, 2) == round(scan["paid"], 2):
        return scan.get("paid_currency") or "USD"
    return fx.setting()


def set_line(conn: sqlite3.Connection, scan: dict, variant: str | None = None, condition: str | None = None,
             quantity: int | None = None, paid: float | None = None) -> None:
    """Review your batch: how the scan's card will be added. Only what's given changes."""
    if variant is not None and variant not in inventory.VARIANTS:
        raise ValueError("Unknown printing.")
    if condition is not None and condition not in inventory.CONDITIONS:
        raise ValueError("Unknown condition.")
    fields = {"variant": variant, "condition": condition,
              "quantity": None if quantity is None else max(1, min(int(quantity), 99))}
    sets = [(k, v) for k, v in fields.items() if v is not None]
    if paid is not None:
        sets.append(("paid", None if paid < 0 else round(paid, 2)))  # a negative paid clears it
        sets.append(("paid_currency", _paid_currency(scan, paid)))
    if not sets:
        return
    conn.execute(f"UPDATE scans SET {', '.join(k + ' = ?' for k, _ in sets)} WHERE id = ?",
                 (*(v for _, v in sets), scan["id"]))
    conn.commit()


def use_guess(conn: sqlite3.Connection, scan: dict, pick: int) -> None:
    """Scan Details' "Use this match" on one of the text guesses: it moves to the front."""
    if scan["status"] not in OPEN:
        raise ValueError("This photo was already dealt with.")
    if not 0 <= pick < len(scan["guesses"]):
        raise ValueError("Pick one of the guesses.")
    guesses = scan["guesses"][:]
    guesses.insert(0, guesses.pop(pick))
    conn.execute("UPDATE scans SET guesses_json = ?, status = 'ready', message = NULL, variant = NULL, checked = 0 "
                 "WHERE id = ?", (json.dumps(guesses), scan["id"]))
    pictures.unlearn(conn, scan["id"])
    conn.commit()


def matched(scan: dict) -> bool:
    """Has a card to add: read or picked, and not waiting on anything."""
    return scan["status"] in OPEN and bool(scan["guesses"])


def needs_match(conn: sqlite3.Connection, batch_id: int) -> int:
    """Photos left in the batch with no card to add yet: being read, in
    Enhanced Search, or with no match. They stay in the batch when the rest are added."""
    rows = conn.execute(f"SELECT status, guesses_json FROM scans WHERE batch_id = ? AND status IN ({','.join('?' * len(PENDING))})",
                        (batch_id, *PENDING)).fetchall()
    return sum(1 for r in rows if r["status"] not in OPEN or not r["guesses_json"] or r["guesses_json"] == "[]")


def to_review(conn: sqlite3.Connection, batch_id: int) -> list[dict]:
    """The matched photos still waiting to be added, oldest first, the order they were taken."""
    rows = conn.execute(f"SELECT * FROM scans WHERE batch_id = ? AND status IN ({','.join('?' * len(OPEN))}) ORDER BY id",
                        (batch_id, *OPEN)).fetchall()
    return [s for s in (_with_details(conn, dict(r)) for r in rows) if s["guesses"]]


# --- the batch screen -------------------------------------------------

_UNDO_FIELDS = ("status", "guesses_json", "variant", "condition", "quantity", "paid", "in_binder", "checked")


def counts(conn: sqlite3.Connection, batch_id: int) -> dict:
    """The summary line and the Add button: photos in the grid, how many are checked,
    how many still need a match, and how many cards Add would add."""
    ready = to_review(conn, batch_id)
    photos = conn.execute(f"SELECT COUNT(*) FROM scans WHERE batch_id = ? AND status IN ({','.join('?' * len(PENDING))})",
                          (batch_id, *PENDING)).fetchone()[0]
    return {"photos": photos, "checked": sum(1 for s in ready if s["checked"]), "needs_match": needs_match(conn, batch_id),
            "to_add": len(ready), "unchecked": [s["id"] for s in ready if not s["checked"]]}


def position(conn: sqlite3.Connection, scan: dict) -> tuple[int, int]:
    """"3 of 7": where the photo sits in the grid."""
    ids = [r[0] for r in conn.execute(
        f"SELECT id FROM scans WHERE batch_id = ? AND status IN ({','.join('?' * len(PENDING))}) ORDER BY id",
        (scan["batch_id"], *PENDING))]
    return (ids.index(scan["id"]) + 1 if scan["id"] in ids else 0), len(ids)


def next_open(conn: sqlite3.Connection, scan: dict) -> int | None:
    """Save & next: the next photo in grid order that can be looked at (photos being read are skipped)."""
    row = conn.execute(f"SELECT id FROM scans WHERE batch_id = ? AND id > ? AND status IN ({','.join('?' * len(OPEN))}) "
                       "ORDER BY id LIMIT 1", (scan["batch_id"], scan["id"], *OPEN)).fetchone()
    return row[0] if row else None


def snapshot(conn: sqlite3.Connection, scan: dict) -> None:
    """Opening the card view: remember the scan as it is, so Cancel can put it back.
    A view left open (the app closed) counts as cancelled when the card is opened again."""
    if scan.get("undo_json"):
        restore(conn, scan)
        scan = get_scan(conn, scan["collection_id"], scan["id"])
    conn.execute("UPDATE scans SET undo_json = ? WHERE id = ?",
                 (json.dumps({k: scan[k] for k in _UNDO_FIELDS}), scan["id"]))
    conn.commit()


def restore(conn: sqlite3.Connection, scan: dict) -> bool:
    """Cancel: the match and choices go back to how they were when the card view opened.
    A photo that moved on meanwhile (added, deleted, read again) is left as it is."""
    if not scan.get("undo_json"):
        return False
    before = json.loads(scan["undo_json"])
    if scan["status"] not in OPEN or before.get("status") not in OPEN:
        conn.execute("UPDATE scans SET undo_json = NULL WHERE id = ?", (scan["id"],))
        conn.commit()
        return False
    conn.execute(f"UPDATE scans SET {', '.join(k + ' = ?' for k in _UNDO_FIELDS)}, undo_json = NULL WHERE id = ?",
                 (*(before.get(k) for k in _UNDO_FIELDS), scan["id"]))
    conn.commit()
    return True


def save(conn: sqlite3.Connection, scan: dict, variant: str, condition: str, quantity: int,
         paid: float | None, in_binder: bool) -> None:
    """Save in the card view: how the card will be added, and it's checked (✓)."""
    if not matched(scan):
        raise ValueError("Pick the card first.")
    if variant not in inventory.VARIANTS:
        raise ValueError("Unknown printing.")
    if condition not in inventory.CONDITIONS:
        raise ValueError("Unknown condition.")
    conn.execute("UPDATE scans SET variant = ?, condition = ?, quantity = ?, paid = ?, paid_currency = ?, in_binder = ?, "
                 "checked = 1, undo_json = NULL WHERE id = ?",
                 (variant, condition, max(1, min(int(quantity), 99)), None if paid is None else round(paid, 2),
                  _paid_currency(scan, paid), int(bool(in_binder)), scan["id"]))
    conn.commit()


def set_all(conn: sqlite3.Connection, batch_id: int, condition: str | None = None, paid: float | None = None) -> int:
    """Set for all: the condition and/or price paid on every matched card not yet added.
    Nothing is marked checked. Returns how many cards changed."""
    if condition is not None and condition not in inventory.CONDITIONS:
        raise ValueError("Unknown condition.")
    ids = [s["id"] for s in to_review(conn, batch_id)]
    sets = ([("condition", condition)] if condition is not None else []) + \
           ([("paid", None if paid < 0 else round(paid, 2)), ("paid_currency", fx.setting())] if paid is not None else [])
    if not ids or not sets:
        return 0
    conn.execute(f"UPDATE scans SET {', '.join(k + ' = ?' for k, _ in sets)} WHERE id IN ({','.join('?' * len(ids))})",
                 (*(v for _, v in sets), *ids))
    conn.commit()
    return len(ids)


CAME = ("add", "new", "none")


def set_from(conn: sqlite3.Connection, batch_id: int, came: str, purchase_id: int | None = None,
             new_label: str = "", new_cost: float | None = None, binder: str = "", new_binder: str = "") -> None:
    """The batch's "From · Binder" line: where the cards came from and the binder they go in.
    It's kept for the next Add in the same batch."""
    came = came if came in CAME else "none"
    conn.execute("UPDATE scan_batches SET came = ?, purchase_id = ?, new_label = ?, new_cost = ?, binder = ?, new_binder = ? "
                 "WHERE id = ?", (came, purchase_id if came == "add" else None, (new_label or "").strip()[:80] or None,
                                  new_cost if came == "new" else None, binder or None,
                                  (new_binder or "").strip()[:40] or None if binder == "new" else None, batch_id))
    conn.commit()


# --- likeness words -------------------------------------------------------------

CLOSE, POSSIBLE, LONG_SHOT = "Very close", "Possible", "Long shot"
LIKENESS_CLASS = {CLOSE: "close", POSSIBLE: "maybe", LONG_SHOT: "long"}


def guess_word(guess: dict | None, first: bool = True) -> str | None:
    """A text guess: a sure one is Very close, any other best guess Possible.
    A card picked by hand gets no word ("Picked by you" shows instead)."""
    if not guess or guess.get("by_hand"):
        return None
    if guess.get("sure"):
        return CLOSE
    return POSSIBLE if first else None


def picture_words(matches: list[dict]) -> list[str]:
    """Enhanced Search matches, best first. The score is how alike the pictures
    look (0-100), and every card looks a bit alike, so the gap to the next
    match counts too. Tune the cut-offs on real cards."""
    words = []
    for i, m in enumerate(matches):
        score = m.get("score") or 0
        nxt = (matches[i + 1].get("score") or 0) if i + 1 < len(matches) else 0
        if i == 0 and score >= 85 and score - nxt >= 4:  # only the best can be Very close
            words.append(CLOSE)
        elif score >= 75:
            words.append(POSSIBLE)
        else:
            words.append(LONG_SHOT)
    return words


def send_to_pictures(conn: sqlite3.Connection, scan: dict) -> bool:
    """Into the Picture search queue."""
    if scan["status"] not in OPEN or not scan["photo"]:
        return False
    conn.execute("UPDATE scans SET status = 'picture_waiting', picture_json = NULL, message = NULL, undo_json = NULL "
                 "WHERE id = ?",
                 (scan["id"],))
    conn.commit()
    return True


def _batch_language(conn, scan: dict) -> dict:
    row = conn.execute("SELECT language FROM scan_batches WHERE id = ?", (scan["batch_id"],)).fetchone()
    return {"language": row["language"]} if row else {}


def picture_scan(conn: sqlite3.Connection, scan: dict, encoder, folder: Path | None = None) -> None:
    """Compare one queued photo with the picture index."""
    from PIL import Image
    path = photo_path(scan, folder)
    if path is None:
        conn.execute("UPDATE scans SET status = 'failed', message = 'The photo is missing.' WHERE id = ?", (scan["id"],))
        conn.commit()
        return
    lang = scan.get("language") or batch_majority(conn, scan["batch_id"]) or lang_of(scan, _batch_language(conn, scan))
    vector: list = []
    with Image.open(path) as im:
        matches = pictures.search(conn, encoder, im.convert("RGB"), scanner.Reading.from_json(scan["read_json"]).box,
                                  batch_lang=lang if lang in SCAN_LANGS else "en",
                                  vector_out=vector)
    message = None if matches else "No pictures to compare with yet. They're fingerprinted in the background."
    import numpy as np
    conn.execute(f"UPDATE scans SET {_KEEP_DELETED}, picture_json = ?, message = ?, picture_vector = ? WHERE id = ?",
                 ("picture_ready", "picture_ready", json.dumps(matches), message,
                  np.asarray(vector[0], np.float32).tobytes() if vector else None, scan["id"]))
    conn.commit()
    _ask_pictures(conn, scan, matches)


def clear_finished(conn: sqlite3.Connection, collection_id: int, folder: Path | None = None) -> int:
    """Delete the batches with nothing left to read or confirm. Cards added stay."""
    done = [r[0] for r in conn.execute(
        f"""SELECT b.id FROM scan_batches b WHERE b.collection_id = ? AND NOT EXISTS (
              SELECT 1 FROM scans s WHERE s.batch_id = b.id AND s.status IN ({','.join('?' * len(PENDING))}))""",
        (collection_id, *PENDING))]
    for batch_id in done:
        delete_batch(conn, collection_id, batch_id, folder)
    return len(done)


def drop_chinese_batches(conn: sqlite3.Connection, folder: Path | None = None) -> int:
    """Once, on upgrade: Chinese batches from before every Chinese set was here
    are deleted, photos and all. Cards already added from them stay."""
    if conn.execute("SELECT 1 FROM app_meta WHERE key = 'chinese_batches_dropped'").fetchone():
        return 0
    old = conn.execute("SELECT id, collection_id FROM scan_batches WHERE language = 'zh-cn'").fetchall()
    for batch_id, collection_id in old:
        delete_batch(conn, collection_id, batch_id, folder)
    conn.execute("INSERT INTO app_meta (key, value) VALUES ('chinese_batches_dropped', '1')")
    conn.commit()
    return len(old)


def clear_collection(conn: sqlite3.Connection, collection_id: int, folder: Path | None = None) -> None:
    for (batch_id,) in conn.execute("SELECT id FROM scan_batches WHERE collection_id = ?", (collection_id,)).fetchall():
        delete_batch(conn, collection_id, batch_id, folder)


# --- reading in the background ----------------------------------------------

class CardLookup:
    """Cards of TCGdex sets for matching, in one language: from the database
    when the set is loaded (cards added by hand included), else from TCGdex
    (kept for the rest of the run). Ids are TCGdex's, without our language prefix."""

    def __init__(self, conn: sqlite3.Connection, api_for, translate=None):
        self.conn, self.api_for = conn, api_for
        self.translate = translate or (lambda text, lang: None)  # an English Pokémon name in lang
        self._cards: dict[tuple[str, str], list[dict]] = {}
        self._sets: dict[str, list[dict]] = {}

    def sets(self, lang: str = "en") -> list[dict]:
        if lang not in self._sets:
            stored = [{"id": catalog.tcgdex_set_id(r["set_id"]), "name": r["name"],
                       "cardCount": {"official": r["printed_total"]}}
                      for r in self.conn.execute("SELECT * FROM sets WHERE language = ? ORDER BY release_date", (lang,))]
            try:
                found = list(self.api_for(lang).list_sets())
            except Exception:
                found = []  # offline: the sets already loaded are all there is to go on
            listed = {s["id"] for s in found}
            # Sets TCGdex doesn't list, such as ones made for cards added by hand, go last.
            self._sets[lang] = found + [s for s in stored if s["id"] not in listed]
        return self._sets[lang]

    def codes(self, lang: str = "en") -> dict[str, list[str]] | None:
        """Printed set codes for the language; None means the English ones."""
        return None if lang == "en" else scanner.set_codes_for(self.sets(lang))

    def cards_of(self, set_id: str, lang: str = "en") -> list[dict]:
        key = (lang, set_id)
        if key not in self._cards:
            rows = self.conn.execute("SELECT id, number, name, image_url FROM cards WHERE set_id = ?",
                                     (catalog.local_id(lang, set_id),)).fetchall()
            if rows:
                cards = [{"id": catalog.tcgdex_set_id(r["id"]), "number": r["number"], "name": r["name"],
                          "image": r["image_url"]} for r in rows]
            else:
                try:
                    data = self.api_for(lang).get_set(set_id)
                    cards = [{"id": c["id"], "number": c["localId"], "name": c["name"],
                              "image": f"{c['image']}/low.webp" if c.get("image") else None}
                             for c in data.get("cards", [])]
                except Exception as e:
                    log.warning("couldn't load set %s (%s): %s", set_id, lang, e)
                    cards = []
            self._cards[key] = cards
        return self._cards[key]

    def name_forms(self, name: str, lang: str) -> list[str]:
        """What a typed name is called on cards of lang: as typed, plus the Pokémon's name in lang
        when an English name was typed into a Japanese or Chinese batch."""
        forms = [name]
        if lang != "en":
            try:
                local = self.translate(name, lang)
            except Exception:
                local = None
            if local and local.casefold() != name.casefold():
                forms.append(local)
        return forms

    def _stored(self, where: str, args: tuple, lang: str, seen: set[str]) -> list[dict]:
        """Every card stored here in lang (mainland Chinese sets and cards added by hand included)."""
        out = []
        for r in self.conn.execute(
                "SELECT c.id, c.set_id, c.number, c.name, c.image_url FROM cards c JOIN sets s ON s.set_id = c.set_id "
                f"WHERE s.language = ? AND {where} LIMIT 60", (lang, *args)):
            card_id = catalog.tcgdex_set_id(r["id"])
            if card_id not in seen:
                seen.add(card_id)
                out.append({"id": card_id, "number": r["number"], "name": r["name"],
                            "set_id": catalog.tcgdex_set_id(r["set_id"]), "image": r["image_url"]})
        return out

    def search_name(self, name: str, lang: str = "en") -> list[dict]:
        out, seen = [], set()
        for form in self.name_forms(name, lang):
            try:
                found = self.api_for(lang).search_cards(name=form)
            except Exception:
                found = []
            for c in found[:60]:
                if c["id"] not in seen:
                    seen.add(c["id"])
                    out.append({"id": c["id"], "number": c.get("localId", ""), "name": c.get("name", ""),
                                "set_id": c["id"].rsplit("-", 1)[0],
                                "image": f"{c['image']}/low.webp" if c.get("image") else None})
            out += self._stored("c.name LIKE ?", (f"%{form}%",), lang, seen)
        return out

    def search_number(self, number: str, lang: str = "en") -> list[dict]:
        """Cards with this number in any set (TCGdex, plus every card stored here in lang)."""
        forms = search._number_forms(number)
        try:
            found = self.api_for(lang).search_cards(local_id=forms)
        except Exception:
            found = []
        out = [{"id": c["id"], "number": c.get("localId", ""), "name": c.get("name", ""),
                "set_id": c["id"].rsplit("-", 1)[0], "image": f"{c['image']}/low.webp" if c.get("image") else None}
               for c in found]
        seen = {c["id"] for c in out}
        return out + self._stored(f"c.number IN ({','.join('?' * len(forms))})", tuple(forms), lang, seen)


# --- Text search --------------------------------------------------------------

# Scan Details' three tabs, each from the picks below. Cards matching all three
# lead the first tab; when all three are empty the first tab falls back to name-only or number-only.
TABS = (
    ("name_number", "Name + Number", ("name_number_set", "name_number")),
    ("name_set", "Name + Set", ("name_set",)),
    ("number_set", "Set + Number", ("number_set",)),
)
GROUP_LIMIT = 30


def sets_for_code(lookup: CardLookup, code: str, lang: str = "en") -> list[str]:
    """Set ids for a printed set code ('SSP', 'M1S') or a set id typed as is."""
    code = (code or "").strip()
    if not code:
        return []
    codes = lookup.codes(lang) or scanner.SET_CODES
    upper = code.upper()
    ids = list(codes.get(upper, [])) + [i for k, v in codes.items() if k.startswith(upper + ":") for i in v]
    ids += [s["id"] for s in lookup.sets(lang) if s["id"].lower() == code.lower()]
    return list(dict.fromkeys(ids))


def text_tabs(lookup: CardLookup, lang: str, name: str = "", number: str = "", code: str = "") -> list[dict]:
    """Cards for Scan Details' tabs: [{key, label, cards}] for the three TABS, always all three
    (a tab can be empty). Cards are dicts with id, set_id, set_name, number, name, image
    (TCGdex ids in lang)."""
    name = (name or "").strip()
    number = (number or "").strip().split("/")[0].strip()
    if not (name or number or code):
        return [{"key": key, "label": label, "cards": []} for key, label, _ in TABS]
    sets = lookup.sets(lang)
    order = {s["id"]: i for i, s in enumerate(sets)}
    set_names = {s["id"]: s["name"] for s in sets}
    forms = getattr(lookup, "name_forms", lambda n, _lang: [n])(name, lang) if name else []

    def name_ok(card):
        return any(search._fold(f) in search._fold(card["name"] or "") or scanner._similar(f, card["name"]) >= 0.8
                   for f in forms)

    def number_ok(card):
        return bool(number) and scanner.same_number(card["number"] or "", number)

    in_set: list[dict] = []
    code_sets = sets_for_code(lookup, code, lang)
    for set_id in code_sets[:6]:
        in_set += [dict(c, set_id=set_id) for c in lookup.cards_of(set_id, lang)]
    code_ids = set(code_sets)
    elsewhere = (lookup.search_number(number, lang) if number else []) + (lookup.search_name(name, lang) if name else [])
    elsewhere = [c for c in elsewhere if c["set_id"] not in code_ids]

    def newest_first(cards):
        return sorted(cards, key=lambda c: -order.get(c["set_id"], len(order)))

    picks = {
        "name_number_set": [c for c in in_set if name_ok(c) and number_ok(c)],
        "name_number": newest_first([c for c in elsewhere if name_ok(c) and number_ok(c)]),
        "number_set": [c for c in in_set if number_ok(c)],
        "name_set": [c for c in in_set if name_ok(c)],
        "number": newest_first([c for c in elsewhere if number_ok(c)]),
        "name": newest_first([c for c in elsewhere if name_ok(c)]),
    }

    def tab(keys):
        cards, seen = [], set()
        for key in keys:
            for c in picks[key]:
                if c["id"] not in seen:
                    seen.add(c["id"])
                    cards.append(dict(c, set_name=set_names.get(c["set_id"], c["set_id"])))
        return cards[:GROUP_LIMIT]

    tabs = [{"key": key, "label": label, "cards": tab(keys)} for key, label, keys in TABS]
    if not any(t["cards"] for t in tabs):  # only a name or only a number was read
        tabs[0]["cards"] = tab(("name", "number"))
        tabs[0]["fallback"] = bool(tabs[0]["cards"])
    return tabs


def batch_tabs(lookup: CardLookup, batch_lang: str, name: str = "", number: str = "", code: str = "") -> list[dict]:
    """Scan Details' tabs for a batch: only its language. A Chinese batch searches mainland
    first, then Taiwan and Hong Kong, mainland cards above Taiwan ones in each tab. Each card carries its lang."""
    langs = ("zh-cn", "zh-tw") if batch_lang in ("zh-cn", "zh") else (batch_lang,)
    merged = None
    for lang in langs:
        tabs = text_tabs(lookup, lang, name, number, code)
        for t in tabs:
            t["cards"] = [dict(c, lang=lang) for c in t["cards"]]
        if merged is None:
            merged = tabs
            continue
        for m, t in zip(merged, tabs):
            seen = {c["id"] for c in m["cards"]}
            m["cards"] = (m["cards"] + [c for c in t["cards"] if c["id"] not in seen])[:GROUP_LIMIT]
            m["fallback"] = m.get("fallback", False) or t.get("fallback", False)
    return merged


def _photo_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return 0, 0


# --- Each photo's own language ------------------------------------------

SCAN_LANGS = {"en": "EN", "ja": "JA", "zh-cn": "ZH-CN", "zh-tw": "ZH-TW", "ko": "KO"}  # the tag on each row
LANG_NAMES = {"en": "English", "ja": "Japanese", "zh-cn": "Chinese (mainland)", "zh-tw": "Chinese (Taiwan & Hong Kong)",
              "ko": "Korean"}
_KANA = re.compile(r"[\u3040-\u30ff]")
_HANGUL = re.compile(r"[\uac00-\ud7af\u1100-\u11ff]")
_HAN = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")
# Characters written differently in simplified (mainland) and traditional (Taiwan & Hong Kong) Chinese,
# picked from words printed on cards (宝可梦 / 寶可夢, 进化 / 進化, 伤害 / 傷害, 弱点 / 弱點 ...).
_PAIRS = ("宝寶 梦夢 进進 伤傷 点點 练練 级級 阶階 击擊 场場 气氣 电電 龙龍 鸟鳥 风風 战戰 斗鬥 时時 张張 对對 开開 关關 "
          "这這 个個 为為 从從 后後 发發 种種 体體 态態 无無 们們 让讓 给給 过過 还還 转轉 换換 选選 择擇 将將 复復 获獲").split()
_SIMPLIFIED = {p[0] for p in _PAIRS}
_TRADITIONAL = {p[1] for p in _PAIRS}
_MAINLAND_CODE = re.compile(r"\bC(?:BB|SVL?|S)\d+[A-Za-z]*C\b")  # CBB4C, CSV8C, CSVL2C, CS3aC
_ENGLISH_CODE = re.compile(r"\b[A-Z0-9]{2,5} ?EN ?\d{1,3}/")  # "TSX EN 002/088" on the bottom line
_MIN_CHARS = 3  # One or two kana, Hangul or Han characters are often a misread mark, not the card's language
_ENGLISH_WORDS = re.compile(r"\b(?:weakness|resistance|retreat|basic|stage ?[12]|evolves|trainer|energy|"
                            r"supporter|item|ability|illus)\b", re.I)


def detect_language(lines: list[str]) -> tuple[str | None, bool]:
    """(language, sure) from the text read on a photo. 'zh' means Chinese, mainland or Taiwan unknown;
    None means nothing to go on."""
    text = " ".join(lines)
    if _ENGLISH_CODE.search(text):  # The printed "EN" code beats a stray character the reader made up
        return "en", True
    if len(_KANA.findall(text.replace("ー", "").replace("・", ""))) >= _MIN_CHARS:  # long-vowel mark and dot aren't enough
        return "ja", True
    if len(_HANGUL.findall(text)) >= _MIN_CHARS:
        return "ko", True
    if len(_HAN.findall(text)) >= _MIN_CHARS:
        if _MAINLAND_CODE.search(text):
            return "zh-cn", True
        simple = sum(c in _SIMPLIFIED for c in text)
        traditional = sum(c in _TRADITIONAL for c in text)
        if simple > traditional:
            return "zh-cn", True
        if traditional > simple:
            return "zh-tw", True
        return "zh", False
    if _LATIN.search(text) and _ENGLISH_WORDS.search(text):
        return "en", True
    return None, False


def lang_of(scan: dict, batch: dict | None = None) -> str:
    """The language a scan's card is looked up in."""
    lang = scan.get("language") or ((batch or {}).get("language") if (batch or {}).get("language") != "auto" else None)
    return lang if lang in SCAN_LANGS else ("zh-cn" if lang == "zh" else "en")


def batch_majority(conn: sqlite3.Connection, batch_id: int, not_scan: int | None = None) -> str | None:
    """The language most of a batch's photos came out as, counting only the sure ones."""
    row = conn.execute("""SELECT language, COUNT(*) AS n FROM scans WHERE batch_id = ? AND id <> ? AND language IS NOT NULL
                          AND language_sure = 1 GROUP BY language ORDER BY n DESC, MIN(id) LIMIT 1""",
                       (batch_id, not_scan or 0)).fetchone()
    return row["language"] if row else None


def _languages_to_try(conn, scan: dict, lines: list[str]) -> tuple[list[str], bool]:
    """The languages to look the card up in, best first, and whether the language is sure."""
    if scan.get("language") in SCAN_LANGS and scan.get("language_sure", 1):
        return [scan["language"]], True  # picked on the tag, or the batch's language in older versions
    found, sure = detect_language(lines)
    if sure:
        return [found], True
    usual = batch_majority(conn, scan["batch_id"], scan.get("id")) or "en"
    if found == "zh":
        langs = ["zh-cn", "zh-tw"]  # mainland first, as Chinese batches always searched
    elif usual in ("ja", "zh-tw"):  # Japanese and Taiwan cards share set codes and numbering: try both
        langs = [usual, "zh-tw" if usual == "ja" else "ja"]
    else:
        langs = [usual]
    return list(dict.fromkeys(langs + [usual, "en"])), False  # unsure: the batch's usual language, then English


def read_scan(conn: sqlite3.Connection, scan: dict, reader, lookup: CardLookup, folder: Path | None = None) -> None:
    path = photo_path(scan, folder)
    if path is None:
        conn.execute("UPDATE scans SET status = 'failed', message = 'The photo is missing.' WHERE id = ?", (scan["id"],))
        conn.commit()
        return
    boxes = reader.read(path)
    langs, sure = _languages_to_try(conn, scan, [b.text for b in boxes])
    best = None
    for lang in langs:
        codes = lookup.codes(lang)
        reading = scanner.read_text(boxes, codes)
        reading.box = scanner.card_box(boxes, *_photo_size(path))
        guesses = scanner.match(reading, lookup.sets(lang), lambda set_id, lang=lang: lookup.cards_of(set_id, lang),
                                lambda name, lang=lang: lookup.search_name(name, lang), codes=codes, limit=GUESSES)
        for g in guesses:
            g["lang"] = lang
        top = guesses[0]["score"] if guesses else -1
        if best is None or top > best[0]:
            best = (top, lang, reading, guesses)
    _, lang, reading, guesses = best
    if guesses:
        status, message = "ready", None
    elif not reading.lines:
        status, message = "nomatch", "No text found. Try a sharper photo with the card filling the frame."
    else:
        status, message = "nomatch", "Couldn't tell which card this is."
    conn.execute(f"UPDATE scans SET {_KEEP_DELETED}, read_json = ?, guesses_json = ?, message = ?, "
                 "variant = NULL, condition = NULL, quantity = NULL, checked = 0, language = ?, language_sure = ? WHERE id = ?",
                 (status, status, reading.to_json(), json.dumps(guesses), message, lang, int(sure), scan["id"]))
    conn.commit()
    _ask_pictures(conn, dict(scan, language=lang), guesses)


def set_language(conn: sqlite3.Connection, scan: dict, lang: str) -> bool:
    """The row's language tag, picked by hand: the card is looked up again in that language."""
    if lang not in SCAN_LANGS or not scan["photo"] or scan["status"] in ("added", "skipped", "expired"):
        return False
    conn.execute("UPDATE scans SET language = ?, language_sure = 1, status = 'waiting', guesses_json = NULL, "
                 "picture_json = NULL, message = NULL, checked = 0, undo_json = NULL WHERE id = ?", (lang, scan["id"]))
    conn.commit()
    return True


SUGGESTION_PICTURES = 8


def _ask_pictures(conn: sqlite3.Connection, scan: dict, suggestions: list[dict]) -> None:
    """The suggestions shown for a scan that have no picture get one looked up (at most 8)."""
    from . import card_pictures
    lang = lang_of(scan, _batch_language(conn, scan))
    try:
        card_pictures.ask(conn, [catalog.local_id(g.get("lang") or lang, g["id"])
                                 for g in suggestions[:SUGGESTION_PICTURES] if g.get("id") and not g.get("image")])
    except Exception as e:
        log.info("pictures for scan %s not asked: %s", scan["id"], e)


class Worker:
    """Reads waiting photos one at a time on its own thread, then lets the
    reader go until there are more."""

    def __init__(self, api_for, folder: Path | None = None, reader=None):
        self.api_for = api_for
        self.folder = folder
        self.reader = reader or scanner.Reader()
        self.problem: str | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return not isinstance(self.reader, scanner.Reader) or scanner.Reader.available()

    def running(self) -> bool:
        return self._thread is not None

    def start(self, wait: bool = False) -> None:
        if not self.available():
            self.problem = "The photo reader isn't installed. Run: pip install rapidocr onnxruntime"
            return
        self.problem = None
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
            thread = self._thread
        if wait:
            thread.join()

    def _run(self) -> None:
        conn = db.connect()
        lookup = CardLookup(conn, self.api_for)
        next_sql = "SELECT * FROM scans WHERE status = 'waiting' ORDER BY id LIMIT 1"
        try:
            while True:
                row = conn.execute(next_sql).fetchone()
                if row is None:
                    # Checked again under the lock, so a photo added right now isn't left waiting.
                    with self._lock:
                        row = conn.execute(next_sql).fetchone()
                        if row is None:
                            self._thread = None
                            break
                scan = dict(row)
                conn.execute("UPDATE scans SET status = 'reading' WHERE id = ? AND status = 'waiting'", (scan["id"],))
                conn.commit()
                try:
                    read_scan(conn, scan, self.reader, lookup, self.folder)
                except Exception as e:
                    log.exception("scan %s failed", scan["id"])
                    conn.execute(f"UPDATE scans SET {_KEEP_DELETED}, message = ? WHERE id = ?",
                                 ("failed", "failed", f"Couldn't read this photo: {e}", scan["id"]))
                    conn.commit()
        except Exception:
            log.exception("scan worker stopped")
            with self._lock:
                self._thread = None
        finally:
            self.reader.close()
            conn.close()


class PictureWorker:
    """Matches photos in the Picture search queue one at a time on its own
    thread. The model takes turns with the photo reader (scanner.HEAVY)."""

    def __init__(self, encoder, folder: Path | None = None, ready=None):
        self.encoder = encoder
        self.folder = folder
        self.ready = ready or (lambda: pictures.available() and pictures.has_model())
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def running(self) -> bool:
        return self._thread is not None

    def start(self, wait: bool = False) -> None:
        if not self.ready():
            return
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
            thread = self._thread
        if wait:
            thread.join()

    def _run(self) -> None:
        conn = db.connect()
        next_sql = "SELECT * FROM scans WHERE status = 'picture_waiting' ORDER BY id LIMIT 1"
        try:
            pictures.sync_learned(conn)  # What your confirmed scans taught it
            while True:
                row = conn.execute(next_sql).fetchone()
                if row is None:
                    with self._lock:
                        row = conn.execute(next_sql).fetchone()
                        if row is None:
                            self._thread = None
                            break
                scan = dict(row)
                conn.execute("UPDATE scans SET status = 'picture_searching' WHERE id = ? AND status = 'picture_waiting'", (scan["id"],))
                conn.commit()
                try:
                    picture_scan(conn, scan, self.encoder, self.folder)
                except Exception as e:
                    log.exception("picture search for scan %s failed", scan["id"])
                    conn.execute(f"UPDATE scans SET {_KEEP_DELETED}, picture_json = '[]', message = ? WHERE id = ?",
                                 ("picture_ready", "picture_ready", f"Enhanced Search didn't work: {e}", scan["id"]))
                    conn.commit()
        except Exception:
            log.exception("picture worker stopped")
            with self._lock:
                self._thread = None
        finally:
            conn.close()


def resume(conn: sqlite3.Connection, worker: Worker, folder: Path | None = None,
           picture_worker: PictureWorker | None = None) -> None:
    """After a restart: photos that were being read or matched go back in their queue."""
    conn.execute("UPDATE scans SET status = 'waiting' WHERE status = 'reading'")
    conn.execute("UPDATE scans SET status = 'picture_waiting' WHERE status = 'picture_searching'")
    conn.commit()
    if conn.execute("SELECT 1 FROM scans WHERE status = 'waiting' LIMIT 1").fetchone():
        worker.start()
    if picture_worker and conn.execute("SELECT 1 FROM scans WHERE status = 'picture_waiting' LIMIT 1").fetchone():
        picture_worker.start()


# --- Prices before Add (round 27) ---------------------------------------------------------------------------------

STILL_READING = ("waiting", "reading", "picture_waiting", "picture_searching")


def unpriced_matches(conn: sqlite3.Connection, batch: dict) -> list[dict]:
    """A batch's matched photos (not added yet) whose card isn't kept for its prices yet, once no photo in the batch
    is still being read. Each: scan_id, lang, id (TCGdex's), set_id (TCGdex's), our (our card id)."""
    if conn.execute(f"SELECT 1 FROM scans WHERE batch_id = ? AND status IN ({','.join('?' * len(STILL_READING))}) LIMIT 1",
                    (batch["id"], *STILL_READING)).fetchone():
        return []
    out = []
    for r in conn.execute("SELECT * FROM scans WHERE batch_id = ? AND status = 'ready' AND card_id IS NULL "
                          "AND guesses_json IS NOT NULL", (batch["id"],)).fetchall():
        guesses = json.loads(r["guesses_json"] or "[]")
        if not guesses or not guesses[0].get("id"):
            continue
        g = guesses[0]
        lang = g.get("lang") or lang_of(dict(r), batch)
        our = catalog.local_id(lang, g["id"])
        if not conn.execute("SELECT 1 FROM scan_matches WHERE scan_id = ? AND card_id = ?", (r["id"], our)).fetchone():
            out.append({"scan_id": r["id"], "lang": lang, "id": g["id"], "set_id": g.get("set_id"), "our": our})
    return out


def keep_matches(conn: sqlite3.Connection, matches: list[dict]) -> None:
    """The matched cards are kept (prices.KEPT_SQL) while their photos wait; a photo's old match goes."""
    with db.transaction(conn):
        for m in matches:
            conn.execute("DELETE FROM scan_matches WHERE scan_id = ?", (m["scan_id"],))
            conn.execute("INSERT OR IGNORE INTO scan_matches (scan_id, card_id) VALUES (?, ?)", (m["scan_id"], m["our"]))
