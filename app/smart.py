"""Smart words in Find: "pikachu stamped", "charizard surging sparks",
"shiny full art", "vintage holo". Recognised words are taken out of the search and
become filters, each shown as a chip with ✕; the rest is the name/number search as
before. Unknown words stay in the name search. No moods: nothing describes a card's picture."""

import re
from dataclasses import dataclass, field
from datetime import date

from . import display, spelling

# word or phrase -> (filter, chip label)
WORDS = {
    "holo": ("holo", "Holo"),
    "reverse": ("reverse", "Reverse holo"), "reverse holo": ("reverse", "Reverse holo"),
    "rev holo": ("reverse", "Reverse holo"), "reverse holos": ("reverse", "Reverse holo"),
    "1st edition": ("first_ed", "1st edition"), "first edition": ("first_ed", "1st edition"),
    "1st ed": ("first_ed", "1st edition"),
    "stamped": ("stamped", "Stamped"), "stamp": ("stamped", "Stamped"),
    "shiny": ("shiny", "Shiny"),
    "full art": ("full_art", "Full art"), "fullart": ("full_art", "Full art"), "full arts": ("full_art", "Full art"),
    "vintage": ("vintage", "Vintage"),
    "promo": ("promo", "Promo"), "promos": ("promo", "Promo"),
}
# Card mechanics read from the card's name ('Pikachu ex', 'Mega Lucario ex', 'Charizard VMAX').
MECHANICS = {"ex": "ex", "gx": "GX", "v": "V", "vmax": "VMAX", "vstar": "VSTAR", "mega": "Mega", "break": "BREAK",
             "tera": "Tera"}
DETAIL_FILTERS = {"holo", "reverse", "first_ed", "stamped", "shiny", "full_art"}
MAX_PHRASE = 6


@dataclass
class Reading:
    q: str                                   # what was typed
    rest: str                                # left for the name/number search
    set: dict | None = None                  # {id, plain_name, lang, ...} from the candidates
    flags: set = field(default_factory=lambda: set())  # WORDS filters (a lambda: "set" is also a field)
    mechanics: list = field(default_factory=list)
    tags: list = field(default_factory=list)  # your card tags
    chips: list = field(default_factory=list)  # [(label, q without that word)]

    @property
    def any(self) -> bool:
        return bool(self.set or self.flags or self.mechanics or self.tags)

    @property
    def needs_detail(self) -> bool:
        return bool(self.flags & DETAIL_FILTERS)


def _without(tokens: list[str], start: int, length: int) -> str:
    return " ".join(tokens[:start] + tokens[start + length:])


def read(q: str, sets: list[dict] = (), tags: list[str] = (), is_pokemon=lambda text: False) -> Reading:
    """Split q into recognised words and the rest. sets: [{id, plain_name, lang}] to match set names
    and codes against; tags: your own card tags; is_pokemon: whether text is a Pokémon's name, so a
    misspelt set name is only guessed for words that aren't a Pokémon."""
    tokens = q.split()
    low = [t.lower() for t in tokens]
    by_name = {}
    for s in sets:
        for key in (s.get("plain_name") or "", s.get("id") or ""):
            k = key.lower().strip()
            if len(k) >= 3 and not k.isdigit():
                by_name.setdefault(k, s)
    tag_keys = {t.lower(): t for t in tags}
    found: list[tuple[int, int, str, object]] = []  # (start, length, kind, value)
    used = [False] * len(tokens)
    for length in range(min(MAX_PHRASE, len(tokens)), 0, -1):
        for start in range(0, len(tokens) - length + 1):
            if any(used[start:start + length]):
                continue
            phrase = " ".join(low[start:start + length])
            hit = None
            if phrase in by_name and not phrase.isdigit():
                hit = ("set", by_name[phrase])
            elif phrase in WORDS:
                hit = ("word", WORDS[phrase])
            elif length == 1 and phrase in MECHANICS:
                hit = ("mech", MECHANICS[phrase])
            elif phrase in tag_keys:
                hit = ("tag", tag_keys[phrase])
            if hit:
                found.append((start, length, *hit))
                used[start:start + length] = [True] * length

    # A misspelt set name at the end ('pikachu surging sparcs'), never a Pokémon's name.
    if not any(kind == "set" for _, _, kind, _ in found) and by_name:
        free = [i for i, u in enumerate(used) if not u]
        names = list(by_name)
        for length in (3, 2, 1):
            tail = free[-length:] if len(free) >= length else []
            if not tail or tail != list(range(tail[0], tail[0] + length)):
                continue
            phrase = " ".join(tokens[i] for i in tail)
            if len(phrase) < 5 or is_pokemon(phrase) or (length == 1 and len(free) == 1):
                continue
            guess = spelling.best(phrase, names, cutoff=0.85)
            if guess:
                found.append((tail[0], length, "set", by_name[guess]))
                for i in tail:
                    used[i] = True
                break

    r = Reading(q=q, rest=" ".join(t for t, u in zip(tokens, used) if not u))
    for start, length, kind, value in sorted(found):
        rest_q = _without(tokens, start, length)
        if kind == "set":
            if r.set is None:
                r.set = value
                r.chips.append((value.get("plain_name") or value["id"], rest_q))
        elif kind == "word":
            if value[0] not in r.flags:
                r.flags.add(value[0])
                r.chips.append((value[1], rest_q))
        elif kind == "mech":
            if value not in r.mechanics:
                r.mechanics.append(value)
                r.chips.append((value, rest_q))
        else:
            r.tags.append(value)
            r.chips.append((f"#{value}", rest_q))
    if r.any and r.rest:
        r.chips.insert(0, (r.rest, " ".join(t for t, u in zip(tokens, used) if u)))
    return r


def name_ok(r: Reading, name: str) -> bool:
    """The mechanic words, read from a card's name."""
    for m in r.mechanics:
        n = name or ""
        if m == "Mega":
            if not (n.startswith("Mega ") or n.startswith("M ") or n.startswith("メガ") or n.startswith("超级")):
                return False
        elif m == "Tera":
            continue  # checked on the card's details
        elif m in ("ex",):
            if not re.search(r"(?<![A-Za-z])ex$", n, re.IGNORECASE):  # 'Pikachu ex', 'リザードンex', old 'Charizard-EX'
                return False
        elif not re.search(rf"[\s\-]{re.escape(m)}$", n, re.IGNORECASE):
            return False
    return True


def stamped(detail: dict) -> bool:
    """TCGdex lists stamps on some printings (variants_detailed[].stamp); coverage is unknown."""
    return any(d.get("stamp") for d in detail.get("variants_detailed") or [])


def detail_ok(r: Reading, detail: dict, set_name: str = "") -> bool:
    """The words that need a card's details: printings, stamps, rarity groups, Tera."""
    v = detail.get("variants") or {}
    groups = display.rarity_groups(detail.get("rarity"))
    if "holo" in r.flags and not (v.get("holo") or "holo" in groups):
        return False
    if "reverse" in r.flags and not v.get("reverse"):
        return False
    if "first_ed" in r.flags and not v.get("firstEdition"):
        return False
    if "stamped" in r.flags and not stamped(detail):
        return False
    if "shiny" in r.flags and not ("shiny" in groups or "shiny vault" in set_name.lower()
                                   or str(detail.get("localId", "")).upper().startswith("SV")):
        return False
    if "full_art" in r.flags and "full art" not in groups:
        return False
    if "Tera" in r.mechanics and "tera" not in str(detail.get("suffix", "")).lower() \
            and "tera" not in " ".join(str(a.get("name", "")) for a in detail.get("abilities") or []).lower():
        return False
    return True


def rarity_ok(r: Reading, rarity: str | None) -> bool:
    """For stored cards with only a rarity (mainland Chinese): the rarity words."""
    groups = display.rarity_groups(rarity)
    return all(g in groups for f, g in (("shiny", "shiny"), ("full_art", "full art")) if f in r.flags)


def is_promo(set_name: str, set_id: str = "") -> bool:
    return "promo" in (set_name or "").lower() or bool(re.fullmatch(r"[a-z]+p", set_id or ""))


def vintage_year(years: int, today: date | None = None) -> int:
    """Sets released before this year are vintage: older than 20 years in 2026 means 2006 and earlier."""
    return (today or display.today()).year - years + 1


def is_vintage(release_date: str | None, years: int, today: date | None = None) -> bool:
    return bool(release_date) and int(release_date[:4]) < vintage_year(years, today)


VINTAGE_KEY, VINTAGE_DEFAULT = "vintage_years", 20


def vintage_years(conn) -> int:
    """Data sources' "Vintage = sets older than N years" (app_meta), 20 by default."""
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (VINTAGE_KEY,)).fetchone()
    try:
        return max(1, min(100, int(row[0]))) if row else VINTAGE_DEFAULT
    except ValueError:
        return VINTAGE_DEFAULT


def save_vintage_years(conn, years: int) -> None:
    conn.execute("INSERT INTO app_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (VINTAGE_KEY, str(max(1, min(100, years)))))
    conn.commit()
