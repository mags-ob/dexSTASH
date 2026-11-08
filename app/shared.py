"""Share my binder (design 13 Oct + access codes, 14 Oct).

A binder can be shared at /b/<token>: a random link, off by default. The link alone shows only "Enter your
access code". Each code is made for one person ("Mike", "Card club"), shown once, and kept only as a scrypt
hash with its own salt. A device that entered a working code gets a signed cookie (dexstash_pass) for that one
link, for 30 days; every request checks the code still stands, so Revoke, Turn sharing off and New link stop
it at once. Wrong codes and unknown links from one address are slowed down (Lockout).

Visitors see a whitelist of each card's details (public_row): never condition, notes, who added it, what was
paid, where it came from, other binders, chases, or your photos and scans.
"""

import hashlib
import hmac
import secrets
import sqlite3
from datetime import timedelta

from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import clock, db

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L look-alikes
CODE_LENGTH = 8
PASS_COOKIE = "dexstash_pass"
PASS_DAYS = 30
MAX_CODES = 50
MAX_NAME = 40
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


def _now() -> str:
    return clock.now()


# --- the link ---------------------------------------------------------------------------

def share(conn: sqlite3.Connection, collection_id: int, binder_id: int, on: bool) -> None:
    """Turn sharing on (making the link the first time) or off. Off and on again keeps the link."""
    with db.transaction(conn):
        row = conn.execute("SELECT share_token FROM binders WHERE id = ? AND collection_id = ?",
                           (binder_id, collection_id)).fetchone()
        if row is None:
            raise ValueError("No such binder.")
        token = row["share_token"] or secrets.token_urlsafe(16)
        conn.execute("UPDATE binders SET share_token = ?, share_on = ?, shared_at = COALESCE(shared_at, ?) "
                     "WHERE id = ? AND collection_id = ?", (token, int(on), _now(), binder_id, collection_id))


def new_link(conn: sqlite3.Connection, collection_id: int, binder_id: int) -> None:
    """A fresh link; the old one stops at once (codes stay, and work with the new link)."""
    with db.transaction(conn):
        conn.execute("UPDATE binders SET share_token = ? WHERE id = ? AND collection_id = ?",
                     (secrets.token_urlsafe(16), binder_id, collection_id))


def by_token(conn: sqlite3.Connection, token: str) -> dict | None:
    """The shared binder behind a link, only while sharing is on."""
    if not token or len(token) > 64:
        return None
    row = conn.execute("SELECT id, collection_id, name, share_token FROM binders WHERE share_token = ? AND share_on = 1",
                       (token,)).fetchone()
    return dict(row) if row and hmac.compare_digest(row["share_token"], token) else None


def info(conn: sqlite3.Connection, collection_id: int, binder_id: int) -> dict:
    row = conn.execute("SELECT share_token, share_on FROM binders WHERE id = ? AND collection_id = ?",
                       (binder_id, collection_id)).fetchone()
    return {"token": row["share_token"] if row else None, "on": bool(row and row["share_on"]),
            "codes": codes(conn, binder_id), "history": history(conn, binder_id)}


def shared_ids(conn: sqlite3.Connection, collection_id: int) -> set[int]:
    return {r[0] for r in conn.execute("SELECT id FROM binders WHERE collection_id = ? AND share_on = 1", (collection_id,))}


# --- access codes -----------------------------------------------------------------------

def _hash(code: str, salt: bytes) -> str:
    return hashlib.scrypt(normalise(code).encode(), salt=salt, **_SCRYPT).hex()


def normalise(code: str) -> str:
    """'k7qm-4zpa', 'K7QM 4ZPA' and 'K7QM4ZPA' are the same code."""
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())[:32]


def pretty(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def lookup_tag(code: str, secret: str) -> str:
    """A short keyed tag of the code, so a try only runs scrypt on the one code it could be (not every code)."""
    return hmac.new(secret.encode(), normalise(code).encode(), hashlib.sha256).hexdigest()[:16]


def make_code(conn: sqlite3.Connection, collection_id: int, binder_id: int, name: str, by: int | None,
              secret: str) -> str:
    """A new code for one person. Returns it as K7QM-4ZPA; only its hash is kept, so it's shown once."""
    name = " ".join((name or "").split())[:MAX_NAME]
    if not name:
        raise ValueError("Give the code a name, like who it's for.")
    if conn.execute("SELECT 1 FROM binders WHERE id = ? AND collection_id = ?", (binder_id, collection_id)).fetchone() is None:
        raise ValueError("No such binder.")
    if conn.execute("SELECT COUNT(*) FROM binder_codes WHERE binder_id = ? AND revoked_at IS NULL", (binder_id,)).fetchone()[0] >= MAX_CODES:
        raise ValueError(f"A binder can have {MAX_CODES} codes. Revoke one first.")
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    salt = secrets.token_bytes(16)
    with db.transaction(conn):
        conn.execute("INSERT INTO binder_codes (binder_id, name, code_hash, code_tag, created_by, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (binder_id, name, f"scrypt${salt.hex()}${_hash(code, salt)}", lookup_tag(code, secret), by, _now()))
        _log(conn, binder_id, name, "added", by)
    return pretty(code)


def _log(conn: sqlite3.Connection, binder_id: int, name: str, action: str, by: int | None) -> None:
    conn.execute("INSERT INTO binder_code_log (binder_id, name, action, by_user, at) VALUES (?, ?, ?, ?, ?)",
                 (binder_id, name, action, by, _now()))


def codes(conn: sqlite3.Connection, binder_id: int) -> list[dict]:
    """The codes that still work, newest first (revoked ones live on only in the history)."""
    return [dict(r) for r in conn.execute(
        "SELECT c.id, c.name, c.created_at, c.last_used_at, u.username AS added_by FROM binder_codes c "
        "LEFT JOIN users u ON u.id = c.created_by WHERE c.binder_id = ? AND c.revoked_at IS NULL "
        "ORDER BY c.created_at DESC, c.id DESC", (binder_id,))]


def history(conn: sqlite3.Connection, binder_id: int) -> list[dict]:
    """Codes added and revoked, by whom and when, newest first."""
    return [dict(r) for r in conn.execute(
        "SELECT l.name, l.action, l.at, u.username AS by_name FROM binder_code_log l "
        "LEFT JOIN users u ON u.id = l.by_user WHERE l.binder_id = ? ORDER BY l.at DESC, l.id DESC", (binder_id,))]


def revoke(conn: sqlite3.Connection, collection_id: int, binder_id: int, code_id: int, by: int | None = None) -> None:
    with db.transaction(conn):
        row = conn.execute("""SELECT name FROM binder_codes WHERE id = ? AND binder_id = ? AND revoked_at IS NULL
                              AND binder_id IN (SELECT id FROM binders WHERE collection_id = ?)""",
                           (code_id, binder_id, collection_id)).fetchone()
        if row is None:
            return
        conn.execute("UPDATE binder_codes SET revoked_at = ? WHERE id = ?", (_now(), code_id))
        _log(conn, binder_id, row["name"], "revoked", by)


def clear_history(conn: sqlite3.Connection, collection_id: int, binder_id: int) -> None:
    """Empty the history, and forget revoked codes for good (working codes stay)."""
    with db.transaction(conn):
        if conn.execute("SELECT 1 FROM binders WHERE id = ? AND collection_id = ?", (binder_id, collection_id)).fetchone():
            conn.execute("DELETE FROM binder_code_log WHERE binder_id = ?", (binder_id,))
            conn.execute("DELETE FROM binder_codes WHERE binder_id = ? AND revoked_at IS NOT NULL", (binder_id,))


def check_code(conn: sqlite3.Connection, binder_id: int, code: str, secret: str) -> int | None:
    """The id of the binder's code that matches, if any (revoked ones never match). Only the code with the
    same lookup tag is hashed, so a wrong guess costs one scrypt at most (usually none)."""
    code = normalise(code)
    if len(code) != CODE_LENGTH:
        return None
    found = None
    for r in conn.execute("SELECT id, code_hash FROM binder_codes WHERE binder_id = ? AND code_tag = ? AND revoked_at IS NULL",
                          (binder_id, lookup_tag(code, secret))):
        _, salt, digest = r["code_hash"].split("$")
        if hmac.compare_digest(_hash(code, bytes.fromhex(salt)), digest):
            found = r["id"]
    return found


# --- the remembered device --------------------------------------------------------------

def _signer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt="dexstash-binder-pass")


def make_pass(secret: str, binder_id: int, code_id: int) -> str:
    return _signer(secret).dumps({"b": binder_id, "c": code_id})


def check_pass(conn: sqlite3.Connection, secret: str, binder: dict, cookie: str | None) -> int | None:
    """The code id the device's pass stands for, if it's for this binder and the code still stands. Notes
    when it was last used, at most once an hour."""
    if not cookie:
        return None
    try:
        data = _signer(secret).loads(cookie, max_age=PASS_DAYS * 86400)
    except BadSignature:
        return None
    if not isinstance(data, dict) or data.get("b") != binder["id"]:
        return None
    row = conn.execute("SELECT id, last_used_at FROM binder_codes WHERE id = ? AND binder_id = ? AND revoked_at IS NULL",
                       (data.get("c"), binder["id"])).fetchone()
    if row is None:
        return None
    hour_ago = clock.since(timedelta(hours=1))
    if (row["last_used_at"] or "") < hour_ago:
        conn.execute("UPDATE binder_codes SET last_used_at = ? WHERE id = ?", (_now(), row["id"]))
        conn.commit()
    return row["id"]


# --- what visitors see ------------------------------------------------------------------

PUBLIC_SOURCE = {"your price": "Owner's price", "your PriceCharting link": "PriceCharting link",
                 "your TCGplayer link": "TCGplayer link", "TCGplayer": "TCGplayer price"}


def public_source(src: dict | None) -> dict | None:
    if not src:
        return None
    label = PUBLIC_SOURCE.get(src["label"], src["label"]).replace(" · Guess · confirm", "")
    return {"label": label, "url": src.get("url") if (src.get("url") or "").startswith("https://") else None}


def public_rows(rows: list[dict]) -> list[dict]:
    """My Stash' rows for one binder, merged per card and printing (condition never shows), with only the
    whitelisted details."""
    merged: dict[tuple[str, str], dict] = {}
    order = []
    for r in rows:
        key = (r["card_id"], r["variant"])
        if key not in merged:
            order.append(key)
            merged[key] = {
                "card_id": r["card_id"], "name": r["name"], "number": r["number"], "set_id": r["set_id"],
                "set_name": r["set_name"], "release_date": r["release_date"], "rarity": r["rarity"],
                "language": r["language"], "variant": r["variant"], "variant_label": r["variant_label"],
                "twin_name": r.get("twin_name"), "image_url": r.get("image_url"), "logo_url": r.get("logo_url"),
                "quantity": 0, "value": 0.0, "price": r["price"], "source": public_source(r.get("source")),
            }
        merged[key]["quantity"] += r["quantity"]
        merged[key]["value"] += r["value"]
    return [merged[k] for k in order]
