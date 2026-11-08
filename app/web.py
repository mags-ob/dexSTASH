"""What every page shares: the database connection, templates, sign-in, the TCGdex clients and the
background workers. app.main builds the FastAPI app around it; the pages are in app/routes/."""
import asyncio
import functools
import inspect
import hashlib
import json
import logging
import math
import re
import os
import secrets
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.concurrency import run_in_threadpool

from . import celebrate, clock, icons, pwa, tile_prices, scanning, settings, sites, spelling, startup, welcome
from . import (auth, backup, catalog, chinese, jobs, collection, db, display, extras, importer, inventory,
               pictures, prices, scans, spending, sync)
from .names import Names
from . import (binders, bulbapedia, card_index, card_pictures, fx, tcgcsv, shared, value as
               worth, chase, describe, evolution, pokeapi, pokemon_words, scanner, tags, tcgdex, themes)
from .tcgdex import LANGUAGES, TCGdex
from . import REPO_URL, __version__


def _secret_key(folder: Path | None = None) -> str:
    """The key that signs sign-ins and shared-binder codes. A key in .env wins; the example value or a short one
    stops the app. With none set, one is made on first start and kept in data/secret_key."""
    key = (settings.get("SECRET_KEY") or "").strip()
    if key:
        if len(key) < 32 or key.startswith("replace-me"):
            raise RuntimeError("DEXSTASH_SECRET_KEY in .env must be a long random value (32+ characters), or empty to "
                               "have one made for you. Make one with: python3 -c \"import secrets; print(secrets.token_hex(32))\"")
        return key
    path = (folder or db.DB_PATH.parent) / "secret_key"
    if path.exists():
        kept = path.read_text().strip()
        if len(kept) >= 32:
            return kept
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    return key


try:  # very large pictures are refused instead of using a lot of memory
    from PIL import Image as _Image
    _Image.MAX_IMAGE_PIXELS = 40_000_000
except ImportError:
    pass

SECRET_KEY = _secret_key()
SESSION_DAYS = 14


def _one_transaction(endpoint):
    """A sync page's writes are its own, saved when it finishes and undone if it
    fails (db.ThreadConnection.request). Async pages run on the event loop, where each statement saves on
    its own; their writes that belong together use db.transaction, with no await inside."""
    if inspect.iscoroutinefunction(endpoint) or getattr(endpoint, "one_transaction", False):
        return endpoint  # (a router's pages are wrapped once, not again when the app adds them)

    @functools.wraps(endpoint)
    def run(*args, **kwargs):
        with conn.request(), tcgdex.page_wait():
            return endpoint(*args, **kwargs)
    run.one_transaction = True
    return run


class _Route(APIRoute):
    def __init__(self, path, endpoint, **kwargs):
        super().__init__(path, _one_transaction(endpoint), **kwargs)


async def _in_thread(fn, *args):
    """An async page's slow part (TCGdex, link checks, shrinking photos) runs in
    a worker thread as one transaction, so the event loop keeps answering everyone else's pages."""
    def run():
        with conn.request(), tcgdex.page_wait():
            return fn(*args)
    return await run_in_threadpool(run)


def _from_elsewhere(request: Request) -> bool:
    """A form sent from another site. The browser's Origin says where a POST came
    from (Referer if there's no Origin); a POST with neither, like a script's, is let through."""
    source = request.headers.get("origin")
    if not source or source == "null":
        source = request.headers.get("referer")
    if not source:
        return False
    sent_from = urlsplit(source).netloc.lower()
    here = {h.strip().lower() for h in (request.headers.get("host", ""), request.headers.get("x-forwarded-host", "")) if h}
    return sent_from not in {h.strip() for x in here for h in x.split(",")}
templates = Jinja2Templates(directory=Path(__file__).with_name("templates"))
_ASSET_VERSIONS: dict[str, str] = {}


def asset(path: str) -> str:
    """A static file's address with its content hash, so it can be cached for good and an update
    is never shown with old styles or scripts."""
    version = _ASSET_VERSIONS.get(path)
    if version is None:
        body = (Path(__file__).with_name("static") / path).read_bytes()
        version = _ASSET_VERSIONS[path] = hashlib.sha256(body).hexdigest()[:10]
    return f"/static/{path}?v={version}"


def asset_version(path: str) -> str:
    """Just the content hash, for addresses outside /static (the manifest follows the home-screen icon)."""
    return asset(path).rsplit("=", 1)[1]


templates.env.globals["asset"] = asset
templates.env.globals["asset_version"] = asset_version
templates.env.globals["splash_links"] = lambda: pwa.splash_links(asset)  # the Home Screen app's launch images
templates.env.globals["site_on"] = sites.on  # Price and picture sites switched on in Data sources
templates.env.globals["other_site"] = extras.other_site
templates.env.globals["big_url"] = card_pictures.big_url  # a card's sharpest picture, for full screen
templates.env.filters["money"] = display.money  # In the chosen currency (fx.py)
templates.env.globals["currency_symbol"] = lambda: fx.symbol(fx.setting()).strip()  # for boxes you type an amount in
templates.env.globals["shown_symbol"] = lambda: fx.symbol().strip()
templates.env.globals["symbol_of"] = fx.symbol
templates.env.globals["currency_shown_name"] = lambda: fx.CURRENCIES[fx.shown()][0].lower()
templates.env.filters["to_usd"] = fx.to_usd
templates.env.globals["condition_labels"] = inventory.CONDITION_LABELS
templates.env.globals["scan_word"] = scans.guess_word  # Very close / Possible for a photo's best guess
templates.env.globals["word_classes"] = scans.LIKENESS_CLASS
templates.env.globals["picture_words"] = scans.picture_words
templates.env.globals["app_version"] = __version__
templates.env.globals["upgrade_finishing"] = lambda: startup.STATE["finishing"]  # slow upgrade work still running
templates.env.globals["repo_url"] = REPO_URL
templates.env.globals["app_name"] = settings.app_name()  # APP_NAME in .env, dexSTASH by default
templates.env.globals["icon"] = icons.icon  # Flat icons instead of emoji
# Every grid and list: one picture list, the saved copy first.
templates.env.globals["card_pics"] = lambda card_id, image_url=None: card_pictures.for_id(conn, card_id, image_url)
templates.env.globals["pictures_for"] = lambda card, extra=card_pictures._UNSET: card_pictures.for_id(  # extra: from the page's extras_map
    conn, card_pictures._get(card, "card_id") or card_pictures._get(card, "id"), card_pictures._get(card, "image_url"), extra)
templates.env.globals["our_card_id"] = lambda card_id, lang="en": (card_id if ":" in (card_id or "") else catalog.local_id(lang or "en", card_id or ""))
templates.env.filters["guess_view"] = lambda g, scan_id: {
    "name": g.get("name"), "number": g.get("number"), "set_name": g.get("set_name"), "image": g.get("image") or "",
    "view": f"/preview/{quote(g.get('lang') or 'en')}/{quote(g['id'])}?scan={scan_id}"}  # a scan guess, for the batch page
# Dates and times show in the app's time zone (display.zone(), region.py); stored values stay UTC.
templates.env.filters["ago"] = display.ago            # 'today', '3 days ago', '12 Oct'
templates.env.filters["when"] = display.when            # 'Oct 10, 2026, 4:00 AM'
templates.env.filters["long_date"] = display.long_date  # 'October 10, 2026'
templates.env.filters["short_date"] = display.short_date  # 'Oct 10, 2026'
templates.env.filters["clock"] = display.clock          # '11:51 AM'
templates.env.filters["when_unix"] = lambda t: display.when(clock.from_unix(t))
templates.env.globals["rarity_of"] = display.rarity
templates.env.globals["card_types"] = display.card_types
templates.env.globals["type_colours"] = display.TYPES
templates.env.globals["type_letters"] = display.TYPE_LETTERS
templates.env.globals["type_glyphs"] = display.TYPE_GLYPHS
templates.env.globals["set_grid_view"] = display.set_grid_view
templates.env.globals["norm_number"] = lambda n: tcgcsv._number(n)
templates.env.globals["qs"] = lambda **kw: urlencode({k: v for k, v in kw.items() if v})  # a query string without the empty parts
templates.env.globals["set_code"] = display.set_code
def _set_label(set_id: str | None, name: str | None) -> str:
    """Round 27: a Japanese or Chinese set's English name with its own (Data sources › Names on Japanese and
    Chinese cards picks English and original, English only or original only). Mainland sets' code-based English
    names (display.set_name) are the fallback."""
    from . import english
    lang = (set_id or "").split(":", 1)[0] if ":" in (set_id or "") else "en"
    if lang not in english.LANGS:
        return display.set_name(set_id, name)
    fallback = display.english_set_name(set_id.split(":", 1)[1]) if lang == "zh-cn" else None
    return english.label(conn, set_id, name, fallback)


templates.env.globals["set_label"] = _set_label
templates.env.globals["set_where"] = display.set_where
templates.env.globals["words_used"] = lambda: pokemon_words.suggestions(conn)
# Search and name boxes: no autocorrect, capitals or spell check. Notes keep them.
templates.env.globals["no_correct"] = Markup('autocorrect="off" autocapitalize="off" spellcheck="false"')
templates.env.globals["describe_colours"] = describe.COLOURS
templates.env.globals["describe_types"] = describe.TYPES

conn = db.ThreadConnection()  # each thread has its own connection; a sync page's writes are one transaction
BACKUPS = db.DB_PATH.parent / "backups"
# Nothing here touches the database: startup.py (from main.py's start-up hook) upgrades it and sets up the rest.
api = TCGdex()  # English
log = logging.getLogger("dexstash")


# The first account. With no accounts, either .env names the first admin, or /setup asks for a
# one-time code that only the server's log shows, so whoever finds a new install first can't take it over.
_setup_code: str | None = None
_setup_lock = threading.Lock()


SETUP_FILE = db.DB_PATH.parent / "setup-code.txt"


def setup_code() -> str | None:
    """The code /setup asks for while there are no accounts (made, and logged, when first needed). It's also in
    data/setup-code.txt, for hosts where the log is hard to reach (PikaPods' file browser); that goes once
    the first account exists."""
    global _setup_code
    with _setup_lock:
        if auth.user_count(conn):
            _setup_code = None
            SETUP_FILE.unlink(missing_ok=True)
        elif _setup_code is None:
            _setup_code = auth.new_setup_code()
            log.warning("Setup code: %s  (open /setup to make the first account)", _setup_code)  # hidden in data/logs
            print(f"\n  dexSTASH setup code: {_setup_code}\n  Open /setup in the app to make the first account.\n",
                  file=sys.stderr, flush=True)  # what docker logs shows
            try:
                SETUP_FILE.parent.mkdir(parents=True, exist_ok=True)
                SETUP_FILE.write_text(f"{_setup_code}\n")
                SETUP_FILE.chmod(0o600)
            except OSError as e:
                log.warning("Couldn't write %s: %s", SETUP_FILE.name, e)
        return _setup_code


def _first_account() -> None:
    if auth.user_count(conn):
        SETUP_FILE.unlink(missing_ok=True)  # left from before, if the app stopped just as the account was made
        return
    username, password = (settings.get("ADMIN_USER") or "").strip(), settings.get("ADMIN_PASSWORD") or ""
    if username and password:
        try:
            auth.create_user(conn, username, password, is_admin=True)
            log.warning("First admin account %r made from DEXSTASH_ADMIN_USER in .env", username)
            return
        except ValueError as e:
            log.error("DEXSTASH_ADMIN_USER / DEXSTASH_ADMIN_PASSWORD not used: %s", e)
    setup_code()


_other_apis: dict[str, TCGdex] = {}
names = Names(db.DB_PATH.parent / "pokemon_species_names.csv")


def _longer_names(text: str) -> list[str]:
    try:
        return names.longer_names(text)
    except Exception:  # the name list isn't downloaded yet: search as before
        return []
lines = evolution.Lines()  # evolution lines, from PokéAPI's species list (downloaded once)
descriptions = bulbapedia.Descriptions(db.DB_PATH.parent / "bulbapedia")  # Bulbapedia's Origin text, never committed
describer = describe.Describe(db.DB_PATH.parent / "describe.db", names.cache_path, origins=lambda: descriptions.load(),
                              own=lambda: pokemon_words.own_and_hidden(conn))


def api_for(lang: str) -> TCGdex:
    if lang not in LANGUAGES:
        raise HTTPException(400, f"Unknown language '{lang}'.")
    if lang == "en":
        return api
    return _other_apis.setdefault(lang, TCGdex(lang))


def chase_api(lang: str) -> TCGdex:
    """A TCGdex client for any language the Pokémon chase lists, not only the ones Find offers."""
    return api_for(lang) if lang in LANGUAGES else _other_apis.setdefault(lang, TCGdex(lang))


def _live_card(card_id: str) -> dict | None:
    """A stored card's live TCGdex data (remembered for an hour), or None for
    cards added by hand or when TCGdex can't be reached."""
    row = conn.execute("SELECT c.hand_added_by, c.source, s.language FROM cards c JOIN sets s ON s.set_id = c.set_id "
                       "WHERE c.id = ?", (card_id,)).fetchone()
    if row is None or row["hand_added_by"] is not None or row["source"] is not None:
        return None
    client = api_for(row["language"])
    try:
        return getattr(client, "get_card_cached", client.get_card)(catalog.tcgdex_set_id(card_id))
    except Exception:
        return None


def _store_price(card_id: str) -> None:
    """Prices are kept for cards you have: work this one's out from what's stored now, then a short run for its
    set in the background (TCGCSV, TCGdex for gaps) prices it straight away, a card from a set never read too.
    The page itself never waits on TCGdex (0.20.1).
    A card from a set the picture index doesn't have yet starts fingerprinting it."""
    _index_new_set(card_id)
    with db.transaction(conn):
        prices.resolve(conn, card_id)  # a new printing: CardTrader, your price, else ≈ another printing
    if conn.execute("SELECT 1 FROM inventory WHERE card_id = ? LIMIT 1", (card_id,)).fetchone() is None:
        return
    row = conn.execute("SELECT set_id FROM cards WHERE id = ? AND hand_added_by IS NULL AND source IS NULL",
                       (card_id,)).fetchone()
    if row is not None and not _priced_today(card_id):
        updater.start_sets([row["set_id"]])


def _price_soon(card_id: str) -> None:
    """A card just kept (a link or price on a card you don't have): a short run for its set, unless it has a TCGplayer
    price already."""
    if conn.execute(f"SELECT 1 FROM prices WHERE card_id = ? AND {prices.OWN_PRICE} LIMIT 1", (card_id,)).fetchone():
        return
    row = conn.execute("SELECT set_id FROM cards WHERE id = ? AND hand_added_by IS NULL AND source IS NULL", (card_id,)).fetchone()
    if row is not None:
        updater.start_sets([row["set_id"]])


def _priced_today(card_id: str) -> bool:
    """Every printing you have of the card has a price fetched in the last day (nothing to fetch again)."""
    return conn.execute(
        """SELECT NOT EXISTS (SELECT 1 FROM inventory i WHERE i.card_id = ? AND NOT EXISTS (
               SELECT 1 FROM price_quotes q WHERE q.card_id = i.card_id AND q.variant IN (i.variant, '')
                 AND q.updated_at >= ?))""", (card_id, clock.since(timedelta(days=1)))).fetchone()[0] == 1



def _field(card, key: str, default=None):
    """A field of a cards row or a preview dict."""
    try:
        return card[key]
    except (KeyError, IndexError, TypeError):
        return default


def _prices_for(our_id: str, card=None, detail: dict | None = None) -> tuple[list[dict], object]:
    """(price rows, TCGdex pricing) for a card, from what's stored only (0.20.1: a page never looks a price up):
    the kept card's prices, else TCGCSV's from the last read of its set (a set with kept cards, or one someone
    browsed). Otherwise none ("Not priced yet"), and the set is read in the background for the next visit."""
    rows = prices.card_prices(conn, our_id) if card is not None and not isinstance(card, dict) else []
    pricing = _field(card, "pricing_json") or (detail or {}).get("pricing")
    if not rows and our_id:  # 0.17.1: TCGCSV's price from the last run (a card in a set you keep cards from)
        rows = [{"variant": v, "price": t["market"], "origin": prices.TCGPLAYER, "updated_at": t["updated_at"],
                 "basis": "tcgcsv", "low": t["low"], "mid": t["mid"]} for v, t in tcgcsv.for_card(conn, our_id).items()]
    if not rows and our_id:
        rows = _browsed_prices(our_id, card, detail)
    return rows, pricing


def _browsed_prices(our_id: str, card=None, detail: dict | None = None) -> list[dict]:
    """TCGCSV's per-printing prices for a card nobody keeps (tcgcsv_product_prices). Round 28: the product TCGdex
    names for it first, in whichever TCGplayer set it was read; else the card's number in its matched set. When
    the set hasn't been read, it is, in the background. Failing both, TCGdex's own TCGplayer price (no request)."""
    lang = our_id.split(":", 1)[0] if ":" in our_id else "en"
    pid = None
    if detail:
        set_id, set_name, number = (detail.get("set") or {}).get("id"), (detail.get("set") or {}).get("name"), detail.get("localId")
        english = detail.get("name")
        pid = prices.product_id(detail.get("pricing"))
    else:
        row = conn.execute("SELECT c.set_id, c.number, c.name, c.english_name, c.tcgplayer_id, s.name AS set_name FROM cards c "
                           "JOIN sets s ON s.set_id = c.set_id WHERE c.id = ?", (our_id,)).fetchone()
        if row is None:
            return []
        set_id, set_name, number = catalog.tcgdex_set_id(row["set_id"]), row["set_name"], row["number"]
        english, pid = row["english_name"] or row["name"], row["tcgplayer_id"]
    if not pid and our_id:
        got = conn.execute("SELECT tcgplayer_id FROM cards WHERE id = ?", (our_id,)).fetchone()
        pid = got[0] if got else None
    out = _product_rows(None, pid) if pid else []
    if not out and set_id and lang in tcgcsv.CATEGORIES:
        group = tcgcsv.group_for(conn, lang, set_id, set_name)
        if group and tcgcsv.known(conn, group[1]):
            if not pid:  # with TCGdex's product, a number in another TCGplayer set is another card
                product = tcgcsv.product_for(conn, group[1], number, english)
                out = _product_rows(group[1], product["product_id"]) if product else []
        elif group or not pid:
            _read_set_later(lang, set_id, set_name)
    if not out and detail and detail.get("pricing"):  # round 28: TCGdex's TCGplayer price, already here
        spans = prices.ranges(detail)
        out = [{"variant": v, "price": price, "origin": prices.TCGPLAYER, "basis": None,
                "low": (spans.get(v) or (None, None))[0], "mid": (spans.get(v) or (None, None))[1], "updated_at": None}
               for v, source, price, _ in prices.extract(detail) if source == "tcgplayer"]
    return out


def _product_rows(group_id: int | None, product_id: int) -> list[dict]:
    sql = "SELECT subtype, market, low FROM tcgcsv_product_prices WHERE product_id = ?"
    out, seen = [], set()
    for r in conn.execute(sql + (" AND group_id = ?" if group_id is not None else ""),
                          (product_id, group_id) if group_id is not None else (product_id,)):
        variant = tcgcsv.SUBTYPES.get((r["subtype"] or "").lower())
        if variant and r["market"] and variant not in seen:
            seen.add(variant)
            out.append({"variant": variant, "price": r["market"], "origin": prices.TCGPLAYER, "basis": "tcgcsv",
                        "low": r["low"], "mid": None, "updated_at": None})
    return out


def _tcgcsv_product(card_id: str) -> int | None:
    """TCGplayer's product id from TCGCSV's open product list, for a card not kept yet."""
    if not card_id:
        return None
    row = conn.execute("SELECT product_id FROM tcgcsv_cards WHERE card_id = ? AND variant = ''", (card_id,)).fetchone()
    return row[0] if row else None


def _links(card, lang: str, pricing, extra: dict | None = None, our_id: str | None = None) -> dict:
    """TCGplayer and PriceCharting links for a card: its own product page or your
    link when there is one, else a search with the English name and number."""
    our_id = our_id or _field(card, "id") or ""
    name = _link_name(_field(card, "name"), our_id if ":" in our_id else lang, _field(card, "twin_name")) or ""
    number, set_name = _field(card, "number") or "", _field(card, "set_name") or ""
    if not set_name.isascii():  # round 27: searches use a Japanese or Chinese set's English name
        from . import english
        set_name = english.labels(conn)["names"].get(_field(card, "set_id") or "") or ""
    set_row = conn.execute("SELECT printed_total FROM sets WHERE set_id = ?", (_field(card, "set_id") or "",)).fetchone()
    total = set_row[0] if set_row else _field(card, "printed_total")
    tcg_url, tcg_exact = prices.tcgplayer_link(pricing, name, number, set_name, lang,
                                               product_id=_field(card, "tcgplayer_id") or _tcgcsv_product(our_id),
                                               printed_total=total)
    if extra and extra.get("tcgplayer_url"):
        tcg_url, tcg_exact = extra["tcgplayer_url"], True
    pc_url = (extra or {}).get("pricecharting_url")
    pc_exact = bool(pc_url)
    pc_url = pc_url or prices.pricecharting_link(name, number, set_name, lang)
    cm_url = (extra or {}).get("other_url") or ""
    cm_exact = "cardmarket.com" in cm_url  # a Cardmarket link you saved (Modify links keeps it as the other link)
    cm_url = cm_url if cm_exact else (prices.cardmarket_link(name) if name else None)
    ebay_url = prices.ebay_link(name, number, set_name, lang, prices.ebay_site(conn), total)
    from . import cardtrader
    ct_page = cardtrader.card_link(conn, our_id) if our_id else None
    return {"tcg_url": tcg_url, "tcg_exact": tcg_exact, "pc_url": pc_url, "pc_exact": pc_exact,
            "cm_url": cm_url, "cm_exact": cm_exact, "ebay_url": ebay_url,
            "ct_url": ct_page or prices.cardtrader_search_link(name, set_name), "ct_exact": bool(ct_page)}


# Background work, one job at a time (jobs.py): the "Running now" box on Data sources lists it.
background = jobs.Jobs(conn, "app", after=lambda: picture_saver.poke_if_changed(conn))
card_index.runner = background
# Prices update when asked (Update prices) and once a night (Nightly), from Data sources.
updater = sync.PriceUpdater(lambda lang: api_for(lang), jobs=background)
# Card pictures: kept on the server for cards you have or chase, looked for after changes.
picture_saver = card_pictures.Checker(enabled=settings.on("PICTURE_CHECK"), jobs=background)
def _after_nightly() -> None:
    try:  # A copy of the database every night, the last 7 kept
        backup.make(conn, BACKUPS, "nightly")
    except Exception:
        log.exception("nightly backup not made")
    picture_saver.poke(recheck=True, tidy_up=True)
    tidy_conn = db.connect()  # Old jobs and loose picture files go
    try:
        jobs.tidy(tidy_conn)
        card_pictures.drop_orphans(tidy_conn)
        prices.prune(tidy_conn)  # only cards someone keeps have stored prices (until round 22, at every start)
        chinese.prune_pictures(tidy_conn)  # only they keep a Chinese card's picture
    except Exception:
        log.exception("nightly tidy didn't finish")
    finally:
        tidy_conn.close()
    rollup_conn = db.connect()  # older price history into weekly and monthly averages, as Data sources says
    try:
        worth.rollup(rollup_conn)
        for (collection_id,) in rollup_conn.execute("SELECT DISTINCT user_id FROM inventory").fetchall():
            worth.series(rollup_conn, collection_id)  # Value over time ready for the morning
    except Exception:
        log.exception("price history not thinned")
    finally:
        rollup_conn.close()


nightly = sync.Nightly(updater, after=_after_nightly, jobs=background)

matcher = importer.Matcher(lambda lang: api_for(lang), jobs=background)


class _InScanner:
    """With the scanner container (DEXSTASH_SCANNER=separate) or its child process (inside), photos are
    read, pictures compared and looked up there; the web app only queues them, and never loads either model.
    With scanning off nothing reads them, and the app doesn't load either model either."""

    loaded = False
    problem = None

    def start(self, wait: bool = False) -> None:
        pass

    def running(self) -> bool:
        return True

    def available(self) -> bool:
        return scanner.Reader.available()

    def unload_if_idle(self, now=None) -> bool:
        return False


# DEXSTASH_SCANNER in .env decides; without it, the in-app switch (Data sources › Scanning) does: off or
# inside, saved in app_meta 'scanning', and changed while the app runs (set_scanning).
SCANNER_LOCKED = bool((settings.get("SCANNER") or "").strip())  # empty counts as unset
SCANNER_MODE = settings.scanner_mode() if SCANNER_LOCKED else "off"  # Off | inside | separate (threads: the tests);
# with no .env setting, the in-app switch is read once the database is ready (read_scanning_switch, at startup)
SCANNER_EXTERNAL = SCANNER_MODE in ("inside", "separate")  # read by app.scan_worker; this app only queues
scanner_child = scanning.Child() if SCANNER_MODE == "inside" else None
light_work = scanning.LightWork(enabled=SCANNER_MODE == "off" and settings.on("PICTURE_CHECK"))
if SCANNER_MODE != "threads":
    scan_worker = picture_worker = picture_encoder = _InScanner()
else:
    scan_worker = scans.Worker(lambda lang: api_for(lang))
    picture_encoder = pictures.Encoder()
    picture_worker = scans.PictureWorker(picture_encoder, ready=lambda: _pictures_ready())
SCANNER_STALE = 60  # seconds without a heartbeat before the scanner counts as off
_scanner_seen: dict = {}


def _scanner_state() -> dict:
    """The scanner: {'mode', 'external', 'on', 'seen' (unix time), 'model_loaded', 'refused'}, read at most every 5 s."""
    if SCANNER_MODE == "off":
        return {"mode": "off", "external": False, "on": False, "seen": None, "model_loaded": False, "refused": None}
    if not SCANNER_EXTERNAL:
        return {"mode": SCANNER_MODE, "external": False, "on": True, "seen": None, "model_loaded": picture_encoder.loaded,
                "refused": None}
    if "at" not in _scanner_seen or _scanner_seen["at"] < time.monotonic() - 5:
        rows = dict(conn.execute("SELECT key, value FROM app_meta WHERE key IN ('scanner_seen', 'scanner_model_loaded')").fetchall())
        seen = clock.to_unix(rows.get("scanner_seen")) or 0
        _scanner_seen.update(at=time.monotonic(), state={
            "mode": SCANNER_MODE, "external": True, "on": time.time() - seen < SCANNER_STALE, "seen": seen or None,
            "model_loaded": rows.get("scanner_model_loaded") == "1", "refused": scanner_child.refused if scanner_child else None})
    return _scanner_seen["state"]


SCANNER_OFF = ("Scanner is off. Photos wait here and are read once it's running again "
               "(on the server: sudo docker compose --profile scanner up -d).")
SCANNING_OFF = ("Reading photos is turned off on this server. Your photos are kept here, and they're read if scanning "
                "is turned on (an admin can turn it on under Account › Data sources › Scanning).")
SCANNING_OFF_ENV = ("Reading photos is turned off on this server. Your photos are kept here, and they're read if scanning "
                    "is turned on (DEXSTASH_SCANNER in .env).")
INSIDE_OFF = "The photo reader isn't running. It starts again by itself; photos wait here until then."


def _scan_problem() -> str | None:
    state = _scanner_state()
    if state["on"]:
        return None
    if state["mode"] == "off":
        return SCANNING_OFF_ENV if SCANNER_LOCKED else SCANNING_OFF
    if state["mode"] == "inside":
        return state["refused"] or INSIDE_OFF
    return SCANNER_OFF


templates.env.globals["scanner_off"] = lambda: not _scanner_state()["on"]
templates.env.globals["got_waiting"] = celebrate.waiting  # A chased card just added (_got.html)
templates.env.globals["scanning_on"] = lambda: SCANNER_MODE != "off"  # No Scan tab or buttons when off
# A picture looked up once for a card that has none (scanner container; here when running inline).
picture_lookups = card_pictures.LookupWorker(
    api_for=lambda lang: api_for(lang),
    english_of=lambda name, lang: (name if lang == "en" else (names.to_english(name) if name else None)),
    enabled=settings.on("PICTURE_CHECK") and not SCANNER_EXTERNAL, jobs=background)
card_pictures.on_ask = picture_lookups.poke


def set_scanning(on: bool, start: bool = True) -> str:
    """The in-app switch, when .env doesn't set DEXSTASH_SCANNER. On runs the photo reader inside the app
    (as DEXSTASH_SCANNER=inside would), off stops it; no restart. start=False: only set up (at startup, the
    start-up hook starts what runs). Returns the mode now."""
    global SCANNER_MODE, SCANNER_EXTERNAL, scanner_child
    if SCANNER_LOCKED:
        raise ValueError("Scanning is set in .env (DEXSTASH_SCANNER), so it's changed there.")
    mode = "inside" if on else "off"
    if on and (short := scanning.too_little_memory()):
        raise ValueError(short)
    conn.execute("INSERT INTO app_meta (key, value) VALUES ('scanning', ?) "
                 "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (mode,))
    if mode == SCANNER_MODE:
        return mode
    SCANNER_MODE, SCANNER_EXTERNAL = mode, mode == "inside"
    if on:
        light_work.stop()
        light_work.enabled = False
        scanner_child = scanning.Child()
        if start:
            scanner_child.start()
    else:
        child, scanner_child = scanner_child, None
        if child is not None:
            threading.Thread(target=child.stop, daemon=True, name="scanner-child-stop").start()
        light_work.enabled = settings.on("PICTURE_CHECK")
        light_work.start()
    picture_lookups.enabled = settings.on("PICTURE_CHECK") and not SCANNER_EXTERNAL
    _scanner_seen.clear()
    if start:
        log.info("scanning turned %s in Data sources", "on (inside the app)" if on else "off")
    return mode


def _pictures_ready() -> bool:
    """Picture search is turned on and its model is here (never with scanning off: nothing would use it)."""
    return SCANNER_MODE != "off" and pictures.is_on(conn) and pictures.available() and pictures.has_model()


def _index_pictures(label: str = "Fingerprinting card pictures") -> bool:
    """Fingerprint the sets Enhanced Search doesn't have yet, in the background."""
    def run(job_conn, job):
        return pictures.index_sets(job_conn, picture_encoder, api_for, job)
    if SCANNER_EXTERNAL:
        started = background.queue("pictures", label, "pictures-index")
    else:
        started = background.start("pictures", label, run)
    if not started:
        pictures.ask_again()
    return started


def _index_new_set(card_id: str) -> None:
    if not _pictures_ready():
        return
    row = conn.execute("SELECT c.set_id, s.language FROM cards c JOIN sets s ON s.set_id = c.set_id WHERE c.id = ?",
                       (card_id,)).fetchone()
    if row and (SCANNER_EXTERNAL or row["set_id"] not in pictures.index_for(row["language"]).sets()):
        _index_pictures()  # the scanner skips sets it has


def read_scanning_switch() -> None:
    """Startup: with no DEXSTASH_SCANNER in .env, the in-app switch (Data sources › Scanning) saved in app_meta."""
    if SCANNER_LOCKED:
        return
    row = conn.execute("SELECT value FROM app_meta WHERE key = 'scanning'").fetchone()
    if (row[0] if row else "off") == "inside" and SCANNER_MODE != "inside":
        set_scanning(True, start=False)


lockout = auth.Lockout()


class LoginRequired(Exception):
    pass


class SetupPending(Exception):
    """The first admin hasn't finished the first-run wizard, so every page but /welcome waits."""


def _sign_in(request: Request, user_id: int) -> None:
    request.session.clear()
    request.session["user_id"] = user_id
    request.session["key"] = auth.session_key(conn, user_id)


def current_user(request: Request) -> dict:
    """The person signed in. user["collection_id"] is whose cards they see and
    change; user["id"] is who they are, for 'added by'. Until the first-run wizard is finished, only the wizard
    opens."""
    user = signed_in(request)
    if not welcome.done(conn) and not request.url.path.startswith(welcome.OPEN_PATHS):
        raise SetupPending()
    return user


def signed_in(request: Request) -> dict:
    """The person signed in, wizard or not."""
    user_id = request.session.get("user_id")
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone() if user_id else None
    # A changed password changes the key, which signs out every other device.
    if row is None or (row["session_key"] and request.session.get("key") != row["session_key"]):
        request.session.clear()
        raise LoginRequired()
    user = dict(row)
    user["collection_id"] = user["collection_id"] or user["id"]
    user["owner"] = _is_owner(user)  # sees Data sources and backups
    return user


def admin_user(user=Depends(current_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(403, "Only an admin can do that.")
    return user

_LANG_MARKS = {"ja": "j", "zh-cn": "c", "zh-tw": "c", "zh": "c", "ko": "k"}


def _card_language(where: str | None) -> str:
    """A card's language from its id ('ja:M1S-054' -> 'ja') or a language code."""
    if not where:
        return "en"
    if ":" in where:
        return where.split(":", 1)[0]
    return where if where in LANGUAGES or where in _LANG_MARKS else "en"


def _en_name(name: str | None, where: str | None = None, twin: str | None = None) -> dict:
    """How a card's name is shown: in English when it can be, with (j)/(c)/(k) for the
    language it's printed in. original is the printed name."""
    lang = _card_language(where)
    if lang == "en" or not name:
        return {"en": name or "", "mark": None, "original": name or "", "translated": False}
    try:  # The Pokémon's English name first; a mainland card's English twin only when that fails
        english = names.to_english(name)
    except Exception:
        english = None
    if not english:
        english = twin
    if english is None and lang.startswith("zh") and where and ":" in where:
        row = conn.execute("SELECT twin_name FROM cards WHERE id = ?", (where,)).fetchone()
        english = row[0] if row else None
    return {"en": english or name, "mark": _LANG_MARKS.get(lang, lang.split("-")[0]), "original": name,
            "translated": bool(english) and english != name}


def _link_name(name: str | None, where: str | None = None, twin: str | None = None) -> str | None:
    """The English name to search TCGplayer and PriceCharting with, or None when there isn't one: a link
    never searches with Japanese or Chinese text. In order: the English twin (Chinese cards),
    the name worked out from the printed one, then a name kept from before (a link found for a Japanese card, or
    typed on Links to confirm)."""
    if _card_language(where) == "en":
        return name or None
    card_id = where if where and ":" in where else None
    row = conn.execute("SELECT twin_name, english_name FROM cards WHERE id = ?", (card_id,)).fetchone() if card_id else None
    english = twin or (row["twin_name"] if row else None)
    if not english and name:
        try:
            english = names.to_english(name)
        except Exception:
            english = None
    if not english and row:
        english = row["english_name"]
    return english if english and prices.latin(english) else None


def _sprite_for(name: str | None) -> str | None:
    """The sprite of the Pokémon a card is named after, to stand in for a missing picture."""
    if not name or not names.available():
        return None
    try:
        species = names.species_id(name) or names.species_id(names.to_english(name) or "")
    except Exception:
        return None
    if species and themes.sprite_path(species, False).exists():
        return f"/sprites/{species}.png"
    return None


def _sprite(species_id: int, shiny: bool):
    path = themes.sprite_path(species_id, shiny)
    if not 1 <= species_id <= themes.SPECIES or not path.exists():
        raise HTTPException(404)
    return _picture_reply(path, {"Cache-Control": "private, max-age=2592000"}, "image/png")


def _species_swatches() -> dict:
    """Each Pokémon's 5 colours, for the strips in the Pokémon sheet."""
    return {r["species_id"]: json.loads(r["swatches"])[:5]
            for r in conn.execute("SELECT species_id, swatches FROM pokemon_palette WHERE shiny = 0")}


def _species_choices() -> list[list]:
    """[number, English name] for every Pokémon, for the Theme search list."""
    try:
        if not names.available():
            names.translate("Pikachu", "ja")  # downloads the list once, here rather than on every page
        return [[i, names.english_name(i)] for i in range(1, themes.SPECIES + 1) if names.english_name(i)]
    except Exception:  # the names list couldn't be downloaded: numbers still work
        return []


def _set_sort(request: Request, user) -> str:
    """Value, Number or Name on set pages and Find's set preview, kept per login."""
    sort = request.query_params.get("sort", "")
    if sort in display.SET_SORTS:
        conn.execute("INSERT INTO user_prefs (user_id, key, value) VALUES (?, 'set_sort', ?) "
                     "ON CONFLICT (user_id, key) DO UPDATE SET value = excluded.value", (user["id"], sort))
        conn.commit()
        return sort
    row = conn.execute("SELECT value FROM user_prefs WHERE user_id = ? AND key = 'set_sort'", (user["id"],)).fetchone()
    return row[0] if row and row[0] in display.SET_SORTS else "value"


_set_reads: set[int] = set()
_set_reads_lock = threading.Lock()
_set_read_turn = threading.Lock()  # one set read at a time (round 27: Find can ask for several at once)


def _read_set_later(lang: str, tcgdex_set_id: str, set_name: str | None) -> None:
    """Read a set's TCGCSV products and prices once, in the background, so its next visit sorts by
    value and Find's tiles fill in. The page never waits on it: one set at a time, behind pages being answered."""
    if lang not in tcgcsv.CATEGORIES or not settings.on("TCGCSV"):
        return
    key = hash((lang, tcgdex_set_id))
    with _set_reads_lock:
        if key in _set_reads:
            return
        _set_reads.add(key)

    def run():
        jobs.mark_background()
        with _set_read_turn:
            _read_set_now(lang, tcgdex_set_id, set_name, key)
    threading.Thread(target=run, daemon=True, name="set-values").start()


def _read_set_now(lang: str, tcgdex_set_id: str, set_name: str | None, key: int) -> None:
    jobs.pause_for_pages()
    job_conn = db.connect()
    try:
        tcgcsv.ensure_groups(job_conn)
        group = tcgcsv.group_for(job_conn, lang, tcgdex_set_id, set_name)
        if group:
            tcgcsv.read_group(job_conn, *group)
    except Exception as e:
        log.warning("TCGCSV prices for set %s not read: %s", tcgdex_set_id, e)
    finally:
        job_conn.close()
        with _set_reads_lock:
            _set_reads.discard(key)


def _set_values(lang: str, tcgdex_set_id: str, set_name: str | None, our_set_id: str | None = None) -> dict[str, float]:
    """Number -> the price each card's tile shows, for sorting a set by value (round 27: the price you see, not
    the dearest printing): a kept card's main printing, else TCGCSV's price list for the whole set. Asks for that
    list in the background when it isn't here yet."""
    out: dict[str, float] = {}
    group = tcgcsv.group_for(conn, lang, tcgdex_set_id, set_name) if lang in tcgcsv.CATEGORIES else None
    if group and tcgcsv.known(conn, group[1]):
        out.update(tile_prices.set_values(conn, group))
    else:
        _read_set_later(lang, tcgdex_set_id, set_name)
    if our_set_id:
        rows = conn.execute("SELECT id, number FROM cards WHERE set_id = ?", (our_set_id,)).fetchall()
        found, _ = tile_prices.for_cards(conn, [{"key": r["number"], "id": r["id"]} for r in rows])  # kept cards only
        for number, price in found.items():
            out[tcgcsv._number(number)] = price
    return out


def _card_view_extras(request: Request, card, lang: str, card_prices: list[dict], offered: list[str],
                      holdings: list[dict], links: dict | None = None, extra: dict | None = None) -> dict:
    """What the shared card view needs beyond the card itself: the printing
    to pick first, a price per printing, links and what you already have."""
    price_map = prices.by_variant(card_prices)
    real = [v for v in price_map if v in prices.real_variants(card_prices)] or prices.listed(_field(card, "pricing_json"))
    extra = extra or {}
    sources = prices.sources(card_prices, extra, (links or {}).get("tcg_url"))
    for h in holdings:
        h["price"] = price_map.get(h["variant"])
        h["source"] = sources.get(h["variant"])
        h["counted"] = h["price"] is not None and prices.share(h["condition"]) > 0
    chips = {r["variant"]: price_chip(r, extra) for r in card_prices}
    for h in holdings:
        h["chip"] = chips.get(h["variant"])
    held = {h["source"]["label"] for h in holdings if h["price"] is not None}
    variant = inventory.default_variant(offered, real, request.session.get("entry_variant"))
    return {
        "variant": variant,
        "price_more": _price_more(card, card_prices, extra, links or {},
                                  next((h["variant"] for h in holdings if h["price"] is not None), holdings[0]["variant"] if holdings else variant)),
        "price_map": price_map, "price_sources": sources, "price_chips": chips,
        "held_source": next(iter(held)) if len(held) == 1 else ("several sources" if held else None),
        **(links or {}),
        "owned_count": sum(h["quantity"] for h in holdings),
        "owned_value": sum(h["quantity"] * (h["price"] or 0) * prices.share(h["condition"]) for h in holdings),
        "condition_labels": inventory.CONDITION_LABELS,
    }


def price_chip(row: dict, extra: dict | None) -> dict:
    """Where a price came from and how old it is, for the chip under it: {'text', 'approx', 'big'}.
    'TCGplayer · 2 days ago', 'CardTrader · today', 'Your price · 12 Oct', '≈ Holofoil price'. With more than one
    source, big is the range and the chip the average. Amounts are the ones saved in the install's currency."""
    origin = row.get("origin")
    if origin == prices.PRINTING:
        return {"text": f"≈ {inventory.VARIANTS.get(row.get('basis') or '', row.get('basis') or '')} price", "approx": True,
                "big": display.saved(row)}
    first = next((f for f in row.get("sources") or () if f.get("origin") == origin), {})
    name, at = prices.source_label(origin), first.get("updated_at") or row.get("updated_at")
    age = display.ago(at) if at else ""
    out = {"text": f"{name} · {age}" if age else name, "approx": False, "big": _range_text(row)}
    count = len(prices.counted(row))
    if count > 1:
        out["text"] = f"Average {display.saved(row)} · {count} sources"
    return out


def _range_text(row: dict) -> str:
    """A printing's range across its sources ('$8.40 – $9.57'), or its one price."""
    low, high = row.get("range_low"), row.get("range_high")
    if low is not None and high is not None and round(low, 2) != round(high, 2):
        return f"{display.saved(row, 'range_low')} – {display.saved(row, 'range_high')}"
    return display.saved(row)


def _source_line(row: dict, f: dict, extra: dict, links: dict, card_id: str | None) -> dict:
    """One line of Every price: the source's name, what it is, its saved amount and date."""
    origin = f["origin"]
    note, url = "", None
    if origin == prices.TCGPLAYER:
        note = "TCGCSV" if f.get("basis") == "tcgcsv" else "TCGdex"
        if f.get("low") or f.get("mid"):  # TCGplayer's own low – mid, saved like the rest
            saved = dict(f, local_currency=row.get("local_currency"))
            low = display.saved(saved, "low") if f.get("low") is not None else "?"
            mid = display.saved(saved, "mid") if f.get("mid") is not None else "?"
            note += f" · range {low} – {mid}"
    elif origin == prices.MANUAL:
        note = "typed by you"
    elif origin == prices.PRICECHARTING:
        note, url = ("last read" if f.get("basis") == "read" else "typed by you"), (extra or {}).get("pricecharting_url")
    elif origin == prices.CARDTRADER:
        note = "lowest listing, any condition" if f.get("basis") == "any" else "Near Mint median"
    if f.get("left_out"):
        note += " · left out, far above the rest"
    url = url or f.get("url")
    return {"name": prices.source_label(origin), "note": note, "url": url, "left_out": bool(f.get("left_out")),
            "amount": display.saved(dict(f, local_currency=row.get("local_currency")), "source"),
            "at": f.get("updated_at")}


def _price_more(card, card_prices: list[dict], extra: dict, links: dict, shown: str | None) -> dict:
    """Every price under the main one: for each printing, each source its range and average were worked out from,
    with the amount saved at the price update and its date. Nothing is fetched or converted here."""
    from . import cardtrader
    card_id = _field(card, "id")
    lines = {r["variant"]: [_source_line(r, f, extra, links, card_id) for f in r.get("sources") or ()]
             for r in card_prices}
    lines = {v: ls for v, ls in lines.items() if ls}
    return {"lines": lines, "shown": shown if shown in lines else next(iter(lines), None),
            "cardtrader": cardtrader.side_line(conn, card_id, shown) if card_id else None,
            "cardtrader_url": cardtrader.card_link(conn, card_id) if card_id else None}


def _came_from_find(request: Request) -> bool:
    """A card opened from Find keeps the Find tab lit."""
    if request.query_params.get("from") == "find":
        return True
    back = request.headers.get("referer", "")
    return back.startswith(str(request.base_url)) and ("/search" in back or "/preview/" in back or "/set-preview/" in back)


def _tcgdex_links(card, lang: str, tcgdex_card_id: str, tcgdex_set: str) -> dict | None:
    """The card's and its set's raw TCGdex entries, for checking what TCGdex has."""
    card = dict(card)
    if card.get("source") or card.get("hand_added_by") is not None:
        return None
    base = f"{tcgdex.BASE_URL}/{lang}"
    return {"card": f"{base}/cards/{quote(tcgdex_card_id)}", "set": f"{base}/sets/{quote(tcgdex_set)}"}


def link_label(site: str, url: str | None = None) -> str:
    """TCGplayer, PriceCharting, or the other site's name."""
    return extras.other_site(url) if site == "other" else LINK_LABELS[site]


LINK_LABELS = {"tcgplayer": "TCGplayer", "pricecharting": "PriceCharting", "cardmarket": "Cardmarket"}


def _save_found_link(our_card_id: str, site: str | None, url: str, user, pc_price: str = "",
                     variant: str = "") -> RedirectResponse:
    """The link pasted after a TCGplayer or PriceCharting search. Whichever site
    the pasted link is for. site is the search that asked; None is Modify links. Saved, never read;
    pc_price is the price you saw on PriceCharting, typed with a PriceCharting link, for the printing
    variant (the one you own when empty)."""
    try:
        saved, error = extras.add_link(conn, our_card_id, user["id"], url)
        if saved == "pricecharting" and (pc_price or "").strip():
            value = _price(pc_price)
            extras.save(conn, our_card_id, user["id"], pc_price=value, variant=variant or None)
    except HTTPException as e:
        return RedirectResponse(f"/cards/{quote(our_card_id)}?extra_error={quote(str(e.detail))}#fix", status_code=303)
    except ValueError as e:
        if site is None:
            return RedirectResponse(f"/cards/{quote(our_card_id)}?extra_error={quote(str(e))}#fix", status_code=303)
        return RedirectResponse(f"/cards/{quote(our_card_id)}?ask={site}&paste_error={quote(str(e))}#links", status_code=303)
    picture_saver.queue([our_card_id])
    _price_soon(our_card_id)  # as Modify does: a card you don't have is kept, so its set is read
    note = f"Saved. {error}" if error else f"Saved the {link_label(saved, url)} link."
    return RedirectResponse(f"/cards/{quote(our_card_id)}?extra_saved={quote(note)}#fix", status_code=303)


def _price(text: str) -> float | None:
    """'' -> None, '$4.50' -> 4.5."""
    text = text.strip().lstrip("$").replace(",", "")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise HTTPException(400, f"'{text}' isn't a price.")
    if not math.isfinite(value):  # "inf", "nan", "1e400"
        raise HTTPException(400, f"'{text}' isn't a price.")
    if value < 0:
        raise HTTPException(400, "A price can't be negative.")
    return value


def _build_describe(job_conn, job, again: bool = False) -> str:
    count = describer.build(again=again)
    return f"Describe a Pokémon is ready ({count} Pokémon)."


def _ensure_card(lang: str, set_id: str, card_id: str) -> str:
    """Our id for a TCGdex card, loading its set first if it's new."""
    our_card_id = catalog.local_id(lang, card_id)
    if conn.execute("SELECT 1 FROM cards WHERE id = ?", (our_card_id,)).fetchone() is None:
        try:
            catalog.cache_set(conn, api_for(lang), set_id)
        except Exception as e:
            raise HTTPException(502, f"Could not load set '{set_id}' from TCGdex: {e}")
        if conn.execute("SELECT 1 FROM cards WHERE id = ?", (our_card_id,)).fetchone() is None:
            raise HTTPException(404, f"Card {card_id} isn't in set {set_id}.")
    return our_card_id


def other_names(q: str) -> tuple[str, ...]:
    """The Pokémon typed, in Japanese, Chinese and Korean (folded), so searching your stash by its English
    name finds those cards too. Nothing when the name list isn't here or no Pokémon is named."""
    if not q.strip() or not names.available():
        return ()
    found = []
    for lang in ("ja", "zh-tw", "zh-cn", "ko"):
        try:
            t = names.translate(q, lang)
        except Exception:
            return ()
        if t and display.fold(t.name) not in found:
            found.append(display.fold(t.name))
    return tuple(found)


def _username(user_id: int | None) -> str | None:
    if user_id is None:
        return None
    row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["username"] if row else "someone"


# Chinese pictures waiting for their turn to download (chinese.DOWNLOADS at a time)
# wait here, on the event loop, so they never hold the worker threads other pages need.
_chs_turns = None


async def _chs_download(collection_id: str, number: str, keep: bool):
    global _chs_turns
    found = chinese.kept_picture(collection_id, number, keep)
    if found is not None:
        return found
    if _chs_turns is None:
        _chs_turns = asyncio.Semaphore(chinese.DOWNLOADS)
    async with _chs_turns:
        return await run_in_threadpool(chinese.picture, collection_id, number, keep=keep)


def _picture_reply(path, headers: dict, media_type: str = "image/jpeg") -> Response:
    """A picture is read whole before anything is sent. A FileResponse sends its headers and then
    opens the file, so a picture deleted or replaced in between (a Chinese picture growing to full size, a
    picture check saving a new copy) broke off the reply ("Response content shorter than Content-Length")."""
    try:
        body = Path(path).read_bytes()
    except FileNotFoundError:
        raise HTTPException(404)
    return Response(body, media_type=media_type, headers=headers)



def _my_cards_filters(q: str, language: str, set: str, rarity: str, method: str, by: int | None, origin: str,
                      price: str, people: list, sets: list[dict], tag: str = "") -> list[tuple[str, str]]:
    """The filters that are on, as (label, parameter) pairs for the chips that clear them."""
    on = []
    if q.strip():
        on.append((f"“{q.strip()}”", "q"))
    if language:
        on.append((collection.LANGUAGE_FILTERS[language], "language"))
    if set:
        on.append((next((x["name"] for x in sets if x["id"] == set), set), "set"))
    if rarity:
        on.append((rarity, "rarity"))
    if method:
        on.append((inventory.METHOD_FILTERS[method], "method"))
    if by:
        on.append(("By " + next((p["username"] for p in people if p["id"] == by), "someone"), "by"))
    if origin:
        on.append((inventory.ORIGINS[origin], "origin"))
    if price:
        on.append((collection.price_ranges()[price][0], "price"))
    if tag:
        on.append((f"#{tag}", "tag"))
    return on


_NOT_FILTERS = {"moved", "done", "clear", "error", "from", "view"}
CARDS_CHUNK = 120  # My Stash loads this many tiles at a time as you scroll
SCROLL_KEPT, SCROLL_FOR = 8, 600  # My Stash lists kept for scrolling (per person, page and filters), and for how long (s)
_scrolls: dict[tuple, tuple[float, list]] = {}
_scrolls_lock = threading.Lock()


def _keep_scroll(key: tuple, rows: list) -> None:
    """The list a My Stash page showed, so scrolling it reads the next tiles from it instead of building and
    sorting the whole collection again. Each page load builds it afresh."""
    with _scrolls_lock:
        _scrolls[key] = (time.monotonic(), rows)
        for old in sorted(_scrolls, key=lambda k: _scrolls[k][0])[:-SCROLL_KEPT]:
            del _scrolls[old]


def _scroll_rows(key: tuple) -> list | None:
    with _scrolls_lock:
        kept = _scrolls.get(key)
    return kept[1] if kept and time.monotonic() - kept[0] < SCROLL_FOR else None


def _cards_data(user, in_binder: dict | None, q: str = "", rarity: str = "", set: str = "", sort: str = "price_desc",
                method: str = "", by: str = "", origin: str = "", price: str = "", language: str = "", tag: str = "",
                binder: str = "", added: str = "") -> tuple[dict, dict, list]:
    """My Stash' rows for these filters (unknown values are ignored), the filters as used, and the binders."""
    sort = sort if sort in collection.SORTS else "price_desc"
    method = method if method in inventory.METHOD_FILTERS else ""
    origin = origin if origin in inventory.ORIGINS else ""
    price = "none" if price == "missing" else (price if price in collection.PRICE_RANGES else "")
    language = language if language in collection.LANGUAGE_FILTERS else ""
    added = added if added in collection.ADDED else ""
    all_binders = binders.all_for(conn, user["collection_id"])
    if in_binder is not None:
        binder = "none" if in_binder["id"] is None else str(in_binder["id"])
    elif binder != "none" and not any(str(b["id"]) == binder for b in all_binders):
        binder = ""
    by_id = int(by) if by.isdigit() else None
    data = collection.cards(conn, user["collection_id"], q=q, rarity=rarity, set_id=set, sort=sort,
                            method=method, by=by_id, origin=origin, price=price, language=language, tag=tag,
                            binder=binder, added=added, also=other_names(q))
    return data, {"sort": sort, "method": method, "origin": origin, "price": price, "language": language,
                  "added": added, "binder": binder, "by": by_id}, all_binders


def _stash_spelling(here: str, q: str, found: int, params: dict) -> dict | None:
    """"Did you mean Gengar?" for My Stash and binder searches, as Find has: when the words aren't a Pokémon's
    name and little was found. The link keeps the other filters."""
    words = q.strip()
    if not words or found >= 3 or not names.available() or names.species_id(words) is not None:
        return None
    try:
        guess = spelling.best(words, names.english_names())
    except Exception:
        return None
    if not guess or guess.casefold() == words.casefold():
        return None
    keep = [(k, v) for k, v in params.items() if v and k not in _NOT_FILTERS and k not in ("q", "from")]
    return {"name": guess, "href": f"{here}?{urlencode([('q', guess)] + keep)}"}


def _cards_page(request: Request, user, here: str, in_binder: dict | None, q: str = "", rarity: str = "", set: str = "",
                sort: str = "price_desc", method: str = "", by: str = "", origin: str = "", price: str = "",
                language: str = "", tag: str = "", binder: str = "", added: str = "", extra: dict | None = None):
    """My Stash' grid: the Cards tab, or one binder's cards (in_binder: {id, name}; id None is Not in a binder).
    extra: more for the page (a binder's header)."""
    params = dict(request.query_params)
    start = int(params["from"]) if params.get("from", "").isdigit() else 0
    keep = urlencode([(k, v) for k, v in request.query_params.multi_items() if k not in _NOT_FILTERS])
    scroll = (user["id"], here, keep)
    if start:
        rows = _scroll_rows(scroll)
        if rows is not None:  # the rest of the list the page showed, without building it again
            more = f"{here}?{keep}{'&' if keep else ''}from={start + CARDS_CHUNK}" if len(rows) > start + CARDS_CHUNK else None
            return templates.TemplateResponse(request, "_collection_tiles.html", {
                "rows": rows[start:start + CARDS_CHUNK], "more_url": more, "extras_map": extras.all_extras(conn)})
    data, f, all_binders = _cards_data(user, in_binder, q=q, rarity=rarity, set=set, sort=sort, method=method, by=by,
                                       origin=origin, price=price, language=language, tag=tag, binder=binder, added=added)
    sort, method, origin, price, language, added, binder = (f[k] for k in ("sort", "method", "origin", "price", "language",
                                                                           "added", "binder"))
    by_id = f["by"]
    people = auth.list_users(conn, user["collection_id"])
    binder_names = {str(b["id"]): b["name"] for b in all_binders} | {"none": "Not in a binder"}
    active = _my_cards_filters(q, language, set, rarity, method, by_id, origin, price, people, data["sets"], tag)
    if binder and in_binder is None:
        active.append((binder_names[binder], "binder"))
    if added:
        active.append((collection.ADDED[added], "added"))
    # The first CARDS_CHUNK tiles come with the page, the rest as you scroll (htmx "revealed").
    rows = data["rows"]
    _keep_scroll(scroll, rows)
    more = f"{here}?{keep}{'&' if keep else ''}from={start + CARDS_CHUNK}" if len(rows) > start + CARDS_CHUNK else None
    if start:
        return templates.TemplateResponse(request, "_collection_tiles.html", {
            "rows": rows[start:start + CARDS_CHUNK], "more_url": more, "extras_map": extras.all_extras(conn)})
    data = {**data, "rows": rows[:CARDS_CHUNK], "lines": len(rows), "more_url": more,
            "mismatched": [r for r in rows if r["other_printings"]], "did_you_mean": _stash_spelling(here, q, len(rows), params)}
    return templates.TemplateResponse(request, "collection.html", {
        "price": price, "moved": request.query_params.get("moved"), "done": request.query_params.get("done"),
        "user": user, "q": q, "rarity": rarity, "set": set, "sort": sort, "sorts": collection.SORTS, **data, "tag": tag,
        "method": method, "methods": inventory.METHOD_FILTERS, "by": by_id, "origin": origin, "language": language,
        "origins": inventory.ORIGINS, "people": people, "price_ranges": collection.price_ranges(),
        "language_names": collection.LANGUAGE_FILTERS, "active": active, "filter_count": len(active),
        "clear": {key: here + "?" + ("&".join(f"{k}={quote(v)}" for k, v in params.items() if k != key and v and k not in _NOT_FILTERS) or "clear=1")
                  for _, key in active},
        "query": str(request.url.query), "purchases": spending.purchase_choices(conn, user["collection_id"]),
        "extras_map": extras.all_extras(conn),
        "language_options": [(k, collection.LANGUAGE_FILTERS[k]) for k in data["languages"]],
        "people_options": [(p["id"], p["username"]) for p in people], "price_options": _price_options(),
        "here": here, "in_binder": in_binder, "binder": binder, "binders": all_binders, "added": added,
        "binder_options": [(str(b["id"]), b["name"]) for b in all_binders] + [("none", "Not in a binder")],
        "added_options": list(collection.ADDED.items()),
        "share": shared.info(conn, user["collection_id"], in_binder["id"]) if in_binder and in_binder["id"] else None,
        "share_open": request.query_params.get("share") == "1", "new_code": _new_code(request, in_binder),
        **(extra or {}),
    })


def _new_code(request: Request, in_binder: dict | None) -> dict | None:
    """A code made a moment ago, shown once on its binder's Share sheet (never put in the address)."""
    made = request.session.get("new_code")
    if made and in_binder and made.get("binder") == in_binder["id"]:
        request.session.pop("new_code", None)
        return made
    return None


def _price_options() -> list[tuple[str, str]]:
    return [(key, label) for key, (label, _, _) in collection.price_ranges().items()]


def _binder_choice(user, to_binder: str, new_binder: str) -> tuple[int | None, str]:
    """Move to binder's pick: (binder id or None for Not in a binder, its name). 'new' makes one."""
    if to_binder == "new":
        binder_id = binders.create(conn, user["collection_id"], new_binder, user["id"])
        return binder_id, binders.get(conn, user["collection_id"], binder_id)["name"]
    if to_binder == "none":
        return None, "Not in a binder"
    found = binders.get(conn, user["collection_id"], int(to_binder)) if to_binder.isdigit() else None
    if found is None:
        raise ValueError("Pick a binder.")
    return found["id"], found["name"]


def _chart(points, width=640, height=200):
    return worth.svg_path(points, width, height)


def _pokemon_name(dex: int) -> str:
    if dex < 0:  # A followed artist
        return (chase.artist(conn, dex) or {}).get("name") or "This artist"
    try:
        return names.english_name(dex) or f"#{dex:03d}"
    except Exception:
        return f"#{dex:03d}"


def _group_in_scanner(dex: int) -> bool:
    """The follow is saved and its cards show at once; the scanner then groups them by artwork."""
    if not SCANNER_EXTERNAL or not _pictures_ready():
        return False
    return background.queue(f"recheck:{dex}", f"Grouping {_pokemon_name(dex)}'s cards by artwork", "recheck", str(dex))


_FORMS = re.compile(r"^(?:.+'s |mega |dark |light |radiant |shining |galarian |alolan |hisuian |paldean |team rocket's )+|"
                    r"(?: (?:ex|gx|v|vmax|vstar|break|lv\.x|prime|star|δ|◇|☆))+$", re.IGNORECASE)


def _card_tags_with_pokemon(card_id: str, card) -> dict:
    """The card's tags, plus your own words on its Pokémon, carried onto the card."""
    entry = tags.for_card(conn, card_id)
    dex = (_card_pokemon({"collection_id": 0}, card["name"], card_id if ":" in card_id else card["language"],
                         card["twin_name"]) or {}).get("dex")
    if dex:
        have = {t.casefold() for t, _ in entry["tags"]} | {h.casefold() for h in entry["hidden"]}
        for (word,) in conn.execute("SELECT tag FROM pokemon_tags WHERE dex = ? ORDER BY rowid", (dex,)):
            if word.casefold() not in have:
                entry["tags"].append((word, "pokemon"))
    return entry


def _card_pokemon(user, name: str | None, where: str, twin: str | None = None) -> dict | None:
    """The Pokémon a card is of, for the "Clefairy's set ›" line: only when the
    whole name is that Pokémon (forms like ex, V or "Team Rocket's" allowed),
    so a Trainer such as Clefairy Doll has no line."""
    if not name or not names.available():
        return None
    try:
        english = _en_name(name, where, twin)["en"]
        dex = names.species_id(english)
        if not dex or _FORMS.sub("", english).strip().casefold() != (names.english_name(dex) or "").casefold():
            return None
    except Exception:
        return None
    return {"dex": dex, "name": names.english_name(dex), "following": chase.is_followed(conn, user["collection_id"], dex),
            "sprite": f"/sprites/{dex}.png" if themes.sprite_path(dex, False).exists() else None}


def _artist_matches(q: str, limit: int = 4) -> list[str]:
    """Artist names containing what was typed (accents and case don't matter), whole-word starts first."""
    wanted = chase.artist_norm(q)
    if len(wanted) < 3:
        return []
    every = list(dict.fromkeys(list(tcgdex.artist_names(api)) + [r[0] for r in conn.execute(
        "SELECT DISTINCT illustrator FROM pokemon_cards WHERE illustrator IS NOT NULL")] + [r[0] for r in conn.execute(
        "SELECT name FROM artists")]))
    hits = [n for n in every if wanted in chase.artist_norm(n)]
    hits.sort(key=lambda n: (chase.artist_norm(n) != wanted, not any(w.startswith(wanted) for w in chase.artist_norm(n).split()), n))
    return hits[:limit]


def _following(collection_id: int, dex: int) -> bool:
    return conn.execute("SELECT 1 FROM followed_pokemon WHERE collection_id = ? AND dex_id = ?",
                        (collection_id, dex)).fetchone() is not None


def _evolution(collection_id: int, dex: int, start: bool = True) -> dict | None:
    """The evolution line for a Pokémon's set page, with names, sprites and ★ on followed ones.
    The species list is a one-time download; until it's here the block doesn't show.
    Find passes start=False: it shows the line once the list is here but never starts the download."""
    if not lines.available():
        if start:
            background.start("evolution", "Getting the evolution lines",
                             lambda job_conn, job: (pokeapi.fetch("pokemon_species"), "Evolution lines are ready.")[1])
        return None
    try:
        line = lines.line(dex)
    except Exception as e:
        log.info("no evolution line for #%s: %s", dex, e)
        return None
    if line is None:
        return None
    following = {f["dex_id"] for f in chase.followed(conn, collection_id)}

    def mon(d):
        return {"dex": d, "name": _pokemon_name(d), "following": d in following, "here": d == dex,
                "sprite": f"/sprites/{d}.png" if themes.sprite_path(d, False).exists() else None}
    branches = [[mon(st) if isinstance(st, int) else [mon(d) for d in st] for st in b] for b in line["branches"]]
    return {"trunk": [mon(d) for d in line["trunk"]], "branches": branches, "multi": any(len(b) > 1 for b in branches)}



def _is_owner(user) -> bool:
    """Backups hold every account, so only admins of the first collection see them."""
    first = conn.execute("SELECT MIN(id) FROM users").fetchone()[0]
    return bool(user["is_admin"]) and user["collection_id"] == first


def owner_user(user=Depends(current_user)) -> dict:
    if not _is_owner(user):
        raise HTTPException(403, f"Only the admin who set up {settings.app_name()} can do that.")
    return user


def _account_context(request: Request, user, message: str | None = None, error: bool = False) -> dict:
    owner = _is_owner(user)
    return {
        "user": user, "users": auth.list_users(conn, user["collection_id"]) if user["is_admin"] else [],
        "message": message, "error": error, "min_password": auth.MIN_PASSWORD, "session_days": SESSION_DAYS,
        "owner": owner, "backups": backup.list_backups(BACKUPS) if owner else [], "backup_labels": backup.LABELS,
        "theme_setting": themes.user_setting(conn, user["id"]), "theme_shared": themes.shared_settings(conn),
        "species_choices": _species_choices(), "theme_saved": request.query_params.get("theme") == "saved",
        "theme_name": themes.species_name(names, themes.user_setting(conn, user["id"])["species_id"]),
        "sprites_ready": themes.counts(conn)["normal"] > 0,
        "species_swatches": _species_swatches(), "owned_species": sorted(themes.owned_species(conn, names)),
        "theme_now": themes.for_user(conn, user["id"], names),
    }


def _account_page(request: Request, user, message: str, error: bool = False):
    return templates.TemplateResponse(request, "account.html", _account_context(request, user, message, error),
                                      status_code=400 if error else 200)



def _fix_row(user, fix) -> dict | None:
    """The import row being fixed from search, if any (?fix=<row id>)."""
    if not fix or not str(fix).isdigit():
        return None
    return importer.get_row(conn, user["collection_id"], int(fix))



def _scan_fix(user, scan) -> dict | None:
    """The scan whose card is being found by hand, if any (?scan=<scan id>)."""
    if not scan or not str(scan).isdigit():
        return None
    found = scans.get_scan(conn, user["collection_id"], int(scan))
    return found if found and found["status"] in scans.OPEN else None


def _chinese_set_to_load(lang: str, code: str | None) -> str | None:
    """A mainland Chinese set code that loading the Chinese sets would bring, if it isn't here yet."""
    code = (code or "").strip().upper()
    if lang != chinese.LANG or not code or chinese.is_loaded(conn, code):
        return None
    return code if not chinese.loaded_codes(conn) or chinese.is_known(code) else None
