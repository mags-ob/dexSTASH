"""English names beside Japanese and Chinese ones (round 27).

A Japanese or Chinese set's English name comes, in this order, from:
1. TCGCSV's Japanese set list (TCGplayer's Japanese product line), when the set is matched to one of its groups:
   "SV1a: Triplet Beat" -> "Triplet Beat".
2. CardTrader's expansion list, when the set is matched to an expansion (needs the CardTrader token).
3. set_names_en.json, shipped with the app, for sets neither has (set id -> English name).
Nothing else: a set with none shows its own name only, never a made-up translation. The names are kept in
sets.english_name and worked out again after the set lists are matched (a price update, a set stored).

A card's English name is the one stored already (cards.twin_name from the English card, or english_name).
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

LANGS = ("ja", "zh-tw", "zh-cn")
SHIPPED = Path(__file__).with_name("set_names_en.json")
SETTING = "names_shown"  # app_meta: how Japanese and Chinese names show
CHOICES = {"both": "English and original", "english": "English only", "original": "Original only"}

_shipped: dict[str, str] | None = None


def shipped() -> dict[str, str]:
    global _shipped
    if _shipped is None:
        try:
            _shipped = {k: v for k, v in json.loads(SHIPPED.read_text(encoding="utf-8")).items() if not k.startswith("_")}
        except (OSError, ValueError):
            _shipped = {}
    return _shipped


def _plain(group_name: str | None) -> str | None:
    """'SV1a: Triplet Beat' -> 'Triplet Beat'."""
    name = re.sub(r"^[A-Za-z0-9.\- ]{1,8}:\s*", "", group_name or "").strip()
    return name or None


def _ascii(name: str | None) -> str | None:
    """Only a name in Latin letters counts as an English name."""
    return name if name and all(ord(ch) < 0x2E80 for ch in name) else None


def work_out(conn: sqlite3.Connection, set_id: str) -> str | None:
    """A set's English name from the sources above, or None."""
    row = conn.execute("SELECT g.name FROM tcgcsv_sets t JOIN tcgcsv_groups g ON g.group_id = t.group_id "
                       "WHERE t.set_id = ?", (set_id,)).fetchone()
    if row is None and set_id.startswith("ja:"):  # not matched yet: the same code or name as a group (never a guess)
        from . import tcgcsv
        ours = conn.execute("SELECT name FROM sets WHERE set_id = ?", (set_id,)).fetchone()
        group = tcgcsv.group_for(conn, "ja", set_id.split(":", 1)[1], ours[0] if ours else None)
        row = conn.execute("SELECT name FROM tcgcsv_groups WHERE group_id = ?", (group[1],)).fetchone() if group else None
    if row and _ascii(_plain(row[0])):
        return _ascii(_plain(row[0]))
    row = conn.execute("SELECT e.name FROM ct_sets c JOIN ct_expansions e ON e.id = c.expansion_id "
                       "WHERE c.set_id = ? AND c.expansion_id IS NOT NULL", (set_id,)).fetchone()
    if row and _ascii(row[0]):
        return row[0].strip()
    return _ascii(shipped().get(set_id))


def refresh(conn: sqlite3.Connection) -> int:
    """Work out every Japanese and Chinese set's English name again. Returns how many sets have one."""
    found = 0
    rows = conn.execute(f"SELECT set_id, english_name FROM sets WHERE language IN ({','.join('?' * len(LANGS))})",
                        LANGS).fetchall()
    for r in rows:
        name = work_out(conn, r["set_id"])
        found += name is not None
        if name != r["english_name"]:
            conn.execute("UPDATE sets SET english_name = ? WHERE set_id = ?", (name, r["set_id"]))
    conn.commit()
    changed()
    return found


def shown(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (SETTING,)).fetchone()
    return row[0] if row and row[0] in CHOICES else "both"


def set_shown(conn: sqlite3.Connection, value: str) -> None:
    if value not in CHOICES:
        raise ValueError("Pick one of the choices listed.")
    conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)", (SETTING, value))
    conn.commit()
    changed()


def names(original: str | None, english: str | None, how: str = "both") -> tuple[str, str | None]:
    """(the name shown first, the smaller one after it or None) for one setting."""
    original = original or ""
    if not english or english == original or how == "original":
        return original, None
    if how == "english":
        return english, None
    return english, original


# What set_label shows, kept in memory: set id -> English name, and the setting. Dropped when either changes.
_labels: dict | None = None


def changed() -> None:
    global _labels
    _labels = None


def labels(conn: sqlite3.Connection) -> dict:
    global _labels
    if _labels is None:
        try:
            names_by_set = dict(conn.execute("SELECT set_id, english_name FROM sets WHERE english_name IS NOT NULL").fetchall())
            how = shown(conn)
        except sqlite3.Error:  # before the database is ready
            return {"names": {}, "how": "both"}
        _labels = {"names": names_by_set, "how": how}
    return _labels


def label(conn: sqlite3.Connection, set_id: str | None, original: str | None, fallback: str | None = None) -> str:
    """A set's name as shown everywhere: 'Triplet Beat · 強化拡張パック トリプレットビート' (English and original), or
    one of them, by the setting. fallback: an English name worked out another way (a mainland set's code)."""
    got = labels(conn)
    english = got["names"].get(set_id or "") or fallback
    first, second = names(original, english, got["how"])
    return f"{first} · {second}" if second else (first or set_id or "")
