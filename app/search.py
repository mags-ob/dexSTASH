"""Finding a card across every set by its number, e.g. '125', '125/197',
'charizard 125' or just 'charizard'. Can be narrowed to one set, and runs in
whichever language the TCGdex client is for."""

import re

from . import jobs, tcgdex
from .display import fold
from .numbers import parse_number
from .tcgdex import TCGdex, image_url

MAX_RESULTS = 400     # Shown 24 at a time (PAGE), so more can come back
PAGE = 24
MAX_SET_RESULTS = 400  # a whole set, when browsing one
MAX_DETAILS = 400      # cards looked up one by one for the rarity and type filters


KINDS = {"pokemon": "Pokémon", "trainer": "Trainer", "energy": "Energy"}
_TRAINER_WORDS = ("trainer", "dresseur", "entrenador", "allenatore", "treinador", "トレーナー", "트레이너", "訓練家")

# optional name, then an optional number with an optional printed total
_QUERY = re.compile(r"^\s*(?P<name>.*?)\s*(?:(?P<number>[A-Za-z]*\d+[A-Za-z]?)\s*(?:/\s*(?P<total>\d+))?)?\s*$")


def parse_query(q: str) -> tuple[str | None, int | None, str | None]:
    """Return (number, printed_total, name). Any of them may be None."""
    m = _QUERY.match(q)
    if not m:
        return None, None, q.strip() or None
    number, total, name = m.group("number"), m.group("total"), m.group("name").strip()
    return number, int(total) if total else None, name or None


def _number_forms(number: str) -> list[str]:
    """TCGdex pads some sets' numbers ('001') and not others ('1')."""
    if not number.isdigit():
        return [number.upper()]
    n = str(int(number))
    return list(dict.fromkeys([n, n.zfill(2), n.zfill(3)]))


def _same_number(a: str, b: str) -> bool:
    if a.isdigit() and b.isdigit():
        return int(a) == int(b)
    return a.lower() == b.lower()


_fold = fold  # One fold for the whole app (display.fold)


def card_kind(detail: dict) -> str | None:
    """'pokemon', 'trainer' or 'energy'. Read from fields only one kind has, so
    it works in every language; the category name is the fallback."""
    if detail.get("energyType"):
        return "energy"
    if detail.get("trainerType"):
        return "trainer"
    if detail.get("hp") or detail.get("types"):
        return "pokemon"
    category = _fold(detail.get("category") or "")
    if "energ" in category or "エネルギー" in category or "에너지" in category or "能量" in category:
        return "energy"
    if any(w in category for w in _TRAINER_WORDS):
        return "trainer"
    return "pokemon" if category else None


def _details(api, briefs: list[dict]) -> list[dict]:
    get = getattr(api, "get_card_cached", api.get_card)
    with jobs.pool(8) as pool:
        return list(pool.map(lambda b: get(b["id"]), briefs))


def find_cards(api: TCGdex, q: str, set_id: str | None = None, rarity: str | None = None,
               kind: str | None = None, smart=None, release=None, vintage_years: int = 20,
               stored=None, artist: str | None = None, longer=None) -> list[dict]:
    """Matching cards, newest sets first. With set_id, only that set is
    searched, and an empty query lists the whole set in number order.
    rarity and kind ('pokemon', 'trainer', 'energy') need each card's details,
    so they look at up to MAX_DETAILS cards. smart: the smart words read from the
    search (smart.Reading; q is then what's left), release: set id -> release date."""
    number, total, name = parse_query(q)
    if not number and not name and not set_id and not artist:
        return []

    sets = api.list_sets()
    order = {s["id"]: i for i, s in enumerate(sets)}
    by_id = {s["id"]: s for s in sets}

    if artist:
        # An artist's cards (TCGdex matches the name loosely; the set filter applies after).
        briefs = api.search_cards(illustrator=artist)
        if set_id:
            briefs = [c for c in briefs if c["id"][: -(len(c["localId"]) + 1)] == set_id]
    elif set_id:
        # One set: its card list is small, so fetch it and filter here.
        briefs = tcgdex.cached_set(api, set_id).get("cards", [])
    elif number:
        found = {c["id"]: c for c in api.search_cards(local_id=_number_forms(number), name=name)}
        briefs = list(found.values())
    else:
        briefs = api.search_cards(name=name)

    # Filter again here too, in case the API's filtering is looser than asked.
    if number:
        briefs = [c for c in briefs if _same_number(c["localId"], number)]
    if name:
        wanted = _fold(name)
        briefs = [c for c in briefs if wanted in _fold(c["name"])]
        # A whole Pokémon name leaves out other Pokémon whose names contain it (Gastly ゴース
        # isn't Haunter ゴースト). A card naming both, like a TAG TEAM, stays.
        others = longer(name) if longer else []
        if others:
            def own(card_name):
                text = _fold(card_name)
                for other in others:
                    text = text.replace(other, " ")
                return wanted in text
            briefs = [c for c in briefs if own(c["name"])]

    def set_of(c):
        return set_id or c["id"][: -(len(c["localId"]) + 1)]  # ids are '<set id>-<number>'

    if smart is not None and smart.any:
        from . import smart as words
        briefs = [c for c in briefs if words.name_ok(smart, c["name"])]
        if "promo" in smart.flags:
            briefs = [c for c in briefs if words.is_promo(by_id.get(set_of(c), {}).get("name", ""), set_of(c))]
        if "vintage" in smart.flags and release is not None:
            ids = list(dict.fromkeys(set_of(c) for c in briefs))
            with jobs.pool(8) as pool:
                dates = dict(zip(ids, pool.map(release, ids)))
            briefs = [c for c in briefs if words.is_vintage(dates.get(set_of(c)), vintage_years)]

    detail_check = smart is not None and (smart.needs_detail or "Tera" in smart.mechanics)
    rarity_of: dict[str, str | None] = {}
    if rarity or kind or detail_check:
        briefs.sort(key=lambda c: order.get(set_of(c), -1), reverse=True)
        briefs = briefs[:MAX_DETAILS]
        kept = []
        here = {c["id"]: stored(c["id"]) for c in briefs} if stored and not detail_check else {}
        if not detail_check:  # The local catalog's briefs carry rarity and kind already
            here.update({c["id"]: {"rarity": c.get("rarity"), "_kind": c["_kind"]}
                         for c in briefs if c.get("_kind") and not here.get(c["id"])})
        ask = [c for c in briefs if not here.get(c["id"])]
        asked = dict(zip((c["id"] for c in ask), _details(api, ask)))
        for c in briefs:
            d = here.get(c["id"]) or asked[c["id"]]
            if rarity and _fold(d.get("rarity") or "") != _fold(rarity):
                continue
            if kind and (d.get("_kind") or card_kind(d)) != kind:
                continue
            if detail_check and not words.detail_ok(smart, d, by_id.get(set_of(c), {}).get("name", "")):
                continue
            rarity_of[c["id"]] = d.get("rarity")
            kept.append(c)
        briefs = kept

    results = []
    for c in briefs:
        card_set = set_of(c)
        s = by_id.get(card_set, {})
        official = (s.get("cardCount") or {}).get("official")
        if total and official and official != total:
            continue
        results.append({
            "id": c["id"], "number": c["localId"], "name": c["name"], "image_url": image_url(c.get("image")),
            "set_id": card_set, "set_name": s.get("name", card_set), "printed_total": official,
            "set_logo": f"{s['logo']}.webp" if s.get("logo") else None, "rarity": rarity_of.get(c["id"]),
            "_order": order.get(card_set, -1),
            "_sort": parse_number(c["localId"], official or 0)[0],
        })
    if set_id:
        results.sort(key=lambda r: (r["_sort"], r["number"]))
        return results[:MAX_SET_RESULTS]
    results.sort(key=lambda r: r["_order"], reverse=True)  # newest sets first
    return results[:MAX_RESULTS]


SORTS = {"number": "Number", "name": "Name", "price_desc": "Price high→low", "price_asc": "Price low→high",
         "newest": "Newest set", "oldest": "Oldest set"}  # "" is best match, the order results come in


def _number_key(number: str) -> tuple:
    digits = re.match(r"\D*(\d+)", number or "")
    return (int(digits.group(1)) if digits else 10 ** 9, number or "")


def sort_results(results: list[dict], sort: str, set_rank) -> list[dict]:
    """Find's Sort by. Cards with no price go last either way; ties keep best-match order."""
    if sort == "number":
        return sorted(results, key=lambda r: _number_key(r.get("number")))
    if sort == "name":
        return sorted(results, key=lambda r: (r.get("name") or "").casefold())
    if sort in ("price_desc", "price_asc"):
        sign = -1 if sort == "price_desc" else 1
        return sorted(results, key=lambda r: (r.get("price") is None, sign * (r.get("price") or 0)))
    if sort in ("newest", "oldest"):
        sign = -1 if sort == "newest" else 1
        return sorted(results, key=lambda r: (sign * set_rank(r), _number_key(r.get("number"))))
    return results


def set_choices(api: TCGdex) -> list[dict]:
    """Sets for the filter list, newest first."""
    return [{"id": s["id"], "name": s["name"]} for s in reversed(api.list_sets())]


# Offered in the rarity filter alongside the rarities of sets you've loaded.
COMMON_RARITIES = [
    "Common", "Uncommon", "Rare", "Rare Holo", "Double rare", "Ultra Rare", "Illustration rare",
    "Special illustration rare", "Hyper rare", "ACE SPEC Rare", "Shiny rare", "Shiny Ultra Rare", "Promo",
]


def rarity_choices(known: list[str], lang: str) -> list[str]:
    """Rarities from loaded sets in this language, plus the common English ones."""
    out: dict[str, str] = {}
    for r in known + (COMMON_RARITIES if lang == "en" else []):
        out.setdefault(_fold(r), r)
    return sorted(out.values(), key=_fold)


def preview(api: TCGdex, card_id: str) -> dict:
    """A card straight from TCGdex, for looking at before it's added."""
    d = getattr(api, "get_card_cached", api.get_card)(card_id)
    s = d.get("set") or {}
    return {
        "detail": d, "id": d.get("id", card_id), "name": d.get("name", card_id), "number": d.get("localId", ""),
        "rarity": d.get("rarity"), "image_url": image_url(d.get("image")),
        "set_id": s.get("id") or card_id.rsplit("-", 1)[0], "set_name": s.get("name", ""),
        "logo_url": f"{s['logo']}.webp" if s.get("logo") else None,
        "variants": d.get("variants") or {},
    }
