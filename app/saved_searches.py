"""Find's saved searches: a name and the search's address, kept per login
in the settings table (user_prefs), shown as chips under the Find box."""

import json
from urllib.parse import parse_qsl, urlencode

KEY = "saved_searches"
MAX = 30
KEEP = ("q", "lang", "set", "rarity", "kind", "owned", "sort")  # what a saved search remembers


def _load(conn, user_id: int) -> list[dict]:
    row = conn.execute("SELECT value FROM user_prefs WHERE user_id = ? AND key = ?", (user_id, KEY)).fetchone()
    try:
        return json.loads(row[0]) if row else []
    except ValueError:
        return []


def _store(conn, user_id: int, items: list[dict]) -> None:
    conn.execute("INSERT INTO user_prefs (user_id, key, value) VALUES (?, ?, ?) "
                 "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value", (user_id, KEY, json.dumps(items)))
    conn.commit()


def clean_query(query: str) -> str:
    """Only the search and its filters (not a scan or import being fixed)."""
    return urlencode([(k, v) for k, v in parse_qsl(query or "") if k in KEEP and v])


def all_for(conn, user_id: int) -> list[dict]:
    return [dict(s, href="/search?" + s["query"]) for s in _load(conn, user_id)]


def save(conn, user_id: int, name: str, query: str) -> None:
    name, query = (name or "").strip()[:40], clean_query(query)
    if not name or not query:
        raise ValueError("Give the search a name.")
    items = [s for s in _load(conn, user_id) if s["name"].casefold() != name.casefold()]
    if len(items) >= MAX:
        raise ValueError(f"You can keep up to {MAX} saved searches.")
    _store(conn, user_id, items + [{"name": name, "query": query}])


def rename(conn, user_id: int, old: str, new: str) -> None:
    new = (new or "").strip()[:40]
    if not new:
        return
    items = _load(conn, user_id)
    for s in items:
        if s["name"] == old:
            s["name"] = new
    _store(conn, user_id, items)


def delete(conn, user_id: int, name: str) -> None:
    _store(conn, user_id, [s for s in _load(conn, user_id) if s["name"] != name])
