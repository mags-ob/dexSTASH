"""Find's results as arts (#95): one tile per art, every language together, and the language chips on a card
page.

The arts come from arts.py (picture matching, #116): a followed Pokémon's arts with your fixes first, then the
arts built for every Pokémon. When Enhanced Search is off there are no picture matches, so a rough rule stands
in: the same Pokémon and the same artist, from the local card catalog. Everything here reads the database
only; no pictures, no other site.
"""

import json
import sqlite3

from . import arts, catalog, chase
from .chase_pages import LANG_GROUPS, lang_group

FIND_LANGS = ("en", "ja", "zh-tw", "zh-cn")  # every language Find groups (Korean searches on its own)
COVER_ORDER = {"en": 0, "ja": 1, "zh-tw": 2, "zh-cn": 3}
INDEXED = ("en", "ja", "zh-tw")  # the catalog's languages; mainland Chinese cards are stored here instead


def lang_of(card_id: str) -> str:
    """The language of one of our card ids ('ja:SV2a-035' -> 'ja', 'sv03.5-035' -> 'en')."""
    return card_id.split(":", 1)[0] if ":" in card_id else "en"


def _rough(conn: sqlite3.Connection, card_ids: list[str]) -> dict[str, str]:
    """card id -> 'r<dex>:<artist>' for catalog cards with both a Pokédex number and an artist."""
    out: dict[str, str] = {}
    by_lang: dict[str, list[str]] = {}
    for cid in card_ids:
        if lang_of(cid) in INDEXED:
            by_lang.setdefault(lang_of(cid), []).append(catalog.tcgdex_set_id(cid))
    for lang, ids in by_lang.items():
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            for r in conn.execute(f"SELECT id, illustrator, dex_ids FROM index_cards WHERE lang = ? AND id IN "
                                  f"({','.join('?' * len(part))})", [lang, *part]):
                dex = json.loads(r["dex_ids"] or "[]")
                artist = chase.artist_norm(r["illustrator"])
                if dex and artist:
                    out[catalog.local_id(lang, r["id"])] = f"r{int(dex[0])}:{artist}"
    return out


def art_keys(conn: sqlite3.Connection, card_ids: list[str]) -> dict[str, str]:
    """card id -> a key shared by the cards of one art. Cards in no art are left out. Picture matches first;
    the rough rule only when there are none (Enhanced Search off)."""
    keys = {cid: a["art"] for cid, a in arts.lookup(conn, card_ids).items()}
    if not arts.built(conn):
        for cid, key in _rough(conn, [c for c in card_ids if c not in keys]).items():
            keys[cid] = key
    return keys


def _tags(ids: list[str], owned_ids: set) -> list[dict]:
    """EN, JA and ZH for the languages among ids, each filled when you have one."""
    out = []
    for key, tag, label in LANG_GROUPS:
        group = [c for c in ids if lang_group(lang_of(c)) == key]
        if group:
            out.append({"key": key, "tag": tag, "label": label, "owned": any(c in owned_ids for c in group)})
    return out


def tiles(conn: sqlite3.Connection, results: list[dict], owned_ids: set, our_id) -> list[dict]:
    """Find's results, one per art, in the order the first card of each art came: the cover (English, else
    Japanese, else Chinese) with "langs" (the art's language tags) and "owned" (any card of it). our_id gives
    a result's card id."""
    ids = [our_id(r) for r in results]
    keys = art_keys(conn, ids)
    groups: dict[str, list[tuple[str, dict]]] = {}
    for cid, r in zip(ids, results):
        groups.setdefault(keys.get(cid) or "one:" + cid, []).append((cid, r))
    out = []
    for key, members in groups.items():
        cid, cover = min(members, key=lambda m: COVER_ORDER.get(lang_of(m[0]), 9))
        every = [m[0] for m in members]
        if key[0] in "fc":  # a picture-matched art: its cards in every language, found or not
            every = list(dict.fromkeys(every + arts.members(conn, cid)))
        out.append(dict(cover, langs=_tags(every, owned_ids), owned=any(c in owned_ids for c in every)))
    return out


def _rough_members(conn: sqlite3.Connection, card_id: str) -> list[str]:
    """The card and the catalog cards of the same Pokémon by the same artist, in English, Japanese and Taiwan
    Chinese (the rough rule)."""
    key = _rough(conn, [card_id]).get(card_id)
    if not key:
        return [card_id]
    dex, artist = key[1:].split(":", 1)
    out = [card_id]
    for r in conn.execute("SELECT lang, id, illustrator FROM index_cards WHERE lang IN ('en', 'ja', 'zh-tw') "
                          "AND json_extract(dex_ids, '$[0]') = ?", (int(dex),)):
        if chase.artist_norm(r["illustrator"]) == artist:
            out.append(catalog.local_id(r["lang"], r["id"]))
    return list(dict.fromkeys(out))


def _link(conn: sqlite3.Connection, card_id: str) -> str:
    """A stored card's page, else its preview."""
    if conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone():
        return f"/cards/{card_id}"
    return f"/preview/{lang_of(card_id)}/{catalog.tcgdex_set_id(card_id)}"


def card_languages(conn: sqlite3.Connection, collection_id: int, card_id: str) -> list[dict]:
    """A card page's language chips: one per language the card's art is printed in (EN, JA, ZH), owned or not,
    with "here" on the card's own and a link to the version a tap opens on the rest (the one you have, else
    English, else Taiwan, else mainland). A card in one language gets its one chip. Empty for other languages (Korean)."""
    if lang_group(lang_of(card_id)) not in {key for key, _, _ in LANG_GROUPS}:
        return []
    every = arts.members(conn, card_id)
    if len(every) == 1 and not arts.built(conn):
        every = _rough_members(conn, card_id)
    every = list(dict.fromkeys([card_id] + every))
    marks = ",".join("?" * len(every))
    have = {r[0] for r in conn.execute(f"SELECT DISTINCT card_id FROM inventory WHERE user_id = ? AND card_id IN ({marks})",
                                       [collection_id, *every])}
    here = lang_group(lang_of(card_id))
    out = []
    for g in _tags(every, have):
        group = sorted((c for c in every if lang_group(lang_of(c)) == g["key"]),
                       key=lambda c: (c not in have, COVER_ORDER.get(lang_of(c), 9), c))
        is_here = g["key"] == here
        out.append(dict(g, here=is_here, link=None if is_here else _link(conn, group[0])))
    return out
