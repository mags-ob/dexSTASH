"""The Pokémon master chase: every card of a Pokémon you follow, in English,
Japanese and Chinese, grouped into arts (the same picture), then prints, then languages.

Only followed Pokémon are processed. Following one starts a background job
(jobs.py, shown under "Running now") that:
1. lists the Pokémon's cards in English, Japanese and Taiwan Chinese, plus loaded mainland
   Chinese sets, keeping the cards whose main Pokémon has its Pokédex number;
2. stores what the chase page needs: illustrator, printings, picture address,
   TCGplayer prices (pokemon_cards);
3. fingerprints the pictures the Enhanced Search indexes don't have yet, into a
   small index of its own (data/index/pokemon/<dex>); pictures aren't kept;
4. groups the cards into arts: same illustrator and near-identical pictures,
   then your fixes (art_fixes) on top.

↻ Re-check repeats 3-4. Check for updates (Data sources) adds new cards.
"""

import json
import logging
import sqlite3

from . import catalog, clock, jobs, prices, tcgdex

log = logging.getLogger("dexstash.chase")

# English, Japanese and Chinese only. Taiwan Chinese comes from TCGdex; mainland
# Chinese from the sets loaded under Data sources (TCGdex lists those sets without cards).
LANGS = ["en", "ja", "zh-tw"]
KEPT = {"en", "ja", "zh-tw", "zh-cn"}
LANG_NAMES = {"en": "English", "ja": "Japanese", "zh-tw": "Chinese (Taiwan)", "zh-cn": "Chinese", "zh": "Chinese"}
LANG_TAGS = {"en": "EN", "ja": "JA", "zh-cn": "ZH", "zh-tw": "ZH"}

THRESHOLD = 0.92   # picture likeness for "the same art"; tune on Clefairy
TOO_MANY = 3000    # a filtered list this long means TCGdex ignored the filter
WORDS = ((0.92, "Very close"), (0.80, "Possible"), (0.0, "Long shot"))


def now() -> str:
    return clock.now()


def likeness_word(score: float | None) -> str:
    return next(word for floor, word in WORDS if (score or 0) >= floor)


# --- following ----------------------------------------------------------------------

def followed(conn: sqlite3.Connection, collection_id: int, artists: bool = False) -> list[dict]:
    """Followed Pokémon, or (artists=True) followed artists, whose dex_id is -artists.id."""
    if artists:
        return [dict(r) for r in conn.execute(
            "SELECT f.*, a.norm FROM followed_pokemon f JOIN artists a ON a.id = -f.dex_id "
            "WHERE f.collection_id = ? AND f.dex_id < 0 ORDER BY a.norm", (collection_id,))]
    return [dict(r) for r in conn.execute(
        "SELECT * FROM followed_pokemon WHERE collection_id = ? AND dex_id > 0 ORDER BY dex_id", (collection_id,))]


# --- Artists ------------------------------------------------------------------

def artist_norm(name: str | None) -> str:
    return " ".join(_norm(name).split())


def artist_key(conn: sqlite3.Connection, name: str) -> int:
    """The artist's key (a negative number, used where a Pokédex number goes), made the first time."""
    name = " ".join((name or "").split())
    norm = artist_norm(name)
    if not norm:
        raise ValueError("No artist name.")
    row = conn.execute("SELECT id FROM artists WHERE norm = ?", (norm,)).fetchone()
    if row is None:
        row = [conn.execute("INSERT INTO artists (norm, name) VALUES (?, ?)", (norm, name)).lastrowid]
        conn.commit()
    return -row[0]


def artist(conn: sqlite3.Connection, key_or_norm) -> dict | None:
    """{key, norm, name} by key (negative int) or folded name."""
    if isinstance(key_or_norm, int):
        row = conn.execute("SELECT * FROM artists WHERE id = ?", (-key_or_norm,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM artists WHERE norm = ?", (artist_norm(key_or_norm),)).fetchone()
    return {"key": -row["id"], "norm": row["norm"], "name": row["name"]} if row else None


def list_artist_briefs(api, name: str) -> list[dict]:
    """The artist's cards in api.lang: TCGdex's illustrator filter, exactly, then checked here."""
    resp = api.client.get("/cards", params={"illustrator": f"eq:{name}"})
    resp.raise_for_status()
    found = resp.json()
    return tcgdex.without_pocket(found if isinstance(found, list) and len(found) < TOO_MANY else [])


def collect_artist(conn: sqlite3.Connection, key: int, name: str, api_for, job=None,
                   langs=None) -> tuple[int, list[str]]:
    """Steps 1-2 for an artist: their cards in English, Japanese and Chinese (Taiwan). Mainland sets
    don't say who drew a card, so they're left out. Returns (new cards, languages not reached)."""
    sets: dict = {}
    new, unreachable = 0, []
    langs = langs or LANGS
    wanted = artist_norm(name)
    for n, lang in enumerate(langs):
        if job is not None:
            job.progress(n, len(langs), f"{LANG_NAMES.get(lang, lang)} cards")
        try:
            api = api_for(lang)
            briefs = list_artist_briefs(api, name)
        except Exception as e:
            log.info("couldn't list %s cards by %s: %s", lang, name, e)
            unreachable.append(lang)
            continue
        removed = {r[0] for r in conn.execute("SELECT card_id FROM pokemon_cards WHERE dex_id = ? AND how = 'removed'", (key,))}

        def detail(b):
            try:
                return api.get_card(b["id"])
            except Exception:
                return None

        with jobs.pool(6) as pool:
            details = [d for d in pool.map(detail, briefs) if d and artist_norm(d.get("illustrator")) == wanted]
        rows = [card_row(lang, d, _set_info(api, (d.get("set") or {}).get("id") or "", sets)) for d in details]
        new += save_cards(conn, key, [r for r in rows if r["card_id"] not in removed])
    return new, unreachable


def is_followed(conn: sqlite3.Connection, collection_id: int, dex: int) -> bool:
    return conn.execute("SELECT 1 FROM followed_pokemon WHERE collection_id = ? AND dex_id = ?",
                        (collection_id, dex)).fetchone() is not None


def follow(conn: sqlite3.Connection, collection_id: int, dex: int, name: str, by: int | None) -> bool:
    """Returns False if it was followed already."""
    if is_followed(conn, collection_id, dex):
        return False
    conn.execute("INSERT INTO followed_pokemon (collection_id, dex_id, name, added_by, added_at) VALUES (?, ?, ?, ?, ?)",
                 (collection_id, dex, name, by, now()))
    conn.commit()
    return True


def unfollow(conn: sqlite3.Connection, collection_id: int, dex: int, folder=None) -> None:
    """Stop following. When no one follows it any more, its cards, arts and
    fingerprints go, except cards someone has and the cards you added or took
    out by hand (so following again keeps your choices). Your art fixes stay too."""
    conn.execute("DELETE FROM followed_pokemon WHERE collection_id = ? AND dex_id = ?", (collection_id, dex))
    if not conn.execute("SELECT 1 FROM followed_pokemon WHERE dex_id = ?", (dex,)).fetchone():
        conn.execute("DELETE FROM art_cards WHERE art_id IN (SELECT id FROM arts WHERE dex_id = ?)", (dex,))
        conn.execute("DELETE FROM arts WHERE dex_id = ?", (dex,))
        conn.execute("DELETE FROM pokemon_cards WHERE dex_id = ? AND how = 'auto' "
                     "AND card_id NOT IN (SELECT card_id FROM inventory)", (dex,))
        _index(dex, folder).drop()
    conn.commit()
    prices.prune(conn)


FOLLOWED_CARDS_SQL = prices.FOLLOWED_SQL


# --- step 1: listing the cards --------------------------------------------------------

def main_dex(detail: dict) -> int | None:
    """The card's main Pokémon (the first one TCGdex lists)."""
    dex = detail.get("dexId") or []
    return int(dex[0]) if dex else None


def _norm(text: str | None) -> str:
    from .display import fold
    return fold(text).strip()


def list_briefs(api, dex: int, names_in: list[str]) -> list[dict]:
    """Brief cards in api.lang that may be this Pokémon: TCGdex's dexId filter,
    or a name search when the filter isn't honoured (the caller checks each
    card's dexId either way)."""
    try:
        resp = api.client.get("/cards", params={"dexId": f"eq:{dex}"})
        resp.raise_for_status()
        found = resp.json()
        if isinstance(found, list) and 0 < len(found) < TOO_MANY:
            return found
    except Exception as e:
        log.info("dexId filter failed in %s: %s", api.lang, e)
    out, seen = [], set()
    for name in dict.fromkeys(n for n in names_in if n):
        try:
            for b in api.search_cards(name=name):
                if b["id"] not in seen:
                    seen.add(b["id"])
                    out.append(b)
        except Exception as e:
            log.info("name search %r failed in %s: %s", name, api.lang, e)
    return out


def _variants(detail: dict) -> list[str]:
    v = detail.get("variants") or {}
    keys = [k for k, flag in (("normal", "normal"), ("holo", "holo"), ("reverse", "reverse"),
                              ("first_ed", "firstEdition")) if v.get(flag)]
    for d in detail.get("variants_detailed") or []:
        foil = {"pokeball": "pokeball", "masterball": "masterball"}.get(d.get("foil") or "")
        if foil and foil not in keys:
            keys.append(foil)
    return keys


def _set_info(api, set_id: str, cache: dict) -> dict:
    key = (api.lang, set_id)
    if key not in cache:
        try:
            cache[key] = tcgdex.cached_set(api, set_id)
        except Exception:
            cache[key] = {}
    return cache[key]


def card_row(lang: str, detail: dict, set_detail: dict) -> dict:
    s = detail.get("set") or {}
    set_id = s.get("id") or set_detail.get("id") or ""
    code = ((set_detail.get("abbreviation") or {}).get("official") or set_id).upper()
    logo = s.get("logo") or set_detail.get("logo")
    return {
        "card_id": catalog.local_id(lang, detail["id"]), "lang": lang, "set_id": catalog.local_id(lang, set_id),
        "set_name": s.get("name") or set_detail.get("name"), "set_code": code,
        "release_date": set_detail.get("releaseDate"), "number": detail.get("localId"), "name": detail.get("name"),
        "illustrator": detail.get("illustrator"), "rarity": detail.get("rarity"),
        "image_url": tcgdex.image_url(detail.get("image")), "variants": json.dumps(_variants(detail)),
        "prices_json": json.dumps(prices.by_variant(prices.as_rows(detail))),
        "set_logo": f"{logo}.webp" if logo else None,
    }


def save_cards(conn: sqlite3.Connection, dex: int, rows: list[dict]) -> int:
    """Add or refresh cards. Cards you took out (how 'removed') stay out; ones
    you added by hand stay 'manual'. Returns how many were new."""
    have = {r[0] for r in conn.execute("SELECT card_id FROM pokemon_cards WHERE dex_id = ?", (dex,))}
    stored = _stored(conn, [r["card_id"] for r in rows])
    stamp = now()
    for r in rows:
        if r["card_id"] in stored:  # a stored card's prices are in prices: no second copy
            r = {**r, "prices_json": None}
        conn.execute(
            """INSERT INTO pokemon_cards (dex_id, card_id, lang, set_id, set_name, set_code, release_date, number, name,
                 illustrator, rarity, image_url, variants, prices_json, how, updated_at)
               VALUES (:dex, :card_id, :lang, :set_id, :set_name, :set_code, :release_date, :number, :name,
                 :illustrator, :rarity, :image_url, :variants, :prices_json, 'auto', :at)
               ON CONFLICT(dex_id, card_id) DO UPDATE SET lang = excluded.lang, set_id = excluded.set_id,
                 set_name = excluded.set_name, set_code = excluded.set_code, release_date = excluded.release_date,
                 number = excluded.number, name = excluded.name, illustrator = excluded.illustrator,
                 rarity = excluded.rarity, image_url = COALESCE(excluded.image_url, image_url),
                 variants = excluded.variants, prices_json = excluded.prices_json, updated_at = excluded.updated_at""",
            {**{k: r.get(k) for k in ("card_id", "lang", "set_id", "set_name", "set_code", "release_date", "number",
                                      "name", "illustrator", "rarity", "image_url", "variants", "prices_json")},
             "dex": dex, "at": stamp})
    conn.commit()
    return len({r["card_id"] for r in rows} - have)


def _stored(conn: sqlite3.Connection, card_ids: list[str]) -> set[str]:
    """Which of these cards are stored in cards (and so priced in prices)."""
    out: set[str] = set()
    for i in range(0, len(card_ids), 500):
        part = card_ids[i:i + 500]
        out |= {r[0] for r in conn.execute(f"SELECT id FROM cards WHERE id IN ({','.join('?' * len(part))})", part)}
    return out


def mainland_rows(conn: sqlite3.Connection, dex: int, names) -> list[dict]:
    """Cards of the Pokémon in the mainland Chinese sets loaded here, by the
    English twin's name or the printed name."""
    out = []
    for c in conn.execute(
            """SELECT c.*, s.name AS set_name, s.release_date FROM cards c JOIN sets s ON s.set_id = c.set_id
               WHERE s.language = 'zh-cn'""").fetchall():
        if (names.species_id(c["twin_name"] or "") or names.species_id(c["name"] or "")) != dex:
            continue
        variants = [k for k, col in (("normal", "has_normal"), ("holo", "has_holo"), ("reverse", "has_reverse"),
                                     ("star", "has_star"), ("star2", "has_star2")) if c[col]]
        out.append({"card_id": c["id"], "lang": "zh-cn", "set_id": c["set_id"], "set_name": c["set_name"],
                    "set_code": catalog.tcgdex_set_id(c["set_id"]).upper(), "release_date": c["release_date"],
                    "number": c["number"], "name": c["name"], "illustrator": None, "rarity": c["rarity"],
                    "image_url": c["image_url"], "variants": json.dumps(variants), "prices_json": "{}"})
    return out


def collect(conn: sqlite3.Connection, dex: int, names, api_for, job=None, langs=None) -> tuple[int, list[str]]:
    """Steps 1-2 for one Pokémon in every language. Returns (new cards, languages TCGdex couldn't be reached for)."""
    english = names.english_name(dex) or ""
    sets: dict = {}
    new, unreachable = 0, []
    langs = langs or LANGS
    for n, lang in enumerate(langs):
        if job is not None:
            job.progress(n, len(langs) + 1, f"{LANG_NAMES.get(lang, lang)} cards")
        try:
            api = api_for(lang)
            briefs = list_briefs(api, dex, [names.name_in(dex, lang), english])
        except Exception as e:
            log.info("couldn't list %s cards for #%s: %s", lang, dex, e)
            unreachable.append(lang)
            continue
        removed = {r[0] for r in conn.execute("SELECT card_id FROM pokemon_cards WHERE dex_id = ? AND how = 'removed'", (dex,))}

        def detail(b):
            try:
                return api.get_card(b["id"])
            except Exception:
                return None

        with jobs.pool(6) as pool:
            details = [d for d in pool.map(detail, briefs) if d and main_dex(d) == dex]
        rows = [card_row(lang, d, _set_info(api, (d.get("set") or {}).get("id") or "", sets)) for d in details]
        new += save_cards(conn, dex, [r for r in rows if r["card_id"] not in removed])
    if job is not None:
        job.progress(len(langs), len(langs) + 1, "Chinese cards")
    new += save_cards(conn, dex, mainland_rows(conn, dex, names))
    return new, unreachable


# --- step 3: fingerprints -----------------------------------------------------------

def _index(dex: int, folder=None):
    from . import pictures
    return pictures.Index(str(dex), folder or (pictures.INDEX / "pokemon"))


def picture_key(url: str) -> str:
    """The key pictures.picture_image reads a picture by."""
    if url.startswith("/chs-img/"):
        _, _, cid, n = url.split("/")
        return f"chs:{cid}/{n}"
    if url.startswith("/card-photos/"):
        return f"hand:{url.rsplit('/', 1)[1]}"
    return url


def fingerprints(conn: sqlite3.Connection, dex: int, encoder, job=None, fetch=None, folder=None,
                 known=None) -> dict:
    """card id -> picture fingerprint, for the Pokémon's cards with a picture.
    Reuses the Enhanced Search indexes; the rest are fetched, fingerprinted
    and let go, into the Pokémon's own index. Without an encoder, only the
    fingerprints already made (for regrouping after a fix, in a second)."""
    import numpy as np
    from . import pictures
    cards = [dict(r) for r in conn.execute(
        "SELECT card_id, lang, set_id, image_url FROM pokemon_cards WHERE dex_id = ? AND how <> 'removed'", (dex,))]
    out = {}
    if known is None:
        known = {}
        wanted = {c["card_id"] for c in cards}
        for lang in pictures.languages():
            index = pictures.index_for(lang)
            with index.lock:
                for i, row in enumerate(index.rows):
                    if row["card"] in wanted and not row["key"].startswith(("hand:", "scan:")):
                        known.setdefault(row["card"], index.vectors[i])
    own = _index(dex, folder)
    for row, vector in zip(own.rows, own.vectors):
        known.setdefault(row["card"], vector)
    todo = [c for c in cards if c["card_id"] not in known and c["image_url"]] if encoder is not None else []
    for i in range(0, len(todo), 8):
        if job is not None:
            job.progress(i, len(todo), "Fingerprinting pictures")
        rows, images = [], []
        for c in todo[i:i + 8]:
            try:
                images.append(pictures.picture_image({"key": picture_key(c["image_url"]), "url": c["image_url"]}, fetch))
                rows.append({"card": c["card_id"], "set": c["set_id"], "key": c["image_url"]})
            except Exception as e:
                log.info("no picture for %s: %s", c["card_id"], e)
        if images:
            vectors = encoder.embed(images)
            own.put(rows, vectors)
            for r, v in zip(rows, vectors):
                known[r["card"]] = v
    if todo:
        own.save()
    for c in cards:
        if c["card_id"] in known:
            v = np.asarray(known[c["card_id"]], np.float32)
            out[c["card_id"]] = v / max(float(np.linalg.norm(v)), 1e-9)
    return out


# --- step 4: arts -------------------------------------------------------------------

def group(cards: list[dict], vectors: dict, fixes: list[dict], threshold: float = THRESHOLD) -> list[dict]:
    """Group cards into arts. cards: {card_id, lang, illustrator, release_date}.
    Two cards are one art when they have the same illustrator and their
    pictures score at least threshold. Your fixes win: a card you took out
    stays on its own, one you added joins that art. A card with no picture is
    its own art. Returns [{cover, cards: {card_id: score}}], arts with the
    most cards first."""
    import numpy as np
    ids = [c["card_id"] for c in cards]
    by_id = {c["card_id"]: c for c in cards}
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    pinned = {f["card_id"] for f in fixes if f["card_id"] in parent}  # placed by you: no automatic grouping
    by_artist: dict[str, list[str]] = {}
    for c in cards:
        if c["card_id"] in vectors and c["card_id"] not in pinned and _norm(c.get("illustrator")):
            by_artist.setdefault(_norm(c["illustrator"]), []).append(c["card_id"])
    for members in by_artist.values():
        if len(members) < 2:
            continue
        m = np.stack([vectors[x] for x in members])
        scores = m @ m.T
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                if scores[i, j] >= threshold:
                    union(members[i], members[j])
    for f in fixes:
        if f["card_id"] in parent and f.get("join_card_id") in parent:
            union(f["join_card_id"], f["card_id"])

    groups: dict[str, list[str]] = {}
    for i in ids:
        groups.setdefault(find(i), []).append(i)
    arts = []
    for members in groups.values():
        cover = cover_of([by_id[m] for m in members])
        scores = {}
        for m in members:
            if m == cover:
                scores[m] = 1.0
            elif m in vectors and cover in vectors:
                scores[m] = round(float(np.dot(vectors[m], vectors[cover])), 3)
            else:
                scores[m] = None
        arts.append({"cover": cover, "cards": scores})
    arts.sort(key=lambda a: (-len(a["cards"]), by_id[a["cover"]].get("release_date") or "9999", a["cover"]))
    return arts


def cover_of(cards: list[dict]) -> str:
    """The English version, else the earliest one."""
    def key(c):
        return (c["lang"] != "en", c.get("release_date") or "9999", c["card_id"])
    return min(cards, key=key)["card_id"]


def fixes_of(conn: sqlite3.Connection, dex: int) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM art_fixes WHERE dex_id = ?", (dex,))]


def regroup(conn: sqlite3.Connection, dex: int, vectors: dict, threshold: float = THRESHOLD) -> int:
    """Rebuild the Pokémon's arts from its cards, fingerprints and your fixes. Returns the number of arts."""
    cards = [dict(r) for r in conn.execute(
        "SELECT card_id, lang, illustrator, release_date FROM pokemon_cards WHERE dex_id = ? AND how <> 'removed'", (dex,))]
    fixes = fixes_of(conn, dex)
    arts = group(cards, vectors, fixes, threshold)
    manual = {f["card_id"] for f in fixes if f.get("join_card_id")}
    conn.execute("DELETE FROM art_cards WHERE art_id IN (SELECT id FROM arts WHERE dex_id = ?)", (dex,))
    conn.execute("DELETE FROM arts WHERE dex_id = ?", (dex,))
    for art in arts:
        art_id = conn.execute("INSERT INTO arts (dex_id, cover_card_id) VALUES (?, ?)", (dex, art["cover"])).lastrowid
        conn.executemany("INSERT INTO art_cards (art_id, card_id, how, score) VALUES (?, ?, ?, ?)",
                         [(art_id, cid, "cover" if cid == art["cover"] else ("manual" if cid in manual else "auto"), score)
                          for cid, score in art["cards"].items()])
    conn.commit()
    return len(arts)


# --- your fixes --------------------------------------------------------------------

def take_out(conn: sqlite3.Connection, dex: int, card_id: str, by: int | None) -> None:
    """✕: the card becomes its own art. The art it left is remembered for Undo."""
    row = conn.execute("""SELECT a.cover_card_id FROM arts a JOIN art_cards ac ON ac.art_id = a.id
                          WHERE a.dex_id = ? AND ac.card_id = ?""", (dex, card_id)).fetchone()
    left = row[0] if row else None
    if left == card_id:  # the cover itself: remember the art by another of its cards
        other = conn.execute("""SELECT ac.card_id FROM art_cards ac JOIN arts a ON a.id = ac.art_id
                                WHERE a.dex_id = ? AND a.cover_card_id = ? AND ac.card_id <> ?
                                ORDER BY ac.score DESC LIMIT 1""", (dex, card_id, card_id)).fetchone()
        left = other[0] if other else None
    conn.execute("""INSERT INTO art_fixes (dex_id, card_id, join_card_id, left_card_id, by, at) VALUES (?, ?, NULL, ?, ?, ?)
                    ON CONFLICT(dex_id, card_id) DO UPDATE SET join_card_id = NULL, left_card_id = excluded.left_card_id,
                      by = excluded.by, at = excluded.at""", (dex, card_id, left, by, now()))
    conn.commit()


def put_with(conn: sqlite3.Connection, dex: int, card_id: str, art_card_id: str, by: int | None) -> None:
    """＋ Add a version: the card joins the art art_card_id is in."""
    conn.execute("""INSERT INTO art_fixes (dex_id, card_id, join_card_id, left_card_id, by, at) VALUES (?, ?, ?, NULL, ?, ?)
                    ON CONFLICT(dex_id, card_id) DO UPDATE SET join_card_id = excluded.join_card_id, left_card_id = NULL,
                      by = excluded.by, at = excluded.at""", (dex, card_id, art_card_id, by, now()))
    conn.commit()


def undo_fix(conn: sqlite3.Connection, dex: int, card_id: str) -> None:
    conn.execute("DELETE FROM art_fixes WHERE dex_id = ? AND card_id = ?", (dex, card_id))
    conn.commit()


def add_card(conn: sqlite3.Connection, dex: int, row: dict, by: int | None) -> None:
    """Add any card to a Pokémon's set by hand ("Added by you")."""
    save_cards(conn, dex, [row])
    conn.execute("UPDATE pokemon_cards SET how = 'manual', added_by = ? WHERE dex_id = ? AND card_id = ?",
                 (by, dex, row["card_id"]))
    conn.commit()


def remove_card(conn: sqlite3.Connection, dex: int, card_id: str) -> None:
    """Take a card out of a Pokémon's set: one you added goes; an automatic one
    is kept out of every later listing."""
    conn.execute("DELETE FROM pokemon_cards WHERE dex_id = ? AND card_id = ? AND how = 'manual'", (dex, card_id))
    conn.execute("UPDATE pokemon_cards SET how = 'removed' WHERE dex_id = ? AND card_id = ?", (dex, card_id))
    conn.commit()


def row_from_cards(conn: sqlite3.Connection, card_id: str) -> dict | None:
    """A pokemon_cards row for a card stored in cards (for adding it by hand)."""
    c = conn.execute("""SELECT c.*, s.name AS set_name, s.release_date, s.language FROM cards c
                        JOIN sets s ON s.set_id = c.set_id WHERE c.id = ?""", (card_id,)).fetchone()
    if c is None:
        return None
    variants = [k for k, col in (("normal", "has_normal"), ("holo", "has_holo"), ("reverse", "has_reverse"),
                                 ("first_ed", "has_first_ed"), ("star", "has_star"), ("star2", "has_star2")) if c[col]]
    return {"card_id": c["id"], "lang": c["language"], "set_id": c["set_id"], "set_name": c["set_name"],
            "set_code": catalog.tcgdex_set_id(c["set_id"]).upper(), "release_date": c["release_date"],
            "number": c["number"], "name": c["name"], "illustrator": None, "rarity": c["rarity"],
            "image_url": c["image_url"], "variants": json.dumps(variants),
            "prices_json": None}  # stored: its prices are in prices


# --- jobs ------------------------------------------------------------------------------

def run_follow(conn: sqlite3.Connection, dex: int, names, api_for, encoder, job=None, fetch=None,
               only_new: bool = False) -> str:
    """The follow job (and Check for updates, with only_new): list, store, fingerprint, group.
    A negative dex is an artist."""
    if dex < 0:
        name = (artist(conn, dex) or {}).get("name") or "This artist"
        new, unreachable = collect_artist(conn, dex, name, api_for, job)
    else:
        name = names.english_name(dex) or f"#{dex}"
        new, unreachable = collect(conn, dex, names, api_for, job)
    if job is not None:
        job.progress(0, 0, "Comparing pictures")
    try:
        vectors = fingerprints(conn, dex, encoder, job, fetch)
    except Exception as e:  # no picture model yet: every card is its own art until it's there
        log.info("no fingerprints for #%s: %s", dex, e)
        vectors = {}
    arts = regroup(conn, dex, vectors)
    conn.execute("UPDATE followed_pokemon SET checked_at = ? WHERE dex_id = ?", (now(), dex))
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM pokemon_cards WHERE dex_id = ? AND how <> 'removed'", (dex,)).fetchone()[0]
    text = (f"{name}: {new} new card{'' if new == 1 else 's'}." if only_new
            else f"{name}: {total} cards in {arts} art{'' if arts == 1 else 's'}.")
    if not vectors and total:
        text += " Pictures weren't compared: turn on Enhanced Search to group arts."
    if unreachable:
        text += f" Couldn't reach TCGdex for {', '.join(LANG_NAMES.get(l, l) for l in unreachable)}."
    return text


def run_recheck(conn: sqlite3.Connection, dex: int, encoder, job=None, fetch=None) -> str:
    """↻ Re-check: compare the pictures again (only new ones are fetched). Your fixes stay."""
    vectors = fingerprints(conn, dex, encoder, job, fetch)
    arts = regroup(conn, dex, vectors)
    return f"Compared again: {arts} art{'' if arts == 1 else 's'}."


def refresh_prices(conn: sqlite3.Connection, api_for, seen: dict | None = None) -> tuple[int, list[str]]:
    """Update prices for followed Pokémon's cards that aren't stored in cards (part of Update prices). seen: card
    details the same update already fetched, used instead of fetching them again."""
    # Only cards not stored in cards: a stored one is priced by the update itself, into prices.
    rows = conn.execute(f"""SELECT DISTINCT card_id, lang FROM pokemon_cards WHERE card_id IN ({FOLLOWED_CARDS_SQL})
                            AND lang <> 'zh-cn' AND card_id NOT IN (SELECT id FROM cards)""").fetchall()
    seen = seen or {}

    def fetch(r):
        if r["card_id"] in seen:
            return r, seen[r["card_id"]], None
        try:
            return r, api_for(r["lang"]).get_card(catalog.tcgdex_set_id(r["card_id"])), None
        except Exception as e:
            return r, None, e

    with jobs.pool(8) as pool:
        results = list(pool.map(fetch, rows))
    stamp = now()
    for r, detail, _ in results:
        if detail is not None:
            conn.execute("UPDATE pokemon_cards SET prices_json = ?, updated_at = ? WHERE card_id = ?",
                         (json.dumps(prices.by_variant(prices.as_rows(detail))), stamp, r["card_id"]))
    conn.commit()
    errors = [f"{r['card_id']}: {e}" for r, _, e in results if e is not None]
    return len(results) - len(errors), errors


def purge_languages(conn: sqlite3.Connection) -> list[int]:
    """The app keeps English, Japanese and Chinese only: delete the other languages' cards
    (added by hand too) and the art fixes that involve them. Returns the Pokémon to regroup."""
    keep = ",".join(f"'{l}'" for l in sorted(KEPT))
    gone = conn.execute(f"SELECT dex_id, card_id FROM pokemon_cards WHERE lang NOT IN ({keep})").fetchall()
    if not gone:
        return []
    for dex, card_id in gone:
        conn.execute("DELETE FROM art_fixes WHERE dex_id = ? AND (card_id = ? OR join_card_id = ? OR left_card_id = ?)",
                     (dex, card_id, card_id, card_id))
        conn.execute("DELETE FROM art_cards WHERE card_id = ? AND art_id IN (SELECT id FROM arts WHERE dex_id = ?)", (card_id, dex))
    conn.execute(f"DELETE FROM pokemon_cards WHERE lang NOT IN ({keep})")
    conn.commit()
    return sorted({dex for dex, _ in gone})
