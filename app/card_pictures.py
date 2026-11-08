"""Card pictures (P1-P8): one picture list for every grid, a small saved copy of the picture of
every card you have or chase, same-art twins from other languages, and your scan as the last resort.

The list, best first: the saved picture, TCGdex, TCGplayer (your pasted link's product, then the
product saved from TCGdex), your TCGplayer link's picture, your PriceCharting link's picture, the
same-art twin (labelled), your photo.

The picture check walks that list on the NAS for cards you have or chase, one at a time, and keeps
the first picture that works as a ~400 px JPEG in data/card-pictures/. Grids then load it from the NAS
(/card-pictures/<file>), one request per tile. It runs after something changes (a card added, a link
pasted, a set loaded), with Check for updates, and with the nightly update. Nothing else starts it.
"""

import io
import logging
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from . import clock, db, extras, sites

log = logging.getLogger("dexstash.card_pictures")

FOLDER = db.DB_PATH.parent / "card-pictures"
SIDE = 400                       # the long side of a saved picture, in pixels
RECHECK = timedelta(days=7)      # how often a borrowed or missing picture is looked for again
PAUSE = 0.3                      # seconds between pictures, to be gentle with the picture servers
SCAN = "Your scan"
LANG_ORDER = ("en", "ja", "zh-tw", "zh-cn")  # a twin's language you read most first
VERSION_LABEL = {"en": "English version", "ja": "Japanese version", "zh-tw": "Taiwan version",
                 "zh-cn": "Chinese version"}

LANG_NAMES = {"en": "English", "ja": "Japanese", "zh-tw": "Chinese (Taiwan)", "zh-cn": "Chinese (mainland)", "ko": "Korean"}

# Cards you have or chase (single-card chases).
WANTED_SQL = "SELECT card_id FROM inventory UNION SELECT card_id FROM wants"
# The cards of Pokémon and artists you follow get a saved copy too, checked a few at a time
# in the scanner container (check_followed), so scans and pages come first.
FOLLOWED_SQL = ("SELECT card_id FROM pokemon_cards WHERE how != 'removed' "
                "AND dex_id IN (SELECT dex_id FROM followed_pokemon)")
KEPT_SQL = f"{WANTED_SQL} UNION {FOLLOWED_SQL}"
FOLLOWED_BATCH = 5


def _now() -> str:
    return clock.now()


def _get(card, key):
    try:
        return card[key]
    except (KeyError, IndexError):
        return None


def saved_url(card) -> str | None:
    name = _get(card, "saved_picture")
    return f"/card-pictures/{name}" if name else None


def candidates(card, extra: dict | None) -> list[tuple[str, str]]:
    """Where a card's picture can come from, best first, as (address, label); '' is the card's own."""
    out = []
    if _get(card, "image_url"):
        out.append((card["image_url"], ""))
    product = extras.tcgplayer_id((extra or {}).get("tcgplayer_url")) or _get(card, "tcgplayer_id") \
        or extras._product_id(_get(card, "pricing_json"))
    if product:
        out.append((extras.tcgplayer_picture(product), "TCGplayer picture"))
    found, guess = _get(card, "found_image"), _get(card, "found_image_guess")
    if found and not guess:  # a link the app found: sure ones before the same art in another language
        out.append((found, "Picture from a found link"))
    if _get(card, "twin_picture"):
        out.append((card["twin_picture"], _get(card, "twin_label") or "Same art"))
    jp = limitless(_get(card, "id"))
    if jp:  # Japanese and Taiwan cards: so the picture check saves a copy when TCGdex has none
        out.append((jp, _limitless_label(card["id"])))
    if found and guess:  # a guess comes after it
        out.append((found, "Guess · picture from a found link"))
    if extra and extra.get("photo"):
        out.append((extra["photo"], "Your photo"))
    return _unique(out)


def _unique(pics):
    """Each address once, and none from a site that's off in Data sources, so neither the app nor
    the phone asks it. Saved copies are on this server and always show."""
    seen, out = set(), []
    for url, label in pics:
        if url and url not in seen and sites.allowed(url):
            seen.add(url)
            out.append((url, label))
    return out


def grid(card, extra: dict | None) -> list[tuple[str, str]]:
    """A tile's pictures: the saved copy first, then the list (each tried in turn if one doesn't load)."""
    saved = saved_url(card)
    head = [(saved, _get(card, "saved_from") or "")] if saved else []
    return _unique(head + candidates(card, extra))


_CARD_SQL = ("SELECT id, image_url, pricing_json, tcgplayer_id, twin_picture, twin_label, saved_picture, saved_from, "
             "found_image, found_image_guess "
             "FROM cards WHERE id = ?")


_UNSET = object()


def for_id(conn: sqlite3.Connection, card_id: str | None, image_url: str | None = None,
           extra=_UNSET) -> list[tuple[str, str]]:
    """Every grid and list: what's stored for the card, else the picture it was given; then
    whatever else is known here without asking anyone: the same card in the other language of the
    Japanese/Taiwan pair, the English version found for it, and a picture looked up once. Each is tried in
    turn if one doesn't load; with none, the tile shows the Pokémon's sprite. extra: the card's extras when the
    page already loaded them all (None for none), so a grid doesn't ask again for each tile."""
    card = conn.execute(_CARD_SQL, (card_id,)).fetchone() if card_id else None
    if card is None:
        pics = [(image_url, "")] if image_url else []
        kept = conn.execute("SELECT saved_picture, saved_from FROM followed_pictures WHERE card_id = ? AND saved_picture IS NOT NULL",
                            (card_id,)).fetchone() if card_id else None
        if kept:  # A followed card's saved copy comes first
            pics = [(f"/card-pictures/{kept['saved_picture']}", kept["saved_from"] or "")] + pics
    else:
        pics = grid(card, extras.get(conn, card_id) if extra is _UNSET else extra)
        if image_url and not pics:
            pics = [(image_url, "")]
    # Searches and navigation come first: a stored card that has a picture skips the extra look.
    return _unique(pics + more(conn, card_id)) if card_id and (card is None or not pics) else pics


# --- Japanese and Taiwan pictures from Limitless TCG ---------------------------

LIMITLESS = "https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/tpc/{set}/{set}_{n}_R_JP_{size}.png"
LIMITLESS_LANGS = ("ja", "zh-tw")  # Taiwan sets use the Japanese set codes and numbers


def limitless(card_id: str | None, size: str = "SM") -> str | None:
    """The Limitless TCG picture of a Japanese or Taiwan card, built from its TCGdex set id and number
    without leading zeros (SV8-001 -> SV8/SV8_1). No network; a miss falls through to the next picture.
    None for other languages and for numbers with letters."""
    if not card_id or ":" not in card_id:
        return None
    lang, tcgdex_id = card_id.split(":", 1)
    set_code, _, num = tcgdex_id.rpartition("-")
    if lang not in LIMITLESS_LANGS or not set_code or not num.isdigit() or not set_code.replace("-", "").isalnum():
        return None
    return LIMITLESS.format(set=set_code, n=int(num), size=size)


def is_limitless(url: str | None) -> bool:
    return bool(url) and url.startswith(LIMITLESS.split("{")[0])


def known(pics: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The pictures that aren't a Limitless address: Limitless is built, not checked, so a Japanese card
    still gets its one-off picture lookup when nothing else is known."""
    return [p for p in pics if not is_limitless(p[0])]


def _limitless_label(card_id: str) -> str:
    return "" if card_id.startswith("ja:") else VERSION_LABEL["ja"]


def big_url(pics: list[tuple[str, str]]) -> str | None:
    """The sharpest picture for full screen: the first one on the web, in its large size (TCGdex's high.webp,
    Limitless's LG), since a saved copy is kept small. Else the first picture."""
    for url, _label in pics:
        if url.startswith("https://"):
            return large([(url.replace("/low.webp", "/high.webp"), "")])[0][0]
    return pics[0][0] if pics else None


def large(pics: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The card page shows the big Limitless picture (LG) instead of the tile size (SM)."""
    return [(url.replace("_R_JP_SM.png", "_R_JP_LG.png") if is_limitless(url) else url, label)
            for url, label in pics]


# --- pictures wherever we have one ------------------------------------------------

PAIR = {"ja": "zh-tw", "zh-tw": "ja", "zh-cn": "ja"}  # same TCGdex set and number, same picture


def _dataset_card(conn, card_id: str) -> bool:
    """A mainland card from PTCG-CHS-Datasets (its own /chs-img picture, its own set codes): no Japanese pair."""
    row = conn.execute("SELECT source FROM cards WHERE id = ?", (card_id,)).fetchone()
    return bool(row and row["source"])
MISS_DAYS = 14


def _split(card_id: str) -> tuple[str, str]:
    lang, _, tcgdex_id = card_id.partition(":") if ":" in card_id else ("en", "", card_id)
    return lang, tcgdex_id


def more(conn: sqlite3.Connection, card_id: str) -> list[tuple[str, str]]:
    """Pictures for a card from what's already here (no network): the other language of its pair (stored,
    or in the saved TCGdex replies), a picture looked up once, the Limitless picture (JA/TW)."""
    from . import tcgdex
    out = []
    lang, tcgdex_id = _split(card_id)
    other = PAIR.get(lang)
    if lang == "zh-cn" and _dataset_card(conn, card_id):
        other = None
    if other:
        row = conn.execute("SELECT image_url FROM cards WHERE id = ?", (f"{other}:{tcgdex_id}",)).fetchone()
        url = row["image_url"] if row else None
        if not url:
            body = tcgdex.DISK.get(f"{other}/cards/{tcgdex_id}")
            url = tcgdex.image_url((body or {}).get("image"))
        if url:
            out.append((url, VERSION_LABEL[other]))
    # The English version found for a card no longer lends its picture.
    row = conn.execute("SELECT url, source FROM picture_lookups WHERE card_id = ? AND found = 1", (card_id,)).fetchone()
    if row:
        out.append((row["url"], row["source"] or ""))
    jp = limitless(card_id) or (limitless(f"ja:{tcgdex_id}") if lang == "zh-cn" and other else None)
    if jp:
        out.append((jp, _limitless_label(card_id) if lang != "zh-cn" else VERSION_LABEL["ja"]))
    return sites.keep(out)


def ask(conn: sqlite3.Connection, card_ids, failed: bool = False) -> int:
    """Look these cards' pictures up once, in the background (scanner), if nothing here has one. Never for a
    whole results list: a card page or preview asks for its card, a scan for its few suggestions."""
    asked = 0
    retry = clock.since(timedelta(days=MISS_DAYS))
    for card_id in card_ids:
        if not card_id or (not failed and known(for_id(conn, card_id))):
            continue  # failed: the tile tried every picture known here and none loaded
        row = conn.execute("SELECT looked_at, found FROM picture_lookups WHERE card_id = ?", (card_id,)).fetchone()
        if row and (row["looked_at"] is None or row["found"] or row["looked_at"] > retry):
            continue  # waiting, found, or missed lately
        conn.execute("INSERT OR REPLACE INTO picture_lookups (card_id, asked_at) VALUES (?, ?)", (card_id, _now()))
        asked += 1
    if asked:
        conn.commit()
        on_ask()
    return asked


def waiting(conn: sqlite3.Connection, card_id: str) -> bool:
    return conn.execute("SELECT 1 FROM picture_lookups WHERE card_id = ? AND looked_at IS NULL", (card_id,)).fetchone() is not None


def lookup(conn: sqlite3.Connection, card_id: str, api_for, english_of, get=None, pause: float = 2.0,
           sleep=time.sleep) -> tuple[str, str] | None:
    """One card's picture, from the first that has it: TCGdex in the other language of its pair, then
    TCGplayer's product picture from TCGCSV's product list (no page is read). english_of(name, lang)
    is the English name to match with, or None. Saves the result. Commits."""
    from . import tcgdex
    lang, tcgdex_id = _split(card_id)
    found = None
    detail = None
    try:
        detail = api_for(lang).get_card_cached(tcgdex_id)
    except Exception as e:
        log.info("picture lookup %s: no TCGdex detail: %s", card_id, e)
    if detail and detail.get("image"):
        found = (tcgdex.image_url(detail["image"]), "")
    other = PAIR.get(lang)
    if lang == "zh-cn" and _dataset_card(conn, card_id):
        other = None
    if found is None and other:
        try:
            body = api_for(other).get_card_cached(tcgdex_id)
            if body.get("image"):
                found = (tcgdex.image_url(body["image"]), VERSION_LABEL[other])
        except Exception as e:
            log.info("picture lookup %s in %s: %s", card_id, other, e)
    english = english_of(detail.get("name"), lang) if detail else None
    if found is None:  # The TCGplayer product of the same number in the matching TCGCSV set
        found = _tcgcsv_picture(conn, card_id, lang, tcgdex_id, detail, english)
    conn.execute("UPDATE picture_lookups SET url = ?, source = ?, found = ?, looked_at = ? WHERE card_id = ?",
                 (found[0] if found else None, found[1] if found else None, int(bool(found)), _now(), card_id))
    conn.commit()
    return found


def _tcgcsv_picture(conn, card_id: str, lang: str, tcgdex_id: str, detail: dict | None, english: str | None) -> tuple[str, str] | None:
    """A card TCGdex has no picture for (trainer kits, promos, Trainer Gallery…) often has one on
    TCGplayer. TCGCSV lists every product of a set with its picture, read once a week per set. English and
    Japanese sets only, and only a set that matches for sure; the product id is kept too."""
    from . import settings, tcgcsv
    if lang not in tcgcsv.CATEGORIES or not settings.on("TCGCSV"):
        return None
    set_code, _, number = tcgdex_id.rpartition("-")
    set_name = ((detail or {}).get("set") or {}).get("name")
    number = str((detail or {}).get("localId") or number)
    fetch = tcgcsv.fetch  # JSON from tcgcsv.com only (not the finder's page reader)
    try:
        tcgcsv.ensure_groups(conn, fetch)
        group = tcgcsv.group_for(conn, lang, set_code, set_name)
        if group is None:
            return None
        tcgcsv.read_group(conn, *group, get=fetch)
        product = tcgcsv.product_for(conn, group[1], number, english or (detail or {}).get("name"))
    except Exception as e:
        log.info("picture lookup %s on TCGCSV: %s", card_id, e)
        return None
    if product is None:
        return None
    conn.execute("UPDATE cards SET tcgplayer_id = ? WHERE id = ? AND tcgplayer_id IS NULL", (product["product_id"], card_id))
    image = _tcgplayer_image(product["image_url"]) or extras.tcgplayer_picture(product["product_id"])
    return (image, "TCGplayer picture") if image else None


_TCGPLAYER_IMAGE_HOSTS = {"tcgplayer-cdn.tcgplayer.com", "product-images.tcgplayer.com"}


def _tcgplayer_image(url: str | None) -> str | None:
    """url when it is an https picture on TCGplayer's own picture hosts, else None."""
    parts = urlparse(url or "")
    host = (parts.hostname or "").lower()
    return url if parts.scheme == "https" and host in _TCGPLAYER_IMAGE_HOSTS and not parts.username else None


on_ask = lambda: None  # noqa: E731  (set by whoever runs the LookupWorker: it's poked when a card is asked)


class LookupWorker:
    """Looks pictures up for cards that have none, one card at a time, a request every 2 seconds at most: a job
    ('picture-lookups'), quick, so it runs between a long job's steps (a picture on your screen shouldn't wait)."""
    KEY = "picture-lookups"

    def __init__(self, api_for, english_of, get=None, pause: float = 2.0, enabled: bool = True, jobs=None):
        self.api_for, self.english_of, self.get, self.pause, self.enabled = api_for, english_of, get, pause, enabled
        self.jobs = jobs

    def poke(self, wait: bool = False) -> None:
        if not self.enabled:
            return
        if self.jobs is None:  # on its own (a test)
            conn = db.connect()
            try:
                self.run(conn)
            finally:
                conn.close()
            return
        self.jobs.start(self.KEY, "Looking up missing card pictures", lambda conn, job: self.run(conn, job),
                        priority=1, again=True, quick=True, wait=wait)

    def run(self, conn, job=None) -> str:
        looked = 0
        while due := lookups_due(conn):
            for card_id in due:
                try:
                    lookup(conn, card_id, self.api_for, self.english_of, self.get, self.pause)
                except Exception:  # One card's error doesn't stop the queue; it counts as looked at
                    log.exception("picture lookup for %s failed", card_id)
                    conn.rollback() if conn.in_transaction else None
                    conn.execute("UPDATE picture_lookups SET found = 0, looked_at = ? WHERE card_id = ?", (_now(), card_id))
                    conn.commit()
                looked += 1
                if job is not None:
                    job.progress(looked)
        return f"Looked up {looked} picture{'' if looked == 1 else 's'}."


def lookups_due(conn: sqlite3.Connection, limit: int = 20) -> list[str]:
    return [r[0] for r in conn.execute("SELECT card_id FROM picture_lookups WHERE looked_at IS NULL "
                                       "ORDER BY asked_at LIMIT ?", (limit,))]


# --- same art in another language ----------------------------------------------

def refresh_twins(conn: sqlite3.Connection) -> int:
    """Work out each card's same-art twins from links the app can prove, and save the picture of the best
    one for cards that have none of their own: Japanese, Taiwan and TCGdex's Chinese cards with the same
    TCGdex set and number (never a mainland card's English version). Chains of these count. Never a guess. Returns how many cards got a twin picture."""
    rows = conn.execute("""SELECT c.id, c.set_id, c.number, c.sort_number, c.image_url, c.source, c.twin_code,
                                  c.twin_number, c.hand_added_by, c.twin_picture, s.language
                           FROM cards c JOIN sets s ON s.set_id = c.set_id""").fetchall()
    parent = {r["id"]: r["id"] for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def join(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    same_print = {}
    for r in rows:
        if r["source"] is None and r["hand_added_by"] is None and r["language"] in ("ja", "zh-tw", "zh-cn"):
            key = r["id"].split(":", 1)[1] if ":" in r["id"] else r["id"]
            if key in same_print:
                join(r["id"], same_print[key])
            else:
                same_print[key] = r["id"]
        # A mainland card's English version (PTCG-card-data) no longer lends its picture.
    groups = {}
    for r in rows:
        groups.setdefault(find(r["id"]), []).append(r)
    rank = {lang: i for i, lang in enumerate(LANG_ORDER)}
    updates = []
    for members in groups.values():
        pictured = sorted((m for m in members if m["image_url"]), key=lambda m: rank.get(m["language"], 9))
        for m in members:
            if m["image_url"]:
                continue
            twin = next((p for p in pictured if p["id"] != m["id"]), None)
            picture = twin["image_url"] if twin else None
            if picture != m["twin_picture"]:
                updates.append((picture, VERSION_LABEL.get(twin["language"], "Same art") if twin else None, m["id"]))
    with db.transaction(conn):
        conn.execute("UPDATE cards SET twin_picture = NULL, twin_label = NULL WHERE twin_picture IS NOT NULL AND image_url IS NOT NULL")
        # A changed twin is looked at again by the picture check.
        conn.executemany("UPDATE cards SET twin_picture = ?, twin_label = ?, picture_checked_at = NULL WHERE id = ?", updates)
    return sum(1 for u in updates if u[0])


# --- the picture check (P3, P5, P6) -------------------------------------------------

def _fetch(url: str) -> bytes:
    with sites.client(timeout=20, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


def _load(url: str, fetch) -> bytes | None:
    """The bytes behind a picture address: our own files are read on the NAS, the rest downloaded."""
    if url.startswith("/card-photos/"):
        from . import handmade
        path = handmade.photo_path(url.rsplit("/", 1)[1])
        return path.read_bytes() if path else None
    if url.startswith("/chs-img/"):
        from . import chinese
        collection_id, number = url.split("/")[2:4]
        found = chinese.picture(collection_id, number, keep=False)
        return found if isinstance(found, bytes) else Path(found).read_bytes()
    if url.startswith("/card-pictures/"):
        return None
    if "assets.tcgdex.net" in url:
        url = url.replace("/low.webp", "/high.webp")
    if is_limitless(url):  # the big one, shrunk to the saved size
        url = large([(url, "")])[0][0]
    return fetch(url)


def _save(image, folder: Path) -> str:
    image = image.convert("RGB")
    image.thumbnail((SIDE, SIDE))
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.jpg"
    tmp = folder / f"{name}.tmp"
    image.save(tmp, "JPEG", quality=82)
    tmp.replace(folder / name)
    return name


def _drop(name: str | None, folder: Path) -> None:
    if name:
        (folder / name).unlink(missing_ok=True)


def check(conn: sqlite3.Connection, card_id: str, fetch=_fetch, folder: Path | None = None) -> str | None:
    """Keep the first picture of the list that works. Returns its label ('' for the card's own),
    or None when none worked (a saved scan stays). Commits."""
    from PIL import Image
    folder = folder or FOLDER
    card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    if card is None:
        return None
    for url, label in candidates(card, extras.get(conn, card_id)):
        if url == card["saved_src"] and card["saved_picture"] and (folder / card["saved_picture"]).exists():
            conn.execute("UPDATE cards SET picture_checked_at = ? WHERE id = ?", (_now(), card_id))
            conn.commit()
            return label
        try:
            data = _load(url, fetch)
            if not data:
                continue
            with Image.open(io.BytesIO(data)) as image:
                name = _save(image, folder)
        except Exception as e:
            log.info("picture %s for %s didn't work: %s", url, card_id, e)
            continue
        conn.execute("UPDATE cards SET saved_picture = ?, saved_from = ?, saved_src = ?, picture_checked_at = ? WHERE id = ?",
                     (name, label, url, _now(), card_id))
        conn.commit()
        _drop(card["saved_picture"], folder)  # the scan or an older picture it replaces
        return label
    if card["saved_from"] != SCAN:  # nothing works any more: forget the old copy, keep a scan
        _drop(card["saved_picture"], folder)
        conn.execute("UPDATE cards SET saved_picture = NULL, saved_from = NULL, saved_src = NULL WHERE id = ?", (card_id,))
    conn.execute("UPDATE cards SET picture_checked_at = ? WHERE id = ?", (_now(), card_id))
    conn.commit()
    return None


def followed_due(conn: sqlite3.Connection, limit: int = FOLLOWED_BATCH, now: datetime | None = None) -> list[str]:
    """Followed cards to check next: never checked first, then ones with no picture not
    checked for a week. Cards you have or chase are the app's own picture check's."""
    week_ago = clock.since(RECHECK, now)
    stored = [r[0] for r in conn.execute(
        f"""SELECT id FROM cards WHERE id IN ({FOLLOWED_SQL}) AND id NOT IN ({WANTED_SQL})
            AND (picture_checked_at IS NULL OR (saved_picture IS NULL AND picture_checked_at < ?))
            ORDER BY picture_checked_at IS NOT NULL, picture_checked_at LIMIT ?""", (week_ago, limit))]
    if len(stored) >= limit:
        return stored
    other = [r[0] for r in conn.execute(
        f"""SELECT DISTINCT p.card_id FROM pokemon_cards p LEFT JOIN followed_pictures f ON f.card_id = p.card_id
            WHERE p.card_id IN ({FOLLOWED_SQL}) AND p.card_id NOT IN (SELECT id FROM cards)
            AND (f.card_id IS NULL OR (f.saved_picture IS NULL AND f.checked_at < ?))
            ORDER BY f.checked_at IS NOT NULL, f.checked_at LIMIT ?""", (week_ago, limit - len(stored)))]
    return stored + other


def check_followed(conn: sqlite3.Connection, card_id: str, fetch=_fetch, folder: Path | None = None) -> str | None:
    """Keep the first picture that works for a followed card. A stored card uses the usual check; one that's
    only on a Pokémon's or artist's page keeps its copy in followed_pictures. Commits."""
    if conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone():
        return check(conn, card_id, fetch, folder)
    from PIL import Image
    folder = folder or FOLDER
    row = conn.execute("SELECT image_url FROM pokemon_cards WHERE card_id = ? AND image_url IS NOT NULL LIMIT 1", (card_id,)).fetchone()
    old = conn.execute("SELECT saved_picture FROM followed_pictures WHERE card_id = ?", (card_id,)).fetchone()
    for url, label in _unique(([(row["image_url"], "")] if row else []) + more(conn, card_id)):
        try:
            data = _load(url, fetch)
            if not data:
                continue
            with Image.open(io.BytesIO(data)) as image:
                name = _save(image, folder)
        except Exception as e:
            log.info("picture %s for %s didn't work: %s", url, card_id, e)
            continue
        if old:
            _drop(old["saved_picture"], folder)
        conn.execute("INSERT OR REPLACE INTO followed_pictures (card_id, saved_picture, saved_from, saved_src, checked_at) "
                     "VALUES (?, ?, ?, ?, ?)", (card_id, name, label, url, _now()))
        conn.commit()
        return label
    conn.execute("INSERT INTO followed_pictures (card_id, checked_at) VALUES (?, ?) "
                 "ON CONFLICT (card_id) DO UPDATE SET checked_at = excluded.checked_at", (card_id, _now()))
    conn.commit()
    return None


def drop_english(conn: sqlite3.Connection, folder: Path | None = None) -> int:
    """Saved copies that came from a card's English version go (the next check looks again)."""
    folder = folder or FOLDER
    rows = conn.execute("SELECT id, saved_picture FROM cards WHERE saved_from = 'English version'").fetchall()
    for _, name in rows:
        _drop(name, folder)
    conn.executemany("UPDATE cards SET saved_picture = NULL, saved_from = NULL, saved_src = NULL, picture_checked_at = NULL "
                     "WHERE id = ?", [(r[0],) for r in rows])
    conn.commit()
    return len(rows)


def picture_score(conn: sqlite3.Connection, collection_id: int) -> dict:
    """Of the cards you have or chase, how many have any picture (saved copy, TCGdex, TCGplayer,
    links, twins, Limitless, a lookup): {have, total, percent, langs: [(language, have, total)]}."""
    rows = conn.execute("""SELECT c.*, s.language FROM cards c JOIN sets s ON s.set_id = c.set_id
                           WHERE c.id IN (SELECT card_id FROM inventory WHERE user_id = ? UNION
                                          SELECT card_id FROM wants WHERE user_id = ?)""", (collection_id, collection_id)).fetchall()
    extra_map = extras.all_extras(conn)
    langs: dict[str, list[int]] = {}
    for card in rows:
        has = bool(card["saved_picture"] or candidates(card, extra_map.get(card["id"])) or more(conn, card["id"]))
        tally = langs.setdefault(card["language"], [0, 0])
        tally[0] += has
        tally[1] += 1
    have, total = sum(t[0] for t in langs.values()), sum(t[1] for t in langs.values())
    order = {lang: i for i, lang in enumerate(LANG_ORDER)}
    return {"have": have, "total": total, "percent": round(100 * have / total) if total else None,
            "langs": [(lang, t[0], t[1]) for lang, t in sorted(langs.items(), key=lambda kv: order.get(kv[0], 9))]}


def needs_scan(conn: sqlite3.Connection, card_id: str) -> bool:
    """A card with no picture from any source and none saved."""
    card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return card is not None and not card["saved_picture"] and not candidates(card, extras.get(conn, card_id))


def save_scan(conn: sqlite3.Connection, card_id: str, crop, folder: Path | None = None) -> bool:
    """P6: a scan confirmed as a card with no picture from anywhere else keeps the card cut out of the
    photo, at grid size, as that card's picture ("Your scan"). Doesn't commit."""
    folder = folder or FOLDER
    card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    if crop is None or card is None or card["saved_picture"] or candidates(card, extras.get(conn, card_id)):
        return False
    name = _save(crop, folder)
    conn.execute("UPDATE cards SET saved_picture = ?, saved_from = ?, saved_src = 'scan', picture_checked_at = ? WHERE id = ?",
                 (name, SCAN, _now(), card_id))
    return True


def due(conn: sqlite3.Connection, recheck: bool = False, now: datetime | None = None) -> list[str]:
    """Cards to check: ones you have or chase that were never checked (a new TCGdex picture or twin
    clears the check, so it replaces a borrowed one). With recheck (nightly, Check for updates), also borrowed or
    missing pictures not checked for a week. Picked in the query (E6), not by loading every kept card."""
    sql = f"SELECT id FROM cards WHERE id IN ({WANTED_SQL}) AND (picture_checked_at IS NULL"
    args: list = []
    if recheck:
        sql += " OR ((saved_picture IS NULL OR saved_picture = '' OR COALESCE(saved_from, '') != '') AND picture_checked_at < ?)"
        args.append(clock.since(RECHECK, now))
    return [r[0] for r in conn.execute(sql + ") ORDER BY rowid", args)]


def tidy(conn: sqlite3.Connection, folder: Path | None = None) -> int:
    """Nightly: pictures of cards nobody has, chases or follows any more go. Returns how many."""
    folder = folder or FOLDER
    gone = conn.execute(f"SELECT id, saved_picture FROM cards WHERE saved_picture IS NOT NULL AND id NOT IN ({KEPT_SQL})").fetchall()
    for card_id, name in gone:
        _drop(name, folder)
    conn.executemany("UPDATE cards SET saved_picture = NULL, saved_from = NULL, saved_src = NULL, picture_checked_at = NULL "
                     "WHERE id = ?", [(r[0],) for r in gone])
    unfollowed = conn.execute(f"SELECT card_id, saved_picture FROM followed_pictures WHERE card_id NOT IN ({FOLLOWED_SQL})").fetchall()
    for _, name in unfollowed:
        _drop(name, folder)
    conn.executemany("DELETE FROM followed_pictures WHERE card_id = ?", [(r[0],) for r in unfollowed])
    conn.commit()
    return len(gone) + len(unfollowed)


def missing(conn: sqlite3.Connection, owned_only: bool = True, collection_id: int | None = None) -> list[dict]:
    """P7: cards with no picture at all, with their set, for the Data sources list."""
    where = "WHERE c.id IN (SELECT card_id FROM inventory" + (" WHERE user_id = ?)" if collection_id else ")") \
        if owned_only else ""
    rows = conn.execute(f"""SELECT c.*, s.name AS set_name, s.language FROM cards c JOIN sets s ON s.set_id = c.set_id
                            {where} ORDER BY s.language, s.name, c.sort_number""",
                        (collection_id,) if owned_only and collection_id else ()).fetchall()
    extra_map = extras.all_extras(conn)
    out = []
    for card in rows:
        if card["saved_picture"]:
            continue
        checked_and_failed = card["picture_checked_at"] is not None and owned_only
        if checked_and_failed or not candidates(card, extra_map.get(card["id"])):
            out.append(dict(card))
    return out


class Checker:
    """Saves the pictures of cards you have or chase, one card at a time: a job ('picture-saver', automatic). It's
    asked for when what's kept changed (poke_if_changed), at night (recheck and tidy-up) and for a card whose link or
    photo changed (queue)."""
    KEY = "picture-saver"

    def __init__(self, fetch=_fetch, folder: Path | None = None, enabled: bool = True, jobs=None):
        self.fetch, self.folder, self.enabled, self.jobs = fetch, folder, enabled, jobs
        self._queue: list[str] = []
        self._lock = threading.Lock()
        self._cards_seen = None  # (count, max rowid) when twins were last worked out
        self._kept_seen = None  # kept_rev and the cards' (count, max rowid) when last asked
        self._pending: tuple[bool, bool] | None = None  # (recheck, tidy) asked for since the last plan
        self.checked = 0

    @property
    def running(self) -> bool:
        return self.jobs is not None and self.jobs.running(self.KEY)

    def queue(self, card_ids, wait: bool = False) -> None:
        """Check these cards soon (a link or photo changed: P5)."""
        with self._lock:
            for card_id in card_ids:
                if card_id not in self._queue:
                    self._queue.append(card_id)
        self._start(wait)

    def poke(self, recheck: bool = False, tidy_up: bool = False, wait: bool = False) -> None:
        """Something changed: work out new twins if cards were added, then check the cards that are due."""
        with self._lock:
            before = self._pending or (False, False)
            self._pending = (before[0] or recheck, before[1] or tidy_up)
        self._start(wait)

    def poke_if_changed(self, conn) -> bool:
        """E6: only when your stash, chases or followed Pokémon changed (kept_rev, counted by triggers), or cards were
        added (their twins), since the last look. Cheap enough to ask after every form and every job."""
        if not self.enabled:
            return False
        rev = conn.execute("SELECT (SELECT value FROM app_meta WHERE key = 'kept_rev'), COUNT(*), COALESCE(MAX(rowid), 0) "
                           "FROM cards").fetchone()
        seen = tuple(rev)
        if seen == self._kept_seen:
            return False
        first, self._kept_seen = self._kept_seen is None, seen
        if first and self.jobs is not None:
            return False  # the first look only notes where things are (the nightly run catches up)
        self.poke()
        return True

    def _start(self, wait: bool) -> None:
        if not self.enabled:
            return
        if self.jobs is None:  # on its own (a test)
            conn = db.connect()
            try:
                self.run(conn)
            finally:
                conn.close()
            return
        self.jobs.start(self.KEY, "Saving card pictures", lambda conn, job: self.run(conn, job), priority=1, again=True,
                        wait=wait)

    def _plan(self, conn) -> None:
        with self._lock:
            pending, self._pending = self._pending, None
        if pending is None:
            return
        recheck, tidy_up = pending
        seen = tuple(conn.execute("SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM cards").fetchone())
        if recheck or seen != self._cards_seen:
            refresh_twins(conn)
            self._cards_seen = seen
        if tidy_up:
            tidy(conn, self.folder)
        found = due(conn, recheck)
        with self._lock:
            self._queue += [c for c in found if c not in self._queue]

    def run(self, conn, job=None) -> str:
        checked = 0
        while True:
            self._plan(conn)
            with self._lock:
                if not self._queue and not self._pending:
                    break
                card_id = self._queue.pop(0) if self._queue else None
                left = len(self._queue)
            if card_id is None:
                continue
            try:
                check(conn, card_id, self.fetch, self.folder)
                self.checked += 1
                checked += 1
            except Exception:
                log.exception("picture check for %s failed", card_id)
            if job is not None:
                job.progress(checked, checked + left)
            time.sleep(PAUSE)
        return f"Saved pictures checked for {checked} card{'' if checked == 1 else 's'}."


def drop_orphans(conn: sqlite3.Connection, folder: Path | None = None) -> int:
    """Nightly: saved picture files no card points to (left by a crash or an old bug) are
    deleted. The count is logged first. Returns how many."""
    folder = folder or FOLDER
    if not folder.is_dir():
        return 0
    used = {r[0] for r in conn.execute("SELECT saved_picture FROM cards WHERE saved_picture IS NOT NULL "
                                       "UNION SELECT saved_picture FROM followed_pictures WHERE saved_picture IS NOT NULL")}
    cutoff = time.time() - 3600  # a picture being saved right now isn't in the table yet
    loose = [p for p in folder.iterdir() if p.is_file() and p.name not in used and p.stat().st_mtime < cutoff]
    if loose:
        log.info("card pictures: %d files no card uses; deleting them", len(loose))
    for p in loose:
        p.unlink(missing_ok=True)
    return len(loose)
