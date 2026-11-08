"""How things are shown: dates and times in the app's time zone, rarity and type
symbols, and English names for mainland Chinese sets."""

import os
import re
import unicodedata
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

# Every date and time on screen is in one time zone (a setting in Data sources,
# see region.py). Stored values stay UTC.
def _zone_or_none(name: str | None) -> ZoneInfo | None:
    try:
        return ZoneInfo((name or "").strip()) if (name or "").strip() else None
    except (ValueError, KeyError, OSError):  # ZoneInfoNotFoundError is a KeyError
        return None


def env_zone() -> str | None:
    """The zone .env gives: DISPLAY_TZ, else TZ (only real zone names count)."""
    for name in ("DISPLAY_TZ", "TZ"):
        if _zone_or_none(os.environ.get(name)):
            return os.environ[name].strip()
    return None


_zone = _zone_or_none(env_zone()) or ZoneInfo("UTC")


def zone() -> ZoneInfo:
    return _zone


def set_zone(name: str) -> None:
    """Raises ValueError for a name that isn't a time zone."""
    global _zone
    found = _zone_or_none(name)
    if found is None:
        raise ValueError(f"{name!r} isn't a time zone.")
    _zone = found


def local(value) -> datetime | None:
    """A stored time ('2026-10-10 15:51:00', ISO with or without an offset; UTC when
    it has none) as a time in the app's zone. None for empty or unreadable values."""
    if not value:
        return None
    if isinstance(value, datetime):
        when = value
    else:
        try:
            when = datetime.fromisoformat(str(value).strip().replace(" ", "T").replace("Z", "+00:00"))
        except ValueError:
            return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(_zone)


def now() -> datetime:
    return datetime.now(_zone)


def today() -> date:
    return now().date()


def _is_day(value) -> bool:
    return isinstance(value, str) and len(value.strip()) == 10


def _day(value) -> date | None:
    """A typed date ('2026-10-08') stays that day; a stored time becomes its day in the app's zone."""
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if _is_day(value):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    when = local(value)
    return when.date() if when else None


def clock(value) -> str:
    """'11:51 AM'."""
    when = local(value)
    return f"{when.hour % 12 or 12}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}" if when else ""


def long_date(value) -> str:
    """'October 10, 2026'."""
    day = _day(value)
    return f"{day:%B} {day.day}, {day.year}" if day else ""


def short_date(value) -> str:
    """'Oct 10, 2026'."""
    day = _day(value)
    return f"{day:%b} {day.day}, {day.year}" if day else ""


def when(value) -> str:
    """'Oct 10, 2026, 4:00 AM', or 'never'."""
    if not value:
        return "never"
    if _is_day(value):
        return short_date(value)
    return f"{short_date(value)}, {clock(value)}" if local(value) else str(value)


def ago(value) -> str:
    """'today', 'yesterday', '3 days ago', then '12 Oct' ('12 Oct 2025' in another year)."""
    day = _day(value)
    if not day:
        return ""
    days = (today() - day).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    return f"{day.day} {day:%b}" + ("" if day.year == today().year else f" {day.year}")


def month_key(value) -> str:
    """'2026-10' for grouping by month, in the app's zone."""
    day = _day(value)
    return f"{day.year:04d}-{day.month:02d}" if day else ""


def month_label(key: str) -> str:
    """'2026-10' -> 'October 2026'."""
    try:
        return f"{date(int(key[:4]), int(key[5:7]), 1):%B %Y}"
    except ValueError:
        return key


# --- card types -----------------------------------------------------------------

# The 11 card-game types, with the colours of the small round symbols the app draws.
TYPES = {
    "Grass": "#3f9a3c", "Fire": "#e2502c", "Water": "#2f8fd8", "Lightning": "#e7b416", "Psychic": "#9b4fb6",
    "Fighting": "#b5602c", "Darkness": "#3c3a48", "Metal": "#8b939b", "Fairy": "#e46aa6", "Dragon": "#a08316",
    "Colorless": "#b9b4a8",
}
TYPE_LETTERS = {"Lightning": "L", "Colorless": "C", "Darkness": "D", "Dragon": "N", "Fairy": "Y", "Fighting": "F",
                "Fire": "R", "Grass": "G", "Metal": "M", "Psychic": "P", "Water": "W"}

# The 11 energy symbols, drawn by us in the card game's style (no downloaded images): white
# shapes on the type's colour, in a 24x24 box. (fill path, stroke path) per type.
TYPE_GLYPHS = {
    "Grass": ("M6.5 17.5C6 11 10 6.5 17.5 6c.4 7.5-4 11.6-11 11.5z", "M6.8 17.2l6.4-6.4"),
    "Fire": ("M12 3.6c.9 3 5.2 5.2 5.2 9.6a5.2 5.2 0 0 1-10.4 0c0-2.1 1-3.4 2.1-4.3-.1 2 .8 3.3 2 3.5-.6-3.1-.3-5.8 1.1-8.8z", ""),
    "Water": ("M12 3.8c3.2 4.2 6 7.2 6 10.4a6 6 0 0 1-12 0c0-3.2 2.8-6.2 6-10.4z", ""),
    "Lightning": ("M13.4 3 6.2 13.2h5l-1.8 7.8 8.4-11.1h-5.2z", ""),
    "Psychic": ("M2.8 12C5.6 8 8.7 6.2 12 6.2s6.4 1.8 9.2 5.8c-2.8 4-5.9 5.8-9.2 5.8S5.6 16 2.8 12zm9.2 3.1a3.1 3.1 0 1 0 0-6.2 3.1 3.1 0 0 0 0 6.2z", ""),
    "Fighting": ("M7 10.2c0-1.3.8-2.4 1.9-2.4s1.6.6 1.6.6.5-1.4 1.9-1.4 1.8 1.1 1.8 1.1.6-.9 1.8-.9 1.9 1 1.9 2.1v.6s.5-.4 1.1-.4c.9 0 1.5.7 1.5 1.7v2.6c0 3.3-2.5 5.6-5.6 5.6h-2.4C8.8 19.4 7 17.4 7 15z", "M10.5 8.6v2.6M14.2 8v3M7.2 12.8h6.4"),
    "Darkness": ("M14.8 3.6a8.6 8.6 0 1 0 5.6 14.1 7 7 0 0 1-5.6-14.1z", ""),
    "Metal": ("M12 3.4 20.6 18.4H3.4zm0 6-3.6 6.2h7.2z", ""),
    "Fairy": ("M12 2.8l2.1 7.1 7.1 2.1-7.1 2.1-2.1 7.1-2.1-7.1L2.8 12l7.1-2.1z", ""),
    "Dragon": ("", "M8.6 4.2c-2.6 4.6-2.8 10-.6 15.4M12.6 4.2c-2.6 4.6-2.8 10-.6 15.4M16.6 4.2c-2.6 4.6-2.8 10-.6 15.4"),
    "Colorless": ("M12 3.6l2.5 5.3 5.8.8-4.2 4 1 5.8L12 16.7l-5.1 2.8 1-5.8-4.2-4 5.8-.8z", ""),
}

# PTCG-CHS 'attribute' codes (checked against Pikachu, Charmander, Mewtwo, Dragonite…).
CHS_TYPES = {"1": "Grass", "2": "Fire", "3": "Water", "4": "Lightning", "5": "Psychic", "6": "Fighting",
             "7": "Darkness", "8": "Metal", "9": "Fairy", "10": "Dragon", "11": "Colorless"}


def card_types(value) -> list[str]:
    """The stored types ('Fire,Water') as a list of known types."""
    return [t for t in (value or "").split(",") if t in TYPES]


# --- rarity -----------------------------------------------------------------------

GOLD = "gold"
# symbol, label, colour class ('' or gold). Keys are folded (lower case, no spaces or dashes).
_RARITY = {
    "common": ("●", "Common", ""), "uncommon": ("◆", "Uncommon", ""), "rare": ("★", "Rare", ""),
    "rareholo": ("★", "Rare Holo", ""), "holorare": ("★", "Rare Holo", ""),
    "doublerare": ("★★", "Double Rare", ""), "ultrarare": ("★★", "Ultra Rare", ""), "rareultra": ("★★", "Ultra Rare", ""),
    "illustrationrare": ("★", "Illustration Rare", GOLD),
    "specialillustrationrare": ("★★", "Special Illustration Rare", GOLD),
    "hyperrare": ("★★★", "Hyper Rare", GOLD), "megahyperrare": ("★★★", "Mega Hyper Rare", GOLD),
    "secretrare": ("★★★", "Secret Rare", GOLD), "raresecret": ("★★★", "Secret Rare", GOLD),
    "rarerainbow": ("★★★", "Rainbow Rare", GOLD),
    "shinyrare": ("★", "Shiny Rare", GOLD), "shinyultrarare": ("★★", "Shiny Ultra Rare", GOLD),
    "aceSpecrare".lower(): ("★", "ACE SPEC Rare", ""),
}
# Printed codes on Japanese and mainland Chinese cards, with their full names.
_CODES = {
    "C": ("●", "Common", ""), "U": ("◆", "Uncommon", ""), "R": ("★", "Rare", ""), "RR": ("★★", "Double Rare", ""),
    "RRR": ("★★★", "Triple Rare", ""), "PR": ("", "Promo", ""), "TR": ("★★", "Trainer Rare", ""),
    "AR": ("★", "Art Rare", GOLD), "SAR": ("★★", "Special Art Rare", GOLD), "SR": ("★★", "Super Rare", GOLD),
    "SSR": ("★★", "Shiny Super Rare", GOLD), "HR": ("★★★", "Hyper Rare", GOLD), "UR": ("★★★", "Ultra Rare", GOLD),
    "CHR": ("★", "Character Rare", GOLD), "CSR": ("★★", "Character Super Rare", GOLD),
    "ACE": ("★", "ACE SPEC Rare", ""), "S": ("★", "Shiny", GOLD), "A": ("", "Amazing Rare", GOLD),
    "K": ("", "Radiant Rare", GOLD), "MUR": ("★★★", "Mega Ultra Rare", GOLD), "BWR": ("★★★", "Black White Rare", GOLD),
    "FUR": ("★★★", "Full Ultra Rare", GOLD),
}
# Mainland Chinese symbols used instead of codes on some sets.
_SYMBOLS = {"●": ("●", "Common", ""), "◆": ("◆", "Uncommon", ""), "★": ("★", "Rare", ""),
            "★★": ("★★", "Double Rare", ""), "★★★": ("★★★", "Triple Rare", "")}


def fold(text: str | None) -> str:
    """For comparing names and words everywhere: case, full-width letters and accents on
    Latin letters don't matter, so "pokemon" finds "Pokémon" and "flabebe" finds "Flabébé". Marks on
    other scripts stay (the dakuten in ガ isn't an accent)."""
    out = []
    for ch in unicodedata.normalize("NFKD", text or ""):
        if unicodedata.combining(ch) and out and out[-1] < "\u0250":
            continue
        out.append(ch)
    return unicodedata.normalize("NFKC", "".join(out)).casefold()


def _fold(text: str) -> str:
    return re.sub(r"[\s\-_]", "", fold(text))


def rarity(text: str | None) -> dict | None:
    """{symbol, label, code, gold} for a stored rarity, or None when there's none.
    A code ('SAR') keeps the code in front of its full name: 'SAR · Special Art Rare'.
    Unknown values show as they are, with no symbol (Data sources lists them)."""
    text = (text or "").strip()
    if not text:
        return None
    if text.upper() in _CODES:
        symbol, label, gold = _CODES[text.upper()]
        return {"symbol": symbol, "label": f"{text.upper()} · {label}", "code": text.upper(), "gold": bool(gold)}
    if text in _SYMBOLS:
        symbol, label, gold = _SYMBOLS[text]
        return {"symbol": symbol, "label": label, "code": None, "gold": bool(gold)}
    found = _RARITY.get(_fold(text))
    if found:
        symbol, label, gold = found
        return {"symbol": symbol, "label": label, "code": None, "gold": bool(gold)}
    return {"symbol": "", "label": text, "code": None, "gold": False, "unknown": True}


# Rarity groups for smart words and card tags, by the label rarity() gives.
_FULL_ART = {"Ultra Rare", "Illustration Rare", "Special Illustration Rare", "Hyper Rare", "Mega Hyper Rare", "Secret Rare",
             "Rainbow Rare", "Shiny Ultra Rare", "Art Rare", "Special Art Rare", "Super Rare", "Shiny Super Rare",
             "Character Rare", "Character Super Rare", "Trainer Rare", "Full Ultra Rare", "Black White Rare", "Mega Ultra Rare"}
_SECRET = {"Hyper Rare", "Mega Hyper Rare", "Secret Rare", "Rainbow Rare", "Ultra Rare", "Mega Ultra Rare",
           "Black White Rare", "Full Ultra Rare"}


def rarity_groups(text: str | None) -> set[str]:
    """{'holo', 'full art', 'shiny', 'secret'} that a rarity belongs to. 'secret' only for printed
    codes and the gold rarities above the set number (Hyper, Secret, Rainbow…)."""
    r = rarity(text)
    if not r:
        return set()
    label = r["label"].split(" · ", 1)[-1]
    out = set()
    if label == "Rare Holo":
        out.add("holo")
    if label in _FULL_ART:
        out.add("full art")
    if "Shiny" in label:
        out.add("shiny")
    if label in _SECRET and (r["code"] or r["gold"]):
        out.add("secret")
    return out


def unknown_rarities(values) -> list[str]:
    return sorted({v for v in values if v and (rarity(v) or {}).get("unknown")})


# --- mainland Chinese set names ----------------------------------------------------

def english_set_name(code: str | None) -> str | None:
    """An English name for a mainland Chinese set code, when there is one:
    CBB1C–CBB6C are the Gem Packs; CSVnC are China-only mixes, named from the code."""
    code = (code or "").strip()
    match = re.fullmatch(r"CBB(\d+)C", code)
    if match:
        return f"Gem Pack {int(match.group(1))}"
    match = re.fullmatch(r"CSV(\d+(?:\.\d+)?)C", code)
    if match:
        return f"Scarlet & Violet {match.group(1)}C"
    return None


def set_name(set_id: str | None, name: str | None) -> str:
    """The name a set is shown with: mainland sets get their English name when they have one."""
    if set_id and set_id.startswith("zh-cn:"):
        return english_set_name(set_id.split(":", 1)[1]) or name or set_id
    return name or set_id or ""


LANG_SHORT = {"ja": "JA", "zh-tw": "TW", "zh-cn": "ZH", "ko": "KO"}


def set_where(set_id: str | None, stored_name: str | None, number: str | None = None) -> str:
    """Where to find a card: 'Surging Sparks' for English, 'JA · Terastal Festival ex (SV8A) · 054' for others."""
    lang = (set_id or "").split(":", 1)[0] if ":" in (set_id or "") else "en"
    name = set_name(set_id, stored_name)
    if lang == "en":
        return name + (f" · {number}" if number else "")
    code = set_code(set_id)
    out = f"{LANG_SHORT.get(lang, lang.upper())} · {name}" + (f" ({code})" if code and code.casefold() not in name.casefold() else "")
    return out + (f" · {number}" if number else "")


def set_code(set_id: str | None) -> str:
    """The short code shown in a set's image box when it has no logo."""
    code = (set_id or "").split(":", 1)[-1]
    return code.upper() if len(code) <= 8 else code[:8].upper()




SET_SORTS = {"value": "Value", "number": "Number", "name": "Name"}  # Replaces Popular


def set_grid_view(cards: list[dict], pick: str = "", sort: str = "value") -> dict:
    """A set page's grid with its Filter sheet's rarity and its Sort.
    cards: the grid's tiles in number order (each with 'rarity', 'name' and 'value', the card's dearest price or
    None); pick: the rarity picked. Value: dearest first, then the cards with no price in number order."""
    rarities: list[str] = []
    for c in cards:
        r = rarity(c.get("rarity"))
        if r and r["label"] not in rarities:
            rarities.append(r["label"])
    if pick not in rarities:
        pick = ""
    sort = sort if sort in SET_SORTS else "value"
    shown = [c for c in cards if not pick or (rarity(c.get("rarity")) or {}).get("label") == pick]
    # A card with no stored price shows the set price list's (TCGplayer's, from TCGCSV) that the
    # Value sort uses, so the dearest cards never say "No price". Value still sorts by the dearest printing.
    shown = [dict(c, price=c["value"], from_list=True) if c.get("price") is None and c.get("value") is not None else c
             for c in shown]
    if sort == "value":
        worth = lambda c: c.get("value") if c.get("value") is not None else c.get("price")  # noqa: E731
        shown = sorted(shown, key=lambda c: (worth(c) is None, -(worth(c) or 0)))  # stable: number order after
    elif sort == "name":
        shown = sorted(shown, key=lambda c: (str(c.get("name") or "").casefold()))
    return {"cards": shown, "rarities": rarities if len(rarities) > 1 else [], "rarity": pick, "sort": sort,
            "sorts": SET_SORTS, "priced": any(c.get("value") is not None for c in cards),
            "from_list": any(c.get("from_list") for c in shown)}


def money(value, currency: str | None = None) -> str:
    """A stored price (dollars) in the chosen currency at the last saved rates; with currency, an amount you
    typed, shown as typed."""
    from . import fx
    if value is None:
        return ""
    return fx.fmt(value, currency) if currency else fx.fmt(fx.from_usd(value))


def saved(row: dict, key: str = "price") -> str:
    """A price row's amount (price, range_low, range_high, or a source's) as saved in the install's currency at
    the price update, else (a row from before that currency was picked) the dollar amount at the saved rates."""
    from . import fx
    local = row.get("local" if key == "source" else f"{key}_local")
    usd = row.get("price" if key == "source" else key)
    if local is not None and row.get("local_currency", fx.shown()) == fx.shown():
        return fx.fmt(local)
    return money(usd)
