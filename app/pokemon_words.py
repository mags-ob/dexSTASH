"""A Pokémon's tags: colour, types, shape, genus, legendary…, its looks-like words (name roots,
Bulbapedia) and your own words. "+ word" adds one Describe searches from then on; ✕ hides a wrong
automatic word or deletes yours. Shared by both logins."""

import sqlite3

from . import clock, db, tags as card_tags
from .display import fold as _fold  # Accents don't matter


def own_and_hidden(conn: sqlite3.Connection) -> tuple[dict, dict]:
    own, hidden = {}, {}
    for dex, tag in conn.execute("SELECT dex, tag FROM pokemon_tags ORDER BY rowid"):
        own.setdefault(dex, []).append(tag)
    for dex, tag in conn.execute("SELECT dex, tag FROM pokemon_tag_hidden"):
        hidden.setdefault(dex, []).append(tag)
    return own, hidden


def for_pokemon(conn: sqlite3.Connection, describer, dex: int) -> dict:
    """{"tags": [(tag, kind)], "hidden": [tag]}; kind is the automatic source or 'mine'."""
    own = [r[0] for r in conn.execute("SELECT tag FROM pokemon_tags WHERE dex = ? ORDER BY rowid", (dex,))]
    hidden = [r[0] for r in conn.execute("SELECT tag FROM pokemon_tag_hidden WHERE dex = ? ORDER BY tag", (dex,))]
    hide = {_fold(h) for h in hidden}
    seen, out = set(), []
    for t, kind in describer.tags_for(dex) + [(t, "mine") for t in own]:
        key = _fold(t)
        if key in seen or (kind != "mine" and key in hide):
            continue
        seen.add(key)
        out.append((t, kind))
    return {"tags": out, "hidden": hidden}


def _refresh(conn, describer, dex: int) -> None:
    own, hidden = own_and_hidden(conn)
    describer.update_words(dex, own.get(dex, []), hidden.get(dex, []))


def add(conn: sqlite3.Connection, describer, dex: int, word: str, user_id: int) -> str:
    word = card_tags.clean(word)
    if not word:
        raise ValueError("Type a word first.")
    with db.transaction(conn):
        conn.execute("DELETE FROM pokemon_tag_hidden WHERE dex = ? AND tag = ?", (dex, word))
        conn.execute("INSERT OR IGNORE INTO pokemon_tags (dex, tag, added_by, added_at) VALUES (?, ?, ?, ?)",
                     (dex, word, user_id, clock.now()))
    _refresh(conn, describer, dex)
    return word


def remove(conn: sqlite3.Connection, describer, dex: int, word: str) -> None:
    with db.transaction(conn):
        if not conn.execute("DELETE FROM pokemon_tags WHERE dex = ? AND tag = ?", (dex, word)).rowcount:
            conn.execute("INSERT OR IGNORE INTO pokemon_tag_hidden (dex, tag) VALUES (?, ?)", (dex, word))
    _refresh(conn, describer, dex)


def show(conn: sqlite3.Connection, describer, dex: int, word: str) -> None:
    with db.transaction(conn):
        conn.execute("DELETE FROM pokemon_tag_hidden WHERE dex = ? AND tag = ?", (dex, word))
    _refresh(conn, describer, dex)


def suggestions(conn: sqlite3.Connection) -> list[str]:
    """+ word's suggestions as you type: words you've used, then the looks-like vocabulary."""
    from .looks_like import VOCABULARY
    own = [r[0] for r in conn.execute("SELECT tag FROM pokemon_tags GROUP BY tag COLLATE NOCASE ORDER BY MAX(rowid) DESC LIMIT 200")]
    seen = {_fold(w) for w in own}
    return own + [w for w in VOCABULARY if _fold(w) not in seen]
