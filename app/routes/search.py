"""Search pages."""
import json
from html import escape
import re
import secrets
import threading
import time
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import catalog, chinese, display, english, extras, handmade, inventory, notes, prices, search, spending
from .. import (card_pictures, recent, chase, chase_pages, pokemon_words, saved_searches, smart, spelling, tags,
                tcgdex, themes, tile_prices)
from ..tcgdex import LANGUAGES
from .. import web
from ..web import (_Route, _artist_matches, _build_describe, _card_pokemon, _card_view_extras, _ensure_card,
                   _evolution, _fix_row, _following, _links, _longer_names, _pokemon_name, _price, _prices_for,
                   _read_set_later, _save_found_link, _scan_fix, _set_sort, _set_values, _store_price, _tcgdex_links, background, conn,
                   current_user, log, templates)

router = APIRouter(route_class=_Route)


@router.get("/search")
def search_view(
    request: Request, q: str = "", lang: str = "", set: str = "", rarity: str = "", kind: str = "", owned: str = "",
    sort: str = "", fix: str = "", scan: str = "", tag: str = "", by: str = "", user=Depends(current_user),
):
    # Language sticks between searches, like variant and condition. "Chinese"
    # searches TCGdex's Taiwan and Hong Kong sets and the mainland sets stored here.
    lang = lang or request.session.get("search_lang", "en")
    lang = tcgdex.choice_of(lang) if tcgdex.choice_of(lang) in tcgdex.CHOICES else "en"
    request.session["search_lang"] = lang
    langs = tcgdex.expand(lang)
    many = len(langs) > 1  # set filter values carry their language: 'zh-tw:SV8a'
    set_lang, _, set_code = set.partition(":") if many and ":" in set else (langs[0], "", set)
    if many and ":" in set:
        langs = [set_lang] if set_lang in langs else langs

    translation, translate_error = None, None
    # Search by artist. The Artist chip (by=artist), or a name that is an artist and not a Pokémon.
    by = by if by == "artist" else ""
    picked_by = by  # the Artist chip sticks to the next search; an artist found by name doesn't
    artist_q = " ".join(q.split())
    if not by and len(artist_q) >= 3 and not any(ch.isdigit() for ch in artist_q):
        try:
            is_pokemon = web.names.species_id(artist_q) is not None or web.names.species_id(web.names.to_english(artist_q) or "") is not None
        except Exception:
            is_pokemon = False
        if not is_pokemon and chase.artist_norm(artist_q) in {chase.artist_norm(n) for n in tcgdex.artist_names(web.api)}:
            by = "artist"
    reading = _read_words(q, lang) if not by else smart.Reading(q=q, rest=q)
    if reading.set and not set:  # a set name typed in the search: that set only
        set_lang = reading.set["lang"]
        set_code = reading.set["id"]
        langs = [set_lang]
    query = reading.rest if reading.any else q
    number, total, name = search.parse_query(query)
    tag = tags.clean(tag)
    wanted_tags = list(dict.fromkeys(reading.tags + ([tag] if tag else [])))
    results, error, sets, set_matches = [], None, [], []
    vintage = smart.vintage_years(conn)
    owned_ids = {r[0] for r in conn.execute("SELECT DISTINCT card_id FROM inventory WHERE user_id = ?", (user["collection_id"],))}
    stored: dict = {}  # round 27: every tile's price comes from _price_results (tile_prices), as on the card page
    stored_sets = {r[0] for r in conn.execute("SELECT set_id FROM sets")}
    owned_sets = {r[0] for r in conn.execute("SELECT DISTINCT c.set_id FROM inventory i JOIN cards c ON c.id = i.card_id "
                                             "WHERE i.user_id = ?", (user["collection_id"],))}
    kind = kind if kind in search.KINDS else ""
    sort = sort if sort in search.SORTS else ""
    set_rank: dict = {}  # (language, TCGdex set id) -> 0 (oldest) .. 1 (newest)
    hand = []
    for one in tcgdex.expand(lang):
        # An English Pokémon name searches for that Pokémon's name in the chosen language.
        one_query, one_translation = query, None
        if one != "en" and name and not by:
            try:
                one_translation = web.names.translate(name, one)
            except Exception as e:  # name list couldn't be downloaded
                translate_error = f"Couldn't load the Pokémon name list, so names are searched as typed ({e})."
            if one_translation:
                one_query = one_translation.name + (f" {number}" if number else "") + (f"/{total}" if total else "")
                translation = translation or one_translation
        one_set = set_code if one == set_lang else ""

        if one == chinese.LANG:
            # Mainland sets: TCGdex lists them without cards, so they're searched here.
            one_sets = [{"id": catalog.tcgdex_set_id(r["set_id"]), "name": f"{r['name']} ({catalog.tcgdex_set_id(r['set_id'])})",
                         "plain_name": r["name"], "logo": r["logo_url"]}
                        for r in conn.execute("SELECT set_id, name, logo_url FROM sets WHERE language = ? "
                                              "ORDER BY release_date DESC, set_id DESC", (one,))]
            set_rank.update({(one, x["id"]): 1 - i / max(len(one_sets), 1) for i, x in enumerate(one_sets)})
            sets += [dict(x, id=f"{one}:{x['id']}") for x in one_sets] if many else one_sets
            codes = {x["id"].upper() for x in one_sets}
            if one_set and one_set.upper() not in codes:
                one_set = ""
            code, card_name, card_number = one_set or None, name, number
            if name and name.split()[0].upper() in codes:  # 'CSV7C 141'
                code, card_name = name.split()[0].upper(), " ".join(name.split()[1:]) or None
            elif number and not name and number.upper() in codes:  # 'CSV7C' alone reads like a number
                code, card_number = number.upper(), None
            if one in langs and (query.strip() or one_set) and not wanted_tags and not by:  # (no artists in these)
                found = chinese.search_local(conn, card_number, total, card_name,
                                             chinese_name=one_translation.name if one_translation and card_name else None,
                                             code=code, limit=search.MAX_SET_RESULTS if code and not (card_number or card_name)
                                             else search.MAX_RESULTS)
                for r in found:
                    if rarity and (r["rarity"] or "").casefold() != rarity.casefold():
                        continue
                    if reading.any and not (smart.name_ok(reading, r["name"]) and smart.rarity_ok(reading, r["rarity"])
                                            and ("vintage" not in reading.flags)):
                        continue
                    results.append({
                        "id": r["id"], "local": True, "lang": one, "number": r["number"], "name": r["name"],
                        "image_url": r["image_url"], "set_id": r["set_id"], "set_name": r["set_name"],
                        "printed_total": r["printed_total"], "rarity": r["rarity"], "owned": r["id"] in owned_ids,
                        "price": stored.get(r["id"]), "english": r.get("twin_name"),
                    })
            one_sets_for_match = [dict(x, lang=one, set_id=f"{one}:{x['id']}") for x in one_sets]
        else:
            client = web.api_for(one)
            one_sets_for_match = []
            try:
                tcg_sets = client.list_sets()
                set_rank.update({(one, x["id"]): i / max(len(tcg_sets), 1) for i, x in enumerate(tcg_sets)})
                one_sets = search.set_choices(client)
                sets += [dict(x, id=f"{one}:{x['id']}") for x in one_sets] if many else one_sets
                one_sets_for_match = [{"id": s["id"], "plain_name": s["name"], "lang": one,
                                       "logo": f"{s['logo']}.webp" if s.get("logo") else None,
                                       "set_id": catalog.local_id(one, s["id"])} for s in reversed(tcg_sets)]
                if one_set and one_set not in {x["id"] for x in one_sets}:
                    one_set = ""  # a set from another language; drop it rather than show nothing
                if one in langs and (query.strip() or one_set) and not wanted_tags:
                    found, seen_ids = [], {}  # (the set filter is called set here)
                    # "pokemon" also asks TCGdex for "pokémon", which it matches only as written.
                    for form in _accent_queries(one_query, name if one == "en" else None) if not by else [""]:
                        for r in search.find_cards(client, form, set_id=one_set or None, rarity=rarity or None,
                                                   kind=kind or None, smart=reading if reading.any else None,
                                                   release=getattr(client, "release_date", None), vintage_years=vintage,
                                                   stored=_stored_detail(one), artist=artist_q if by else None,
                                                   longer=_longer_names):
                            if r["id"] not in seen_ids:
                                seen_ids[r["id"]] = True
                                found.append(r)
                    for r in found:
                        r["lang"] = one
                        r["owned"] = catalog.local_id(one, r["id"]) in owned_ids
                        # Prices: stored ones for cards you have; the rest are looked up as they come on screen.
                        r["price"] = stored.get(catalog.local_id(one, r["id"]))
                    results += found
            except Exception as e:  # network error from TCGdex
                error = f"Couldn't search TCGdex: {e}"

            # Cards added by hand never come back from TCGdex, so they're looked up here.
            if query.strip() and not set and not reading.any and one in langs and not by:
                hand_name = (one_translation.name if one_translation else name) or None
                found_hand = handmade.for_language(conn, one, number, hand_name)
                if not found_hand and one_translation:
                    found_hand = handmade.for_language(conn, one, number, name)
                for h in found_hand:
                    h["owned"] = h["id"] in owned_ids
                    h["price"], h["local"] = None, True  # priced below with the rest
                hand += found_hand

        # Sets whose name or code matches what was typed show at the top.
        wanted = display.fold(q.strip())
        if one in langs and len(wanted) >= 3 and not set and not reading.any and not by:
            english_names = english.labels(conn)["names"]  # round 27: a Japanese or Chinese set by its English name too
            for x in one_sets_for_match:
                if wanted in display.fold(x["plain_name"]) or wanted == display.fold(x["id"]) or \
                        wanted in display.fold(english_names.get(x["set_id"]) or ""):
                    set_matches.append({**x, "stored": x["set_id"] in stored_sets, "owned": x["set_id"] in owned_sets})

    if set and set not in {x["id"] for x in sets}:
        set = ""
    if wanted_tags:  # tags live on stored cards, so a tag search lists those
        results = _tagged_results(wanted_tags, langs, number, name, translation, set_code if set else "",
                                  owned_ids, stored)
    if owned == "yes":
        results = [r for r in results if r["owned"]]
    elif owned == "no":
        results = [r for r in results if not r["owned"]]
    if sort in ("price_desc", "price_asc"):
        _price_results(results, user)  # sorting by price needs them all first
    results = search.sort_results(results, sort, lambda r: set_rank.get((r.get("lang"), catalog.tcgdex_set_id(r.get("set_id") or "")), 0))
    # Close matches: nothing for the name and number together, so the number alone and the name alone.
    # Also for a number on its own, and whenever a filter left nothing (worked out without the filter).
    filtered = bool(set or rarity or kind or owned or wanted_tags) and not by
    close = None
    if not results and not hand and not by and (number or (name and filtered)) and not reading.any:
        newest = lambda r: -set_rank.get((r.get("lang"), catalog.tcgdex_set_id(r.get("set_id") or "")), 0)
        close = _close_matches(langs, name, number, owned_ids, stored, newest, user)
    if q.strip() or set:
        request.session["find_url"] = str(request.url.path) + ("?" + request.url.query if request.url.query else "")

    # Spelling help: "Did you mean …?" when the name isn't a Pokémon and little was found.
    did_you_mean = None
    if name and not number and len(results) + len(hand) < 3 and web.names.available() and not by:
        try:
            guess = spelling.best(name, web.names.english_names())
        except Exception:
            guess = None
        if guess and web.names.species_id(name) is None:
            did_you_mean = {"name": guess, "href": "/search?" + urlencode(
                {"q": " ".join([guess] + [w for w in q.split() if w not in name.split()]), "lang": lang})}
    # One Pokémon searched: its evolution line above the results, tapping one searches it, and its tags.
    evo, poke = None, None
    if name and web.names.available() and not by:
        try:
            dex = web.names.species_id(name)
            if dex and _plain_name(web.names.english_name(dex)) in (_plain_name(name), _plain_name(translation.english if translation else "")):
                evo = _evolution(user["collection_id"], dex, start=False)
                # The box always shows the Pokémon (sprite and name, even with no evolution line),
                # its ★ (followed or not) and a link to its Pokémon set; tags only once the describer is ready.
                poke = {"dex": dex, "name": _pokemon_name(dex), "following": _following(user["collection_id"], dex),
                        "sprite": f"/sprites/{dex}.png" if themes.sprite_path(dex, False).exists() else None,
                        "tags": pokemon_words.for_pokemon(conn, web.describer, dex) if web.describer.ready() else None}
        except Exception as e:
            log.info("no evolution strip for %r: %s", name, e)
    known_rarities = [r[0] for r in conn.execute(
        "SELECT DISTINCT c.rarity FROM cards c JOIN sets s ON s.set_id = c.set_id "
        f"WHERE s.language IN ({','.join('?' * len(tcgdex.expand(lang)))}) AND c.rarity IS NOT NULL",
        tcgdex.expand(lang))]
    carry = {k: v for k, v in (("fix", fix if _fix_row(user, fix) else ""), ("scan", scan if _scan_fix(user, scan) else "")) if v}
    keep = {k: v for k, v in (("by", picked_by), ("set", set), ("rarity", rarity), ("kind", kind), ("owned", owned), ("sort", sort), ("tag", tag)) if v}
    # Artists whose name has what was typed, offered above the results.
    artist_hits = [] if by or evo or number or not name else [
        n for n in _artist_matches(q, 3) if chase.artist_norm(n) != chase.artist_norm(q)]
    artist_here = chase.artist(conn, artist_q) if by else None
    set_names = {x["id"]: x["name"] for x in sets}
    active = ([(tcgdex.CHOICES[lang], "lang")] if lang not in BUTTON_LANGS else []) + [
        (label, key) for key, label in (("by", "Artist" if by else None), ("set", set_names.get(set)), ("rarity", rarity), ("kind", search.KINDS.get(kind)),
                                        ("owned", {"yes": "Owned", "no": "Not owned"}.get(owned)),
                                        ("sort", search.SORTS.get(sort)), ("tag", f"#{tag}")) if key in keep and label]
    every = {"q": q, "lang": lang, **keep, **carry}
    clear = {key: "/search?" + urlencode({k: v for k, v in every.items() if v and k != key and not (key == "lang" and k == "set")}
                                         | ({"lang": "en"} if key == "lang" else {})) for _, key in active}
    # Load as you scroll: 24 now, priced; the rest 24 at a time as they come on screen.
    _price_results(results[:search.PAGE] + hand, user)
    token = _find_pages.keep(user, results, ("?" + urlencode(carry)) if carry else "", lang) \
        if len(results) > search.PAGE else None
    return templates.TemplateResponse(request, "search.html", {
        "user": user, "q": q, "lang": lang, "set": set, "results": results, "error": error, "hand": hand,
        "number": number, "total": total, "name": name, "set_matches": set_matches[:8],
        "rarity": rarity, "kind": kind, "owned": owned, "kinds": search.KINDS, "sort": sort, "sorts": search.SORTS,
        "rarities": search.rarity_choices(known_rarities, tcgdex.expand(lang)[0]),
        "translation": translation, "translate_error": translate_error,
        "languages": tcgdex.CHOICES, "sets": sets,
        "filter_count": sum(1 for v in (lang not in BUTTON_LANGS, by, set, rarity, kind, owned, sort, tag) if v),
        "by": by, "by_chip": picked_by, "artist_hits": artist_hits,
        "artist_link": "/artists/" + quote(chase.artist_norm(artist_q)) if by and artist_q and (
            artist_here or chase.artist_norm(artist_q) in {chase.artist_norm(n) for n in tcgdex.artist_names(web.api)}) else None,
        "artist_name": (artist_here or {}).get("name") or next(
            (n for n in tcgdex.artist_names(web.api) if chase.artist_norm(n) == chase.artist_norm(artist_q)), artist_q) if by else "",
        "tag": tag, "my_tags": tags.mine(conn),
        # English · Japanese · Chinese buttons: same search and filters in another language (a set belongs to one language).
        "lang_url": "/search?" + urlencode({k: v for k, v in every.items() if v and k not in ("lang", "set")}) + "&lang=__L__",
        "variants": inventory.VARIANTS, "conditions": inventory.CONDITIONS,
        "variant": request.session.get("entry_variant", "normal"),
        "condition": request.session.get("entry_condition", "NM"),
        "origins": inventory.ORIGINS, "origin": request.session.get("entry_origin", ""),
        "max_results": search.MAX_SET_RESULTS if set else search.MAX_RESULTS,
        "token": token, "page": search.PAGE, "close": close, "filtered": filtered,
        "unfiltered": "/search?" + urlencode({k: v for k, v in {"q": q, "lang": lang, **carry}.items() if v}),
        "filter_names": [label for label, key in active if key not in ("sort", "lang")],
        "fix": carry.get("fix", ""), "fix_row": _fix_row(user, fix),
        "scan": carry.get("scan", ""), "scan_fix": _scan_fix(user, scan),
        "carry": ("?" + urlencode(carry)) if carry else "", "keep": {**keep, **carry}, "active": active, "clear": clear,
        "from_find": True, "reading": reading, "did_you_mean": did_you_mean, "evo": evo, "poke": poke,
        "describe_word": request.query_params.get("describe", ""), "here_url": str(request.url.path) + "?" + str(request.url.query),
        "evo_href": "/search?" + urlencode({"lang": lang}) + "&q=",
        "saved": saved_searches.all_for(conn, user["id"]), "this_query": saved_searches.clean_query(request.url.query),
        "recent_views": recent.latest(conn) if not q.strip() and not set and not wanted_tags and not by
        and not request.query_params.get("describe") else [],  # round 27: a tag search shows only its cards
        "words_only": reading.any and not (query.strip() or set or reading.set or wanted_tags),
    })


def _stored_detail(lang: str):
    """For Find's rarity and type filters: a stored card's rarity and types, so TCGdex is asked
    only about cards that aren't stored here."""
    def lookup(tcgdex_id: str) -> dict | None:
        row = conn.execute("SELECT rarity, types FROM cards WHERE id = ?", (catalog.local_id(lang, tcgdex_id),)).fetchone()
        if row is None or not row["types"]:  # no types stored: TCGdex says what kind of card it is
            return None
        return {"rarity": row["rarity"], "types": [t for t in row["types"].split(",") if t]}
    return lookup


_accent_words: dict = {}


def _accent_queries(query: str, name: str | None) -> list[str]:
    """The search as typed, plus the same with accented spellings of its words ("pokemon" -> "pokémon",
    "flabebe" -> "flabébé"), from stored English card names and Pokémon names."""
    if not name:
        return [query]
    if "words" not in _accent_words or _accent_words["at"] < time.monotonic() - 600:
        words: dict[str, str] = {}
        texts = [r[0] for r in conn.execute("SELECT DISTINCT c.name FROM cards c JOIN sets s ON s.set_id = c.set_id "
                                            "WHERE s.language = 'en'")]
        try:
            texts += web.names.english_names() if web.names.available() else []
        except Exception:
            pass
        for text in texts:
            for word in re.findall(r"[^\W\d_][\w'’.-]*", text or ""):
                plain = display.fold(word)
                if not word.isascii() and plain.isascii():
                    words.setdefault(plain, word)
        _accent_words.update(at=time.monotonic(), words=words)
    swapped = " ".join(_accent_words["words"].get(display.fold(w), w) if w.isascii() else w for w in name.split())
    return [query] + ([query.replace(name, swapped, 1)] if swapped != name else [])


def _our_id(r: dict) -> str:
    return r["id"] if r.get("local") else catalog.local_id(r.get("lang") or "en", r["id"])


def _price_results(results: list[dict], user=None) -> None:
    """Find's tile prices (round 27): the same main price the card page shows, from what's stored (tile_prices).
    A result whose set has no TCGCSV prices yet gets price_pending, and that set is read in the background
    (one read per set); /search/prices fills those tiles in when it's done."""
    rows = [r for r in results if not r.get("_priced")]
    if not rows:
        return
    cards = [{"key": i, "id": _our_id(r), "lang": r.get("lang") or "en", "set_id": None if r.get("local") else r.get("set_id"),
              "set_name": r.get("set_name"), "number": r.get("number"),
              "english": r.get("english") or (r.get("name") if (r.get("lang") or "en") == "en" else None)}
             for i, r in enumerate(rows)]
    try:
        found, waiting = tile_prices.for_cards(conn, cards, user["collection_id"] if user else None)
    except Exception as e:  # no price is better than no results
        log.info("no Find prices: %s", e)
        found, waiting = {}, set()
    waiting_sets = {(lang, set_id) for lang, set_id, _ in waiting}
    chased = {x[0] for x in conn.execute("SELECT DISTINCT card_id FROM wants WHERE user_id = ?",
                                         (user["collection_id"],))} if user else set()
    for i, r in enumerate(rows):
        r["chased"] = cards[i]["id"] in chased
        r["price"] = found.get(i)
        r["price_pending"] = r["price"] is None and ((r.get("lang") or "en"), r.get("set_id")) in waiting_sets and not r.get("local")
        r["_priced"] = True
    for lang, set_id, set_name in waiting:
        _read_set_later(lang, set_id, set_name)
    _waiting_tiles.keep(user, {f"{r.get('lang') or 'en'}|{r['id']}": cards[i] for i, r in enumerate(rows) if r["price_pending"]})


class WaitingTiles:
    """Find's tiles whose set is being read (key 'lang|TCGdex id' -> what tile_prices needs), so /search/prices
    can price them when it's done. A few hundred per collection, for half an hour."""

    KEEP, MOST = 1800, 600

    def __init__(self):
        self.tiles: dict[int, dict[str, tuple[float, dict]]] = {}
        self._lock = threading.Lock()

    def keep(self, user, tiles: dict[str, dict]) -> None:
        if not user or not tiles:
            return
        now = time.monotonic()
        with self._lock:
            mine = self.tiles.setdefault(user["collection_id"], {})
            for key in [k for k, (at, _) in mine.items() if at < now - self.KEEP]:
                del mine[key]
            mine.update({k: (now, v) for k, v in tiles.items()})
            for key in list(mine)[:-self.MOST]:
                del mine[key]

    def get(self, user, keys: list[str]) -> dict[str, dict]:
        with self._lock:
            mine = self.tiles.get(user["collection_id"], {})
            return {k: mine[k][1] for k in keys if k in mine}


_waiting_tiles = WaitingTiles()


@router.get("/search/prices")
def search_prices(k: list[str] = Query([]), user=Depends(current_user)):
    """Prices for Find's "Checking price…" tiles once their sets are read: key -> price as shown, '' for no price
    (the set was read and has none), and keys still waiting are left out."""
    wanted = _waiting_tiles.get(user, k[:200])
    found, waiting = tile_prices.for_cards(conn, [dict(c, key=key) for key, c in wanted.items()], user["collection_id"])
    still = {(lang, set_id) for lang, set_id, _ in waiting}
    out = {}
    for key, c in wanted.items():
        if key in found:
            out[key] = display.money(found[key])
        elif (c["lang"], c["set_id"]) not in still:
            out[key] = ""
    return JSONResponse(out)


def _close_matches(langs: list[str], name: str | None, number: str | None, owned_ids: set, stored: dict,
                   newest=None, user=None) -> dict | None:
    """Nothing was found: cards with the number in other sets whose names are close (any name when only a number
    was typed, newest sets first), and cards with the name in other sets (12 of each). No filters apply."""
    by_number, by_name = [], []
    for one in langs:
        if one == chinese.LANG:
            continue
        client = web.api_for(one)
        try:
            if number:
                briefs = client.search_cards(local_id=search._number_forms(number))
                close = [c for c in briefs if search._same_number(c.get("localId", ""), number)
                         and (not name or display.fold(name) in display.fold(c.get("name"))
                              or spelling._score(name, c.get("name") or "") >= 0.6)]
                by_number += [dict(r, lang=one) for r in search.find_cards(client, number) if r["id"] in {c["id"] for c in close}]
            if name:
                by_name += [dict(r, lang=one) for r in search.find_cards(client, name)]
        except Exception as e:
            log.info("no close matches in %s: %s", one, e)
    if newest and not name:
        by_number.sort(key=newest)
    rows = (by_number[:12], by_name[:12])
    for r in rows[0] + rows[1]:
        r["owned"] = catalog.local_id(r["lang"], r["id"]) in owned_ids
        r["price"] = stored.get(catalog.local_id(r["lang"], r["id"]))
    _price_results(rows[0] + rows[1], user)
    return {"number": rows[0], "name": rows[1]} if rows[0] or rows[1] else None


class FindPages:
    """Find's results kept for a while, so the next 24 can be sent as you scroll."""

    KEEP = 1800

    def __init__(self):
        self.pages: dict[str, dict] = {}
        self._lock = threading.Lock()  # Searches run in several threads at once

    def keep(self, user, results: list[dict], carry: str, lang: str) -> str:
        now = time.monotonic()
        token = secrets.token_urlsafe(12)
        with self._lock:
            for old in [t for t, p in self.pages.items() if p["at"] < now - self.KEEP] + list(self.pages)[:-20]:
                self.pages.pop(old, None)
            self.pages[token] = {"at": now, "collection": user["collection_id"], "results": results, "carry": carry, "lang": lang}
        return token

    def get(self, user, token: str) -> dict | None:
        with self._lock:
            page = self.pages.get(token)
        return page if page and page["collection"] == user["collection_id"] else None


_find_pages = FindPages()


@router.get("/search/more")
def search_more(request: Request, t: str = "", offset: int = 0, user=Depends(current_user)):
    """The next 24 of Find's results, priced as they come on screen."""
    page = _find_pages.get(user, t)
    if page is None:
        return HTMLResponse("")  # gone (the app restarted): searching again shows them
    offset = max(0, offset)
    rows = page["results"][offset:offset + search.PAGE]
    _price_results(rows, user)
    return templates.TemplateResponse(request, "_find_page.html", {
        "rows": rows, "carry": page["carry"], "lang": page["lang"], "token": t, "next": offset + search.PAGE,
        "total": len(page["results"]), "more": True})


def _tagged_results(wanted: list[str], langs: list[str], number, name, translation, set_code: str,
                    owned_ids: set, stored: dict) -> list[dict]:
    """Find with a tag: the stored cards with every tag, narrowed by the name, number and set typed."""
    ids = tags.cards_with(conn, wanted)
    if not ids:
        return []
    rows = conn.execute(
        f"SELECT c.*, s.name AS set_name, s.language, s.printed_total FROM cards c JOIN sets s ON s.set_id = c.set_id "
        f"WHERE c.id IN ({','.join('?' * len(ids))}) AND s.language IN ({','.join('?' * len(langs))}) "
        "ORDER BY s.release_date DESC, c.sort_number", [*ids, *langs]).fetchall()
    names_wanted = [search._fold(n) for n in (name, translation.name if translation else None) if n]
    out = []
    for r in rows:
        if names_wanted and not any(n in search._fold(r["name"]) or n in search._fold(r["twin_name"] or "") for n in names_wanted):
            continue
        if number and not search._same_number(r["number"], number):
            continue
        if set_code and catalog.tcgdex_set_id(r["set_id"]).casefold() != set_code.casefold():
            continue
        out.append({"id": r["id"], "local": True, "lang": r["language"], "number": r["number"], "name": r["name"],
                    "image_url": r["image_url"], "set_id": r["set_id"], "set_name": r["set_name"],
                    "printed_total": r["printed_total"], "rarity": r["rarity"], "owned": r["id"] in owned_ids,
                    "price": stored.get(r["id"]), "english": r["twin_name"]})
    return out[:search.MAX_SET_RESULTS]


def _plain_name(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _read_words(q: str, lang: str) -> "smart.Reading":
    """Find's smart words: set names and codes for this language, printings, shiny, full art,
    vintage, ex/V/…, and your tags."""
    if not q.strip():
        return smart.Reading(q=q, rest=q)
    candidates = []
    for one in tcgdex.expand(lang):
        try:
            if one == chinese.LANG:
                listed = [{"id": catalog.tcgdex_set_id(r[0]), "plain_name": r[1]} for r in
                          conn.execute("SELECT set_id, name FROM sets WHERE language = ?", (one,))]
            else:
                listed = [{"id": x["id"], "plain_name": x["name"]} for x in web.api_for(one).list_sets()]
        except Exception:
            continue
        for x in listed:
            # Names only in English (what you type); codes in every language.
            candidates.append({"id": x["id"], "plain_name": x["plain_name"] if one == "en" else "", "lang": one,
                               "label": x["plain_name"]})
    english = set()
    if web.names.available():
        try:
            english = {n.lower() for n in web.names.english_names()}
        except Exception:
            pass
    reading = smart.read(q, candidates, tags.mine(conn), is_pokemon=lambda text: text.lower() in english)
    if reading.set and not reading.rest and not (reading.flags or reading.mechanics or reading.tags):
        return smart.Reading(q=q, rest=q)  # just a set's name: Find lists matching sets at the top, as before
    if reading.set and not reading.set.get("plain_name"):  # matched by code: show the set's name on its chip
        reading.chips = [(reading.set["label"] if label == reading.set["id"] else label, rq) for label, rq in reading.chips]
    return reading


@router.get("/search/suggest", response_class=HTMLResponse)
def search_suggest(q: str = "", lang: str = "en", user=Depends(current_user)):
    """Close Pokémon names under the Find box as you type (never changes the search by itself)."""
    reading = _read_words(q, lang) if q.strip() else None
    _, _, name = search.parse_query(reading.rest if reading and reading.any else q)
    if not name or not web.names.available():
        return HTMLResponse("")
    try:
        found = spelling.suggest(name, web.names.english_names())
    except Exception:
        return HTMLResponse("")
    if not found:
        return HTMLResponse("")
    rest = [w for w in q.split() if w not in name.split()]
    items = "".join(f'<a class="chip" href="/search?{urlencode({"q": " ".join([n] + rest), "lang": lang})}">{escape(n)}</a>'
                    for n in found)
    return HTMLResponse(f'<div class="suggest" role="list" aria-label="Did you mean">{items}</div>')


@router.post("/search/saved")
def search_save(name: str = Form(""), query: str = Form(""), user=Depends(current_user)):
    try:
        saved_searches.save(conn, user["id"], name, query)
    except ValueError as e:
        return RedirectResponse("/search?" + saved_searches.clean_query(query) + "&saved_error=" + quote(str(e)), status_code=303)
    return RedirectResponse("/search?" + saved_searches.clean_query(query), status_code=303)


@router.post("/search/saved/rename")
def search_saved_rename(name: str = Form(...), new: str = Form(""), user=Depends(current_user)):
    saved_searches.rename(conn, user["id"], name, new)
    return RedirectResponse("/search", status_code=303)


@router.post("/search/saved/delete")
def search_saved_delete(name: str = Form(...), user=Depends(current_user)):
    saved_searches.delete(conn, user["id"], name)
    return RedirectResponse("/search", status_code=303)


BUTTON_LANGS = ("en", "ja", "zh")  # Find's language buttons; Korean and the rest are under Filter


@router.get("/describe", response_class=HTMLResponse)
def describe_pokemon(request: Request, words: str = "", colour: str = "", kind: list[str] = Query([]), lang: str = "en",
                     user=Depends(current_user)):
    """Find's "Don't know the name?": sprites of the Pokémon that fit what you typed and picked."""
    ctx = {"user": user, "lang": lang if lang in tcgdex.CHOICES else "en", "results": [], "state": "ready",
           "asked": bool(words.strip() or colour or any(kind))}
    kind = [k for k in kind if k][:2]  # up to two types; a Pokémon must have both
    if not web.describer.ready():
        job = background.last("describe")
        if job is not None and job.error and not job.running:
            ctx["state"] = "failed"
        else:
            background.start("describe", "Getting the Pokédex for Describe a Pokémon", _build_describe)
            ctx["state"] = "loading"
    elif ctx["asked"]:
        ctx["results"] = [dict(r, sprite=f"/sprites/{r['dex']}.png" if themes.sprite_path(r["dex"], False).exists() else None)
                          for r in web.describer.search(words, colour, kind)]
        ctx["used"] = web.describer.read(words, colour, kind)["chips"]  # typed colour/type/legendary words: chips
    return templates.TemplateResponse(request, "_describe_results.html", ctx)


@router.get("/set-preview/{lang}/{set_id}")
def set_preview(request: Request, lang: str, set_id: str, user=Depends(current_user)):
    """A set from Find that isn't stored here yet, every card in color."""
    if lang not in LANGUAGES:
        raise HTTPException(404)
    ours = catalog.local_id(lang, set_id)
    if conn.execute("SELECT 1 FROM sets WHERE set_id = ?", (ours,)).fetchone():
        return RedirectResponse(f"/sets/{ours}?from=find", status_code=303)
    try:
        data = tcgdex.cached_set(web.api_for(lang), set_id)
    except Exception as e:
        raise HTTPException(502, f"Couldn't load set {set_id} from TCGdex: {e}")
    cards = [{"id": c["id"], "number": c["localId"], "name": c["name"], "image_url": tcgdex.image_url(c.get("image"))}
             for c in data.get("cards", [])]
    cards.sort(key=lambda c: (search.parse_number(c["number"], 0)[0], c["number"]))
    return templates.TemplateResponse(request, "set_preview.html", {
        "user": user, "set": data, "lang": lang, "cards": cards, "from_find": True,
        "logo": f"{data['logo']}.webp" if data.get("logo") else None,
        "official": (data.get("cardCount") or {}).get("official"),
        "tcgdex_set_url": f"{tcgdex.BASE_URL}/{lang}/sets/{quote(set_id)}",
        "set_sort": _set_sort(request, user), "set_values": _set_values(lang, set_id, data.get("name")),
    })


@router.post("/preview/{lang}/{card_id}/modify")
def preview_modify(lang: str, card_id: str, set_id: str = Form(...), open: str = Form("mod-price"),
                   user=Depends(current_user)):
    """The ⋯ menu on a card that isn't stored yet: load its set, then open the pop-up on its own page."""
    our_card_id = _ensure_card(lang, set_id, card_id)
    pick = open if open in ("mod-price", "mod-links", "mod-picture", "mod-pokemon") else "mod-price"
    return RedirectResponse(f"/cards/{our_card_id}?from=find&open={pick}", status_code=303)


@router.post("/preview/{lang}/{card_id}/link/{site}")
def preview_found_link(lang: str, card_id: str, site: str, set_id: str = Form(...), url: str = Form(...),
                       pc_price: str = Form(""), user=Depends(current_user)):
    """The link pop-up on a Find preview: the card is kept in the catalogue with its link, not added to your cards."""
    if site not in web.LINK_LABELS:
        raise HTTPException(400)
    return _save_found_link(_ensure_card(lang, set_id, card_id), site, url, user, pc_price)


@router.get("/preview/{lang}/{card_id}")
def preview(request: Request, lang: str, card_id: str, user=Depends(current_user)):
    """A card from search, before it's added. Cards from sets you've loaded
    open their own page instead."""
    our_card_id = catalog.local_id(lang, card_id)
    fix, scan = request.query_params.get("fix"), request.query_params.get("scan")
    if conn.execute("SELECT 1 FROM cards WHERE id = ?", (our_card_id,)).fetchone():
        carry = f"?fix={fix}" if fix else (f"?scan={scan}" if scan else "?from=find")
        return RedirectResponse(f"/cards/{our_card_id}{carry}", status_code=303)
    try:
        card = search.preview(web.api_for(lang), card_id)
    except Exception as e:
        raise HTTPException(502, f"Couldn't load card {card_id} from TCGdex: {e}")
    card_prices, pricing = _prices_for(our_card_id, card, card["detail"])
    recent.record(conn, our_card_id, card.get("name"), card.get("number"), card.get("image_url"), f"/preview/{lang}/{card_id}")
    links = _links(card, lang, pricing, None, our_card_id)
    flags = {"has_normal": "normal", "has_reverse": "reverse", "has_holo": "holo", "has_first_ed": "firstEdition"}
    card_flags = {flag: bool(card["variants"].get(key)) for flag, key in flags.items()}
    offered = inventory.offered_variants(card_flags, prices.real_variants(card_prices))
    pics = card_pictures._unique(extras.pictures(dict(card, pricing_json=json.dumps(card["detail"].get("pricing"))), None)
                                 + card_pictures.more(conn, our_card_id))
    pics = card_pictures.large(pics)
    picture_waiting = not card_pictures.known(pics) and bool(card_pictures.ask(conn, [our_card_id]) or card_pictures.waiting(conn, our_card_id))
    return templates.TemplateResponse(request, "card.html", {
        **_card_view_extras(request, card, lang, card_prices, offered, [], links),
        "user": user, "card": card, "preview": True, "holdings": [], "chase": None, "notes": [], "history": [],
        "fix_row": _fix_row(user, fix), "scan_fix": _scan_fix(user, scan),
        "prices": card_prices,
        "lang": lang, "tcgdex_card_id": card_id, "tcgdex_set": card["set_id"],
        "tcgdex_links": _tcgdex_links(card, lang, card_id, card["set_id"]),
        "pics": pics, "picture_waiting": picture_waiting, "picture_id": our_card_id,
        "from_find": True, "offered": offered, "extra": None, "extra_price": None, "extra_source": None,
        "site_labels": web.LINK_LABELS,
        "pokemon": _card_pokemon(user, card["name"], lang), "pokemon_sets": chase.followed(conn, user["collection_id"]),
        "art_languages": chase_pages.art_languages(conn, user["collection_id"], our_card_id),
        "variants": inventory.VARIANTS, "conditions": inventory.CONDITIONS,
        "condition": request.session.get("entry_condition", "NM"),
        "origins": inventory.ORIGINS, "origin": request.session.get("entry_origin", ""),
    })


@router.post("/search/add")
def search_add(
    request: Request, card_id: str = Form(...), set_id: str = Form(...), lang: str = Form("en"),
    variant: str = Form("normal"), condition: str = Form("NM"), note: str = Form(""), origin: str = Form(""),
    quantity: int = Form(1), paid: str = Form(""), user=Depends(current_user),
):
    """Add a card picked from search, loading its set first if it's new.
    card_id and set_id are TCGdex ids in the given language."""
    if variant not in inventory.VARIANTS or condition not in inventory.CONDITIONS:
        raise HTTPException(400, "Unknown variant or condition.")
    request.session["entry_variant"] = variant
    request.session["entry_condition"] = condition
    our_card_id = _ensure_card(lang, set_id, card_id)
    inventory.adjust(conn, user["collection_id"], our_card_id, variant, condition, max(1, min(quantity, 99)),
                     by=user["id"], origin=origin)
    if paid.strip():
        spending.set_paid(conn, user["collection_id"], our_card_id, variant, condition, _price(paid))
    _store_price(our_card_id)
    request.session["entry_origin"] = origin if origin in inventory.ORIGINS else ""
    if note.strip():
        notes.add(conn, user["collection_id"], our_card_id, user["id"], note)
    return RedirectResponse(f"/cards/{our_card_id}?added=1", status_code=303)
