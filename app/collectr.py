"""Reading Collectr CSV exports and working out which TCGdex set and card each
row is. Pure functions; importer.py does the database side.

Collectr's columns (Oct 2026): Portfolio Name, Category, Set, Product Name,
Card Number, Rarity, Variance, Grade, Card Condition, Average Cost Paid,
Quantity, Market Price (As of <date>), Price Override, Watchlist, Date Added,
Notes. Set names are TCGplayer's.
"""

import csv
import io
import math
import re
from dataclasses import dataclass, field

SOURCE = "collectr"

CONDITIONS = {
    "near mint": "NM", "lightly played": "LP", "moderately played": "MP",
    "heavily played": "HP", "damaged": "DMG",
}

# TCGplayer set names that don't match TCGdex's by name alone -> TCGdex ids.
SET_ALIASES = {
    "sv: 151": ["sv03.5"],
    "base set (unlimited)": ["base1"],
    "base set (1st edition & shadowless)": ["base1"],
    "base set (shadowless)": ["base1"],
    "base set (1st edition)": ["base1"],
    "scarlet & violet base set": ["sv01"],
    "sword & shield base set": ["swsh1"],
    "sun & moon base set": ["sm1"],
    "xy base set": ["xy1"],
    "black and white": ["bw1"],
    "black & white": ["bw1"],
    "heartgold soulsilver": ["hgss1"],
    "diamond and pearl": ["dp1"],
    "expedition": ["ecard1"],
    "ex ruby & sapphire": ["ex1"],
    "scarlet & violet promo": ["svp"],
    "scarlet & violet promos": ["svp"],
    "sword & shield promo": ["swshp"],
    "sword & shield promos": ["swshp"],
    "sun & moon promos": ["smp"],
    "xy promos": ["xyp"],
    "mega evolution promos": ["mep"],
    "mega evolution promo": ["mep"],
    "wotc promo": ["basep"],
    "30th celebration: classic collection": ["30th-c"],
    "celebrations: classic collection": ["cel25cc"],
    "mcdonald's promos 2024": ["2024sv"],
    "mcdonald's promos 2023": ["2023sv"],
    "mcdonald's promos 2022": ["2022swsh"],
    "mcdonald's 25th anniversary promos": ["2021swsh"],
    "sm trainer kit: lycanroc & alolan raichu": ["tk-sm-l", "tk-sm-r"],
    "xy trainer kit: bisharp & wigglytuff": ["tk-xy-b", "tk-xy-w"],
    "xy trainer kit: pikachu libre & suicune": ["tk-xy-p", "tk-xy-su"],
    "xy trainer kit: latias & latios": ["tk-xy-latia", "tk-xy-latio"],
    "xy trainer kit: sylveon & noivern": ["tk-xy-sy", "tk-xy-n"],
    "bw trainer kit: excadrill & zoroark": ["tk-bw-e", "tk-bw-z"],
    "hgss trainer kit: gyarados & raichu": ["tk-hs-g", "tk-hs-r"],
    "dp trainer kit: manaphy & lucario": ["tk-dp-m", "tk-dp-l"],
}

# Sets with a gallery or vault numbered separately in TCGdex ('swsh9' -> 'swsh9tg').
_COMPANION = re.compile(r"^[a-z]{1,3}$")

_PATTERNS = {
    "poke ball pattern": "pokeball", "pokeball pattern": "pokeball", "poké ball pattern": "pokeball",
    "master ball pattern": "masterball",
}
_FOREIGN = re.compile(r"(?:\(|\s|^)(JP|CN|KR|Japanese|Chinese|Korean)\)?\s*$", re.I)


def fold(text: str) -> str:
    """Lowercase, accents and punctuation dropped: 'Pokémon GO' -> 'pokemon go'."""
    from .display import fold as plain
    text = plain(text).replace("&", " and ")
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


@dataclass
class Row:
    line: int
    raw: dict
    set_name: str
    name: str            # as in the file
    clean_name: str      # parentheticals and ' - 094' dropped, for matching
    number: str          # before the '/', zeros kept ('002')
    printed_total: int | None
    variant: str
    condition: str
    quantity: int
    cost: float | None
    note: str | None
    language: str | None  # 'ja', 'zh' etc. when the name or set says so
    skip: str | None = None  # why the row isn't a card to import
    key: str = field(default="")


def _number(text: str) -> tuple[str, int | None]:
    text = (text or "").strip()
    if "/" in text:
        number, total = text.split("/", 1)
        total = total.strip()
        return number.strip(), int(total) if total.isdigit() else None
    return text, None


def _variant(variance: str, rarity: str, name: str) -> tuple[str, str | None]:
    """Our variant, and a note for what doesn't fit one."""
    v, lowered = fold(variance), name.casefold()
    for words, variant in _PATTERNS.items():
        if words in lowered:
            return variant, None
    if v == "reverse holofoil":
        return "reverse", None
    if v == "holofoil":
        return "holo", None
    if v == "1st edition":
        return "first_ed", None
    if v == "unlimited":
        return ("holo" if "holo" in fold(rarity) else "normal"), None
    if v == "shadowless":
        return "other", "Shadowless"
    if v in ("normal", ""):
        return "normal", None
    return "other", variance.strip()


def clean_name(name: str) -> str:
    """'Alolan Exeggutor - 094 (30th Celebration)' -> 'Alolan Exeggutor'."""
    name = re.sub(r"\([^)]*\)", " ", name)
    name = re.sub(r"\s+-\s+\S+.*$", " ", name)
    name = _FOREIGN.sub(" ", name)
    return " ".join(name.split())


def _language(set_name: str, name: str) -> str | None:
    text = f"{set_name} {name}"
    if re.search(r"\(JP\)|\bJP$|\(Japanese\)|Japanese", text):
        return "ja"
    if re.search(r"\(CN\)|\bCN$|Chinese", text):
        return "zh"
    if re.search(r"\(KR\)|\bKR$|Korean", text):
        return "ko"
    return None


def _money(text: str) -> float | None:
    try:
        value = float((text or "").replace("$", "").replace(",", "").strip() or 0)
    except ValueError:
        return None
    return round(value, 2) if math.isfinite(value) and value > 0 else None  # no "inf"


def _note(row: dict, extra: list[str]) -> str | None:
    parts = [p.strip() for p in (row.get("Notes") or "").split(";")]
    grade = (row.get("Grade") or "").strip()
    if grade and fold(grade) != "ungraded":
        parts.append(grade)
    parts += extra
    parts = [p for p in parts if p]
    return "; ".join(dict.fromkeys(parts)) or None


def key_for(set_name: str, number: str, name: str, variance: str) -> str:
    return "|".join(fold(x) for x in (set_name, number, name, variance))


def parse(data: bytes) -> list[Row]:
    """Rows of a Collectr export. Raises ValueError if it doesn't look like one."""
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    needed = {"Set", "Product Name", "Card Number", "Quantity"}
    if not reader.fieldnames or not needed <= set(reader.fieldnames):
        raise ValueError("This doesn't look like a Collectr export. It needs the columns "
                         "Set, Product Name, Card Number and Quantity.")
    rows = []
    for line, raw in enumerate(reader, start=2):
        raw = {k: (v or "") for k, v in raw.items() if k}
        set_name, name = raw["Set"].strip(), raw["Product Name"].strip()
        number, total = _number(raw["Card Number"])
        extra = [m for m in re.findall(r"\(([^)]*)\)", name)
                 if fold(m) not in {fold(p) for p in _PATTERNS} and not _FOREIGN.match(f"({m})")
                 and not m.strip().lstrip("#").isdigit() and fold(m) != fold(set_name)]
        variant, variant_note = _variant(raw.get("Variance", ""), raw.get("Rarity", ""), name)
        if variant_note:
            extra.append(variant_note)
        bad_quantity = False
        try:
            quantity = int(float(raw["Quantity"] or 0))
        except (ValueError, OverflowError):  # "abc", "inf": a problem to show, not a quantity of 0
            quantity, bad_quantity = 0, True
        condition = CONDITIONS.get(fold(raw.get("Card Condition", "")).replace(" ", " "), "NM")
        row = Row(
            line=line, raw=raw, set_name=set_name, name=name, clean_name=clean_name(name),
            number=number, printed_total=total, variant=variant, condition=condition,
            quantity=quantity, cost=_money(raw.get("Average Cost Paid", "")),
            note=_note(raw, [e for e in extra if fold(e) not in ("japanese", "jp", "cn")]),
            language=_language(set_name, name),
        )
        category = fold(raw.get("Category", "pokemon"))
        if category and category != "pokemon":
            row.skip = f"{raw.get('Category')} card, not Pokémon"
        elif bad_quantity:
            row.skip = f"Quantity \"{raw['Quantity'].strip()}\" isn't a number"
        elif quantity < 1:
            row.skip = "Quantity is 0"
        elif not number:
            row.skip = "No card number"
        row.key = key_for(set_name, raw["Card Number"], name, raw.get("Variance", ""))
        rows.append(row)
    return rows


# --- sets -------------------------------------------------------------------

def _set_forms(name: str) -> set[str]:
    """Ways a TCGplayer set name might appear in TCGdex."""
    base = fold(name)
    forms = {base}
    for prefix in ("sv ", "swsh ", "sm ", "xy ", "bw ", "ex ", "pokemon "):
        if base.startswith(prefix):
            forms.add(base[len(prefix):])
    for suffix in (" base set", " set"):
        if base.endswith(suffix):
            forms.add(base[: -len(suffix)])
    forms.add(re.sub(r" promos?$", " black star promos", base))
    return {f for f in forms if f}


def resolve_set(name: str, printed_total: int | None, sets: list[dict]) -> list[str]:
    """TCGdex set ids a Collectr set name could be, best first. sets is TCGdex's
    set list (id, name, cardCount). Includes gallery and vault sets numbered
    separately, like Brilliant Stars' Trainer Gallery."""
    by_id = {s["id"]: s for s in sets}
    found = [i for i in SET_ALIASES.get(name.strip().casefold(), []) if i in by_id]
    if not found:
        forms = _set_forms(name)
        matches = [s for s in sets if fold(s["name"]) in forms]
        if len(matches) > 1 and printed_total:
            same = [s for s in matches if (s.get("cardCount") or {}).get("official") == printed_total]
            matches = same or matches
        # Newest first: TCGdex lists oldest first.
        found = [s["id"] for s in reversed(matches)]
    out = list(found)
    for set_id in found:
        out += [s["id"] for s in sets
                if s["id"].startswith(set_id) and _COMPANION.match(s["id"][len(set_id):])]
    return list(dict.fromkeys(out))


def same_number(a: str, b: str) -> bool:
    a, b = a.strip(), b.strip()
    if a.isdigit() and b.isdigit():
        return int(a) == int(b)
    return a.casefold() == b.casefold()


def names_agree(ours: str, theirs: str) -> bool:
    """Loose: 'Alolan Exeggutor' vs 'Alolan Exeggutor', 'Mewtwo ex' vs 'Mewtwo EX'."""
    a, b = fold(ours).replace(" ", ""), fold(theirs).replace(" ", "")
    if not a or not b:
        return True
    return a == b or a in b or b in a


def pick_card(row: Row, candidates: list[dict], check_name: bool = True) -> dict | None:
    """The card for a row among cards of its possible sets. Each candidate has
    'number' and 'name'. With several sets (trainer kit halves), the name decides.
    Reprint sets like Classic Collection keep the original card's number, so a
    name that only one card in the set has is enough there."""
    by_number = [c for c in candidates if same_number(c["number"], row.number)]
    if not check_name:
        return by_number[0] if by_number else None
    named = [c for c in by_number if names_agree(row.clean_name, c["name"])]
    if named:
        return named[0]
    same_name = [c for c in candidates if fold(c["name"]) == fold(row.clean_name)]
    return same_name[0] if len(same_name) == 1 else None
