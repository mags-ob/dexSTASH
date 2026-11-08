"""Binders as pages you flip through, like a real binder. Each copy takes one pocket, in the
binder's own order (its layout and sort are kept on the binder; Not in a binder keeps its own in app_meta).
After the last card the last page shows empty sleeves. The grid view (My Stash' usual grid, with search,
Filter and bulk select) is a toggle away; each login's last choice is remembered."""

import json
import sqlite3

from .display import fold

LAYOUTS = (4, 9, 12)
SORTS = {"set": "Set and number", "dex": "Pokédex number", "newest": "Newest added", "price": "Price high first",
         "name": "Name"}
SORTED_BY = {"set": "sorted by set and number", "dex": "sorted by Pokédex number", "newest": "newest added first",
             "price": "highest price first", "name": "sorted by name"}
VIEWS = ("pages", "grid")


def _loose_key(collection_id: int) -> str:
    return f"loose_binder:{collection_id}"


def settings(conn: sqlite3.Connection, collection_id: int, binder_id: int | None) -> dict:
    """{'layout', 'sort'} for a binder, or Not in a binder (None)."""
    if binder_id is None:
        row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (_loose_key(collection_id),)).fetchone()
        try:
            saved = json.loads(row[0]) if row else {}
        except ValueError:
            saved = {}
    else:
        row = conn.execute("SELECT layout, sort FROM binders WHERE id = ? AND collection_id = ?",
                           (binder_id, collection_id)).fetchone()
        saved = dict(row) if row else {}
    return {"layout": saved.get("layout") if saved.get("layout") in LAYOUTS else 9,
            "sort": saved.get("sort") if saved.get("sort") in SORTS else "set"}


def save(conn: sqlite3.Connection, collection_id: int, binder_id: int | None, layout: int, sort: str) -> None:
    if layout not in LAYOUTS or sort not in SORTS:
        raise ValueError("Pick a layout and an order from the list.")
    if binder_id is None:
        conn.execute("INSERT INTO app_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                     (_loose_key(collection_id), json.dumps({"layout": layout, "sort": sort})))
    else:
        conn.execute("UPDATE binders SET layout = ?, sort = ? WHERE id = ? AND collection_id = ?",
                     (layout, sort, binder_id, collection_id))
    conn.commit()


def view(conn: sqlite3.Connection, user_id: int, asked: str = "") -> str:
    """'pages' (the default) or 'grid': what was asked for, remembered for this login, else the last choice."""
    if asked in VIEWS:
        conn.execute("INSERT INTO user_prefs (user_id, key, value) VALUES (?, 'binder_view', ?) "
                     "ON CONFLICT (user_id, key) DO UPDATE SET value = excluded.value", (user_id, asked))
        conn.commit()
        return asked
    row = conn.execute("SELECT value FROM user_prefs WHERE user_id = ? AND key = 'binder_view'", (user_id,)).fetchone()
    return row[0] if row and row[0] in VIEWS else "pages"


def ordered(rows: list[dict], sort: str | None, dex_of=lambda r: None) -> list[dict]:
    """The rows in the binder's order (None: as they are)."""
    rows = list(rows)
    if sort is None:
        return rows
    if sort == "dex":
        rows.sort(key=lambda r: (r["sort_number"], r["number"]))
        rows.sort(key=lambda r: (dex_of(r) or 99999, r["release_date"] or ""))
    elif sort == "newest":
        rows.sort(key=lambda r: -(r["added_order"] or 0))
    elif sort == "price":
        rows.sort(key=lambda r: (r["price"] is None, -(r["price"] or 0), fold(r["name"] or "")))
    elif sort == "name":
        rows.sort(key=lambda r: (fold(r["name"] or ""), r["release_date"] or ""))
    else:  # set and number, newest set first, as My Stash' "Set & number"
        rows.sort(key=lambda r: (r["sort_number"], r["number"], r["variant"]))
        rows.sort(key=lambda r: (r["release_date"] or "", r["set_id"]), reverse=True)
    return rows


def pockets(rows: list[dict], sort: str | None, dex_of=lambda r: None) -> list[dict]:
    """One pocket per copy, in order."""
    return [r for r in ordered(rows, sort, dex_of) for _ in range(max(int(r["quantity"] or 0), 0))]


def pages(pocket_list: list, layout: int) -> list[list]:
    """The pockets in pages of `layout`, the last one filled with empty sleeves (None). An empty binder
    still has one page of empty sleeves."""
    out = [pocket_list[i:i + layout] for i in range(0, len(pocket_list), layout)] or [[]]
    out[-1] = out[-1] + [None] * (layout - len(out[-1]))
    return out


def page_of(pocket_list: list, layout: int, matches) -> tuple[int, int] | None:
    """(page number from 1, pocket index) of the first pocket matches(row) is true for."""
    for i, r in enumerate(pocket_list):
        if matches(r):
            return i // layout + 1, i
    return None
