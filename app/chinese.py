"""Mainland Simplified Chinese sets (CSV7C, CBB4C...), which TCGdex lists
without cards, all loaded in one go from PTCG-CHS-Datasets
(github.com/duanxr/PTCG-CHS-Datasets), so Find and Scan know every card.

Its terms allow personal, non-commercial use and forbid redistribution, so
the data is only ever downloaded at run time on your own server: it is never
part of the app's code or its repository. The 26 MB file is downloaded for a
load, the sets are kept (about 2 MB) and the file is deleted again.

Pictures are fetched from GitHub when shown and kept only for cards someone has.

Cards get ids of the same shape as cards added by hand ('zh-cn:CSV7C-141'),
so loading a set merges the cards you already added by hand: copies, notes
and your own photos stay. Each number's ★ and ★★ prints become printings.

The English version of each card comes from PTCG-card-data
(github.com/zsjy947/PTCG-card-data, CC0), one small file per set.
"""

import io
import json
import logging
import re
import sqlite3
import threading
from pathlib import Path

from . import db, display, handmade, sites, tags
from .numbers import parse_number

log = logging.getLogger("dexstash.chinese")

LANG = "zh-cn"
SOURCE = "chs"  # cards.source for cards from PTCG-CHS-Datasets
CATALOG_URL = "https://raw.githubusercontent.com/duanxr/PTCG-CHS-Datasets/main/ptcg_chs_infos.json"
IMAGE_BASE = "https://raw.githubusercontent.com/duanxr/PTCG-CHS-Datasets/main/"
TWINS_BASE = "https://raw.githubusercontent.com/zsjy947/PTCG-card-data/master/data/"
CATALOG_TERMS = "Personal, non-commercial use only. Never shared or put in the app's code."
TWINS_TERMS = "CC0 (public domain)"
FOLDER = db.DB_PATH.parent / "datasets"
PICTURES = db.DB_PATH.parent / "card-photos" / "chs"
PICTURE_SIDE = 800
_SET_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,23}$")
_IMAGE = re.compile(r"^img/(\d{1,6})/(\d{1,6})\.png$")


def _get(url: str, timeout: float = 120) -> tuple[bytes, str]:
    """(body, version) for a URL; the version is GitHub's ETag for the file."""
    with sites.client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content, resp.headers.get("etag", "")


def _version(url: str) -> str:
    with sites.client(timeout=30, follow_redirects=True) as client:
        resp = client.head(url)
        resp.raise_for_status()
        return resp.headers.get("etag", "")


# --- the catalogue ------------------------------------------------------------

def download_catalog(fetch=None, folder: Path | None = None) -> tuple[dict, str, int]:
    """Download the catalogue, keep the list of its sets, and delete it again.
    Returns (data, version, size in bytes)."""
    folder = folder or FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    body, version = (fetch or _get)(CATALOG_URL)
    path = folder / "ptcg_chs_infos.json"
    path.write_bytes(body)
    try:
        data = json.loads(body)
        save_index(data, folder)
    finally:
        path.unlink(missing_ok=True)
    return data, version, len(body)


def save_index(data: dict, folder: Path | None = None) -> list[dict]:
    """The sets in the catalogue, newest first, kept for the Load set picker."""
    sets = []
    for c in data.get("collections", []):
        cards = c.get("cards") or []
        if not cards or not _SET_CODE.match(c.get("commodityCode") or ""):
            continue
        sets.append({"code": c["commodityCode"], "name": c.get("name") or c["commodityCode"],
                     "date": c.get("salesDate") or "", "cards": len(cards)})
    sets.sort(key=lambda s: s["date"], reverse=True)
    folder = folder or FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "chs_sets.json").write_text(json.dumps(sets, ensure_ascii=False))
    return sets


def known_sets(folder: Path | None = None) -> list[dict]:
    """Sets seen in the catalogue the last time it was downloaded ([] before that)."""
    path = (folder or FOLDER) / "chs_sets.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return []


def is_known(code: str, folder: Path | None = None) -> bool:
    return any(s["code"].upper() == (code or "").upper() for s in known_sets(folder))


def card_number(printed: str) -> tuple[str, int | None]:
    """'141/204' -> ('141', 204); the Gem Pack style '06 01/07' -> ('0601', 7)."""
    return handmade.split_number((printed or "").replace(" ", ""))


def _cards_of(collection: dict) -> tuple[list[dict], int, bool]:
    """One entry per printed number with its printings, the printed total, and
    whether the set is numbered like the Gem Packs ('06 01/07')."""
    by_number: dict[str, dict] = {}
    totals: dict[int, int] = {}
    gem_style = False
    for entry in collection.get("cards") or []:
        details = entry.get("details") or {}
        printed = details.get("collectionNumber") or ""
        try:
            number, total = card_number(printed)
        except ValueError:
            continue
        gem_style |= " " in printed.strip()
        if total:
            totals[total] = totals.get(total, 0) + 1
        shiny = int(details.get("special_shiny_type") or 0)
        card = by_number.setdefault(number, {"number": number, "printings": set(), "base": None, "shiny": 99})
        card["printings"].add(shiny)
        if shiny < card["shiny"]:
            card["base"], card["shiny"] = entry, shiny
    printed_total = max(totals, key=totals.get) if totals else len(by_number)
    if gem_style:
        printed_total = len(by_number)
    return list(by_number.values()), printed_total, gem_style


def _rarity(entry: dict, shiny: int) -> str | None:
    text = ((entry.get("details") or {}).get("rarityText") or "").strip()
    if shiny:
        text = text.rstrip("★")
    return None if not text or text == "无标记" else text


def load_set(conn: sqlite3.Connection, data: dict, code: str, version: str = "", size: int = 0,
             twins: dict[str, dict] | None = None) -> dict:
    """Store one set from the catalogue, merging cards added by hand.
    Returns {"code", "name", "cards", "merged"}."""
    found = [c for c in data.get("collections", []) if (c.get("commodityCode") or "").upper() == code.upper()]
    if not found:
        raise ValueError(f"{code} isn't in PTCG-CHS-Datasets.")
    collection = max(found, key=lambda c: len(c.get("cards") or []))
    code = collection["commodityCode"]
    cards, printed_total, gem_style = _cards_of(collection)
    if not cards:
        raise ValueError(f"{code} has no cards in PTCG-CHS-Datasets yet.")
    set_id = f"{LANG}:{code}"
    name = collection.get("name") or code
    twins = twins or {}
    merged, auto = 0, {}
    with db.transaction(conn):
        conn.execute(
            """INSERT INTO sets (set_id, language, name, release_date, printed_total, total)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(set_id) DO UPDATE SET name = excluded.name, release_date = excluded.release_date,
                 printed_total = excluded.printed_total, total = excluded.total""",
            (set_id, LANG, name, collection.get("salesDate"), printed_total, len(cards)))
        for card in cards:
            base, number = card["base"], card["number"]
            details = base.get("details") or {}
            card_id = f"{LANG}:{code}-{number}"
            sort_number, is_main = parse_number(number, printed_total)
            if gem_style:
                sort_number, is_main = int(re.sub(r"\D", "", number) or 0), True
            picture_of = _IMAGE.match(base.get("image") or "")
            image_url = f"/chs-img/{picture_of.group(1)}/{picture_of.group(2)}" if picture_of else None
            twin = twins.get(number) or {}
            existing = conn.execute("SELECT hand_added_by FROM cards WHERE id = ?", (card_id,)).fetchone()
            merged += bool(existing and existing["hand_added_by"] is not None)
            auto[card_id] = tags.chs_tags(details, details.get("cardName") or base.get("name") or "")
            conn.execute(
                """INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name, rarity, image_url,
                                      has_normal, has_star, has_star2, source, twin_code, twin_number, twin_name, types)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name = excluded.name, rarity = excluded.rarity,
                     sort_number = excluded.sort_number, is_main_set = excluded.is_main_set,
                     image_url = CASE WHEN cards.image_url LIKE '/card-photos/%' THEN cards.image_url
                                      ELSE excluded.image_url END,
                     has_normal = excluded.has_normal, has_star = excluded.has_star, has_star2 = excluded.has_star2,
                     source = excluded.source, hand_added_by = NULL, twin_code = excluded.twin_code,
                     twin_number = excluded.twin_number, twin_name = excluded.twin_name, types = excluded.types""",
                (card_id, set_id, number, sort_number, int(is_main), details.get("cardName") or base.get("name") or number,
                 _rarity(base, card["shiny"]), image_url, int(0 in card["printings"]), int(1 in card["printings"]),
                 int(2 in card["printings"]), SOURCE, twin.get("setCodeEn") or None,
                 str(twin.get("cardIndexEn") or "") or None, twin.get("nameEn") or None,
                 display.CHS_TYPES.get(str(details.get("attribute") or ""))))
        tags.save_auto(conn, auto)
        record(conn, f"chs:{code}", "chs", f"{code} {name}", CATALOG_URL, CATALOG_TERMS, len(cards), size, version)
        if twins:
            record(conn, f"twins:{code}", "twins", f"{code} English versions", f"{TWINS_BASE}cards/{code}.json",
                   TWINS_TERMS, len(twins) - ("" in twins), 0, twins.get("", {}).get("md5", ""))
    return {"code": code, "name": name, "cards": len(cards), "merged": merged}


def load_all(conn: sqlite3.Connection, job=None, fetch=None, twins_fetch=None, only_changed: bool = False) -> str:
    """Load every set in the catalogue with its English versions. With
    only_changed, nothing is downloaded when the catalogue is the same as last
    time and every set in it is here (the English versions are still checked).
    Returns a summary."""
    if not sites.on("chinese"):
        return "Chinese card datasets are off in Data sources, so nothing was downloaded."
    progress = job.progress if job is not None else (lambda *a, **k: None)
    progress(0, 1, "checking PTCG-CHS-Datasets")
    known = {s["code"] for s in known_sets()}
    # Sets loaded one at a time before are filled in by the next check.
    if only_changed and known and known <= set(loaded_codes(conn)) and not catalog_changed(conn):
        codes, changed = loaded_codes(conn), 0
        for i, code in enumerate(codes):
            progress(i, len(codes), f"English versions: {code}")
            twins = download_twins(code, fetch=twins_fetch)
            if twins:
                changed += update_twins(conn, code, twins)
        progress(len(codes))
        return "Chinese sets are up to date." + (f" English versions updated for {changed} set{'' if changed == 1 else 's'}."
                                                 if changed else "")
    data, version, size = download_catalog(fetch)
    codes = [s["code"] for s in save_index(data)]
    loaded, failed = 0, []
    for i, code in enumerate(codes):
        progress(i, len(codes), f"saving {code}")
        try:
            load_set(conn, data, code, version, size, download_twins(code, fetch=twins_fetch))
            loaded += 1
        except ValueError as e:
            failed.append(code)
            log.info("set %s not loaded: %s", code, e)
    progress(len(codes))
    return f"Loaded {loaded} Chinese sets." + (f" Skipped {len(failed)} with no cards." if failed else "")


def search_local(conn: sqlite3.Connection, number: str | None, total: int | None, name: str | None,
                 chinese_name: str | None = None, code: str | None = None, limit: int = 120) -> list[dict]:
    """Mainland Chinese cards (loaded or added by hand) by number ('141', '0601'),
    printed total, set code, and name: Chinese as printed, its translation, or
    the English version's name."""
    sql = ("SELECT c.*, s.name AS set_name, s.printed_total, s.release_date FROM cards c JOIN sets s ON s.set_id = c.set_id "
           "WHERE s.language = ?")
    args: list = [LANG]
    if code:
        sql += " AND s.set_id = ? COLLATE NOCASE"
        args.append(f"{LANG}:{code}")
    if number:
        n = number.upper().lstrip("0") or "0"
        sql += " AND (UPPER(c.number) = ? OR LTRIM(UPPER(c.number), '0') = ?)"
        args += [number.upper(), n]
    if total:
        sql += " AND s.printed_total = ?"
        args.append(total)
    if name:
        sql += " AND (c.name LIKE ? OR c.twin_name LIKE ?" + (" OR c.name LIKE ?" if chinese_name else "") + ")"
        args += [f"%{name}%", f"%{name}%"] + ([f"%{chinese_name}%"] if chinese_name else [])
    if not (number or name or code):
        return []
    sql += " ORDER BY s.release_date DESC, c.sort_number LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]


def set_codes(conn: sqlite3.Connection) -> set[str]:
    """Codes of the mainland sets stored, upper case."""
    return {r[0].split(":", 1)[1].upper() for r in conn.execute("SELECT set_id FROM sets WHERE language = ?", (LANG,))}


def remove_set(conn: sqlite3.Connection, code: str, folder: Path | None = None) -> int:
    """Forget a loaded set's cards, except ones someone has, wants, pulled or added a link or price to.
    Returns how many cards were kept."""
    set_id = f"{LANG}:{code}"
    in_use = ("SELECT card_id FROM inventory UNION SELECT card_id FROM wants UNION SELECT card_id FROM pulls "
              "UNION SELECT card_id FROM card_extras")
    with db.transaction(conn):
        gone = conn.execute(f"SELECT id, image_url FROM cards WHERE set_id = ? AND source = ? AND id NOT IN ({in_use})",
                            (set_id, SOURCE)).fetchall()
        for card in gone:
            for table in ("notes", "prices", "price_snapshots"):
                conn.execute(f"DELETE FROM {table} WHERE card_id = ?", (card["id"],))
            conn.execute("DELETE FROM cards WHERE id = ?", (card["id"],))
        kept = conn.execute("SELECT COUNT(*) FROM cards WHERE set_id = ?", (set_id,)).fetchone()[0]
        used = conn.execute("SELECT 1 FROM purchases WHERE set_id = ? LIMIT 1", (set_id,)).fetchone()
        if not kept and not used:
            conn.execute("DELETE FROM sets WHERE set_id = ?", (set_id,))
        conn.execute("DELETE FROM datasets WHERE id IN (?, ?)", (f"chs:{code}", f"twins:{code}"))
    for card in gone:
        _drop_picture(card["image_url"], folder)
    return kept


def loaded_codes(conn: sqlite3.Connection) -> list[str]:
    return [r["id"].split(":", 1)[1] for r in conn.execute("SELECT id FROM datasets WHERE kind = 'chs' ORDER BY id")]


def is_loaded(conn: sqlite3.Connection, code: str) -> bool:
    return conn.execute("SELECT 1 FROM datasets WHERE id = ? COLLATE NOCASE", (f"chs:{code}",)).fetchone() is not None


def catalog_changed(conn: sqlite3.Connection, version_of=None) -> bool:
    """Whether PTCG-CHS-Datasets changed since the sets were loaded."""
    latest = (version_of or _version)(CATALOG_URL)
    versions = {r["version"] for r in conn.execute("SELECT version FROM datasets WHERE kind = 'chs'")}
    conn.execute("UPDATE datasets SET checked_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE kind = 'chs'")
    conn.commit()
    return bool(versions) and versions != {latest}


# --- English versions -----------------------------------------------------------

def download_twins(code: str, fetch=None) -> dict[str, dict]:
    """{number: {nameEn, setCodeEn, cardIndexEn}} for a set, from PTCG-card-data,
    plus "" -> {"md5": ...} for checking for updates. {} when it has no file for the set."""
    try:
        body, version = (fetch or _get)(f"{TWINS_BASE}cards/{code}.json")
    except Exception as e:
        log.info("no English versions for %s: %s", code, e)
        return {}
    out: dict[str, dict] = {"": {"md5": version}}
    for row in json.loads(body):
        try:
            number, _ = card_number(str(row.get("cardIndex") or ""))
        except ValueError:
            continue
        if row.get("setCodeEn") and row.get("cardIndexEn"):
            out[number] = {k: row.get(k) for k in ("nameEn", "setCodeEn", "cardIndexEn")}
    return out


def update_twins(conn: sqlite3.Connection, code: str, twins: dict[str, dict]) -> int:
    """Store newer English versions for a loaded set. Returns 1 if they changed, else 0."""
    dataset_id = f"twins:{code}"
    version = twins.get("", {}).get("md5", "")
    row = conn.execute("SELECT version FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    if row is not None and version and row["version"] == version:
        conn.execute("UPDATE datasets SET checked_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?", (dataset_id,))
        conn.commit()
        return 0
    with db.transaction(conn):
        for number, twin in twins.items():
            if number:
                conn.execute("UPDATE cards SET twin_code = ?, twin_number = ?, twin_name = ? WHERE id = ?",
                             (twin.get("setCodeEn"), str(twin.get("cardIndexEn") or ""), twin.get("nameEn"),
                              f"{LANG}:{code}-{number}"))
        record(conn, dataset_id, "twins", f"{code} English versions", f"{TWINS_BASE}cards/{code}.json", TWINS_TERMS,
               len(twins) - ("" in twins), 0, version)
    return 1


def twin_search(card) -> str | None:
    """Find-a-card address for a Chinese card's English version."""
    if not card["twin_code"] or not card["twin_number"]:
        return None
    from urllib.parse import urlencode
    from .scanner import SET_CODES
    query = {"q": f"{card['twin_name'] or ''} {card['twin_number']}".strip(), "lang": "en"}
    sets = SET_CODES.get(card["twin_code"].upper()) or []
    if len(sets) == 1:
        query["set"] = sets[0]
    return "/search?" + urlencode(query)


# --- pictures ---------------------------------------------------------------------

SMALL_SIDE = 400  # pictures of cards nobody has, kept once viewed


def kept_picture(collection_id: str, number: str, keep: bool = True, folder: Path | None = None) -> Path | None:
    """The picture already kept here, if there is one (no download)."""
    if not (collection_id.isdigit() and number.isdigit()):
        return None
    folder = folder or PICTURES
    path = folder / collection_id / f"{number}.jpg"
    if path.exists():
        return path
    small = folder / "small" / collection_id / f"{number}.jpg"
    return small if not keep and small.exists() else None


def picture(collection_id: str, number: str, fetch=None, folder: Path | None = None, keep: bool = True) -> Path | bytes:
    """A card picture from GitHub as a JPEG. With keep (a card someone has) it's kept at 800 px.
    Otherwise a 400 px copy is kept, so a set page loads fast from the second visit."""
    if not (collection_id.isdigit() and number.isdigit()):
        raise ValueError("Not a picture.")
    folder = folder or PICTURES
    path = folder / collection_id / f"{number}.jpg"
    small = folder / "small" / collection_id / f"{number}.jpg"
    if path.exists():
        return path
    if not keep and small.exists():
        return small
    if not sites.on("chinese"):  # Pictures already here still show
        raise sites.Off("chinese")
    # At most DOWNLOADS pictures come from GitHub at once; the rest wait their turn.
    if not _downloads.acquire(timeout=DOWNLOAD_WAIT):
        raise ValueError("Too many pictures loading; try again in a moment.")
    try:
        if path.exists():  # fetched while this one waited
            return path
        if not keep and small.exists():
            return small
        return _download(collection_id, number, path, small, keep, fetch)
    finally:
        _downloads.release()


DOWNLOADS = 4
DOWNLOAD_WAIT = 60
_downloads = threading.BoundedSemaphore(DOWNLOADS)


def _download(collection_id: str, number: str, path: Path, small: Path, keep: bool, fetch) -> Path:
    body, _ = (fetch or _get)(f"{IMAGE_BASE}img/{collection_id}/{number}.png", timeout=30)
    from PIL import Image
    image = Image.open(io.BytesIO(body))
    image.thumbnail((PICTURE_SIDE, PICTURE_SIDE))
    if not keep:
        image.thumbnail((SMALL_SIDE, SMALL_SIDE))
        small.parent.mkdir(parents=True, exist_ok=True)
        tmp = small.with_suffix(".tmp")
        image.convert("RGB").save(tmp, "JPEG", quality=82)
        tmp.replace(small)
        return small
    small.unlink(missing_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    image.convert("RGB").save(tmp, "JPEG", quality=85)
    tmp.replace(path)
    return path


def is_owned_picture(conn: sqlite3.Connection, collection_id: str, number: str) -> bool:
    """Whether someone has the card this picture belongs to."""
    return conn.execute("SELECT 1 FROM cards c WHERE c.image_url = ? AND c.id IN (SELECT card_id FROM inventory) LIMIT 1",
                        (f"/chs-img/{collection_id}/{number}",)).fetchone() is not None


def prune_pictures(conn: sqlite3.Connection, folder: Path | None = None) -> int:
    """Delete kept pictures of cards nobody has. Returns how many went."""
    folder = folder or PICTURES
    if not folder.exists():
        return 0
    owned = {r[0] for r in conn.execute("SELECT image_url FROM cards WHERE image_url LIKE '/chs-img/%' "
                                        "AND id IN (SELECT card_id FROM inventory)")}
    gone = 0
    for p in folder.glob("*/*.jpg"):
        if f"/chs-img/{p.parent.name}/{p.stem}" not in owned:
            p.unlink(missing_ok=True)
            gone += 1
    return gone


def _drop_picture(image_url: str | None, folder: Path | None = None) -> None:
    m = re.fullmatch(r"/chs-img/(\d+)/(\d+)", image_url or "")
    if m:
        ((folder or PICTURES) / m.group(1) / f"{m.group(2)}.jpg").unlink(missing_ok=True)


def pictures_size(folder: Path | None = None) -> tuple[int, int]:
    """(pictures, bytes) kept for Chinese cards."""
    files = [p for p in (folder or PICTURES).rglob("*.jpg")] if (folder or PICTURES).exists() else []
    return len(files), sum(p.stat().st_size for p in files)


def clear_pictures(folder: Path | None = None) -> int:
    """Delete the kept pictures; they're fetched again when shown."""
    count = 0
    if (folder or PICTURES).exists():
        for p in (folder or PICTURES).rglob("*.jpg"):
            p.unlink(missing_ok=True)
            count += 1
    return count


# --- the datasets table ------------------------------------------------------------

def record(conn: sqlite3.Connection, dataset_id: str, kind: str, name: str, source: str, terms: str,
           rows: int, size: int, version: str) -> None:
    conn.execute(
        """INSERT INTO datasets (id, kind, name, source, terms, rows, bytes, version, loaded_at, checked_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
           ON CONFLICT(id) DO UPDATE SET name = excluded.name, source = excluded.source, terms = excluded.terms,
             rows = excluded.rows, bytes = excluded.bytes, version = excluded.version,
             loaded_at = excluded.loaded_at, checked_at = excluded.checked_at""",
        (dataset_id, kind, name, source, terms, rows, size, version))
