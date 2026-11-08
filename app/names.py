"""Pokémon names across languages, so an English name can be searched in
another language ('Charizard' -> 'リザードン' for Japanese).

The names come from PokéAPI's species list (BSD licence; Pokémon names are
trademarks of Nintendo). It is downloaded once on first use and kept next to
the database, so it needs no setup. Only Pokémon names are translated; trainer
and energy card names are searched as typed.
"""

import csv
import io
import re
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import sites

CSV_URL = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/pokemon_species_names.csv"

# Our TCGdex language codes -> PokéAPI language ids, best first.
_ENGLISH = 9
_LANG_IDS = {
    "ja": [1, 11],      # katakana, as printed on Japanese cards
    "ko": [3],
    "zh-tw": [4],       # Traditional Chinese
    "zh-cn": [12],      # Simplified Chinese
    "fr": [5],
    "de": [6],
    "es": [7],
    "it": [8],
    "pt-br": [13, 9],   # Portuguese cards use the English names
}


@dataclass
class Translation:
    english: str  # the species name as matched, e.g. 'Charizard'
    name: str     # the same Pokémon in the target language


class Names:
    def __init__(self, cache_path: Path, url: str = CSV_URL):
        self.cache_path = cache_path
        self.url = url
        self._table: dict[str, dict[int, str]] | None = None  # english lower -> {lang id: name}
        self._english: dict[str, str] = {}                     # english lower -> english as written
        self._ids: dict[str, int] = {}                         # english lower -> species number
        self._by_id: dict[int, str] = {}                       # species number -> english
        self._pattern: re.Pattern | None = None
        self._lock = threading.Lock()
        self._others: dict[str, int] | None = None             # names in every language -> species number

    def _load(self) -> None:
        with self._lock:
            if self._table is not None:
                return
            if not self.cache_path.exists():
                resp = sites.get(self.url, timeout=30, follow_redirects=True)
                resp.raise_for_status()
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_text(resp.text, encoding="utf-8")
            by_species: dict[str, dict[int, str]] = {}
            for row in csv.DictReader(io.StringIO(self.cache_path.read_text(encoding="utf-8"))):
                by_species.setdefault(row["pokemon_species_id"], {})[int(row["local_language_id"])] = row["name"]
            table = {}
            for species, names in by_species.items():
                english = (names.get(_ENGLISH) or "").replace("\u2019", "'")  # Farfetch’d
                if english:
                    table[english.lower()] = names
                    self._english[english.lower()] = english
                    self._ids[english.lower()] = int(species)
                    self._by_id[int(species)] = english
            # Longest names first, so 'Porygon-Z' wins over 'Porygon'.
            alternatives = sorted(table, key=len, reverse=True)
            self._pattern = re.compile(
                r"(?<![\w])(" + "|".join(re.escape(a) for a in alternatives) + r")(?![\w])", re.IGNORECASE
            )
            self._table = table

    def translate(self, text: str, lang: str) -> Translation | None:
        """The first Pokémon named in text, in lang. None if lang is English,
        unsupported, or no Pokémon name is found."""
        if lang not in _LANG_IDS or not text:
            return None
        self._load()
        m = self._pattern.search(text.replace("\u2019", "'"))
        if not m:
            return None
        key = m.group(1).lower()
        names = self._table[key]
        for lang_id in _LANG_IDS[lang]:
            if names.get(lang_id):
                # NFKC turns full-width letters ('ポリゴンＺ') into the plain ones cards use.
                return Translation(english=self._english[key], name=unicodedata.normalize("NFKC", names[lang_id]))
        return None


    def available(self) -> bool:
        """The list is here without downloading it (pages that only decorate use this)."""
        return self._table is not None or self.cache_path.exists()

    def species_id(self, text: str) -> int | None:
        """The species number of the first Pokémon named in text ('Pikachu ex' -> 25),
        in English or, when the whole name is one, any other language."""
        if not text:
            return None
        self._load()
        m = self._pattern.search(text.replace("\u2019", "'"))
        if m:
            return self._ids[m.group(1).lower()]
        if self._others is None:
            self._others = {unicodedata.normalize("NFKC", n): self._ids[k]
                            for k, names in self._table.items() for n in names.values()}
        base = unicodedata.normalize("NFKC", text).strip()
        for suffix in ("ex", "EX", "GX", "V", "VMAX", "VSTAR"):
            if base.endswith(suffix) and base[:-len(suffix)].strip() in self._others:
                return self._others[base[:-len(suffix)].strip()]
        return self._others.get(base)

    # Printed before a Pokémon's name on Mega Evolution cards.
    _MEGA = ("メガ", "超级", "超級", "메가")
    _SUFFIXES = ("VMAX", "VSTAR", "EX", "GX", "ex", "V")

    # Regional forms, printed before the Pokémon's name.
    REGIONS = {"アローラ": "Alolan", "阿罗拉": "Alolan", "阿羅拉": "Alolan",
               "ガラル": "Galarian", "伽勒尔": "Galarian", "伽勒爾": "Galarian",
               "ヒスイ": "Hisuian", "洗翠": "Hisuian",
               "パルデア": "Paldean", "帕底亚": "Paldean", "帕底亞": "Paldean"}
    # Owner's Pokémon: '<owner>の<Pokémon>' (Japanese) and '<owner>的<Pokémon>' (Chinese).
    OWNERS = {"ロケット団": "Team Rocket's", "火箭队": "Team Rocket's", "火箭隊": "Team Rocket's",
              "マグマ団": "Team Magma's", "アクア団": "Team Aqua's",
              "N": "N's", "Ｎ": "N's",
              "リーリエ": "Lillie's", "莉莉艾": "Lillie's",
              "ホップ": "Hop's", "赫普": "Hop's",
              "マリィ": "Marnie's", "玛俐": "Marnie's", "瑪俐": "Marnie's",
              "ダイゴ": "Steven's", "大吾": "Steven's",
              "シロナ": "Cynthia's", "竹兰": "Cynthia's", "竹蘭": "Cynthia's",
              "ヒビキ": "Ethan's", "阿响": "Ethan's", "阿響": "Ethan's",
              "エリカ": "Erika's", "莉佳": "Erika's",
              "タケシ": "Brock's", "小刚": "Brock's", "小剛": "Brock's",
              "カスミ": "Misty's", "小霞": "Misty's",
              "サカキ": "Giovanni's", "坂木": "Giovanni's",
              "マチス": "Lt. Surge's", "马志士": "Lt. Surge's", "馬志士": "Lt. Surge's",
              "ナツメ": "Sabrina's", "娜姿": "Sabrina's",
              "カツラ": "Blaine's", "夏伯": "Blaine's",
              "キョウ": "Koga's", "阿桔": "Koga's",
              "ナンジャモ": "Iono's", "奇树": "Iono's", "奇樹": "Iono's",
              "ペパー": "Arven's", "派帕": "Arven's",
              "アオキ": "Larry's", "青木": "Larry's"}

    # Round 28: more of a card's name, around the Pokémon's.
    _MORE_SUFFIXES = ("V-UNION", "VMAX", "VSTAR", "BREAK", "LV.X", "LEGEND", "-GX", "-EX", "GX", "EX", "ex", "V", "☆", "δ")
    RADIANT = {"かがやく": "Radiant", "光辉": "Radiant", "光輝": "Radiant"}
    # Forms printed after the Pokémon's name, written first in English ("Teal Mask Ogerpon ex").
    FORMS = {"みどりのめん": "Teal Mask", "いどのめん": "Wellspring Mask", "かまどのめん": "Hearthflame Mask",
             "いしずえのめん": "Cornerstone Mask", "いちげきのかた": "Single Strike", "れんげきのかた": "Rapid Strike",
             "碧草面具": "Teal Mask", "水井面具": "Wellspring Mask", "火灶面具": "Hearthflame Mask", "础石面具": "Cornerstone Mask",
             "一击流": "Single Strike", "连击流": "Rapid Strike", "一擊流": "Single Strike", "連擊流": "Rapid Strike"}

    def to_english(self, text: str) -> str | None:
        """A card's Pokémon name in English ('メガルカリオex' -> 'Mega Lucario ex', 'ロケット団のニャース' ->
        "Team Rocket's Meowth", 'アローラ ロコン' -> 'Alolan Vulpix'; round 28: 'ピカチュウ&ゼクロムGX' ->
        'Pikachu & Zekrom GX', 'かがやくゲッコウガ' -> 'Radiant Greninja', 'MリザードンEX' -> 'M Charizard EX',
        'メガリザードンXex' -> 'Mega Charizard X ex', 'オーガポン みどりのめんex' -> 'Teal Mask Ogerpon ex'), or None when
        the name isn't a Pokémon's (Trainers, Energy) or the list isn't downloaded yet."""
        if not text or not self.available():
            return None
        self._load()
        if self._others is None:
            self._others = {unicodedata.normalize("NFKC", n): self._ids[k]
                            for k, names in self._table.items() for n in names.values()}
        base = unicodedata.normalize("NFKC", text).strip()
        if "&" in base:  # a TAG TEAM: every Pokémon named, the card's mark after the last
            parts = [p.strip() for p in base.split("&")]
            last = self._one(parts[-1])
            firsts = [self._one(p) for p in parts[:-1]]
            if last and all(firsts):
                return " & ".join([*firsts, last])
            return None
        return self._one(base)

    def _one(self, base: str) -> str | None:
        owner = ""
        for mark in ("の", "的"):
            left, sep, right = base.partition(mark)
            if sep and unicodedata.normalize("NFKC", left).strip() in self._owners():
                owner, base = self._owners()[left.strip()] + " ", right.strip()
                break
        suffixes = sorted(set(self._SUFFIXES) | set(self._MORE_SUFFIXES), key=len, reverse=True)
        suffix = next((x for x in suffixes if base.endswith(x) and base[:-len(x)].strip() in self._others), None)
        if suffix is None:
            suffix = next((x for x in suffixes if base.endswith(x)), "")
        core = base[:-len(suffix)].strip() if suffix else base
        suffix = suffix.lstrip("-")
        prefix = ""
        radiant = next((r for r in self.RADIANT if core.startswith(r)), None)
        if radiant:
            prefix, core = self.RADIANT[radiant] + " ", core[len(radiant):].strip()
        form = ""
        if " " in core and core.split(" ", 1)[1].strip() in self.FORMS:
            core, form_words = core.split(" ", 1)
            form = self.FORMS[form_words.strip()] + " "
        region = next((r for r in self.REGIONS if core.startswith(r) and core[len(r):].strip() in self._others), None)
        if region:
            prefix, core = prefix + self.REGIONS[region] + " ", core[len(region):].strip()
        letter = ""
        if core not in self._others:
            mega = next((m for m in self._MEGA if core.startswith(m) and core[len(m):].strip() in self._others), None)
            if mega is None:  # Mega Charizard X / Y
                mega = next((m for m in self._MEGA if core.startswith(m) and core[-1:] in ("X", "Y")
                             and core[len(m):-1].strip() in self._others), None)
                if mega is not None:
                    letter, core = " " + core[-1], core[:-1]
            if mega is not None:
                prefix, core = prefix + "Mega ", core[len(mega):].strip()
            elif core.startswith("M") and core[1:].strip() in self._others and suffix == "EX":  # XY-era Mega: M…EX
                prefix, core = prefix + "M ", core[1:].strip()
            else:
                return None
        english = self._by_id.get(self._others[core])
        return f"{owner}{form}{prefix}{english}{letter}{' ' + suffix if suffix else ''}" if english else None

    def longer_names(self, text: str) -> list[str]:
        """When text is a whole Pokémon name, in any language, the other Pokémon names that
        contain it (Gastly ゴース -> Haunter ゴースト; Mew -> Mewtwo), folded for comparing. Else []."""
        if not text or not self.available():
            return []
        self._load()
        with self._lock:
            if getattr(self, "_all_folded", None) is None:
                from .display import fold
                self._all_folded = {fold(unicodedata.normalize("NFKC", n)) for names in self._table.values()
                                    for n in names.values() if n}
        from .display import fold
        wanted = fold(unicodedata.normalize("NFKC", text)).strip()
        if wanted not in self._all_folded:
            return []
        return sorted((n for n in self._all_folded if wanted in n and n != wanted), key=len, reverse=True)

    def _owners(self) -> dict[str, str]:
        return {unicodedata.normalize("NFKC", k): v for k, v in self.OWNERS.items()}

    def english_names(self) -> list[str]:
        """Every species' English name (for spelling help)."""
        self._load()
        return list(self._by_id.values())

    def english_name(self, species_id: int) -> str | None:
        self._load()
        return self._by_id.get(species_id)

    def name_in(self, species_id: int, lang: str) -> str | None:
        """A species' name in one of our languages (English for the rest)."""
        self._load()
        english = self._by_id.get(species_id)
        if english is None:
            return None
        names = self._table[english.lower()]
        for lang_id in _LANG_IDS.get(lang, []):
            if names.get(lang_id):
                return unicodedata.normalize("NFKC", names[lang_id])
        return english
