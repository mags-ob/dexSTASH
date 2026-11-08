"""Scans pages."""
from html import escape
import re
import sqlite3
import threading
import time
from urllib.parse import quote, unquote, urlsplit

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import catalog, display, extras, inventory, prices, scans, search, spending
from .. import binders, celebrate, jobs, sync, tcgdex
from ..tcgdex import LANGUAGES
from .. import web
from .imports import import_match_row
from ..web import (_Route, _binder_choice, _chinese_set_to_load, _ensure_card, _in_thread, _is_owner, _links,
                   _picture_reply, _pictures_ready, _price, _prices_for, _scan_problem, _store_price, background,
                   conn, current_user, link_label, log, picture_saver, templates)

router = APIRouter(route_class=_Route)


def _scan_batch_or_404(user, batch_id: int) -> dict:
    batch = scans.get_batch(conn, user["collection_id"], batch_id)
    if batch is None:
        raise HTTPException(404)
    return batch


def _scan_or_404(user, scan_id: int) -> dict:
    scan = scans.get_scan(conn, user["collection_id"], scan_id)
    if scan is None:
        raise HTTPException(404)
    return scan


def _scan_item_context(user, scan: dict, batch: dict, message: str | None = None) -> dict:
    return {"user": user, "s": scan, "batch": batch, "languages": LANGUAGES, "scan_languages": tcgdex.SCAN_CHOICES,
            "scan_tags": scans.SCAN_LANGS, "slang": scans.lang_of(scan or {}, batch),  # The photo's own language
            "pictures_ready": _pictures_ready(),
            "variants": inventory.VARIANTS,
            "conditions": inventory.CONDITIONS, "origins": inventory.ORIGINS, "message": message}


def _scan_item(request: Request, user, scan_id: int, message: str | None = None):
    scan = _scan_or_404(user, scan_id)
    batch = _scan_batch_or_404(user, scan["batch_id"])
    return templates.TemplateResponse(request, "_scan_item.html", _scan_item_context(user, scan, batch, message))


@router.get("/scans")
def scans_view(request: Request, user=Depends(current_user)):
    batches = scans.batches(conn, user["collection_id"])
    return templates.TemplateResponse(request, "scans.html", {
        "user": user, "origins": inventory.ORIGINS,
        "reading": [b for b in batches if b["reading"]],
        "to_confirm": [b for b in batches if not b["reading"] and b["to_confirm"]],
        "finished": [b for b in batches if not b["reading"] and not b["to_confirm"]],
        "languages": LANGUAGES, "scan_languages": tcgdex.SCAN_CHOICES,
        "problem": _scan_problem() if web.scan_worker.available() else "The photo reader isn't installed yet. "
                   "Run pip install rapidocr onnxruntime, then restart the app. Photos you take wait until then.",
    })


@router.post("/scans")
def scans_start(request: Request, user=Depends(current_user)):
    """One New batch button; each photo's language is worked out as it's read."""
    batch_id = scans.create_batch(conn, user["collection_id"], user["id"], None, "auto")
    return RedirectResponse(f"/scans/{batch_id}?camera=1", status_code=303)


@router.post("/scans/item/{scan_id}/language")
def scan_language(request: Request, scan_id: int, lang: str = Form(...), user=Depends(current_user)):
    """The row's language tag, changed by hand. The card is looked up again in that language."""
    scan = _scan_or_404(user, scan_id)
    if scans.set_language(conn, scan, lang):
        web.scan_worker.start()
    return _after_scan_action(request, user, scans.get_scan(conn, user["collection_id"], scan_id))


@router.get("/scans/item/{scan_id}")
def scan_item(request: Request, scan_id: int, user=Depends(current_user)):
    return _scan_item(request, user, scan_id)


@router.get("/scans/photo/{scan_id}")
def scan_photo(scan_id: int, user=Depends(current_user)):
    path = scans.photo_path(_scan_or_404(user, scan_id))
    if path is None:
        raise HTTPException(404)
    return _picture_reply(path, {"Cache-Control": "private, max-age=3600"})


def _after_scan_action(request: Request, user, scan: dict, message: str | None = None):
    # HTMX swaps just this photo's row; a plain form post goes back to the batch.
    if request.headers.get("HX-Request"):
        response = _scan_item(request, user, scan["id"], message)
        if celebrate.waiting(user):  # A chased card just added; base-nav.js asks /got
            response.headers["HX-Trigger"] = "dsGot"
        return response
    return RedirectResponse(f"/scans/{scan['batch_id']}#scan-{scan['id']}", status_code=303)


def _first_printing(card_id: str) -> str:
    """The printing a quick Add uses: the first one TCGplayer lists for the card."""
    card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    card_prices, pricing = _prices_for(card_id, card)
    priced = [v for v in prices.by_variant(card_prices) if v in prices.real_variants(card_prices)] or prices.listed(pricing)
    if not priced:  # an Add (not a page): TCGdex says which printings TCGplayer lists
        priced = prices.listed((web._live_card(card_id) or {}).get("pricing"))
    return inventory.default_variant(inventory.offered_variants(card, set(priced)), priced)


@router.post("/scans/item/{scan_id}/add")
def scan_add(
    request: Request, scan_id: int, pick: int = Form(0), variant: str = Form(""), condition: str = Form(""),
    quantity: int = Form(0), origin: str = Form(""), user=Depends(current_user),
):
    """Adds the scan's card. Without a printing, condition or quantity, it uses
    what was picked in Text search, else Near Mint, 1, the first TCGplayer printing."""
    scan = _scan_or_404(user, scan_id)
    if not 0 <= pick < len(scan["guesses"]):
        raise HTTPException(400, "Pick one of the cards, or use Find it.")
    guess = scan["guesses"][pick]
    our_card_id = _ensure_card(guess.get("lang", "en"), guess["set_id"], guess["id"])
    chosen = pick == 0 and scan["variant"]
    variant = variant or (scan["variant"] if chosen else "") or _first_printing(our_card_id)
    condition = condition or (scan["condition"] if chosen else "") or "NM"
    quantity = quantity or (scan["quantity"] if chosen else 0) or 1
    try:
        scans.add_card(conn, scan, user["id"], our_card_id, variant, condition, quantity, origin or scan_batch_origin_of(scan))
    except ValueError as e:
        raise HTTPException(400, str(e))
    _store_price(our_card_id)
    return _after_scan_action(request, user, scan)


def scan_batch_origin_of(scan: dict) -> str | None:
    row = conn.execute("SELECT origin FROM scan_batches WHERE id = ?", (scan["batch_id"],)).fetchone()
    return row["origin"] if row else None


@router.post("/scans/item/{scan_id}/skip")
def scan_skip(request: Request, scan_id: int, back: str = Form(""), view: str = Form(""), user=Depends(current_user)):
    """Remove from batch: the photo moves to Deleted scans. From the card view, the next card opens."""
    scan = _scan_or_404(user, scan_id)
    following = scans.next_open(conn, scan)
    scans.skip(conn, scan)
    if view:
        return _open_next(request, user, scan, following)
    if back:
        return RedirectResponse(_back_to(scan, back), status_code=303)
    return _after_scan_action(request, user, scan)


@router.post("/scans/item/{scan_id}/put-back")
def scan_put_back(request: Request, scan_id: int, user=Depends(current_user)):
    """Deleted scans › Put back."""
    scan = _scan_or_404(user, scan_id)
    if scans.put_back(conn, scan):
        status = scans.get_scan(conn, user["collection_id"], scan_id)["status"]
        if status == "waiting":
            web.scan_worker.start()
        elif status == "picture_waiting":
            web.picture_worker.start()
    return _after_scan_action(request, user, scan)


@router.post("/scans/item/{scan_id}/retry")
def scan_retry(request: Request, scan_id: int, user=Depends(current_user)):
    scan = _scan_or_404(user, scan_id)
    if scans.retry(conn, scan):
        web.scan_worker.start()
    return _after_scan_action(request, user, scan)


def _find_ref(ref: str) -> tuple[str, str, str]:
    """A Find tile's own link as (lang, TCGdex card id, TCGdex set id): /cards/<id> for a stored card,
    /preview/<lang>/<TCGdex id> otherwise."""
    path = urlsplit(ref).path
    if path.startswith("/cards/"):
        card = conn.execute("SELECT c.id, c.set_id, s.language FROM cards c JOIN sets s ON s.set_id = c.set_id WHERE c.id = ?",
                            (unquote(path[len("/cards/"):]),)).fetchone()
        if card is None:
            raise HTTPException(404)
        lang, card_id, set_id = card["language"], catalog.tcgdex_set_id(card["id"]), catalog.tcgdex_set_id(card["set_id"])
    elif path.startswith("/preview/") and path.count("/") >= 3:
        lang, card_id = path[len("/preview/"):].split("/", 1)
        card_id, source = unquote(card_id), web.api_for(lang)  # an unknown language is a 400, not TCGdex's fault
        try:
            set_id = search.preview(source, card_id)["set_id"]
        except Exception as e:
            raise HTTPException(502, f"Couldn't load card {card_id} from TCGdex: {e}")
    else:
        raise HTTPException(400)
    return lang, card_id, set_id


@router.post("/find/use")
def find_use(ref: str = Form(...), scan: str = Form(""), fix: str = Form(""), user=Depends(current_user)):
    """"Use this" on a Find result while finding the card for a photo or an import row."""
    lang, card_id, set_id = _find_ref(ref)
    if scan.isdigit():
        return scan_use(int(scan), card_id, set_id, lang, user)
    if fix.isdigit():
        return import_match_row(int(fix), card_id, set_id, lang, user)
    raise HTTPException(400)


@router.post("/scans/item/{scan_id}/use")
def scan_use(scan_id: int, card_id: str = Form(...), set_id: str = Form(...), lang: str = Form("en"),
             user=Depends(current_user)):
    """A card found by hand (card_id and set_id are TCGdex ids in lang) becomes the scan's pick."""
    scan = _scan_or_404(user, scan_id)
    if scan["status"] not in scans.OPEN:
        raise HTTPException(400, "This photo was already dealt with.")
    our_card_id = _ensure_card(lang, set_id, card_id)
    card = conn.execute("SELECT c.*, s.name AS set_name FROM cards c JOIN sets s ON s.set_id = c.set_id WHERE c.id = ?",
                        (our_card_id,)).fetchone()
    scans.use_card(conn, scan, {"id": card_id, "set_id": set_id, "lang": lang, "number": card["number"],
                                "name": card["name"], "image": card["image_url"], "set_name": card["set_name"],
                                "score": None, "sure": False, "by_hand": True})
    return RedirectResponse(f"/scans/{scan['batch_id']}#scan-{scan_id}", status_code=303)


_lookup: dict = {}


def _local_name(text: str, lang: str) -> str | None:
    """A Pokémon name typed in English, as it's printed on cards of lang (for Scan Details' Name box)."""
    if not web.names.available():
        return None
    t = web.names.translate(text, lang)
    return t.name if t else None


def _scan_lookup() -> scans.CardLookup:
    """One lookup shared by Text searches for a couple of minutes, so typing
    doesn't ask TCGdex for the set list on every key."""
    if "lookup" not in _lookup or _lookup["at"] < time.monotonic() - 120:
        _lookup.update(at=time.monotonic(), lookup=scans.CardLookup(conn, lambda lang: web.api_for(lang), _local_name))
    return _lookup["lookup"]


def _found_context(user, scan: dict, batch: dict, name: str | None, number: str | None, code: str | None,
                   edited: str = "") -> dict:
    """Scan Details' three boxes and tabs: filled from what was read until you type."""
    reading = scan["reading"]
    if name is None and number is None and code is None:
        name = reading.name or ""
        number = (reading.number or "") + (f"/{reading.total}" if reading.total else "")
        code = reading.code or ""
    q = {"name": (name or "").strip(), "number": (number or "").strip(), "code": (code or "").strip()}
    lang = scans.lang_of(scan, batch)
    return {"q": q, "tabs": scans.batch_tabs(_scan_lookup(), lang, **q), "lang": lang, "edited": edited,
            "load_set": _chinese_set_to_load(lang, q["code"] or reading.code), "owner": _is_owner(user),
            "loading": background.running("chs")}


@router.get("/scans/item/{scan_id}/found")
def scan_found(request: Request, scan_id: int, name: str | None = None, number: str | None = None,
               code: str | None = None, back: str = "batch", edited: str = "", user=Depends(current_user)):
    """Scan Details' tabs for what's typed in the three boxes (searched again as you type)."""
    scan = _scan_or_404(user, scan_id)
    batch = _scan_batch_or_404(user, scan["batch_id"])
    return templates.TemplateResponse(request, "_scan_found.html", {
        **_scan_item_context(user, scan, batch), **_found_context(user, scan, batch, name, number, code, edited),
        "back": "review" if back == "review" else "batch"})


@router.post("/scans/item/{scan_id}/found")
def scan_found_pick(request: Request, scan_id: int, card_id: str = Form(...), set_id: str = Form(...), lang: str = Form("en"),
                    back: str = Form("batch"), user=Depends(current_user)):
    """Tapping a card in the tabs makes it the photo's match straight away; Scan Details shows it."""
    scan = _scan_or_404(user, scan_id)
    if scan["status"] not in scans.OPEN:
        raise HTTPException(400, "This photo was already dealt with.")
    our_card_id = _ensure_card(lang, set_id, card_id)
    card = conn.execute("SELECT c.*, s.name AS set_name FROM cards c JOIN sets s ON s.set_id = c.set_id WHERE c.id = ?",
                        (our_card_id,)).fetchone()
    scans.use_card(conn, scan, {"id": card_id, "set_id": set_id, "lang": lang, "number": card["number"],
                                "name": card["name"], "image": card["image_url"], "set_name": card["set_name"],
                                "score": None, "sure": False, "by_hand": True})
    if request.headers.get("HX-Request"):
        return scan_details(request, scan_id, "guess", 0, back, user=user)
    return RedirectResponse(f"/scans/{scan['batch_id']}?open={scan_id}#scan-{scan_id}", status_code=303)


@router.post("/scans/{batch_id}/name")
def scan_batch_name(request: Request, batch_id: int, name: str = Form(""), user=Depends(current_user)):
    """Renames a batch from its screen."""
    if scans.get_batch(conn, user["collection_id"], batch_id) is None:
        raise HTTPException(404)
    scans.set_batch_name(conn, batch_id, name)
    if request.headers.get("HX-Request"):
        return Response(status_code=204)
    return RedirectResponse(f"/scans/{batch_id}", status_code=303)


@router.post("/scans/item/{scan_id}/picture")
def scan_send_to_pictures(request: Request, scan_id: int, back: str = Form(""), user=Depends(current_user)):
    """Moves the photo into the Picture search queue."""
    scan = _scan_or_404(user, scan_id)
    if not _pictures_ready():
        raise HTTPException(400, "Enhanced Search isn't turned on. An admin can turn it on in Data sources.")
    if scans.send_to_pictures(conn, scan):
        web.picture_worker.start()
    if back:  # Coming back opens scan details with Change match open, where the results land
        return RedirectResponse(f"/scans/{scan['batch_id']}?open={scan_id}&change=1#scan-{scan_id}", status_code=303)
    if request.headers.get("HX-Request"):
        response = _scan_item(request, user, scan_id)
        response.headers["HX-Trigger"] = "scan-picked"  # closes the panel
        return response
    return RedirectResponse(f"/scans/{scan['batch_id']}#scan-{scan_id}", status_code=303)


def _back_to(scan: dict, back: str) -> str:
    """Where a card view action returns to: the batch (Review your batch is part of it)."""
    return f"/scans/{scan['batch_id']}#scan-{scan['id']}"


def _offer(lang: str, card_id: str) -> dict:
    """A match's printings and prices (card_id is TCGdex's id in lang). Nothing is stored for a card you don't have."""
    our = catalog.local_id(lang, card_id)
    card = conn.execute("SELECT c.*, s.name AS set_name FROM cards c JOIN sets s ON s.set_id = c.set_id WHERE c.id = ?",
                        (our,)).fetchone()
    card_prices, pricing, extra, twin = [], None, None, None
    if card is not None:
        card_prices, pricing = _prices_for(our, card)
        extra = extras.get(conn, our)
        offered = inventory.offered_variants(card, prices.real_variants(card_prices))
        twin = card["twin_name"]
    elif lang in LANGUAGES:
        try:
            found = search.preview(web.api_for(lang), card_id)
            card_prices, pricing = _prices_for(our, found, found["detail"])
            flags = {"has_normal": "normal", "has_reverse": "reverse", "has_holo": "holo", "has_first_ed": "firstEdition"}
            card = {f: bool((found.get("variants") or {}).get(k)) for f, k in flags.items()}
        except Exception:
            card = None  # TCGdex can't be reached: no price, links still work
        offered = inventory.offered_variants(card, prices.real_variants(card_prices))
    else:
        offered = ["normal"]
    price_map = prices.by_variant(card_prices)
    priced = [v for v in price_map if v in prices.real_variants(card_prices)] or prices.listed(pricing)
    return {"our": our, "stored": card is not None and not isinstance(card, dict), "rows": card_prices, "pricing": pricing,
            "extra": extra, "twin_name": twin, "offered": offered, "price_map": price_map,
            "default": inventory.default_variant(offered, priced)}


def _offer_price(offer: dict, variant: str, condition: str = "NM") -> float | None:
    price_map = offer["price_map"]
    if variant not in price_map and price_map:  # ≈ English only: the closest printing's
        variant = prices.nearest(variant, price_map) or variant
    price = price_map.get(variant)
    return None if price is None else price * prices.share(condition)


def _match_info(user, lang: str, card_id: str, name: str, number: str, set_name: str, offer: dict | None = None,
                variant: str | None = None) -> dict:
    """Price, links and how many you have, for a match in the card view.
    card_id is TCGdex's id in lang. Nothing is stored for a card you don't have."""
    offer = offer or _offer(lang, card_id)
    our = offer["our"]
    owned = sum(h["quantity"] for h in inventory.holdings(conn, user["collection_id"], our)) if offer["stored"] else 0
    about = {"id": our, "name": name, "number": number, "set_name": set_name, "twin_name": offer["twin_name"]}
    variant = variant if variant in inventory.VARIANTS else offer["default"]
    price_map = offer["price_map"]
    shown = variant if variant in price_map or not price_map else (prices.nearest(variant, price_map) or variant)
    links = _links(about, lang, offer["pricing"], offer["extra"], our)
    source = prices.sources(offer["rows"], offer["extra"], links["tcg_url"]).get(shown)
    mine = offer["extra"] or {}

    def check(site: str, exact: bool) -> str | None:
        """After a Search link is tapped, ask for the link you found. Only searches ask."""
        return None if exact or mine.get(f"{site}_url") else "search"
    # Round 27: one line per source (as the card page's Every price), each linking to where its price came from.
    more = web._price_more(about, offer["rows"], offer["extra"] or {}, links, shown)
    lines = more["lines"].get(more["shown"]) or []
    if not lines and price_map.get(shown) is not None:  # TCGCSV's price for a set someone browsed (nothing stored)
        lines = [{"name": "TCGplayer", "note": "TCGCSV", "url": links["tcg_url"] if links["tcg_exact"] else None,
                  "left_out": False, "amount": display.money(price_map[shown]), "at": None}]
    return {"lines": lines,
            "price": price_map.get(shown), "variant_label": inventory.VARIANTS.get(shown) if shown in price_map else None,
            "source": source, "owned": owned, "tcg_url": links["tcg_url"], "pc_url": links["pc_url"],
            "ebay_url": links["ebay_url"], "ct_url": links["ct_url"], "ct_exact": links["ct_exact"],
            "tcg_check": check("tcgplayer", links["tcg_exact"]), "pc_check": check("pricecharting", links["pc_exact"]),
            "tcg_exact": links["tcg_exact"], "pc_exact": links["pc_exact"],
            "view": f"/preview/{quote(lang)}/{quote(card_id)}",
            "sources": prices.sources(offer["rows"], offer["extra"], links["tcg_url"])}


class TilePrices:
    """Prices on the batch grid's tiles. A card whose prices are stored here is priced at once;
    any other is looked up on a background thread, and its tile is drawn again when the price is in."""

    FRESH = 1800

    def __init__(self):
        self.offers: dict[str, tuple[float, dict]] = {}
        self.waiting: dict[str, tuple[str, str, int]] = {}
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None

    def get(self, lang: str, card_id: str, batch_id: int) -> dict | None:
        our = catalog.local_id(lang, card_id)
        hit = self.offers.get(our)
        if hit and hit[0] > time.monotonic() - self.FRESH:
            return hit[1]
        if conn.execute("SELECT 1 FROM prices WHERE card_id = ? LIMIT 1", (our,)).fetchone():
            return self._keep(our, _offer(lang, card_id))
        with self.lock:
            self.waiting.setdefault(our, (lang, card_id, batch_id))
            if self.thread is None:
                self.thread = threading.Thread(target=self.run, daemon=True)
                self.thread.start()
        return None

    def _keep(self, our: str, offer: dict) -> dict:
        self.offers[our] = (time.monotonic(), offer)
        return offer

    def run(self) -> None:
        while True:
            with self.lock:
                if not self.waiting:
                    self.thread = None
                    return
                our, (lang, card_id, batch_id) = next(iter(self.waiting.items()))
            try:
                self._keep(our, _offer(lang, card_id))
                # the batch page fetches rows whose rev changed, so its tiles get the price
                conn.execute("UPDATE scans SET rev = rev WHERE batch_id = ? AND guesses_json LIKE ?",
                             (batch_id, f'[{{"id": "{card_id}"%'))
                conn.commit()
            except Exception:
                log.exception("tile price for %s", our)
                self._keep(our, {"our": our, "price_map": {}, "offered": ["normal"], "default": "normal"})
            finally:
                with self.lock:
                    self.waiting.pop(our, None)

    def wait(self) -> None:
        thread = self.thread
        if thread:
            thread.join()


tile_prices = TilePrices()


def _tile(scan: dict, batch: dict) -> dict | None:
    """A matched photo's tile: the price for the chosen printing and condition, and the printing · condition · ×qty line."""
    if not scans.matched(scan):
        return None
    g = scan["guesses"][0]
    offer = tile_prices.get(g.get("lang") or scans.lang_of(scan, batch), g["id"], batch["id"])
    variant = scan["variant"] if scan["variant"] in inventory.VARIANTS else (offer["default"] if offer else None)
    condition = scan["condition"] or "NM"
    return {"known": offer is not None, "price": _offer_price(offer, variant, condition) if offer and variant else None,
            "variant": variant, "label": inventory.VARIANTS.get(variant or ""), "condition": condition,
            "quantity": scan["quantity"] or 1}


templates.env.globals["scan_tile"] = lambda scan, batch: _tile(scan, batch)  # each tile on the batch page


def _scan_options(scan: dict, lang: str) -> list[dict]:
    """Every card the card view can offer for a photo: the text guesses, then
    Enhanced Search's matches, each with its likeness word."""
    out = []
    for i, g in enumerate(scan["guesses"]):
        out.append({**g, "src": "guess", "pick": i, "lang": g.get("lang") or lang,
                    "word": scans.guess_word(g, first=i == 0), "by_hand": g.get("by_hand")})
    seen = {(o["lang"], o["id"]) for o in out}
    for i, (m, word) in enumerate(zip(scan["matches"], scans.picture_words(scan["matches"]))):
        if (m["lang"], m["id"]) not in seen:
            out.append({**m, "src": "picture", "pick": i, "word": word})
    return out


def _card_view(request: Request, user, scan_id: int, src: str = "guess", pick: int = 0, fresh: bool = False,
               message: str | None = None):
    """The card view (it replaced Scan Details): your photo next to the match, how it will be added,
    Change match, Remove from batch, and Cancel / Save / Save & next. fresh: just opened, so Cancel has
    something to go back to."""
    scan = _scan_or_404(user, scan_id)
    batch = _scan_batch_or_404(user, scan["batch_id"])
    if fresh and scan["status"] in scans.OPEN:
        scans.snapshot(conn, scan)
        scan = _scan_or_404(user, scan_id)
    options = _scan_options(scan, scans.lang_of(scan, batch))
    current = next((o for o in options if o["src"] == src and o["pick"] == pick), options[0] if options else None)
    applied = bool(current) and scans.matched(scan) and current["src"] == "guess" and current["pick"] == 0
    offer = _offer(current["lang"], current["id"]) if current else None
    variant = scan["variant"] if applied and scan["variant"] in inventory.VARIANTS else (offer["default"] if offer else None)
    offered = list(offer["offered"]) if offer else []
    if variant and variant not in offered:
        offered = [variant] + offered
    info = _match_info(user, current["lang"], current["id"], current["name"], current["number"],
                       current.get("set_name", ""), offer, variant) if current else None
    if info is not None:
        from .. import cardtrader
        info["checking"] = scan_prices_running(batch)
        info["ct_checking"] = info["checking"] and bool(cardtrader.token()) and \
            not any(l["name"] == "CardTrader" for l in info["lines"])
    at, total = scans.position(conn, scan)
    ctx = {**_scan_item_context(user, scan, batch, message), "options": options, "current": current, "info": info,
           "applied": applied, "offer": offer, "offered": offered, "variant": variant,
           "condition": (scan["condition"] or "NM") if applied else "NM",
           "quantity": (scan["quantity"] or 1) if applied else 1, "paid": scan["paid"] if applied else None,
           "at": at, "total": total, "next_id": scans.next_open(conn, scan),
           "binder_on": bool(batch.get("binder")), "word_class": scans.LIKENESS_CLASS, "back": "batch",
           **_found_context(user, scan, batch, None, None, None)}
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "_scan_details.html", ctx)
    return templates.TemplateResponse(request, "scan_details_page.html", ctx)


@router.post("/scans/camera-problem")
async def scan_camera_problem(request: Request, user=Depends(current_user)):
    """Why the live camera didn't open, for the Activity log (the error name and the browser only)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    name = re.sub(r"[^A-Za-z]", "", str(body.get("name") or ""))[:40] or "Unknown"
    detail = " ".join(str(body.get("detail") or "").split())[:200]
    agent = " ".join(request.headers.get("user-agent", "").split())[:200]
    log.warning("live camera didn't open: %s %s | %s", name, detail, agent)
    return Response(status_code=204)


@router.post("/scans/item/{scan_id}/link")
def scan_link(request: Request, scan_id: int, site: str = Form(...), lang: str = Form(...), set_id: str = Form(...),
              card_id: str = Form(...), url: str = Form(""), pc_price: str = Form(""), user=Depends(current_user)):
    """After a TCGplayer or PriceCharting search is tapped in scan review, the link you found is
    saved on the matched card (never read; a PriceCharting link can bring the price you saw)."""
    _scan_or_404(user, scan_id)
    if site not in ("tcgplayer", "pricecharting"):
        raise HTTPException(400)
    label = web.LINK_LABELS[site]
    our = _ensure_card(lang, set_id, card_id)
    lc = {"action": f"/scans/item/{scan_id}/link", "site": site, "lang": lang, "set_id": set_id, "card_id": card_id}
    try:
        saved, _ = extras.add_link(conn, our, user["id"], url)  # The site is the pasted link's
        if saved == "pricecharting" and pc_price.strip():
            extras.save(conn, our, user["id"], pc_price=_price(pc_price))
    except (ValueError, HTTPException) as e:
        message = e.detail if isinstance(e, HTTPException) else str(e)
        return templates.TemplateResponse(request, "_scan_link_check.html",
                                          {"lc": lc, "label": label, "message": message, "paste": True, "error": True})
    picture_saver.queue([our])
    return templates.TemplateResponse(request, "_scan_link_check.html",
                                      {"lc": lc, "label": label, "message": f"Saved the {link_label(saved, url)} link.",
                                       "paste": False})


@router.get("/scans/item/{scan_id}/details")
def scan_details(request: Request, scan_id: int, src: str = "guess", pick: int = 0, back: str = "batch",
                 open: str = "", user=Depends(current_user)):
    """The card view. ?open=1 when it's opened from the grid (Cancel goes back to how the card was then)."""
    return _card_view(request, user, scan_id, src, pick, fresh=bool(open))


def _closed(batch_id: int, scan_id: int | None = None, hx: bool = True):
    """Back to the grid: HTMX closes the card view; a plain form goes to the batch page."""
    if hx:
        return Response(status_code=204, headers={"HX-Trigger": "scan-picked"})
    return RedirectResponse(f"/scans/{batch_id}" + (f"#scan-{scan_id}" if scan_id else ""), status_code=303)


def _open_next(request: Request, user, scan: dict, following: int | None):
    """Save & next or Remove: the next card, or back to the grid after the last one."""
    hx = bool(request.headers.get("HX-Request"))
    if following is None:
        return _closed(scan["batch_id"], scan["id"], hx)
    if hx:
        response = _card_view(request, user, following, fresh=True)
        response.headers["HX-Trigger"] = "scan-changed"  # the grid fetches the tile that changed
        return response
    return RedirectResponse(f"/scans/item/{following}/details?open=1", status_code=303)


@router.post("/scans/item/{scan_id}/match")
def scan_use_match(request: Request, scan_id: int, src: str = Form("guess"), pick: int = Form(0),
                   user=Depends(current_user)):
    """Picking another guess or Enhanced Search match in the card view: it becomes the match (Cancel puts the old
    one back), and the choices show again."""
    scan = _scan_or_404(user, scan_id)
    try:
        if src == "picture":
            if not 0 <= pick < len(scan["matches"]) or scan["status"] not in scans.OPEN:
                raise ValueError("Pick one of the matches.")
            m = scan["matches"][pick]
            our_card_id = _ensure_card(m["lang"], m["set_id"], m["id"])
            card = conn.execute("SELECT c.*, s.name AS set_name FROM cards c JOIN sets s ON s.set_id = c.set_id "
                                "WHERE c.id = ?", (our_card_id,)).fetchone()
            scans.use_card(conn, scan, {"id": m["id"], "set_id": m["set_id"], "lang": m["lang"], "number": card["number"],
                                        "name": card["name"], "image": card["image_url"] or m.get("image"),
                                        "set_name": card["set_name"], "score": m.get("score"), "sure": False,
                                        "by_hand": True})
        else:
            scans.use_guess(conn, scan, pick)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if request.headers.get("HX-Request"):
        return _card_view(request, user, scan_id)
    return RedirectResponse(f"/scans/item/{scan_id}/details", status_code=303)


@router.post("/scans/item/{scan_id}/save")
def scan_save(request: Request, scan_id: int, variant: str = Form(""), condition: str = Form("NM"),
              quantity: int = Form(1), paid: str = Form(""), in_binder: str = Form(""), in_binder_shown: str = Form(""),
              next: str = Form(""), user=Depends(current_user)):
    """Save / Save & next in the card view: how the card will be added, and it's checked (✓).
    In the binder is only on the form when the batch has a binder; otherwise the card keeps its setting."""
    scan = _scan_or_404(user, scan_id)
    keep_in = in_binder == "1" if in_binder_shown else bool(scan["in_binder"])
    try:
        scans.save(conn, scan, variant, condition, quantity, _price(paid) if paid.strip() else None, keep_in)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if next:
        return _open_next(request, user, scan, scans.next_open(conn, scan))
    return _closed(scan["batch_id"], scan_id, bool(request.headers.get("HX-Request")))


@router.post("/scans/item/{scan_id}/cancel")
def scan_cancel(request: Request, scan_id: int, user=Depends(current_user)):
    """Cancel (or ✕) in the card view: changes made to this card since it opened are thrown away."""
    scan = _scan_or_404(user, scan_id)
    scans.restore(conn, scan)
    return _closed(scan["batch_id"], scan_id, bool(request.headers.get("HX-Request")))


@router.post("/scans/item/{scan_id}/line")
def scan_line(scan_id: int, variant: str = Form(""), condition: str = Form(""), quantity: int = Form(0),
              paid: str | None = Form(None), user=Depends(current_user)):
    """One card's printing, condition, quantity or price paid (kept for old pages; the card view saves with Save)."""
    scan = _scan_or_404(user, scan_id)
    price = None if paid is None else (_price(paid) if paid.strip() else -1.0)
    try:
        scans.set_line(conn, scan, variant or None, condition or None, quantity or None, price)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return Response(status_code=204)


def _review_rows(user, batch: dict) -> list[dict]:
    """The matched photos Add would add, with the printings each card offers and what it starts as."""
    rows = []
    for s in scans.to_review(conn, batch["id"]):
        g = s["guesses"][0]
        lang = g.get("lang") or scans.lang_of(s, batch)
        try:
            our_card_id = _ensure_card(lang, g["set_id"], g["id"])
        except HTTPException as e:
            rows.append({"s": s, "g": g, "error": e.detail, "quantity": s["quantity"] or 1})
            continue
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (our_card_id,)).fetchone()
        card_prices, pricing = _prices_for(our_card_id, card)
        priced = [v for v in prices.by_variant(card_prices) if v in prices.real_variants(card_prices)] or prices.listed(pricing)
        if not priced and s["variant"] not in inventory.VARIANTS:  # an Add: TCGdex says which printings TCGplayer lists
            priced = prices.listed((web._live_card(our_card_id) or {}).get("pricing"))
        offered = inventory.offered_variants(card, set(priced))
        variant = s["variant"] if s["variant"] in inventory.VARIANTS else inventory.default_variant(offered, priced)
        if variant not in offered:
            offered = [variant] + offered
        rows.append({"s": s, "g": g, "card_id": our_card_id, "offered": offered, "variant": variant,
                     "condition": s["condition"] or "NM", "quantity": s["quantity"] or 1, "paid": s["paid"],
                     "word": scans.guess_word(g)})
    return rows


@router.get("/scans/{batch_id}/review")
def scan_review(batch_id: int, user=Depends(current_user)):
    """Review your batch is part of the batch screen now: old links open the batch."""
    _scan_batch_or_404(user, batch_id)
    return RedirectResponse(f"/scans/{batch_id}", status_code=303)


@router.post("/scans/{batch_id}/from")
def scan_batch_from(request: Request, batch_id: int, came: str = Form("none"), purchase_id: str = Form(""),
                    new_label: str = Form(""), new_cost: str = Form(""), to_binder: str = Form(""),
                    new_binder: str = Form(""), user=Depends(current_user)):
    """The From · Binder sheet's Done: kept on the batch for every Add from it."""
    batch = _scan_batch_or_404(user, batch_id)
    purchase = spending.get_purchase(conn, user["collection_id"], int(purchase_id)) if purchase_id.isdigit() else None
    if came == "add" and purchase is None:
        raise HTTPException(400, "Pick the purchase these came from.")
    if to_binder not in ("", "new") and not (to_binder.isdigit() and binders.get(conn, user["collection_id"], int(to_binder))):
        raise HTTPException(400, "Pick a binder.")
    if to_binder == "new" and not new_binder.strip():
        raise HTTPException(400, "Type a name for the new binder.")
    cost = None
    if came == "new" and new_cost.strip():
        cost = _price(new_cost)
    scans.set_from(conn, batch_id, came, purchase["id"] if purchase else None, new_label, cost, to_binder, new_binder)
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers={"HX-Refresh": "true"})
    return RedirectResponse(f"/scans/{batch['id']}", status_code=303)


@router.post("/scans/{batch_id}/set-all")
def scan_batch_set_all(request: Request, batch_id: int, condition: str = Form(""), paid: str = Form(""),
                       user=Depends(current_user)):
    """Set for all: condition and/or price paid on every matched card not yet added. Nothing is marked checked."""
    _scan_batch_or_404(user, batch_id)
    try:
        scans.set_all(conn, batch_id, condition or None, _price(paid) if paid.strip() else None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers={"HX-Trigger": "scan-changed"})
    return RedirectResponse(f"/scans/{batch_id}", status_code=303)


@router.post("/scans/{batch_id}/add")
@router.post("/scans/{batch_id}/review")
async def scan_review_add(request: Request, batch_id: int, user=Depends(current_user)):
    """Add N to My Stash: every matched photo not yet added, with the batch's From · Binder choice.
    Photos still being read or with no match stay in the batch."""
    batch = _scan_batch_or_404(user, batch_id)
    form = await request.form()

    def work():  # Off the event loop (it reads TCGdex per card), as one transaction
        def fail(text: str):
            return RedirectResponse(f"/scans/{batch_id}?error={quote(text)}", status_code=303)

        came = form.get("came") or batch.get("came") or "none"  # add (to a purchase) | new (purchase) | none
        purchase_id = None
        if came == "add":
            chosen = str(form.get("purchase_id") or batch.get("purchase_id") or "")
            purchase = spending.get_purchase(conn, user["collection_id"], int(chosen)) if chosen.isdigit() else None
            if purchase is None:
                return fail("Pick the purchase these came from.")
            purchase_id = purchase["id"]
        elif came == "new":
            label = (form.get("new_label") or batch.get("new_label") or "").strip()
            cost = form.get("new_cost") or ("" if batch.get("new_cost") is None else str(batch["new_cost"]))
            if not label or not cost.strip():
                return fail("Type what it was and the total.")
        origin = "pack" if came in ("add", "new") else batch["origin"]  # old batches keep the origin they were started with
        lines = []
        for row in _review_rows(user, batch):  # every row checked before any card is added
            s = row["s"]
            if "error" in row:
                return fail(row["error"])
            paid_text = form.get(f"paid-{s['id']}")
            try:
                quantity = int(form.get(f"quantity-{s['id']}") or row["quantity"])
                paid = _price(paid_text) if paid_text is not None else row["paid"]
            except (ValueError, HTTPException):
                return fail(f"Check the quantity and price paid for {row['g']['name']}.")
            lines.append((row, form.get(f"variant-{s['id']}") or row["variant"],
                          form.get(f"condition-{s['id']}") or row["condition"], quantity, paid,
                          scans._paid_currency(s, paid)))
        if not lines:
            return fail("No cards have a match yet.")
        to_binder = form.get("to_binder") if "to_binder" in form else (batch.get("binder") or "")
        binder_id = None
        if to_binder:  # Put in a binder: checked before anything is added
            try:
                binder_id, _ = _binder_choice(user, to_binder, form.get("new_binder") or batch.get("new_binder") or "")
            except ValueError as e:
                return fail(str(e))
        if came == "new":
            try:
                purchase_id = spending.create_purchase(conn, user["collection_id"], label, _price(cost) or 0,
                                                       by=user["id"], purchased_on=display.today().isoformat(), kind="packs")
            except (ValueError, HTTPException):
                return fail("Check the total.")
        if came == "new" or to_binder == "new":  # the next Add from this batch goes to the same purchase and binder
            scans.set_from(conn, batch_id, "add" if purchase_id else came, purchase_id, "", None,
                           str(binder_id) if binder_id else "", "")
        for row, variant, condition, quantity, paid, paid_currency in lines:
            try:
                scans.add_card(conn, row["s"], user["id"], row["card_id"], variant, condition, quantity, origin)
            except ValueError as e:
                conn.rollback()  # none of this Add is kept
                return fail(str(e))
            if purchase_id is not None:
                spending.record_pull(conn, purchase_id, row["card_id"], variant, condition, quantity, paid, paid_currency)
            elif paid is not None:
                spending.set_paid(conn, user["collection_id"], row["card_id"], variant, condition, paid, paid_currency)
            _store_price(row["card_id"])
            in_binder = form.get(f"binder-{row['s']['id']}") if f"binder-{row['s']['id']}" in form else row["s"]["in_binder"]
            if binder_id and in_binder:
                line = conn.execute("SELECT id FROM inventory WHERE user_id = ? AND card_id = ? AND variant = ? AND condition = ?",
                                    (user["collection_id"], row["card_id"], variant, condition)).fetchone()
                if line:  # the copies just added, taken from Not in a binder
                    binders.put_some(conn, user["collection_id"], {line["id"]: quantity}, binder_id)
        if not scans.needs_match(conn, batch_id):
            scans.drop_deleted(conn, batch_id)  # nothing left waiting, so deleted scans' photos go
        return RedirectResponse(f"/scans/{batch_id}?added=1", status_code=303)
    return await _in_thread(work)


_asked_prices: set[tuple[int, str]] = set()


def _price_matches_later(batch: dict) -> None:
    """Round 27: once a batch's photos have their matches, its matched cards are kept and priced in the background
    (TCGCSV and CardTrader for their sets, as after an Add), so scan review shows their prices before you add them.
    The page never waits; the job runs behind your own actions."""
    matches = [m for m in scans.unpriced_matches(conn, batch) if (m["scan_id"], m["our"]) not in _asked_prices]
    if not matches:
        return
    _asked_prices.update((m["scan_id"], m["our"]) for m in matches)  # once per match while the app runs
    sets = {(m["lang"], m["set_id"]) for m in matches if m.get("set_id")}
    name = batch.get("name") or f"batch {batch['id']}"

    def run(job_conn, job):
        set_ids = set()
        for lang, tcgdex_set in sorted(sets):
            our_set = catalog.local_id(lang, tcgdex_set)
            if lang in LANGUAGES and not job_conn.execute("SELECT 1 FROM sets WHERE set_id = ?", (our_set,)).fetchone():
                try:  # the set's cards stored, so they can be priced (as when you add one)
                    catalog.cache_set(job_conn, web.api_for(lang), tcgdex_set)
                except Exception as e:
                    web.log.info("set %s not stored for scan prices: %s", our_set, e)
                    continue
            set_ids.add(our_set)
        scans.keep_matches(job_conn, [m for m in matches
                                      if job_conn.execute("SELECT 1 FROM cards WHERE id = ?", (m["our"],)).fetchone()])
        if set_ids:
            sync.refresh_sets(job_conn, web.api_for, set_ids)
        with tile_prices.lock:  # the batch grid's tiles show the new prices
            for m in matches:
                tile_prices.offers.pop(m["our"], None)
    cards = len({m["our"] for m in matches})
    web.background.start(f"scan-prices:{batch['id']}",
                         f"Prices for scan batch '{name}': {cards} card{'s' if cards != 1 else ''}, "
                         f"{len(sets)} set{'s' if len(sets) != 1 else ''}", run, priority=jobs.SCHEDULED)


def scan_prices_running(batch: dict) -> bool:
    return web.background.running(f"scan-prices:{batch['id']}")


def _batch_context(user, batch: dict) -> dict:
    return {"counts": scans.counts(conn, batch["id"]),
            "purchases": [p for p in spending.purchase_choices(conn, user["collection_id"]) if p["kind"] != spending.BEFORE],
            "binders": binders.all_for(conn, user["collection_id"])}


@router.get("/scans/{batch_id}")
def scan_batch_view(request: Request, batch_id: int, user=Depends(current_user)):
    batch = _scan_batch_or_404(user, batch_id)
    items = scans.batch_scans(conn, batch_id)
    _price_matches_later(batch)
    if any(s["status"] == "waiting" for s in items) and not web.scan_worker.running():
        web.scan_worker.start()  # e.g. the app restarted, or the reader was just installed
    if any(s["status"] == "picture_waiting" for s in items) and not web.picture_worker.running():
        web.picture_worker.start()
    return templates.TemplateResponse(request, "scan_batch.html", {
        **_scan_item_context(user, None, batch), **_batch_context(user, batch), "items": items,
        "problem": web.scan_worker.problem or _scan_problem(), "error": request.query_params.get("error"), "rev": scans.batch_rev(conn, batch_id),
        "added": request.query_params.get("added"), "camera": request.query_params.get("camera"),
    })


@router.get("/scans/{batch_id}/changes")
def scan_batch_changes(batch_id: int, since: int = 0, user=Depends(current_user)):
    """Tiles changed since the page last looked, drawn and ready to swap in, and the summary line's counts."""
    batch = _scan_batch_or_404(user, batch_id)
    rev = scans.batch_rev(conn, batch_id)
    changed = scans.batch_scans(conn, batch_id, since) if rev > since else []
    _price_matches_later(batch)
    if any(s["status"] == "waiting" for s in changed) and not web.scan_worker.running():
        web.scan_worker.start()
    if any(s["status"] == "picture_waiting" for s in changed) and not web.picture_worker.running():
        web.picture_worker.start()
    row = templates.get_template("_scan_item.html")
    counts = scans.counts(conn, batch_id)
    to_confirm = conn.execute("SELECT COUNT(*) FROM scans WHERE batch_id = ? "
                              "AND status IN ('ready', 'nomatch', 'failed', 'picture_ready')",
                              (batch_id,)).fetchone()[0]
    return {"rev": rev, "to_confirm": to_confirm, **counts,
            "rows": [{"id": s["id"], "html": row.render(_scan_item_context(user, s, batch))} for s in changed]}


@router.post("/scans/{batch_id}/photos")
async def scan_upload(request: Request, batch_id: int, photos: list[UploadFile] = File(...),
                      client_id: str = Form(""), user=Depends(current_user)):
    batch = _scan_batch_or_404(user, batch_id)
    added, error = [], None
    client_id = client_id.strip()[:64] if len(photos) == 1 else ""  # One photo at a time carries the phone's id
    if client_id and scans.sent_before(conn, batch_id, client_id):
        return HTMLResponse("", headers={"X-Scan-Repeat": "1"})  # sent before: nothing more to add
    for photo in photos:
        data = await photo.read(scans.MAX_BYTES + 1)
        try:
            # Shrinking the photo (Pillow) runs off the event loop, so other pages stay quick meanwhile.
            # As one transaction of its own.
            added.append(await _in_thread(scans.add_photo, conn, batch, user["id"], data, None, client_id or None))
        except scans.Repeat:  # the same photo arrived twice at once: its row is already on the page
            return HTMLResponse("", headers={"X-Scan-Repeat": "1"})
        except ValueError as e:
            error = f"{photo.filename or 'A file'}: {e}"
        except sqlite3.OperationalError as e:  # Busy for too long; the phone keeps the photo and tries again
            log.warning("photo for batch %s not saved: %s", batch_id, e)
            return HTMLResponse('<p class="error">The app is busy. The photo is kept on your phone; try again in a moment.</p>',
                                status_code=503)
    if added:
        web.scan_worker.start()
    if request.headers.get("X-Scan-Upload"):
        # The page's uploader adds the new rows to the top of the list.
        if error and not added:
            return HTMLResponse(f'<p class="error">{escape(error)}</p>', status_code=400)  # The file name is the phone's text
        html = "".join(templates.get_template("_scan_item.html").render(
            _scan_item_context(user, scans.get_scan(conn, user["collection_id"], i), batch)) for i in reversed(added))
        return HTMLResponse(html)
    target = f"/scans/{batch_id}" + (f"?error={quote(error)}" if error else "")
    return RedirectResponse(target, status_code=303)


@router.post("/scans/{batch_id}/delete")
def scan_batch_delete(batch_id: int, user=Depends(current_user)):
    _scan_batch_or_404(user, batch_id)
    scans.delete_batch(conn, user["collection_id"], batch_id)
    return RedirectResponse("/scans", status_code=303)


@router.post("/scans-finished/clear")
def scans_clear_finished(user=Depends(current_user)):
    scans.clear_finished(conn, user["collection_id"])
    return RedirectResponse("/scans", status_code=303)
