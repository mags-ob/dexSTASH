"""Arts for every Pokémon, not only followed ones: which cards share an illustration.

Built from the Enhanced Search fingerprints (pictures.py) after they change, with the same rule as a followed
Pokémon's page (chase.group): only cards of one Pokémon are compared, so each Pokémon takes a moment. Card
details come from the local card catalog (card_index.py, index_cards) for English, Japanese and Taiwan Chinese,
and from the stored mainland Chinese sets. Taiwan cards use their Japanese twin's fingerprint. Kept in
catalog.card_arts; your fixes on a Pokémon (art_fixes) count here too.

lookup and members answer from the database alone (no pictures, no other site): a followed Pokémon's arts
first, then card_arts.
"""

import json
import logging
import sqlite3

from . import catalog, chase, db

log = logging.getLogger("dexstash.arts")


def built(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM card_arts LIMIT 1").fetchone() is not None


def _illustrations() -> dict:
    """card id -> illustration fingerprint, from every Enhanced Search index (pictures not yet done are left out)."""
    from . import pictures
    out = {}
    for lang in pictures.languages():
        index = pictures.index_for(lang)
        with index.lock:
            for row, v in zip(index.rows, index.art):
                if v.any() and not row["key"].startswith(("hand:", "scan:")):
                    out.setdefault(row["card"], v)
    return out


def _names():
    """The Pokémon name list, when it's already here (for mainland Chinese cards' Pokémon)."""
    from .names import Names
    names = Names(db.DB_PATH.parent / "pokemon_species_names.csv")
    return names if names.available() else None


def _cards(conn: sqlite3.Connection, names=None) -> dict[int, list[dict]]:
    """Pokédex number -> its cards: {card_id, lang, illustrator, rarity, release_date}."""
    by_dex: dict[int, list[dict]] = {}
    for r in conn.execute("SELECT lang, id, rarity, illustrator, dex_ids FROM index_cards "
                          "WHERE lang IN ('en', 'ja', 'zh-tw') AND dex_ids IS NOT NULL"):
        dex = json.loads(r["dex_ids"] or "[]")
        if dex:  # the card's main Pokémon, as on a Pokémon's page
            by_dex.setdefault(int(dex[0]), []).append(
                {"card_id": catalog.local_id(r["lang"], r["id"]), "lang": r["lang"], "illustrator": r["illustrator"],
                 "rarity": r["rarity"], "release_date": None})
    if names is not None:
        for c in conn.execute("""SELECT c.id, c.name, c.twin_name, c.rarity, s.release_date FROM cards c
                                 JOIN sets s ON s.set_id = c.set_id WHERE s.language = 'zh-cn'"""):
            dex = names.species_id(c["twin_name"] or "") or names.species_id(c["name"] or "")
            if dex:
                by_dex.setdefault(dex, []).append({"card_id": c["id"], "lang": "zh-cn", "illustrator": None,
                                                   "rarity": c["rarity"], "release_date": c["release_date"]})
    return by_dex


def rebuild(conn: sqlite3.Connection, job=None, names=None) -> int:
    """Group every Pokémon's cards into arts again, and the followed Pokémon's pages with them. Returns how
    many cards share an art."""
    from .display import rarity_groups
    art = _illustrations()
    for card_id in [c for c in list(art) if c.startswith("ja:")]:  # Taiwan cards are the Japanese ones
        art.setdefault("zh-tw:" + card_id[3:], art[card_id])
    by_dex = _cards(conn, names if names is not None else _names())
    rows = []
    for n, (dex, cards) in enumerate(sorted(by_dex.items())):
        if job is not None and n % 50 == 0:
            job.progress(n, len(by_dex), "Grouping arts")
        cards = [c for c in cards if c["card_id"] in art]
        if len(cards) < 2:
            continue
        rarity = {c["card_id"]: c["rarity"] for c in cards}
        for a in chase.group(cards, {}, chase.fixes_of(conn, dex), art=art):
            if len(a["cards"]) > 1:
                rows += [(cid, dex, a["cover"], int("secret" in rarity_groups(rarity.get(cid)))) for cid in a["cards"]]
    with db.transaction(conn):
        conn.execute("DELETE FROM card_arts")
        conn.executemany("INSERT OR REPLACE INTO card_arts (card_id, dex_id, art, secret) VALUES (?, ?, ?, ?)", rows)
    # Followed Pokémon and artists: their pages use the illustrations too, with your fixes.
    for (dex,) in conn.execute("SELECT DISTINCT dex_id FROM followed_pokemon").fetchall():
        try:
            chase.regroup(conn, dex, chase.fingerprints(conn, dex, None, art=True))
        except Exception:
            log.exception("couldn't group #%s again", dex)
    return len(rows)


def lookup(conn: sqlite3.Connection, card_ids: list[str]) -> dict[str, dict]:
    """card id -> {"art": a key shared by the cards of one art, "secret": a rainbow or gold version}, for the
    cards in an art: a followed Pokémon's first, then card_arts. Cards in no art are left out. One look in
    the database, no pictures."""
    from .display import rarity_groups
    out: dict[str, dict] = {}
    ids = list(dict.fromkeys(card_ids))
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        marks = ",".join("?" * len(part))
        for r in conn.execute(f"""SELECT ac.card_id, ac.art_id, pc.rarity FROM art_cards ac JOIN arts a ON a.id = ac.art_id
                                  JOIN pokemon_cards pc ON pc.dex_id = a.dex_id AND pc.card_id = ac.card_id
                                  WHERE ac.card_id IN ({marks}) ORDER BY a.dex_id < 0, a.dex_id""", part):
            out.setdefault(r["card_id"], {"art": f"f{r['art_id']}", "secret": "secret" in rarity_groups(r["rarity"])})
        for r in conn.execute(f"SELECT card_id, dex_id, art, secret FROM card_arts WHERE card_id IN ({marks})", part):
            out.setdefault(r["card_id"], {"art": f"c{r['dex_id']}:{r['art']}", "secret": bool(r["secret"])})
    return out


def members(conn: sqlite3.Connection, card_id: str) -> list[str]:
    """Every card in this card's art (a followed Pokémon's first, then card_arts), the card itself when it's in none."""
    r = conn.execute("""SELECT ac.art_id FROM art_cards ac JOIN arts a ON a.id = ac.art_id
                        WHERE ac.card_id = ? ORDER BY a.dex_id < 0, a.dex_id LIMIT 1""", (card_id,)).fetchone()
    if r:
        return [x[0] for x in conn.execute("SELECT card_id FROM art_cards WHERE art_id = ? ORDER BY card_id", (r[0],))]
    r = conn.execute("SELECT dex_id, art FROM card_arts WHERE card_id = ?", (card_id,)).fetchone()
    if r:
        return [x[0] for x in conn.execute("SELECT card_id FROM card_arts WHERE dex_id = ? AND art = ? ORDER BY card_id",
                                           (r["dex_id"], r["art"]))]
    return [card_id]
