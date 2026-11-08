"""What the Pokémon chase screens show (design §8): the followed list, a
Pokémon's set of arts, and one art opened as print rows with language tags."""

import json
import sqlite3

from . import card_pictures, catalog, chase, display
from .inventory import VARIANTS

# Language tags: EN, JA, then ZH (mainland, then Taiwan).
TAG_ORDER = ["en", "ja", "zh-cn", "zh-tw"]
SORTS = {"newest": "Newest first", "oldest": "Oldest first", "price": "Price high→low"}  # Newest first
SHOW = {"owned": "Owned", "missing": "Missing", "chased": "♥ Chased", "fullart": "Full art", "promo": "Promo"}
LANG_CHIPS = ("en", "ja", "zh")
PATTERNS = {"reverse": "pat-rev", "pokeball": "pat-pb", "masterball": "pat-mb", "holo": "pat-holo"}


def _lang_rank(lang: str) -> tuple:
    return (TAG_ORDER.index(lang) if lang in TAG_ORDER else len(TAG_ORDER), lang)


def _in_lang(card_lang: str, lang: str) -> bool:
    """The language chips: 'zh' is mainland and Taiwan Chinese together."""
    return card_lang.startswith("zh") if lang == "zh" else card_lang == lang


def owned(conn: sqlite3.Connection, collection_id: int, dex: int) -> dict[str, set[str]]:
    """card id -> the printings you have, for the Pokémon's cards."""
    out: dict[str, set[str]] = {}
    for r in conn.execute("""SELECT i.card_id, i.variant FROM inventory i WHERE i.user_id = ? AND i.card_id IN
                             (SELECT card_id FROM pokemon_cards WHERE dex_id = ? AND how <> 'removed')""",
                          (collection_id, dex)):
        out.setdefault(r["card_id"], set()).add(r["variant"])
    return out


def cards(conn: sqlite3.Connection, dex: int, lang: str = "") -> dict[str, dict]:
    rows = {}
    for r in conn.execute("SELECT * FROM pokemon_cards WHERE dex_id = ? AND how <> 'removed'", (dex,)):
        if lang and not _in_lang(r["lang"], lang):
            continue
        c = dict(r)
        c["variants"] = json.loads(c["variants"] or "[]") or ["normal"]
        c["prices"] = json.loads(c["prices_json"] or "{}")
        c["tcgdex_id"] = catalog.tcgdex_set_id(c["card_id"])
        c["print_key"] = catalog.tcgdex_set_id(c["set_id"]) if c["lang"] == "en" else c["set_id"]
        rows[c["card_id"]] = c
    return rows


def _prices_for(conn: sqlite3.Connection, card_ids) -> dict[str, dict[str, float]]:
    """Stored prices (owned cards, with ≈ and your own) win over the chase's own."""
    out: dict[str, dict[str, float]] = {}
    ids = list(card_ids)
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in conn.execute(f"SELECT card_id, variant, price FROM prices "
                              f"WHERE card_id IN ({','.join('?' * len(part))})", part):
            out.setdefault(r["card_id"], {})[r["variant"]] = r["price"]
    return out


def arts(conn: sqlite3.Connection, dex: int) -> list[dict]:
    """[{id, cover, card_ids}] in the stored order."""
    out: dict[int, dict] = {}
    for r in conn.execute("""SELECT a.id, a.cover_card_id, ac.card_id, ac.how, ac.score FROM arts a
                             JOIN art_cards ac ON ac.art_id = a.id WHERE a.dex_id = ? ORDER BY a.id""", (dex,)):
        art = out.setdefault(r["id"], {"id": r["id"], "cover": r["cover_card_id"], "card_ids": [], "how": {}, "score": {}})
        art["card_ids"].append(r["card_id"])
        art["how"][r["card_id"]] = r["how"]
        art["score"][r["card_id"]] = r["score"]
    return list(out.values())


def summary(conn: sqlite3.Connection, collection_id: int, dex: int, lang: str = "") -> dict:
    """The completion bar: arts, cards and printings, owned of total (for the language shown)."""
    rows = cards(conn, dex, lang)
    have = owned(conn, collection_id, dex)
    art_list = [a for a in arts(conn, dex) if any(c in rows for c in a["card_ids"])]
    printings = sum(len(c["variants"]) for c in rows.values())
    printings_owned = sum(len(set(c["variants"]) & have.get(cid, set())) + len(have.get(cid, set()) - set(c["variants"]))
                          for cid, c in rows.items() if cid in have)
    out = {"arts": len(art_list), "arts_owned": sum(1 for a in art_list if any(c in have for c in a["card_ids"] if c in rows)),
           "cards": len(rows), "cards_owned": sum(1 for cid in rows if cid in have),
           "printings": printings, "printings_owned": min(printings_owned, printings)}
    out["percent"] = round(100 * out["arts_owned"] / out["arts"]) if out["arts"] else 0
    return out


def art_tiles(conn: sqlite3.Connection, collection_id: int, dex: int, lang: str = "", show: str = "",
              sort: str = "newest") -> list[dict]:
    """One tile per art, for the Pokémon set page."""
    rows = cards(conn, dex, lang)
    have = owned(conn, collection_id, dex)
    stored = _prices_for(conn, rows)
    chased = {r[0] for r in conn.execute("SELECT card_id FROM wants WHERE user_id = ?", (collection_id,))}
    tiles = []
    for art in arts(conn, dex):
        members = [rows[c] for c in art["card_ids"] if c in rows]
        if not members:
            continue
        cover = rows.get(art["cover"]) or rows[chase.cover_of(members)]
        prints = {(c["print_key"], v) for c in members for v in c["variants"]}
        prints_owned = {(c["print_key"], v) for c in members for v in have.get(c["card_id"], set())}
        is_owned = any(c["card_id"] in have for c in members)
        is_chased = any(c["card_id"] in chased for c in members)
        if (show == "missing" and is_owned) or (show == "owned" and not is_owned) or (show == "chased" and not is_chased):
            continue
        if show == "fullart" and not any("full art" in display.rarity_groups(c["rarity"]) for c in members):
            continue
        if show == "promo" and not any(_promo(c) for c in members):
            continue
        cover_prices = {**cover["prices"], **stored.get(cover["card_id"], {})}
        all_prices = [p for c in members for p in {**c["prices"], **stored.get(c["card_id"], {})}.values()]
        tiles.append({
            "art_id": art["id"], "cover": cover, "owned": is_owned, "chased": is_chased, "prints": len(prints),
            # A tap opens the card (the one you have, else the cover)
            "link": _link(conn, opened := next((c for c in members if c["card_id"] in have), cover)),
            "pics": art_pics(conn, opened, cover, members),
            "prints_owned": len(prints_owned), "versions": len(members),
            "price": min(cover_prices.values()) if cover_prices else (min(all_prices) if all_prices else None),
            "by_hand": all(c["how"] == "manual" for c in members),
            "release": min((c["release_date"] or "9999") for c in members),
        })
    if sort == "newest":
        tiles.sort(key=lambda t: t["release"], reverse=True)
    elif sort == "price":
        tiles.sort(key=lambda t: (t["price"] is None, -(t["price"] or 0)))
    else:
        tiles.sort(key=lambda t: t["release"])
    return tiles


def _promo(c: dict) -> bool:
    """A promo card (its rarity says so, or its set is a promo set)."""
    return "promo" in (c.get("set_name") or "").lower() or ((display.rarity(c.get("rarity")) or {}).get("label") or "").endswith("Promo")


def _link(conn: sqlite3.Connection, c: dict) -> str:
    if conn.execute("SELECT 1 FROM cards WHERE id = ?", (c["card_id"],)).fetchone():
        return f"/cards/{c['card_id']}"
    return f"/preview/{c['lang']}/{c['tcgdex_id']}"


def art_detail(conn: sqlite3.Connection, collection_id: int, dex: int, card_id: str, lang: str = "") -> dict | None:
    """The art card_id is in, as print rows (option D), plus what you took out of it."""
    every = cards(conn, dex)
    rows = cards(conn, dex, lang) if lang else every
    art = next((a for a in arts(conn, dex) if card_id in a["card_ids"]), None)
    if art is None:
        return None
    have = owned(conn, collection_id, dex)
    members = [rows[c] for c in art["card_ids"] if c in rows] or [every[c] for c in art["card_ids"] if c in every]
    prints: dict[tuple, dict] = {}
    for c in members:
        for v in c["variants"]:
            row = prints.setdefault((c["print_key"], v), {
                "variant": v, "label": VARIANTS.get(v, v), "pattern": PATTERNS.get(v, ""), "cards": [],
                "release": c["release_date"] or "9999"})
            row["cards"].append({"card_id": c["card_id"], "lang": c["lang"], "tag": chase.LANG_TAGS.get(c["lang"], c["lang"].upper()),
                                 "owned": v in have.get(c["card_id"], set()), "link": _link(conn, c),
                                 "name": c["name"], "image": c["image_url"], "set_name": c["set_name"],
                                 "set_code": c["set_code"], "number": c["number"]})
            row["release"] = min(row["release"], c["release_date"] or "9999")
    order = list(VARIANTS)
    out_rows = []
    for (key, v), row in sorted(prints.items(), key=lambda kv: (kv[1]["release"], kv[0][0],
                                                                 order.index(kv[0][1]) if kv[0][1] in order else 99)):
        row["cards"].sort(key=lambda c: _lang_rank(c["lang"]))
        lead = next((c for c in row["cards"] if c["lang"] == "en"), row["cards"][0])
        pics = card_pictures._unique([p for c in [lead] + row["cards"] for p in card_pictures.for_id(conn, c["card_id"], c["image"])])
        row.update(lead=lead, image=pics[0][0] if pics else None, pics=pics,
                   have=sum(1 for c in row["cards"] if c["owned"]), total=len(row["cards"]),
                   card_ids=sorted({c["card_id"] for c in row["cards"]}))
        out_rows.append(row)
    removed = []
    for f in chase.fixes_of(conn, dex):
        if f["join_card_id"] is None and f["left_card_id"] in art["card_ids"] and f["card_id"] in every:
            removed.append(every[f["card_id"]])
    cover = every.get(art["cover"]) or members[0]
    return {"art": art, "cover": cover, "rows": out_rows, "removed": removed,
            "cards": len(members), "anchor": art["cover"],
            "added": [every[c] for c in art["card_ids"] if art["how"].get(c) == "manual" and c in every]}


def candidates(conn: sqlite3.Connection, dex: int, card_id: str, vectors: dict, limit: int = 15) -> list[dict]:
    """＋ Add a version: the Pokémon's other cards, closest to this art's cover first."""
    import numpy as np
    art = next((a for a in arts(conn, dex) if card_id in a["card_ids"]), None)
    if art is None:
        return []
    rows = cards(conn, dex)
    cover = vectors.get(art["cover"])
    out = []
    for cid, c in rows.items():
        if cid in art["card_ids"]:
            continue
        score = float(np.dot(vectors[cid], cover)) if cover is not None and cid in vectors else None
        out.append(dict(c, score=score, word=chase.likeness_word(score)))
    out.sort(key=lambda c: (c["score"] is None, -(c["score"] or 0), c["release_date"] or "9999"))
    return out[:limit]


def followed_list(conn: sqlite3.Connection, collection_id: int) -> list[dict]:
    return [dict(f, **summary(conn, collection_id, f["dex_id"])) for f in chase.followed(conn, collection_id)]


def art_pics(conn: sqlite3.Connection, opened: dict, cover: dict, members: list[dict]) -> list[tuple[str, str]]:
    """An art tile's pictures, best first: every picture known for the card it opens (saved copy,
    TCGplayer, links, twins, photo: the list every other grid uses), then the cover's, then the other prints'
    (the same art by definition). Nothing is asked over the network."""
    pics = list(card_pictures.for_id(conn, opened["card_id"], opened.get("image_url")))
    if cover is not opened:
        pics += card_pictures.for_id(conn, cover["card_id"], cover.get("image_url"))
    pics += [(c.get("image_url"), "") for c in members]
    return card_pictures._unique(pics)


def followed_artist_list(conn: sqlite3.Connection, collection_id: int) -> list[dict]:
    """Followed artists for Chases, with the same completion bar as a Pokémon."""
    return [dict(f, **summary(conn, collection_id, f["dex_id"])) for f in chase.followed(conn, collection_id, artists=True)]


def sets_for_card(conn: sqlite3.Connection, collection_id: int, card_id: str) -> list[dict]:
    """The Pokémon you follow, ticked when this card is in its set (for "Add to a Pokémon set…")."""
    inside = {r[0] for r in conn.execute("SELECT dex_id FROM pokemon_cards WHERE card_id = ? AND how <> 'removed'", (card_id,))}
    return [dict(f, ticked=f["dex_id"] in inside) for f in chase.followed(conn, collection_id)]
