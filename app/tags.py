"""Card tags. Three kinds, shown together on the card page:
- automatic tags worked out from what's stored on the card (rarity group, printings, energy type, series,
  Promo) and Vintage, worked out when shown so the "older than N years" setting applies at once;
- automatic tags from the card's details (card kind, stage, mechanic, illustrator, stamps, Poké Ball and
  Master Ball printings), saved when a set is stored or refreshed (Check for updates), kind 'auto';
- your own tags, kind 'mine'.
✕ hides an automatic tag for that card (card_tag_hidden) or deletes your own. Everything here belongs to
the card, so both logins share it. No moods: nothing describes a card's picture."""

import re
import sqlite3

from . import clock, db, display, smart
from .display import fold as _fold  # Accents don't matter

MAX_TAG = 40
_GROUPS = {"holo": "Holo", "full art": "Full art", "shiny": "Shiny", "secret": "Secret"}
_STAGES = {"basic": "Basic", "stage1": "Stage 1", "stage 1": "Stage 1", "stage2": "Stage 2", "stage 2": "Stage 2"}
_TRAINERS = {"item": "Item", "supporter": "Supporter", "stadium": "Stadium", "tool": "Tool", "pokémon tool": "Tool",
             "technical machine": "Tool", "ace spec": "Item"}
_KINDS = {"pokemon": "Pokémon", "trainer": "Trainer", "energy": "Energy"}
# PTCG-CHS-Datasets codes (its own "dict" lists them)
_CHS_STAGES = {"基础": "Basic", "1阶进化": "Stage 1", "2阶进化": "Stage 2", "BREAK进化": "BREAK", "M进化": "Mega"}
_CHS_KINDS = {"1": "Pokémon", "2": "Trainer", "3": "Energy"}
_CHS_TRAINERS = {"1": "Item", "2": "Supporter", "3": "Stadium", "4": "Tool"}
_CHS_MECHANICS = {"11": "ex", "1": "V", "2": "VMAX", "3": "VSTAR", "4": "GX"}


def clean(tag: str) -> str:
    """A tag as stored: trimmed, single spaces, a leading # dropped, at most MAX_TAG letters."""
    return re.sub(r"\s+", " ", (tag or "").strip().lstrip("#").strip())[:MAX_TAG]


def mechanic(name: str) -> str | None:
    """ex, EX, GX, V, VMAX, VSTAR, BREAK or Mega, from a card's name."""
    n = (name or "").strip()
    if re.search(r"(?<![A-Za-z])ex$", n):
        return "ex"
    for m in ("VMAX", "VSTAR", "GX", "EX", "BREAK", "V"):
        if re.search(rf"(?<![A-Za-z]){m}$", n):
            return m
    if n.startswith(("Mega ", "M ", "メガ", "超级")):
        return "Mega"
    return None


def detail_tags(detail: dict, name: str = "") -> list[str]:
    """Tags from a card's TCGdex details, saved when its set is stored."""
    from .search import card_kind  # search imports this module's neighbours; keep the import local
    out = []
    kind = card_kind(detail)
    if kind:
        out.append(_KINDS[kind])
    trainer = _TRAINERS.get((detail.get("trainerType") or "").casefold())
    if trainer:
        out.append(trainer)
    stage = _STAGES.get((detail.get("stage") or "").casefold())
    if stage and kind == "pokemon":
        out.append(stage)
    m = mechanic(name or detail.get("name") or "")
    if m:
        out.append(m)
    if "tera" in str(detail.get("suffix") or "").lower():
        out.append("Tera")
    if smart.stamped(detail):
        out.append("Stamped")
    for d in detail.get("variants_detailed") or []:
        foil = {"pokeball": "Poké Ball", "masterball": "Master Ball"}.get(d.get("foil") or "")
        if foil:
            out.append(foil)
    if detail.get("illustrator"):
        out.append(clean(detail["illustrator"]))
    return list(dict.fromkeys(t for t in out if t))


def chs_tags(details: dict, name: str = "") -> list[str]:
    """The same from a PTCG-CHS-Datasets card (mainland Chinese)."""
    out = [_CHS_KINDS.get(str(details.get("cardType") or ""))]
    out.append(_CHS_TRAINERS.get(str(details.get("trainerType") or "")))
    if out[0] == "Pokémon":
        out.append(_CHS_STAGES.get(details.get("evolveText") or ""))
    out.append(_CHS_MECHANICS.get(str(details.get("pokemonType") or "")) or mechanic(name))
    out += [clean(x) for x in details.get("illustratorName") or [] if x]
    return list(dict.fromkeys(t for t in out if t))


def save_auto(conn: sqlite3.Connection, tags_by_card: dict[str, list[str]]) -> None:
    """Replace the saved automatic tags of these cards (inside the caller's transaction)."""
    ids = list(tags_by_card)
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        conn.execute(f"DELETE FROM card_tags WHERE kind = 'auto' AND card_id IN ({','.join('?' * len(part))})", part)
    conn.executemany("INSERT OR IGNORE INTO card_tags (card_id, tag, kind) VALUES (?, ?, 'auto')",
                     [(card_id, tag) for card_id, tags in tags_by_card.items() for tag in tags])


def _row_tags(row, years: int) -> list[str]:
    """Tags from the stored card and set, worked out each time."""
    out = [_GROUPS[g] for g in ("holo", "full art", "shiny", "secret") if g in display.rarity_groups(row["rarity"])]
    if row["has_reverse"]:
        out.append("Reverse holo")
    if row["has_holo"] and "Holo" not in out:
        out.append("Holo")
    if row["has_first_ed"]:
        out.append("1st edition")
    if row["has_star"] or row["has_star2"]:
        out.append("★ print")
    out += [t.strip() for t in (row["types"] or "").split(",") if t.strip()]
    if smart.is_promo(row["set_name"], (row["set_id"] or "").split(":")[-1]):
        out.append("Promo")
    if row["series"]:
        out.append(row["series"])
    if smart.is_vintage(row["release_date"], years):
        out.append("Vintage")
    return out


_ROWS = """SELECT c.id, c.rarity, c.has_reverse, c.has_holo, c.has_first_ed, c.has_star, c.has_star2, c.types,
                  c.set_id, s.name AS set_name, s.series, s.release_date
           FROM cards c JOIN sets s ON s.set_id = c.set_id"""


def for_cards(conn: sqlite3.Connection, card_ids=None, years: int | None = None) -> dict[str, dict]:
    """card id -> {"tags": [(tag, kind)], "hidden": [tag]} for these cards (every stored card when None).
    kind is 'auto' or 'mine'; hidden automatic tags are left out of "tags"."""
    years = smart.vintage_years(conn) if years is None else years
    if card_ids is None:
        rows = conn.execute(_ROWS).fetchall()
    else:
        card_ids = list(dict.fromkeys(card_ids))
        rows = []
        for i in range(0, len(card_ids), 500):
            part = card_ids[i:i + 500]
            rows += conn.execute(_ROWS + f" WHERE c.id IN ({','.join('?' * len(part))})", part).fetchall()
    out = {r["id"]: {"tags": [(t, "auto") for t in _row_tags(r, years)], "hidden": []} for r in rows}
    stored = conn.execute("SELECT card_id, tag, kind FROM card_tags ORDER BY kind, rowid").fetchall()
    hidden = conn.execute("SELECT card_id, tag FROM card_tag_hidden").fetchall()
    for card_id, tag, kind in stored:
        if card_id in out:
            out[card_id]["tags"].append((tag, kind))
    for card_id, tag in hidden:
        if card_id in out:
            out[card_id]["hidden"].append(tag)
    for entry in out.values():
        seen, tags = set(), []
        hide = {_fold(h) for h in entry["hidden"]}
        # Your own tags win over an automatic one with the same name.
        mine = {_fold(t) for t, k in entry["tags"] if k == "mine"}
        for t, k in entry["tags"]:
            key = _fold(t)
            if key in seen or (k == "auto" and (key in hide or key in mine)):
                continue
            seen.add(key)
            tags.append((t, k))
        entry["tags"] = tags
        entry["hidden"] = sorted({h for h in entry["hidden"]}, key=str.casefold)
    return out


def for_card(conn: sqlite3.Connection, card_id: str) -> dict:
    return for_cards(conn, [card_id]).get(card_id, {"tags": [], "hidden": []})


def names_of(entry: dict) -> set[str]:
    return {_fold(t) for t, _ in entry["tags"]}


def used(conn: sqlite3.Connection) -> list[str]:
    """Tags already used, for + Tag's suggestions: yours first, then the automatic ones saved."""
    rows = conn.execute("SELECT tag, MIN(kind = 'auto') FROM card_tags GROUP BY tag COLLATE NOCASE "
                        "ORDER BY MIN(kind = 'auto'), tag COLLATE NOCASE LIMIT 400").fetchall()
    return [r[0] for r in rows]


def mine(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT tag FROM card_tags WHERE kind = 'mine' ORDER BY tag COLLATE NOCASE")]


def add(conn: sqlite3.Connection, card_id: str, tag: str, user_id: int) -> str:
    tag = clean(tag)
    if not tag:
        raise ValueError("Type a tag first.")
    with db.transaction(conn):
        conn.execute("DELETE FROM card_tag_hidden WHERE card_id = ? AND tag = ? COLLATE NOCASE", (card_id, tag))
        conn.execute("INSERT INTO card_tags (card_id, tag, kind, added_by, added_at) VALUES (?, ?, 'mine', ?, ?) "
                     "ON CONFLICT(card_id, tag) DO UPDATE SET kind = 'mine', added_by = excluded.added_by, "
                     "added_at = excluded.added_at",
                     (card_id, tag, user_id, clock.now()))
    return tag


def remove(conn: sqlite3.Connection, card_id: str, tag: str) -> None:
    """✕: deletes your own tag, hides an automatic one."""
    with db.transaction(conn):
        gone = conn.execute("DELETE FROM card_tags WHERE card_id = ? AND tag = ? AND kind = 'mine'", (card_id, tag)).rowcount
        if not gone:
            conn.execute("INSERT OR IGNORE INTO card_tag_hidden (card_id, tag) VALUES (?, ?)", (card_id, tag))


def show(conn: sqlite3.Connection, card_id: str, tag: str) -> None:
    with db.transaction(conn):
        conn.execute("DELETE FROM card_tag_hidden WHERE card_id = ? AND tag = ?", (card_id, tag))


def cards_with(conn: sqlite3.Connection, wanted: list[str], card_ids=None) -> set[str]:
    """Stored cards that have every one of these tags (after hides)."""
    keys = {_fold(w) for w in wanted}
    return {cid for cid, entry in for_cards(conn, card_ids).items() if keys <= names_of(entry)}


def reset(conn: sqlite3.Connection) -> int:
    """Data sources › Reset tags: every automatic tag and Pokémon word you removed comes back.
    Your own tags and words stay. Automatic card tags are never deleted, only hidden, so nothing is fetched.
    Returns how many came back; the caller refreshes Describe for the Pokémon in pokemon_tag_hidden first."""
    with db.transaction(conn):
        n = conn.execute("DELETE FROM card_tag_hidden").rowcount
        n += conn.execute("DELETE FROM pokemon_tag_hidden").rowcount
    return n
