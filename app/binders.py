"""Binders: named places shared by both logins, like "Misty's binder" or "Bulk box".

Every copy is in one binder or "Not in a binder". The inventory table isn't changed: binder_copies records
"N copies of this inventory line are in binder X", and copies not listed are Not in a binder. So Sets, value,
Spending, Home and the import diff count every copy as before; only the binder screens, bulk edit, the
card page's copy lines and scan review read binder_copies.

When a line's count drops below what its binders hold (copies removed, a re-import with fewer), copies come
out of Not in a binder first, then out of the binder holding the most. Binder pages, pockets and card order
are tabled for later."""

import sqlite3

from . import clock, db

MAX_NAME = 40


def _now() -> str:
    return clock.now()


def tidy(conn: sqlite3.Connection, collection_id: int) -> None:
    """Drop entries for lines that are gone and trim binders that hold more than the line has."""
    with db.transaction(conn):
        conn.execute("DELETE FROM binder_copies WHERE qty <= 0 OR inventory_id NOT IN (SELECT id FROM inventory)")
        over = conn.execute(
            "SELECT i.id, i.quantity, SUM(bc.qty) AS held FROM binder_copies bc JOIN inventory i ON i.id = bc.inventory_id "
            "WHERE i.user_id = ? GROUP BY i.id HAVING held > i.quantity", (collection_id,)).fetchall()
        for row in over:
            extra = row["held"] - row["quantity"]
            # Not in a binder is already empty here (held > quantity), so take from the fullest binder first.
            for b in conn.execute("SELECT binder_id, qty FROM binder_copies WHERE inventory_id = ? ORDER BY qty DESC, binder_id",
                                  (row["id"],)).fetchall():
                take = min(extra, b["qty"])
                conn.execute("UPDATE binder_copies SET qty = qty - ? WHERE inventory_id = ? AND binder_id = ?",
                             (take, row["id"], b["binder_id"]))
                extra -= take
                if not extra:
                    break
        conn.execute("DELETE FROM binder_copies WHERE qty <= 0")


def all_for(conn: sqlite3.Connection, collection_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT id, name FROM binders WHERE collection_id = ? ORDER BY name COLLATE NOCASE",
                                          (collection_id,))]


def get(conn: sqlite3.Connection, collection_id: int, binder_id: int) -> dict | None:
    row = conn.execute("SELECT id, name FROM binders WHERE id = ? AND collection_id = ?", (binder_id, collection_id)).fetchone()
    return dict(row) if row else None


def _clean(name: str) -> str:
    return " ".join((name or "").split())[:MAX_NAME]


def create(conn: sqlite3.Connection, collection_id: int, name: str, by: int | None) -> int:
    name = _clean(name)
    if not name:
        raise ValueError("Give the binder a name.")
    if conn.execute("SELECT 1 FROM binders WHERE collection_id = ? AND name = ? COLLATE NOCASE", (collection_id, name)).fetchone():
        raise ValueError(f"There's already a binder called {name}.")
    with db.transaction(conn):
        cur = conn.execute("INSERT INTO binders (collection_id, name, created_by, created_at) VALUES (?, ?, ?, ?)",
                           (collection_id, name, by, _now()))
    return cur.lastrowid


def rename(conn: sqlite3.Connection, collection_id: int, binder_id: int, name: str) -> None:
    name = _clean(name)
    if not name:
        raise ValueError("Give the binder a name.")
    if conn.execute("SELECT 1 FROM binders WHERE collection_id = ? AND name = ? COLLATE NOCASE AND id != ?",
                    (collection_id, name, binder_id)).fetchone():
        raise ValueError(f"There's already a binder called {name}.")
    with db.transaction(conn):
        conn.execute("UPDATE binders SET name = ? WHERE id = ? AND collection_id = ?", (name, binder_id, collection_id))


def delete(conn: sqlite3.Connection, collection_id: int, binder_id: int) -> None:
    """Its cards go back to Not in a binder; no card is deleted."""
    with db.transaction(conn):
        if conn.execute("DELETE FROM binders WHERE id = ? AND collection_id = ?", (binder_id, collection_id)).rowcount:
            conn.execute("DELETE FROM binder_copies WHERE binder_id = ?", (binder_id,))
            conn.execute("DELETE FROM binder_codes WHERE binder_id = ?", (binder_id,))  # its shared link's codes


def placement(conn: sqlite3.Connection, collection_id: int) -> dict[int, dict]:
    """inventory id -> {binder id or None: copies}. None is Not in a binder."""
    tidy(conn, collection_id)
    out: dict[int, dict] = {}
    for r in conn.execute("SELECT id, quantity FROM inventory WHERE user_id = ?", (collection_id,)):
        out[r["id"]] = {None: r["quantity"]}
    for r in conn.execute("SELECT bc.inventory_id, bc.binder_id, bc.qty FROM binder_copies bc "
                          "JOIN inventory i ON i.id = bc.inventory_id WHERE i.user_id = ?", (collection_id,)):
        spots = out.get(r["inventory_id"])
        if spots is not None:
            spots[r["binder_id"]] = r["qty"]
            spots[None] -= r["qty"]
    for spots in out.values():
        if not spots[None]:
            del spots[None]
    return out


def move(conn: sqlite3.Connection, collection_id: int, inventory_id: int, qty: int,
         to_binder: int | None, from_binder: int | None = None) -> int:
    """Move qty copies of one line from one place to another (None = Not in a binder).
    Returns how many moved (no more than are there)."""
    if to_binder is not None and get(conn, collection_id, to_binder) is None:
        raise ValueError("That binder isn't there any more.")
    spots = placement(conn, collection_id).get(inventory_id)
    if spots is None:
        raise ValueError("Those cards aren't in your stash any more.")
    qty = max(0, min(qty, spots.get(from_binder, 0)))
    if not qty or from_binder == to_binder:
        return 0
    with db.transaction(conn):
        if from_binder is not None:
            conn.execute("UPDATE binder_copies SET qty = qty - ? WHERE inventory_id = ? AND binder_id = ?",
                         (qty, inventory_id, from_binder))
        if to_binder is not None:
            conn.execute("INSERT INTO binder_copies (inventory_id, binder_id, qty) VALUES (?, ?, ?) "
                         "ON CONFLICT(inventory_id, binder_id) DO UPDATE SET qty = qty + excluded.qty",
                         (inventory_id, to_binder, qty))
        conn.execute("DELETE FROM binder_copies WHERE qty <= 0")
    return qty


def move_all(conn: sqlite3.Connection, collection_id: int, inventory_ids: list[int], to_binder: int | None) -> int:
    """Bulk edit and scan review: every copy of these lines into one binder (or Not in a binder).
    Returns how many copies that is."""
    if to_binder is not None and get(conn, collection_id, to_binder) is None:
        raise ValueError("That binder isn't there any more.")
    spots = placement(conn, collection_id)
    moved = 0
    with db.transaction(conn):
        for inventory_id in dict.fromkeys(inventory_ids):
            if inventory_id not in spots:
                continue
            total = sum(spots[inventory_id].values())
            conn.execute("DELETE FROM binder_copies WHERE inventory_id = ?", (inventory_id,))
            if to_binder is not None:
                conn.execute("INSERT INTO binder_copies (inventory_id, binder_id, qty) VALUES (?, ?, ?)",
                             (inventory_id, to_binder, total))
            moved += total
    return moved


def put_some(conn: sqlite3.Connection, collection_id: int, wanted: dict[int, int], to_binder: int) -> int:
    """Scan review: put this many copies of each line into a binder, taking them from Not in a binder."""
    spots = placement(conn, collection_id)
    moved = 0
    for inventory_id, qty in wanted.items():
        free = spots.get(inventory_id, {}).get(None, 0)
        if free:
            moved += move(conn, collection_id, inventory_id, min(qty, free), to_binder, None)
    return moved


def summaries(conn: sqlite3.Connection, collection_id: int, rows: list[dict]) -> list[dict]:
    """The Binders tab: each binder with its count, value and cover (its most valuable card), then
    Not in a binder. rows: My Stash' rows (collection.cards), each with inventory_id, quantity, value, price."""
    spots = placement(conn, collection_id)
    binders = all_for(conn, collection_id)
    out = {b["id"]: dict(b, count=0, value=0.0, cover=None) for b in binders}
    out[None] = {"id": None, "name": "Not in a binder", "count": 0, "value": 0.0, "cover": None}
    for r in rows:
        for binder_id, qty in spots.get(r["inventory_id"], {}).items():
            entry = out.get(binder_id)
            if entry is None:
                continue
            entry["count"] += qty
            entry["value"] += r["value"] / r["quantity"] * qty if r["quantity"] else 0
            if r["price"] is not None and (entry["cover"] is None or r["price"] > (entry["cover"]["price"] or 0)):
                entry["cover"] = r
            elif entry["cover"] is None:
                entry["cover"] = r
    return [out[b["id"]] for b in binders] + [out[None]]


def rows_in(conn: sqlite3.Connection, collection_id: int, rows: list[dict], binder_id: int | None) -> list[dict]:
    """My Stash' raw rows narrowed to one binder (None = Not in a binder), each with that binder's count
    (value and paid are worked out from it afterwards)."""
    spots = placement(conn, collection_id)
    out = []
    for r in rows:
        qty = spots.get(r["inventory_id"], {}).get(binder_id, 0)
        if qty:
            out.append(dict(r, quantity=qty, line_quantity=r["quantity"]))
    return out


def label(spots: dict, names: dict) -> str:
    """'Misty's binder', '2 in Misty's binder · 1 not in a binder'…"""
    parts = sorted(spots.items(), key=lambda kv: (kv[0] is None, names.get(kv[0], "")))
    if len(parts) == 1:
        return names.get(parts[0][0], "Not in a binder")
    return " · ".join(f"{q} {'not in a binder' if b is None else 'in ' + names.get(b, '?')}" for b, q in parts)
