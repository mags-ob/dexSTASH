"""Notes on cards, each with who wrote it. A note is about the whole card,
not one printing or copy."""

import sqlite3

MAX_LENGTH = 500


def add(conn: sqlite3.Connection, collection_id: int, card_id: str, author_id: int | None, text: str,
        variant: str | None = None, condition: str | None = None, commit: bool = True) -> int | None:
    """variant and condition are accepted for older callers and ignored."""
    text = " ".join(text.split())[:MAX_LENGTH]
    if not text:
        return None
    cur = conn.execute("INSERT INTO notes (collection_id, card_id, author_id, text) VALUES (?, ?, ?, ?)",
                       (collection_id, card_id, author_id, text))
    if commit:
        conn.commit()
    return cur.lastrowid


def exists(conn: sqlite3.Connection, collection_id: int, card_id: str, text: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM notes WHERE collection_id = ? AND card_id = ? AND text = ?", (collection_id, card_id, text)
    ).fetchone() is not None


def for_card(conn: sqlite3.Connection, collection_id: int, card_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT n.*, COALESCE(u.username, CASE WHEN n.author_id IS NULL THEN NULL ELSE 'a removed user' END) AS author "
        "FROM notes n LEFT JOIN users u ON u.id = n.author_id "
        "WHERE n.collection_id = ? AND n.card_id = ? ORDER BY n.id",
        (collection_id, card_id),
    )
    return [dict(r) for r in rows]


def by_card(conn: sqlite3.Connection, collection_id: int) -> dict[str, list[str]]:
    """card_id -> note texts with their authors, for My Stash."""
    out: dict[str, list[str]] = {}
    for r in conn.execute(
        "SELECT n.card_id, n.text, u.username FROM notes n "
        "LEFT JOIN users u ON u.id = n.author_id WHERE n.collection_id = ? ORDER BY n.id",
        (collection_id,),
    ):
        text = f"{r['text']} ({r['username']})" if r["username"] else r["text"]
        out.setdefault(r["card_id"], []).append(text)
    return out


def delete(conn: sqlite3.Connection, collection_id: int, note_id: int, user) -> bool:
    """Only the author or an admin can delete a note."""
    row = conn.execute("SELECT author_id FROM notes WHERE id = ? AND collection_id = ?",
                       (note_id, collection_id)).fetchone()
    if row is None or (row["author_id"] != user["id"] and not user["is_admin"]):
        return False
    conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    conn.commit()
    return True
