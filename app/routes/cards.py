"""Cards pages."""
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse

from .. import catalog, extras, handmade, inventory, notes, prices, scans, spending, wants
from .. import (binders, card_pictures, recent, value as worth, chase_box, chase_pages, pokemon_words,
                tags, tcgdex)
from ..tcgdex import LANGUAGES
from .. import web
from ..web import (_Route, _binder_choice, _came_from_find, _card_pokemon, _card_tags_with_pokemon, _card_view_extras,
                   _chart, _chinese_set_to_load, _fix_row, _in_thread, _index_pictures, _is_owner, _links,
                   _pictures_ready, _price, _prices_for, _save_found_link, _scan_fix, _store_price, _tcgdex_links,
                   _username, conn, current_user, link_label, log, picture_saver, templates)

router = APIRouter(route_class=_Route)


# Before /cards/{card_id}, which would take "new" for a card id.
@router.get("/cards/new")
def card_new_form(request: Request, lang: str = "", code: str = "", number: str = "", name: str = "",
                  scan: str = "", fix: str = "", user=Depends(current_user)):
    lang = lang or request.session.get("search_lang", "zh-cn")
    return _hand_form(request, user, {"lang": lang, "code": code, "number": number, "name": name,
                                      "scan": scan, "fix": fix})


def _version_picture(card) -> tuple[str, str] | None:
    """The English version's picture for a mainland Chinese card, or the
    Japanese one for a Taiwan/Hong Kong card, when the card has none of its own."""
    try:
        if card["source"] == "chs" and card["twin_code"] and card["twin_number"]:
            from .scanner import SET_CODES
            sets = SET_CODES.get(card["twin_code"].upper()) or []
            if len(sets) != 1:
                return None
            row = conn.execute("SELECT image_url FROM cards WHERE set_id = ? AND (number = ? OR sort_number = ?)",
                               (sets[0], card["twin_number"], int(card["twin_number"]) if card["twin_number"].isdigit() else -1)).fetchone()
            image = row["image_url"] if row else tcgdex.image_url(web.api.get_card_cached(f"{sets[0]}-{card['twin_number']}").get("image"))
            return (image, "English version") if image else None
        if card["language"] == "zh-tw" and card["hand_added_by"] is None:
            image = tcgdex.image_url(web.api_for("ja").get_card_cached(catalog.tcgdex_set_id(card["id"])).get("image"))
            return (image, "Japanese version") if image else None
    except Exception as e:  # not found, or TCGdex can't be reached
        log.info("no version picture for %s: %s", card["id"], e)
    return None


@router.get("/cards/{card_id}")
def card_view(request: Request, card_id: str, user=Depends(current_user)):
    card = conn.execute(
        "SELECT c.*, s.name AS set_name, s.logo_url, s.language FROM cards c JOIN sets s ON s.set_id = c.set_id "
        "WHERE c.id = ?", (card_id,)
    ).fetchone()
    if card is None:
        raise HTTPException(404)
    card_prices, pricing = _prices_for(card_id, card)  # not kept: TCGCSV's stored price, never a live one
    recent.record(conn, card_id, card["name"], card["number"], card["image_url"], f"/cards/{card_id}")
    extra = extras.get(conn, card_id)
    links = _links(card, card["language"], pricing, extra)
    holdings = inventory.holdings(conn, user["collection_id"], card_id)
    spots = binders.placement(conn, user["collection_id"]) if holdings else {}
    binder_list = binders.all_for(conn, user["collection_id"])
    names_by_id = {b["id"]: b["name"] for b in binder_list} | {None: "Not in a binder"}
    for h in holdings:  # where each copy is
        h["spots"] = [{"binder_id": b, "qty": q, "name": names_by_id.get(b, "Not in a binder")}
                      for b, q in sorted(spots.get(h["id"], {None: h["quantity"]}).items(),
                                         key=lambda kv: (kv[0] is None, names_by_id.get(kv[0], "")))]
        h["where"] = binders.label(spots.get(h["id"], {None: h["quantity"]}), names_by_id)  # The copy card
    offered = inventory.offered_variants(card, prices.real_variants(card_prices))
    pics = extras.pictures(dict(card, pricing_json=pricing if isinstance(pricing, str) else json.dumps(pricing)), extra)
    if not card["image_url"]:
        version = _version_picture(card)
        if version:
            pics = extras.pictures(dict(card, pricing_json=pricing if isinstance(pricing, str) else json.dumps(pricing)),
                                   extra, version)
    pics = card_pictures._unique(pics + card_pictures.more(conn, card_id)) if not pics else pics
    pics = card_pictures.large(pics)
    picture_waiting = not card_pictures.known(pics) and (card_pictures.ask(conn, [card_id]) or card_pictures.waiting(conn, card_id))
    lang, tcgdex_card_id, tcgdex_set = card["language"], catalog.tcgdex_set_id(card["id"]), catalog.tcgdex_set_id(card["set_id"])
    price_chart = None  # The printing you have (else the one picked for adding), when it has history
    chart_variant = holdings[0]["variant"] if holdings else None
    if conn.execute("SELECT 1 FROM price_snapshots WHERE card_id = ? AND variant <> '' LIMIT 1", (card_id,)).fetchone():
        chart_variant = chart_variant or conn.execute(
            "SELECT variant FROM price_snapshots WHERE card_id = ? AND variant <> '' ORDER BY captured_at DESC LIMIT 1",
            (card_id,)).fetchone()[0]
        data = worth.card_series(conn, card_id, chart_variant)
        price_chart = {"card_id": card_id, "variant": chart_variant, "data": data, "chart": _chart(data["points"], 320, 90),
                       "ranges": worth.RANGE_LABELS, "variant_label": inventory.VARIANTS.get(chart_variant, chart_variant)}
    return templates.TemplateResponse(request, "card.html", {
        **_card_view_extras(request, card, card["language"], card_prices, offered, holdings, links, extra),
        "user": user, "card": card, "added": request.query_params.get("added") == "1", "holdings": holdings,
        "notes": notes.for_card(conn, user["collection_id"], card_id),
        "history": inventory.history(conn, user["collection_id"], card_id),
        "fix_row": _fix_row(user, request.query_params.get("fix")),
        "scan_fix": _scan_fix(user, request.query_params.get("scan")),
        "prices": card_prices,
        "hand_by": _username(card["hand_added_by"]), "hand_error": request.query_params.get("hand_error"),
        "twin_url": None,  # A mainland card's English version isn't shown
        "lang": lang, "tcgdex_card_id": tcgdex_card_id, "tcgdex_set": tcgdex_set,
        "tcgdex_links": _tcgdex_links(card, lang, tcgdex_card_id, tcgdex_set),
        "chase": wants.for_card(conn, user["collection_id"], card_id),
        "chase_links": chase_box.links(conn, user["collection_id"], card_id),
        "chase_photos": chase_box.photos(conn, user["collection_id"], card_id),
        "chase_error": request.query_params.get("chase_error"),
        "offered": offered, "pics": pics, "extra": extra, "typed": {k: extras.typed_for(conn, card_id, k) for k in extras.KINDS},
        "typed_default": extras.default_variant(conn, card_id),
        "picture_waiting": picture_waiting,
        "site_labels": web.LINK_LABELS, "price_chart": price_chart,
        "extra_error": request.query_params.get("extra_error"), "extra_saved": request.query_params.get("extra_saved"),
        "needs_review": None,
        "no_price": bool(holdings) and not prices.by_variant(card_prices),  # The one "Things to check" left
        "from_find": _came_from_find(request),
        "pokemon": _card_pokemon(user, card["name"], card_id if ":" in card_id else lang, card["twin_name"]),
        "pokemon_sets": chase_pages.sets_for_card(conn, user["collection_id"], card_id),
        "art_languages": chase_pages.art_languages(conn, user["collection_id"], card_id),
        "card_tags": _card_tags_with_pokemon(card_id, card), "tags_used": tags.used(conn), "binders": binder_list,
        "binder_error": request.query_params.get("binder_error"),
        "variants": inventory.VARIANTS, "conditions": inventory.CONDITIONS,
        "condition": request.session.get("entry_condition", "NM"),
        "origins": inventory.ORIGINS, "origin": request.session.get("entry_origin", ""),
    })


@router.post("/cards/{card_id}/binder")
def card_binder(card_id: str, inventory_id: int = Form(...), from_binder: str = Form(""), qty: int = Form(1),
                to_binder: str = Form(""), new_binder: str = Form(""), user=Depends(current_user)):
    """The card page's binder line: move some or all copies of one line."""
    try:
        target, _ = _binder_choice(user, to_binder, new_binder)
        binders.move(conn, user["collection_id"], inventory_id, qty, target,
                     None if from_binder in ("", "none") else int(from_binder))
    except ValueError as e:
        return RedirectResponse(f"/cards/{quote(card_id)}?binder_error={quote(str(e))}#inventory", status_code=303)
    return RedirectResponse(f"/cards/{quote(card_id)}#inventory", status_code=303)


def _pokemon_back(back: str, dex: int) -> str:
    return back if back.startswith(("/pokemon/", "/search")) and "//" not in back else f"/pokemon/{dex}"


def _words_changed(request: Request, dex: int, back: str):
    """After + word or ✕: the chip row swaps in place, and the page's results (Find's Describe)
    are asked to reload with the change; without HTMX, back to the page."""
    if request.headers.get("HX-Request"):
        ptags = pokemon_words.for_pokemon(conn, web.describer, dex) if web.describer.ready() else {"tags": [], "hidden": []}
        response = templates.TemplateResponse(request, "_pokemon_tags_row.html", {
            "dex": dex, "ptags": ptags, "back": _pokemon_back(back, dex), "on_find": back.startswith("/search")})
        response.headers["HX-Trigger"] = "words-changed"
        return response
    return RedirectResponse(_pokemon_back(back, dex) + "#ptags", status_code=303)


@router.post("/pokemon/{dex}/words/add")
def pokemon_word_add(request: Request, dex: int, word: str = Form(""), back: str = Form(""), user=Depends(current_user)):
    """+ word on a Pokémon's tags: Describe finds it by that word from now on."""
    try:
        pokemon_words.add(conn, web.describer, dex, word, user["id"])
    except ValueError:
        pass
    return _words_changed(request, dex, back)


@router.post("/pokemon/{dex}/words/remove")
def pokemon_word_remove(request: Request, dex: int, word: str = Form(...), back: str = Form(""), user=Depends(current_user)):
    """✕ deletes your own word or hides an automatic one; Data sources › Reset tags brings hidden ones back."""
    pokemon_words.remove(conn, web.describer, dex, word)
    return _words_changed(request, dex, back)


def _tags_changed(request: Request, card_id: str):
    if request.headers.get("HX-Request"):
        card = conn.execute("SELECT c.*, s.language FROM cards c JOIN sets s ON s.set_id = c.set_id WHERE c.id = ?",
                            (card_id,)).fetchone()
        response = templates.TemplateResponse(request, "_card_tags.html", {
            "card": card, "card_tags": _card_tags_with_pokemon(card_id, card), "tags_used": tags.used(conn)})
        response.headers["HX-Trigger"] = "tags-changed"
        return response
    return RedirectResponse(f"/cards/{quote(card_id)}#tags", status_code=303)


@router.post("/cards/{card_id}/tags/add")
def card_tag_add(request: Request, card_id: str, tag: str = Form(""), user=Depends(current_user)):
    """+ Tag: your own tag on this card, shared by both logins."""
    if conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone() is None:
        raise HTTPException(404)
    try:
        tags.add(conn, card_id, tag, user["id"])
    except ValueError:
        pass
    return _tags_changed(request, card_id)


@router.post("/cards/{card_id}/tags/remove")
def card_tag_remove(request: Request, card_id: str, tag: str = Form(...), user=Depends(current_user)):
    """✕ on a tag: deletes your own, hides an automatic one (Data sources › Reset tags brings it back)."""
    tags.remove(conn, card_id, tag)
    return _tags_changed(request, card_id)


@router.post("/cards/{card_id}/link/{site}")
def card_found_link(card_id: str, site: str, url: str = Form(...), pc_price: str = Form(""), variant: str = Form(""),
                    user=Depends(current_user)):
    if site not in web.LINK_LABELS:
        raise HTTPException(400)
    if conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone() is None:
        raise HTTPException(404)
    return _save_found_link(card_id, site, url, user, pc_price, variant)


@router.post("/cards/{card_id}/extras")
async def card_extras(
    request: Request, card_id: str, tcgplayer_url: str | None = Form(None), pricecharting_url: str | None = Form(None),
    manual_price: str | None = Form(None), remove_photo: str = Form(""), photo: UploadFile | None = File(None),
    link: str | None = Form(None), remove: str = Form(""), pc_price: str = Form(""), pc_form: str = Form(""),
    variant: str = Form(""), user=Depends(current_user),
):
    """A link, a price typed in, or your own photo, for any card. It stays with
    the card, owned or not, until "Back to what TCGdex has". Each Modify pop-up
    sends only its own fields; the others are kept. Modify links sends one pasted link (any site) or
    the saved link to remove. A typed price is for the printing in variant (the one you own when empty)."""
    if conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone() is None:
        raise HTTPException(404)
    data = await photo.read(handmade.MAX_BYTES + 1) if photo is not None and photo.filename else b""
    if link is not None or remove:
        return await _in_thread(_card_link_save, card_id, user, link, remove)
    if variant and manual_price is None and "manual_price" in await request.form():
        manual_price = ""  # an empty box (or a typed price's Remove): that printing's price goes
    if pc_form:  # The PriceCharting price box (empty removes the price)
        return await _in_thread(_card_pc_price_save, card_id, user, pc_price, variant)
    return await _in_thread(_card_extras_save, card_id, user, data, tcgplayer_url, pricecharting_url, manual_price,
                            remove_photo, variant)


def _card_pc_price_save(card_id, user, pc_price, variant=""):
    """The price you saw on PriceCharting for one printing, kept as typed; empty removes it."""
    try:
        extras.save(conn, card_id, user["id"], pc_price=_price(pc_price), variant=variant or None)
    except (ValueError, HTTPException) as e:
        message = e.detail if isinstance(e, HTTPException) else str(e)
        return RedirectResponse(f"/cards/{quote(card_id)}?extra_error={quote(message)}#fix", status_code=303)
    note = "Saved the PriceCharting price." if pc_price.strip() else "Removed the PriceCharting price."
    return RedirectResponse(f"/cards/{quote(card_id)}?extra_saved={quote(note)}#fix", status_code=303)


def _card_link_save(card_id, user, link, remove):
    """Modify links: one box for a link to any site; each saved link has its own Remove."""
    if remove:
        if remove not in extras.LINK_SITES:
            raise HTTPException(400)
        old = (extras.get(conn, card_id) or {}).get(f"{remove}_url")
        extras.save(conn, card_id, user["id"], **{f"{remove}_url": ""})
        note = f"Removed the {link_label(remove, old)} link."
        return RedirectResponse(f"/cards/{quote(card_id)}?extra_saved={quote(note)}#fix", status_code=303)
    return _save_found_link(card_id, None, link or "", user)


def _card_extras_save(card_id, user, data, tcgplayer_url, pricecharting_url, manual_price, remove_photo, variant=""):
    image_url = None
    try:
        if data:
            image_url = handmade.save_photo(data)
        old = extras.get(conn, card_id) or {}
        extras.save(conn, card_id, user["id"], tcgplayer_url=tcgplayer_url, pricecharting_url=pricecharting_url,
                    manual_price="keep" if manual_price is None else _price(manual_price), variant=variant or None,
                    photo=image_url if image_url else ("" if remove_photo else None))
        if (image_url or remove_photo) and old.get("photo"):
            handmade._drop_photo(old["photo"])
    except (ValueError, HTTPException) as e:
        if image_url:
            handmade._drop_photo(image_url)
        message = e.detail if isinstance(e, HTTPException) else str(e)
        return RedirectResponse(f"/cards/{card_id}?extra_error={quote(message)}#fix", status_code=303)
    conn.commit()
    picture_saver.queue([card_id])  # a new link or photo shows in every grid within seconds
    web._price_soon(card_id)  # a card you don't have: its set is read, so it keeps prices with yours from now on
    return RedirectResponse(f"/cards/{card_id}?extra_saved={quote('Saved.')}#fix", status_code=303)


@router.post("/cards/{card_id}/extras/reset")
def card_extras_reset(card_id: str, user=Depends(current_user)):
    """Undo customization: drop your links, price and photo, back to what TCGdex has."""
    old = extras.get(conn, card_id)
    extras.reset(conn, card_id)
    prices.prune(conn)  # a card you don't have stops keeping prices
    if old and old.get("photo"):
        handmade._drop_photo(old["photo"])
    picture_saver.queue([card_id])
    return RedirectResponse(f"/cards/{card_id}?extra_saved={quote('Back to what TCGdex has.')}#fix", status_code=303)


@router.post("/cards/{card_id}/adjust")
def card_adjust(
    request: Request, card_id: str, variant: str = Form(...), condition: str = Form(...), delta: int = Form(...),
    note: str = Form(""), origin: str = Form(""), paid: str = Form(""), user=Depends(current_user),
):
    if conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone() is None:
        raise HTTPException(404)
    try:
        qty = inventory.adjust(conn, user["collection_id"], card_id, variant, condition, max(min(delta, 99), -99),
                               by=user["id"], origin=origin)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if note.strip() and qty:
        notes.add(conn, user["collection_id"], card_id, user["id"], note)
    if paid.strip() and qty:
        spending.set_paid(conn, user["collection_id"], card_id, variant, condition, _price(paid))
    _store_price(card_id)
    if origin in inventory.ORIGINS:
        request.session["entry_origin"] = origin
    return RedirectResponse(f"/cards/{card_id}", status_code=303)


@router.post("/cards/{card_id}/edit")
def card_edit(
    card_id: str, variant: str = Form(...), condition: str = Form(...), to_variant: str = Form(...),
    to_condition: str = Form(...), origin: str = Form(""), paid: str = Form(""), note: str = Form(""),
    user=Depends(current_user),
):
    """Edit one inventory line: printing, condition, where you got it, price paid, note."""
    collection_id = user["collection_id"]
    if conn.execute("SELECT 1 FROM inventory WHERE user_id = ? AND card_id = ? AND variant = ? AND condition = ?",
                    (collection_id, card_id, variant, condition)).fetchone() is None:
        raise HTTPException(404, "You don't have that copy any more.")
    try:
        inventory.move(conn, collection_id, card_id, variant, condition, to_variant, to_condition, by=user["id"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    inventory.set_origin(conn, collection_id, card_id, to_variant, to_condition, origin or None)
    spending.set_paid(conn, collection_id, card_id, to_variant, to_condition, _price(paid))
    if note.strip():
        notes.add(conn, collection_id, card_id, user["id"], note)
    _store_price(card_id)
    return RedirectResponse(f"/cards/{card_id}", status_code=303)


@router.post("/cards/{card_id}/copy")
def card_copy(
    card_id: str, inventory_id: int = Form(...), scope: str = Form("all"), to_variant: str = Form(""),
    to_condition: str = Form(""), binder: str = Form(""), from_binder: str = Form(""), new_binder: str = Form(""),
    origin: str = Form(""), paid: str = Form(""), note: str = Form(""), action: str = Form("save"),
    user=Depends(current_user),
):
    """The copy card's Edit sheet. All copies of a printing + condition line, or One of them. A line keeps
    one paid price, origin and copy note, so One only changes them when it moves to another printing or condition."""
    cid, uid = user["collection_id"], user["id"]
    line = conn.execute("SELECT * FROM inventory WHERE id = ? AND user_id = ? AND card_id = ?",
                        (inventory_id, cid, card_id)).fetchone()
    if line is None:
        raise HTTPException(404, "You don't have that copy any more.")
    variant, condition, qty = line["variant"], line["condition"], line["quantity"]
    one = scope == "one" and qty > 1
    back = f"/cards/{quote(card_id)}"
    if action == "remove":
        inventory.adjust(conn, cid, card_id, variant, condition, -1 if one else -qty, by=uid)
        _store_price(card_id)
        return RedirectResponse(back, status_code=303)
    to_variant, to_condition = to_variant or variant, to_condition or condition
    if to_variant not in inventory.VARIANTS or to_condition not in inventory.CONDITIONS:
        raise HTTPException(400, "Unknown printing or condition.")
    try:
        target = _binder_choice(user, binder, new_binder)[0] if binder else "keep"
    except ValueError as e:
        return RedirectResponse(f"{back}?binder_error={quote(str(e))}", status_code=303)
    source = None if from_binder in ("", "none") else int(from_binder) if from_binder.isdigit() else None
    moving = (to_variant, to_condition) != (variant, condition)
    before = binders.placement(conn, cid).get(inventory_id, {None: qty})

    def line_id() -> int:
        return conn.execute("SELECT id FROM inventory WHERE user_id = ? AND card_id = ? AND variant = ? AND condition = ?",
                            (cid, card_id, to_variant, to_condition)).fetchone()["id"]

    def details(lid: int) -> None:
        inventory.set_origin(conn, cid, card_id, to_variant, to_condition, origin or None)
        spending.set_paid(conn, cid, card_id, to_variant, to_condition, _price(paid))
        inventory.set_note(conn, lid, note)

    if one:
        if moving:
            inventory.adjust(conn, cid, card_id, variant, condition, -1, by=uid)
            inventory.adjust(conn, cid, card_id, to_variant, to_condition, 1, by=uid)
            lid = line_id()
            details(lid)
            if target != "keep" and target is not None:
                binders.move(conn, cid, lid, 1, target, None)
            elif target == "keep" and source is not None:  # the copy stays in the binder it was in
                binders.move(conn, cid, lid, 1, source, None)
        elif target != "keep" and target != source:
            binders.move(conn, cid, inventory_id, 1, target, source)
    else:
        if moving:
            inventory.move(conn, cid, card_id, variant, condition, to_variant, to_condition, by=uid)
        lid = line_id() if moving else inventory_id
        details(lid)
        if target != "keep" and (len(before) > 1 or target not in before):
            binders.move_all(conn, cid, [lid], target)
        elif moving:  # copies keep their binders
            for b, q in before.items():
                if b is not None:
                    binders.move(conn, cid, lid, q, b, None)
    _store_price(card_id)
    return RedirectResponse(back, status_code=303)


@router.post("/cards/{card_id}/move")
def card_move(
    request: Request, card_id: str, variant: str = Form(...), condition: str = Form(...),
    to_variant: str = Form(...), to_condition: str = Form(""), back: str = Form(""), user=Depends(current_user),
):
    """Change the printing or condition of all copies on one line, e.g. a
    Reverse Holo that was really a Normal, or a copy that turned out Damaged."""
    try:
        moved = inventory.move(conn, user["collection_id"], card_id, variant, condition, to_variant,
                               to_condition or condition, by=user["id"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    _store_price(card_id)
    if back == "collection":
        return RedirectResponse(f"/collection?moved={moved}", status_code=303)
    return RedirectResponse(f"/cards/{card_id}", status_code=303)


@router.post("/cards/{card_id}/details")
def card_details(
    card_id: str, variant: str = Form(...), condition: str = Form(...), paid: str = Form(""),
    origin: str = Form("keep"), user=Depends(current_user),
):
    """The price paid per copy and where it came from, for one printing and condition you have."""
    if origin != "keep":  # forms without the picker leave it as it is
        inventory.set_origin(conn, user["collection_id"], card_id, variant, condition, origin)
    spending.set_paid(conn, user["collection_id"], card_id, variant, condition, _price(paid))
    return RedirectResponse(f"/cards/{card_id}", status_code=303)


@router.post("/cards/{card_id}/notes")
def card_note_add(card_id: str, text: str = Form(...), user=Depends(current_user)):
    """A note about the whole card."""
    if conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone() is None:
        raise HTTPException(404)
    notes.add(conn, user["collection_id"], card_id, user["id"], text)
    return RedirectResponse(f"/cards/{card_id}#notes", status_code=303)


@router.post("/notes/{note_id}/delete")
def note_delete(note_id: int, card_id: str = Form(...), user=Depends(current_user)):
    if not notes.delete(conn, user["collection_id"], note_id, user):
        raise HTTPException(403, "Only the person who wrote a note, or an admin, can delete it.")
    return RedirectResponse(f"/cards/{card_id}#notes", status_code=303)



# Cards added by hand need one exact language; Chinese ones are mainland prints.
HAND_CHOICES = {"en": "English", "ja": "Japanese", "zh-cn": "Chinese", "ko": "Korean"}


def _hand_form(request: Request, user, values: dict, error: str | None = None, status_code: int = 200):
    lang = values.get("lang") if values.get("lang") in LANGUAGES else "zh-cn"
    for key in ("scan", "fix"):
        values[key] = values[key] if str(values.get(key) or "").isdigit() else ""
    scan_fix = _scan_fix(user, values.get("scan"))
    return templates.TemplateResponse(request, "card_new.html", {
        "user": user, "v": {**values, "lang": lang}, "error": error, "languages": HAND_CHOICES,
        "known_sets": conn.execute("SELECT set_id, name FROM sets WHERE language = ? ORDER BY release_date DESC, name",
                                   (lang,)).fetchall(),
        "tcgdex_set_id": catalog.tcgdex_set_id, "scan_fix": scan_fix, "fix_row": _fix_row(user, values.get("fix")),
        "load_set": _chinese_set_to_load(lang, values.get("code")),
        "owner": _is_owner(user),
    }, status_code=status_code)


@router.post("/cards/new")
async def card_new(
    request: Request, lang: str = Form(...), code: str = Form(""), number: str = Form(""), name: str = Form(""),
    rarity: str = Form(""), set_name: str = Form(""), scan: str = Form(""), fix: str = Form(""),
    use_scan_photo: str = Form(""), photo: UploadFile | None = File(None), user=Depends(current_user),
):
    data = await photo.read(handmade.MAX_BYTES + 1) if photo is not None and photo.filename else b""
    return await _in_thread(_card_new_save, request, user, lang, code, number, name, rarity, set_name, scan, fix,
                            use_scan_photo, data)


def _card_new_save(request, user, lang, code, number, name, rarity, set_name, scan, fix, use_scan_photo, data):
    values = {"lang": lang, "code": code, "number": number, "name": name, "rarity": rarity, "set_name": set_name,
              "scan": scan, "fix": fix}
    if lang not in LANGUAGES:
        return _hand_form(request, user, values, "Pick a language.", 400)
    scan_fix = _scan_fix(user, scan)
    code = code.strip()
    try:
        card_number, _ = handmade.split_number(number)
    except ValueError as e:
        return _hand_form(request, user, values, str(e), 400)

    # TCGdex may know the set (its name and card count), or even have the card by now.
    tcgdex_set = None
    if code and handmade.find_set(conn, lang, code) is None:
        try:
            tcgdex_set = web.api_for(lang).get_set(code)
        except Exception:
            tcgdex_set = None
        if tcgdex_set and tcgdex_set.get("cards"):
            catalog.cache_set(conn, web.api_for(lang), tcgdex_set["id"])
            found = inventory.find_card(conn, catalog.local_id(lang, tcgdex_set["id"]), card_number)
            if found:
                carry = f"?scan={scan}" if scan_fix else (f"?fix={fix}" if fix else "")
                return RedirectResponse(f"/cards/{found['id']}{carry}", status_code=303)

    image_url = None
    try:
        if data:
            image_url = handmade.save_photo(data)
        elif use_scan_photo and scan_fix and scans.photo_path(scan_fix):
            image_url = handmade.save_photo(scans.photo_path(scan_fix).read_bytes())
        card_id = handmade.add(conn, lang, code, number, name, user["id"], rarity=rarity, set_name=set_name,
                               tcgdex_set=tcgdex_set, image_url=image_url)
        if image_url and _pictures_ready():
            _index_pictures()  # the photo goes in the picture index, so the next copy is recognized
    except ValueError as e:
        if image_url:
            handmade._drop_photo(image_url)
        return _hand_form(request, user, values, str(e), 400)

    if scan_fix:  # straight back to the photo, with this card picked
        card = conn.execute("SELECT c.*, s.name AS set_name FROM cards c JOIN sets s ON s.set_id = c.set_id WHERE c.id = ?",
                            (card_id,)).fetchone()
        scans.use_card(conn, scan_fix, {
            "id": catalog.tcgdex_set_id(card_id), "set_id": catalog.tcgdex_set_id(card["set_id"]), "lang": lang,
            "number": card["number"], "name": card["name"], "image": card["image_url"], "set_name": card["set_name"],
            "score": None, "sure": False, "by_hand": True})
        return RedirectResponse(f"/scans/{scan_fix['batch_id']}#scan-{scan_fix['id']}", status_code=303)
    carry = f"?fix={fix}" if _fix_row(user, fix) else ""
    return RedirectResponse(f"/cards/{card_id}{carry}", status_code=303)


@router.post("/cards/{card_id}/hand")
async def card_hand_edit(request: Request, card_id: str, name: str = Form(...), rarity: str = Form(""),
                         photo: UploadFile | None = File(None), user=Depends(current_user)):
    data = await photo.read(handmade.MAX_BYTES + 1) if photo is not None and photo.filename else b""
    return await _in_thread(_card_hand_save, card_id, name, rarity, data)


def _card_hand_save(card_id, name, rarity, data):
    image_url = None
    try:
        if data:
            image_url = handmade.save_photo(data)
        handmade.update(conn, card_id, name, rarity, image_url)
    except ValueError as e:
        if image_url:
            handmade._drop_photo(image_url)
        return RedirectResponse(f"/cards/{card_id}?hand_error={quote(str(e))}", status_code=303)
    return RedirectResponse(f"/cards/{card_id}", status_code=303)


@router.post("/cards/{card_id}/hand/delete")
def card_hand_delete(card_id: str, user=Depends(current_user)):
    try:
        set_id = handmade.delete(conn, card_id)
    except ValueError as e:
        return RedirectResponse(f"/cards/{card_id}?hand_error={quote(str(e))}", status_code=303)
    return RedirectResponse(f"/sets/{set_id}" if set_id else "/", status_code=303)
