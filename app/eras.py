"""Pokémon sets (and artist pages) in rows by era, newest first.

An era is an English series and the date its first set came out. Japanese sets come out a few months earlier,
so Japanese, Taiwan Chinese and Korean cards are placed by the Japanese start dates; English and mainland
Chinese cards by the English ones. A card goes in the era its own set's date falls in; sets before Diamond &
Pearl share one "Older" row. A series TCGdex lists after the newest one here (an English set in the sets table
whose series isn't known, released after the newest start) becomes a row of its own, so a new era needs no
app update."""

import sqlite3

# (key, name, English start, Japanese start), newest first.
ERAS = [
    ("me", "Mega Evolution", "2025-09-26", "2025-08-01"),
    ("sv", "Scarlet & Violet", "2023-03-31", "2023-01-20"),
    ("swsh", "Sword & Shield", "2020-02-07", "2019-12-06"),
    ("sm", "Sun & Moon", "2017-02-03", "2016-12-09"),
    ("xy", "XY", "2014-02-05", "2013-12-14"),
    ("bw", "Black & White", "2011-04-25", "2010-09-18"),
    ("hgss", "HeartGold & SoulSilver", "2010-02-10", "2009-07-10"),
    ("pl", "Platinum", "2009-02-11", "2008-06-14"),
    ("dp", "Diamond & Pearl", "2007-05-23", "2006-09-28"),
]
OLDER = ("older", "Older")
UNKNOWN = ("unknown", "Date not known")
_EARLY = ("ja", "zh-tw", "ko")
_SKIP = ("Pokémon TCG Pocket", "Miscellaneous", "McDonald's Collection", "POP", "Trainer kits")


def known(conn: sqlite3.Connection | None = None) -> list[tuple[str, str, str, str]]:
    """ERAS, with any newer English series found in the sets table added on top."""
    eras = list(ERAS)
    if conn is None:
        return eras
    names = {e[1] for e in eras}
    newest = eras[0][2]
    rows = conn.execute("SELECT series, MIN(release_date) AS start FROM sets WHERE language = 'en' AND series IS NOT NULL "
                        "AND release_date > ? GROUP BY series ORDER BY start", (newest,)).fetchall()
    for r in rows:
        if r["series"] not in names and r["series"] not in _SKIP:
            key = "".join(ch for ch in r["series"].lower() if ch.isalnum())[:16] or "new"
            eras.insert(0, (key, r["series"], r["start"][:10], r["start"][:10]))
    return eras


def era_of(release: str | None, lang: str, eras=None) -> tuple[str, str]:
    """(key, name) of the era a card's set date falls in."""
    if not release or release.startswith("9999"):
        return UNKNOWN
    column = 3 if lang in _EARLY else 2
    for era in eras or ERAS:
        if release[:10] >= era[column]:
            return era[0], era[1]
    return OLDER


def rows(tiles: list[dict], eras=None) -> list[dict]:
    """The tiles grouped into era rows, newest era first, each keeping the tiles' order (the page's sort):
    [{key, name, tiles, owned}]."""
    eras = eras or ERAS
    order = [e[0] for e in eras] + [OLDER[0], UNKNOWN[0]]
    groups: dict[str, dict] = {}
    for t in tiles:
        key, name = era_of(t["release"], t["cover"]["lang"], eras)
        groups.setdefault(key, {"key": key, "name": name, "tiles": [], "owned": 0})
        groups[key]["tiles"].append(t)
        groups[key]["owned"] += 1 if t["owned"] else 0
    return sorted(groups.values(), key=lambda g: order.index(g["key"]))


def name_of(key: str, eras=None) -> str | None:
    for era in (eras or ERAS):
        if era[0] == key:
            return era[1]
    return dict([OLDER, UNKNOWN]).get(key)
