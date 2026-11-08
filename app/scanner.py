"""Reading a card photo and working out which card it is.

1. RapidOCR (on the CPU) finds the text on the photo.
2. read_text picks out the name (big text at the top) and the bottom line:
   set code, language and number, like 'POR EN 037/088' or 'MEP EN 054'.
   Cards before Scarlet & Violet have no set code, only '037/088'.
3. match looks the card up: set code and number when there's a code, else
   sets with that many cards, else the name across all sets. It returns up to
   3 guesses, best first.

Japanese and Chinese cards print no language after the set code ('M1S 014/063',
'CBB4C 0602/07'), so for them the code is picked out of the words left of the
number by comparing with that language's set codes, and the name can be in
kana or Chinese characters.

RapidOCR is optional (pip install rapidocr onnxruntime); without it the rest of
the app works and scans wait until it's installed.
"""

import difflib
import gc
import json
import logging
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .collectr import fold, same_number

log = logging.getLogger("dexstash.scan")
# Held while the photo reader or the picture model works, so they never run at once.
HEAVY = threading.Lock()

# Printed set code -> TCGdex set ids, from the TCGdex card database. Codes like
# 'BRS:TG' are a set's gallery, numbered TG01...
SET_CODES: dict[str, list[str]] = json.loads(Path(__file__).with_name("set_codes.json").read_text())

_LANGS = ("EN", "FR", "DE", "IT", "ES", "PT")
# Up to 4 digits for Chinese Gem Packs ("0602/07").
_NUMBER = re.compile(r"(?<![\w/])([A-Z]{0,3}\d{1,4}[a-z]?)\s*/\s*([A-Z]{0,3}\d{1,3})")
_CODE_LANG = re.compile(r"\b([A-Z][A-Z0-9]{1,3}?)\s?(" + "|".join(_LANGS) + r")\b")
_PROMO = re.compile(r"\b([A-Z][A-Z0-9]{1,3}?)\s?(?:" + "|".join(_LANGS) + r")\s*([A-Z]{0,4}\d{1,3})\b")
_NOT_NAMES = {"basic", "stage1", "stage2", "stage 1", "stage 2", "trainer", "supporter", "item", "stadium", "energy",
              "pokemon tool", "tool", "hp", "special energy", "basic energy", "ace spec", "mega", "vstar", "vmax", "tera"}
# Kana, Chinese characters and Hangul.
_ASIAN = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
# Card-type words printed near the name on Japanese, Chinese and Korean cards.
_NOT_NAMES_ASIAN = {"たね", "基础", "基礎", "特性", "トレーナーズ", "グッズ", "サポート", "スタジアム", "ポケモンのどうぐ",
                    "エネルギー", "基本エネルギー", "特殊エネルギー", "物品", "支援者", "竞技场", "宝可梦道具", "能量",
                    "基本能量", "特殊能量", "训练家", "訓練家", "기본"}
_CODE_LIKE = re.compile(r"^[A-Z]{1,4}[0-9]{1,2}(?:\.[0-9])?[A-Z]{0,2}$")
# Read after a name in its own box: 'Farigiraf' + 'ex'.
_NAME_SUFFIXES = {"ex", "EX", "GX", "V", "VMAX", "VSTAR"}


@dataclass
class Box:
    x: float
    y: float
    h: float
    text: str
    w: float = 0.0


@dataclass
class Reading:
    name: str | None = None
    number: str | None = None        # as printed: '037', 'TG05'
    total: str | None = None         # '088', 'TG30'; None on promos
    code: str | None = None          # set code as read: 'POR'
    lang: str | None = None          # 'EN'
    lines: list[str] = field(default_factory=list)
    box: list[float] | None = None   # where the card is on the photo, as fractions (x0, y0, x1, y1)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str | None) -> "Reading":
        if not text:
            return cls()
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in json.loads(text).items() if k in known})

    def search_text(self) -> str:
        """What to type in Find a card for this photo."""
        return " ".join(p for p in (self.name, self.number and (self.number + (f"/{self.total}" if self.total else "")))
                        if p)


# --- reading ----------------------------------------------------------------

def _no_spinning() -> None:
    """RapidOCR 3.9 doesn't pass onnxruntime's spinning switch through, so add it to its session options."""
    try:
        import rapidocr.inference_engine.onnxruntime.main as ort_main
        session = ort_main.OrtInferSession
        if getattr(session, "_dexstash_quiet", False):
            return
        original = session._init_sess_opts

        def quiet(cfg):
            options = original(cfg)
            options.add_session_config_entry("session.intra_op.allow_spinning", "0")
            options.add_session_config_entry("session.inter_op.allow_spinning", "0")
            return options
        session._init_sess_opts = staticmethod(quiet)
        session._dexstash_quiet = True
    except Exception as e:  # a newer RapidOCR: it still runs, just with spinning on
        log.info("photo reader spinning not turned off: %s", e)


class Reader:
    """RapidOCR, loaded on first use and let go with close() so it only takes
    memory while there are photos to read."""

    def __init__(self):
        self._engine = None
        self._lock = threading.Lock()

    @staticmethod
    def available() -> bool:
        try:
            import rapidocr  # noqa: F401
            import onnxruntime  # noqa: F401
        except ImportError:
            return False
        return True

    def read(self, path: Path) -> list[Box]:
        with HEAVY, self._lock:
            if self._engine is None:
                from rapidocr import RapidOCR
                logging.getLogger("RapidOCR").setLevel(logging.WARNING)
                _no_spinning()
                # One thread each, so reading never takes more than the scanner's one CPU and never
                # spins waiting for work (the cause of the slow app while reading, measured 14 Oct).
                self._engine = RapidOCR(params={"EngineConfig.onnxruntime.intra_op_num_threads": 1,
                                                "EngineConfig.onnxruntime.inter_op_num_threads": 1})
            result = self._engine(str(path))
        if not result.txts:
            return []
        out = []
        for box, text in zip(result.boxes, result.txts):
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            out.append(Box(x=float(min(xs)), y=float(min(ys)), h=float(max(ys) - min(ys)), text=text,
                           w=float(max(xs) - min(xs))))
        return out

    def close(self) -> None:
        with self._lock:
            self._engine = None
        gc.collect()


def card_box(boxes: list[Box], width: float, height: float) -> list[float] | None:
    """Where the card is on a photo, guessed from the text found on it: the
    text runs from the name near the top to the copyright line at the bottom,
    and across most of the card's width. Fractions of the photo (x0, y0, x1, y1)."""
    if len(boxes) < 4 or not width or not height:
        return None
    top = min(b.y for b in boxes)
    bottom = max(b.y + b.h for b in boxes)
    if bottom - top < height * 0.2:
        return None
    card_h = (bottom - top) / 0.92
    card_w = card_h * 63 / 88
    starts = sorted(b.x for b in boxes)
    ends = sorted(b.x + (b.w or len(b.text) * b.h * 0.6) for b in boxes)
    middle = (starts[len(starts) // 10] + ends[-1 - len(ends) // 10]) / 2
    y0 = top - card_h * 0.035
    box = [(middle - card_w / 2) / width, y0 / height, (middle + card_w / 2) / width, (y0 + card_h) / height]
    return [round(min(1.0, max(0.0, v)), 4) for v in box]


def _is_name(text: str) -> bool:
    if _ASIAN.search(text):
        packed = re.sub(r"\s+", "", text)
        return (len(_ASIAN.findall(packed)) >= 2 and packed not in _NOT_NAMES_ASIAN and not re.search(r"\d", packed)
                and not packed.endswith(("進化", "进化", "진화")))
    letters = re.sub(r"[^A-Za-zÀ-ÿ]", "", text)
    folded = fold(text)
    return (len(letters) >= 3 and folded not in _NOT_NAMES and not folded.startswith(("evolves from", "no "))
            and not re.match(r"^(hp|illus)\b", folded))


def _known_code(word: str, codes: dict[str, list[str]]) -> str | None:
    """The set code a word read next to the number stands for, if any. The
    reader often glues the regulation mark on ('0M1S') or reads 1 as I ('MIS')."""
    word = word.upper().strip(".")
    tries = [word]
    if len(word) > 2 and word[0] in "DEFGHIJ0O":
        tries.append(word[1:])
    tries += [t[:1] + t[1:].replace("I", "1").replace("L", "1") for t in tries]
    for t in tries:
        if t in codes:
            return t
    if len(word) >= 4:  # one letter off: 'CBB84C' -> 'CBB4C'
        close = difflib.get_close_matches(word, [c for c in codes if abs(len(c) - len(word)) <= 1], n=1, cutoff=0.8)
        if close:
            return close[0]
    return None


def read_text(boxes: list[Box], codes: dict[str, list[str]] | None = None) -> Reading:
    """The name, number and set code among the text found on a card photo.
    codes are the set codes to look for when the card prints no language
    after its code (Japanese and Chinese cards); None for English cards."""
    reading = Reading(lines=[b.text for b in sorted(boxes, key=lambda b: (b.y, b.x))])
    if not boxes:
        return reading

    # Bottom line: the number, with the set code and language just left of it.
    # Boxes on the same line are joined, since the reader may split 'POR EN' and '037/088'.
    for b in sorted(boxes, key=lambda b: -b.y):
        line = " ".join(o.text for o in sorted(boxes, key=lambda o: o.x) if abs(o.y - b.y) <= max(b.h, o.h) * 0.8)
        upper = line.upper().replace("★", " ").replace("☆", " ")
        m = _NUMBER.search(upper)
        if m:
            reading.number, reading.total = m.group(1), m.group(2)
            before = upper[:m.start()]
            code = None
            for c in _CODE_LANG.finditer(before):
                code = c
            if code is None:  # glued: 'POREN 037/088'
                g = re.search(r"([A-Z][A-Z0-9]{1,3}?)(" + "|".join(_LANGS) + r")\s*$", before.strip())
                code = g
            if code:
                reading.code, reading.lang = code.group(1), code.group(2)
            elif codes is not None:
                words = list(reversed(re.findall(r"[A-Z0-9.]+", before)))
                reading.code = next((c for c in (_known_code(w, codes) for w in words) if c), None)
                if reading.code is None:
                    # A set we know nothing about yet: keep what looks like a code, for adding the card by hand.
                    reading.code = next((w for w in words if _CODE_LIKE.match(w)), None)
            break
        p = _PROMO.search(upper)
        if p and p.group(1) in SET_CODES:
            reading.code, reading.number = p.group(1), p.group(2)
            reading.lang = next((lang for lang in _LANGS if lang in upper[p.start():p.end()]), None)
            break

    # Name: the biggest words near the top.
    top = min(b.y for b in boxes)
    height = max(b.y + b.h for b in boxes) - top
    names = [b for b in boxes if b.y - top <= height * 0.2 and _is_name(b.text)]
    if names:
        best = max(names, key=lambda b: b.h)
        reading.name = re.sub(r"\s+", " ", best.text).strip()
        after = [b for b in boxes if b.x > best.x and abs(b.y - best.y) <= best.h * 0.5 and b.text.strip() in _NAME_SUFFIXES]
        if after and _ASIAN.search(reading.name):
            reading.name += min(after, key=lambda b: b.x).text.strip()
    return reading


# --- matching ---------------------------------------------------------------

def _similar(a: str | None, b: str | None) -> float:
    a, b = fold(a or "").replace(" ", ""), fold(b or "").replace(" ", "")
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) >= 4 and (a in b or b in a):
        return 0.9
    return difflib.SequenceMatcher(None, a, b).ratio()


def _close_codes(code: str, codes: dict[str, list[str]]) -> list[str]:
    """Known codes one letter off from what was read ('CR' -> 'CRI', 'CRE', ...)."""
    out = []
    for known in codes:
        base = known.split(":")[0]
        if base != known:
            continue
        if abs(len(base) - len(code)) <= 1 and difflib.SequenceMatcher(None, base, code).ratio() >= 0.66:
            out.append(known)
    return out


def _official(s: dict):
    if "official" in s:
        return s.get("official")
    return (s.get("cardCount") or {}).get("official")


def set_codes_for(sets: list[dict]) -> dict[str, list[str]]:
    """Printed set code -> set ids for a language whose TCGdex set ids are the
    printed codes (Japanese 'M1S', Chinese 'CSV7C')."""
    out: dict[str, list[str]] = {}
    for s in sets:
        out.setdefault(s["id"].upper(), []).append(s["id"])
    return out


def candidate_sets(reading: Reading, sets: list[dict], codes: dict[str, list[str]] | None = None) -> tuple[list[str], bool]:
    """TCGdex set ids the card could be from, and whether the set code was read exactly."""
    codes = SET_CODES if codes is None else codes
    by_id = {s["id"]: s for s in sets}
    total = int(reading.total) if reading.total and reading.total.isdigit() else None
    exact = bool(reading.code and reading.code in codes)
    tried = [reading.code] if exact else (_close_codes(reading.code, codes) if reading.code else [])
    ids: list[str] = []
    for code in tried:
        ids += codes.get(code, [])
        ids += [i for k, v in codes.items() if k.startswith(code + ":") for i in v]  # galleries
    ids = [i for i in dict.fromkeys(ids)]
    if total is not None:
        same_total = [s["id"] for s in sets if _official(s) == total]
        newest_first = list(reversed(same_total))
        if ids:
            narrowed = [i for i in ids if i in same_total or (i in by_id and _official(by_id[i]) is None)]
            if narrowed:
                ids = narrowed
            elif exact:
                exact = False  # trust the code over a total that may be misread ('037/8')
            else:
                ids = newest_first + ids  # a code one letter off that doesn't fit: go by the total
        else:
            ids = newest_first
    return ids, exact


def match(reading: Reading, sets: list[dict], cards_of, search_name=None, limit: int = 3,
          codes: dict[str, list[str]] | None = None) -> list[dict]:
    """Up to `limit` guesses for a reading, best first. sets is TCGdex's set
    list in the card's language; cards_of(set_id) gives a set's cards as dicts
    with id, number, name and image; search_name(name) finds cards by name
    across all sets (same dicts plus set_id). codes maps printed set codes to
    set ids (English codes when None). Each guess has set_id, id, number,
    name, image, score, sure."""
    set_ids, exact = candidate_sets(reading, sets, codes)
    names = {s["id"]: s["name"] for s in sets}
    scored = []
    for set_id in set_ids[:12]:
        for card in cards_of(set_id):
            number_ok = bool(reading.number) and same_number(card["number"], reading.number)
            name_score = _similar(reading.name, card["name"])
            if not number_ok and name_score < 0.8:
                continue
            score = (3 if number_ok else 0) + 2 * name_score + (1 if exact else 0)
            scored.append(dict(card, set_id=set_id, set_name=names.get(set_id, set_id), score=round(score, 2),
                               name_score=name_score, sure=bool(exact and number_ok and name_score >= 0.8)))
    if not any(g["score"] >= 4 for g in scored) and reading.name and search_name is not None:
        for card in search_name(reading.name):
            number_ok = bool(reading.number) and same_number(card["number"], reading.number)
            name_score = _similar(reading.name, card["name"])
            if name_score < 0.8:
                continue
            scored.append(dict(card, set_name=names.get(card["set_id"], card["set_id"]),
                               score=round((3 if number_ok else 0) + 2 * name_score, 2), name_score=name_score,
                               sure=False))
    # When the name was read and something has it, cards that only share the number are noise.
    if any(g["name_score"] >= 0.8 for g in scored):
        scored = [g for g in scored if g["name_score"] >= 0.5]
    best: dict[str, dict] = {}
    for g in sorted(scored, key=lambda g: -g["score"]):
        best.setdefault(g["id"], g)
    return list(best.values())[:limit]
