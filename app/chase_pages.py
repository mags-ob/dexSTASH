"""What the Pokémon chase screens show (design §8): the followed list, a
Pokémon's set of arts, and one art opened as print rows with language tags."""

import json
import sqlite3

from . import card_pictures, catalog, chase, display
from .inventory import VARIANTS

# Language tags: EN, JA, then ZH (mainland, then Taiwan).
TAG_ORDER = ["en", "ja", "zh-cn", "zh-tw"]
SORTS = {"value": "Value", "number": "Number", "name": "Name"}  # Value first, like a set page
SHOW = {"owned": "Owned", "missing": "Missing", "chased": "♥ Chased", "fullart": "Full art", "promo": "Promo"}
# The language tags on a tile and the Languages chips on a card page: mainland and Taiwan Chinese are one ZH.
LANG_GROUPS = (("en", "EN", "English"), ("ja", "JA", "Japanese"), ("zh", "ZH", "Chinese"))


def _lang_rank(lang: str) -> tuple:
    return (TAG_ORDER.index(lang) if lang in TAG_ORDER else len(TAG_ORDER), lang)


def lang_group(card_lang: str) -> str:
    """'en', 'ja' or 'zh' (mainland and Taiwan Chinese together)."""
    return "zh" if card_lang.startswith("zh") else card_lang


def _langs(members: list[dict], have: dict) -> list[dict]:
    """The language groups an art is printed in, in tag order, each owned or not."""
    out = []
    for key, tag, label in LANG_GROUPS:
        group = [c for c in members if lang_group(c["lang"]) == key]
        if group:
            out.append({"key": key, "tag": tag, "label": label, "owned": any(c["card_id"] in have for c in group)})
    return out


def owned(conn: sqlite3.Connection, collection_id: int, dex: int) -> dict[str, set[str]]:
    """card id -> the printings you have, for the Pokémon's cards."""
    out: dict[str, set[str]] = {}
    for r in conn.execute("""SELECT i.card_id, i.variant FROM inventory i WHERE i.user_id = ? AND i.card_id IN
                             (SELECT card_id FROM pokemon_cards WHERE dex_id = ? AND how <> 'removed')""",
                          (collection_id, dex)):
        out.setdefault(r["card_id"], set()).add(r["variant"])
    return out


def cards(conn: sqlite3.Connection, dex: int) -> dict[str, dict]:
    """The Pokémon's (or artist's) cards in every language."""
    rows = {}
    for r in conn.execute("SELECT * FROM pokemon_cards WHERE dex_id = ? AND how <> 'removed'", (dex,)):
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


def summary(conn: sqlite3.Connection, collection_id: int, dex: int) -> dict:
    """The completion bar: arts, cards and printings, owned of total, every language counted. An art is
    yours when you have any card of it, in any language."""
    rows = cards(conn, dex)
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


def art_tiles(conn: sqlite3.Connection, collection_id: int, dex: int, show: str = "", sort: str = "value") -> list[dict]:
    """One tile per art, for the Pokémon (or artist) page, every language together."""
    rows = cards(conn, dex)
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
        all_prices = [p for c in members for p in {**c["prices"], **stored.get(c["card_id"], {})}.values() if p is not None]
        tiles.append({
            "art_id": art["id"], "cover": cover, "owned": is_owned, "chased": is_chased, "prints": len(prints),
            # A tap opens the card (the one you have, else the cover)
            "link": _link(conn, opened := next((c for c in members if c["card_id"] in have), cover)),
            "pics": art_pics(conn, opened, cover, members),
            "prints_owned": len(prints_owned), "versions": len(members),
            "price": min(cover_prices.values()) if cover_prices else (min(all_prices) if all_prices else None),
            "value": max(all_prices) if all_prices else None,  # the dearest printing, for the Value sort
            "langs": _langs(members, have),
            "by_hand": all(c["how"] == "manual" for c in members),
            "release": max((c["release_date"] or "") for c in members) or "0000",
            "number": cover["number"] or "",
        })
    if sort == "number":  # newest set first, then card number
        tiles.sort(key=lambda t: (_number_key(t["number"]), t["cover"]["card_id"]))
        tiles.sort(key=lambda t: (t["cover"]["release_date"] or "0000", t["cover"]["set_id"]), reverse=True)
    elif sort == "name":
        tiles.sort(key=lambda t: ((t["cover"]["name"] or "").casefold(), t["release"]))
    else:  # value: the dearest printing first, then the ones with no price, newest first
        tiles.sort(key=lambda t: t["release"], reverse=True)
        tiles.sort(key=lambda t: (t["value"] is None, -(t["value"] or 0)))
    return tiles


def _number_key(number: str) -> tuple:
    """Card numbers in printed order: 2 before 10, letters after digits ('TG05', 'SV001')."""
    digits = "".join(ch for ch in number if ch.isdigit())
    return (number.rstrip("0123456789") if not number[:1].isdigit() else "", int(digits) if digits else 0, number)


def _promo(c: dict) -> bool:
    """A promo card (its rarity says so, or its set is a promo set)."""
    return "promo" in (c.get("set_name") or "").lower() or ((display.rarity(c.get("rarity")) or {}).get("label") or "").endswith("Promo")


def _link(conn: sqlite3.Connection, c: dict) -> str:
    if conn.execute("SELECT 1 FROM cards WHERE id = ?", (c["card_id"],)).fetchone():
        return f"/cards/{c['card_id']}"
    return f"/preview/{c['lang']}/{c['tcgdex_id']}"


def art_detail(conn: sqlite3.Connection, collection_id: int, dex: int, card_id: str) -> dict | None:
    """The art card_id is in, for the Edit art sheet: its cards one per row (language, set and number, and
    the printings it comes in, ticked when you have them), plus what you took out of it."""
    every = cards(conn, dex)
    art = next((a for a in arts(conn, dex) if card_id in a["card_ids"]), None)
    if art is None:
        return None
    have = owned(conn, collection_id, dex)
    members = [every[c] for c in art["card_ids"] if c in every]
    if not members:
        return None
    order = list(VARIANTS)
    rows = []
    for c in sorted(members, key=lambda c: (_lang_rank(c["lang"]), c["release_date"] or "9999", c["card_id"])):  # EN, JA, ZH
        mine = have.get(c["card_id"], set())
        variants = list(c["variants"]) + sorted(mine - set(c["variants"]))  # a printing you have always shows
        variants.sort(key=lambda v: order.index(v) if v in order else 99)
        pics = card_pictures._unique(list(card_pictures.for_id(conn, c["card_id"], c["image_url"])))
        rows.append({"card_id": c["card_id"], "lang": c["lang"], "tag": chase.LANG_TAGS.get(c["lang"], c["lang"].upper()),
                     "group": lang_group(c["lang"]), "owned": bool(mine), "link": _link(conn, c), "name": c["name"],
                     "set_name": c["set_name"], "set_code": c["set_code"], "number": c["number"],
                     "image": pics[0][0] if pics else None, "pics": pics,
                     "prints": [{"variant": v, "label": VARIANTS.get(v, v), "owned": v in mine} for v in variants]})
    cover = every.get(art["cover"]) or members[0]
    return {"art": art, "cover": cover, "rows": rows, "cards": len(members),
            "printings": sum(len(r["prints"]) for r in rows), "langs": _langs(members, have), "anchor": art["cover"]}


def art_languages(conn: sqlite3.Connection, collection_id: int, card_id: str) -> list[dict]:
    """A card page's Languages: the language groups of the card's art on a Pokémon or artist you follow,
    each owned or not, with the version a tap opens (the one you have, else English, else the earliest).
    Empty when the card is in no followed art, or its art is in one language only."""
    followed = {f["dex_id"] for f in chase.followed(conn, collection_id)} | \
               {f["dex_id"] for f in chase.followed(conn, collection_id, artists=True)}
    for r in conn.execute("""SELECT a.dex_id FROM art_cards ac JOIN arts a ON a.id = ac.art_id
                             WHERE ac.card_id = ? ORDER BY a.dex_id < 0, a.dex_id""", (card_id,)):
        if r["dex_id"] not in followed:
            continue
        every = cards(conn, r["dex_id"])
        art = next((a for a in arts(conn, r["dex_id"]) if card_id in a["card_ids"]), None)
        members = [every[c] for c in (art or {}).get("card_ids", []) if c in every]
        if not members or card_id not in every:
            continue
        have = owned(conn, collection_id, r["dex_id"])
        out = []
        for g in _langs(members, have):
            group = sorted((c for c in members if lang_group(c["lang"]) == g["key"]),
                           key=lambda c: (c["card_id"] not in have, c["lang"] != "en", c["release_date"] or "9999", c["card_id"]))
            here = lang_group(every[card_id]["lang"]) == g["key"]
            out.append(dict(g, here=here, link=None if here else _link(conn, group[0])))
        return out if len(out) > 1 else []
    return []


CLOSEST = 8  # how many suggestions the Closest pictures row shows


def candidates(conn: sqlite3.Connection, dex: int, card_id: str, vectors: dict) -> dict:
    """Add a card to this art: {"close": the closest pictures to the art's cover, "every": every other card of
    the Pokémon, newest first}. How alike a card is never decides whether it can be added."""
    import numpy as np
    art_list = arts(conn, dex)
    art = next((a for a in art_list if card_id in a["card_ids"]), None)
    if art is None:
        return {"close": [], "every": []}
    rows = cards(conn, dex)
    inside = {}  # a card already grouped with others: the set its art is named for
    for a in art_list:
        if len(a["card_ids"]) > 1 and a is not art and a["cover"] in rows:
            for c in a["card_ids"]:
                inside[c] = rows[a["cover"]]["set_name"] or rows[a["cover"]]["set_code"]
    cover = vectors.get(art["cover"])
    every = []
    for cid, c in rows.items():
        if cid in art["card_ids"]:
            continue
        score = float(np.dot(vectors[cid], cover)) if cover is not None and cid in vectors else None
        every.append(dict(c, score=score, in_art=inside.get(cid), tag=chase.LANG_TAGS.get(c["lang"], c["lang"].upper()),
                          group=lang_group(c["lang"])))
    close = sorted((c for c in every if c["score"] is not None), key=lambda c: -c["score"])[:CLOSEST]
    every.sort(key=lambda c: (c["release_date"] or "0000", c["set_id"], _number_key(c["number"] or "")), reverse=True)
    return {"close": close, "every": every}


def search_cards(conn: sqlite3.Connection, dex: int, card_id: str, q: str, limit: int = 60) -> list[dict]:
    """All cards: any stored card, by name, set or number, for a card the Pokémon is in but not named on.
    Leaves out the art's own cards."""
    from .display import fold
    words = fold(q).split()
    if not words:
        return []
    art = next((a for a in arts(conn, dex) if card_id in a["card_ids"]), None)
    mine = set(art["card_ids"]) if art else set()
    out = []
    for r in conn.execute("""SELECT c.id, c.set_id, c.name, c.number, c.image_url, s.name AS set_name, s.language, s.release_date
                             FROM cards c JOIN sets s ON s.set_id = c.set_id
                             ORDER BY s.release_date DESC, c.sort_number"""):
        if r["id"] in mine:
            continue
        code = catalog.tcgdex_set_id(r["set_id"]).upper()
        hay = fold(f"{r['name'] or ''} {r['set_name'] or ''} {r['number'] or ''} {code}")
        if all(w in hay for w in words):
            lang = r["language"] or "en"
            out.append({"card_id": r["id"], "name": r["name"], "number": r["number"], "image_url": r["image_url"],
                        "set_name": r["set_name"], "set_code": code,
                        "lang": lang, "tag": chase.LANG_TAGS.get(lang, lang.upper()), "group": lang_group(lang)})
            if len(out) >= limit:
                break
    return out


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
