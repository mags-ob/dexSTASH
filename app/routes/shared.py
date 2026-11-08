"""Shared pages."""
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from .. import settings
from .. import auth, catalog, collection, extras, inventory, prices
from .. import binder_pages, card_pictures, shared, tags
from ..web import (_Route, _chs_download, _in_thread, _links, _picture_reply, _sprite, _sprite_for, _tcgdex_links,
                   conn, current_user, log, SECRET_KEY, templates)

router = APIRouter(route_class=_Route)


# Owner side: the Share sheet on a binder's page. Visitor side: /b/<token>, which never reads or writes the
# app's own session; its only cookie is dexstash_pass, for that one link.

def _shared_back(binder_id: int, error: str | None = None) -> RedirectResponse:
    return RedirectResponse(f"/collection/binders/{binder_id}?share=1" + (f"&done={quote(error)}" if error else ""),
                            status_code=303)


@router.post("/collection/binders/{binder_id}/share")
def binder_share(binder_id: int, on: str = Form(""), user=Depends(current_user)):
    try:
        shared.share(conn, user["collection_id"], binder_id, bool(on))
    except ValueError:
        raise HTTPException(404)
    return _shared_back(binder_id)


@router.post("/collection/binders/{binder_id}/share/new")
def binder_share_new(binder_id: int, user=Depends(current_user)):
    shared.new_link(conn, user["collection_id"], binder_id)
    return _shared_back(binder_id)


@router.post("/collection/binders/{binder_id}/codes")
def binder_code_add(request: Request, binder_id: int, name: str = Form(""), user=Depends(current_user)):
    try:
        code = shared.make_code(conn, user["collection_id"], binder_id, name, user["id"], SECRET_KEY)
    except ValueError as e:
        return _shared_back(binder_id, str(e))
    request.session["new_code"] = {"binder": binder_id, "code": code, "name": " ".join(name.split())[:shared.MAX_NAME]}
    return _shared_back(binder_id)


@router.post("/collection/binders/{binder_id}/codes/{code_id}/revoke")
def binder_code_revoke(binder_id: int, code_id: int, user=Depends(current_user)):
    shared.revoke(conn, user["collection_id"], binder_id, code_id, user["id"])
    return _shared_back(binder_id)


@router.post("/collection/binders/{binder_id}/codes/history/clear")
def binder_code_history_clear(binder_id: int, user=Depends(current_user)):
    shared.clear_history(conn, user["collection_id"], binder_id)
    return _shared_back(binder_id)


share_limit = auth.Lockout(limit=10, window=600)  # wrong codes and unknown links, per address
_SHARE_KEY = "shared binder"
_PRIVATE_PICTURES = ("Your photo", card_pictures.SCAN)


def _private_saved(card) -> bool:
    """The saved copy is your own photo or scan: by its label, or because it was copied from a photo you
    took for a card added by hand (its image_url is /card-photos/…)."""
    src = card["saved_src"] or ""
    # A copy of a mainland Chinese dataset picture (/chs-img/...) is shown like a web picture.
    return card["saved_from"] in _PRIVATE_PICTURES or not (src.startswith("https://") or src.startswith("/chs-img/"))


def _public(request: Request, template: str, ctx: dict, status: int = 200):
    response = templates.TemplateResponse(request, template, ctx, status_code=status)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _visitor(request: Request, token: str):
    """(binder, code id, refusal): the shared binder and the device's standing pass, or the page to show.
    A device with a good pass always gets in: a lockout (from wrong codes on the same Wi-Fi, say) only stops
    devices without one."""
    address = request.client.host if request.client else ""
    binder = shared.by_token(conn, token)
    code_id = shared.check_pass(conn, SECRET_KEY, binder, request.cookies.get(shared.PASS_COOKIE)) if binder else None
    if code_id:
        return binder, code_id, None
    if share_limit.locked_for(_SHARE_KEY, address):
        return None, None, _public(request, "public_message.html", {"message": "Too many tries. Wait a few minutes and try again."}, 429)
    if binder is None:
        share_limit.failed(_SHARE_KEY, address)
        return None, None, _public(request, "public_message.html", {"message": "This binder isn't shared any more."}, 404)
    return binder, None, None


def _binder_rows(binder: dict, **filters) -> list[dict]:
    data = collection.cards(conn, binder["collection_id"], binder=str(binder["id"]), **filters)
    return shared.public_rows(data["rows"])


def _public_pictures(token: str, card_id: str) -> list[tuple[str, str]]:
    """A card's pictures for visitors: the saved copy (unless it's your photo or scan), pictures on the web, then
    the Pokémon's sprite. Never your photos, scans or anything behind the login."""
    card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    if card is None:
        return []
    out = []
    if card["saved_picture"] and not _private_saved(card):
        out.append((f"/b/{token}/picture/{quote(card_id)}", ""))
    for url, label in card_pictures.candidates(card, extras.get(conn, card_id)) + card_pictures.more(conn, card_id):
        if label in _PRIVATE_PICTURES:
            continue
        if url.startswith("https://"):
            out.append((url, ""))
        elif url.startswith("/chs-img/"):  # Through this binder's own address, never /chs-img itself
            out.append((f"/b/{token}/picture/{quote(card_id)}?chs=1", ""))
    out.append((f"/b/{token}/picture/{quote(card_id)}?sprite=1", ""))
    return list(dict.fromkeys(out))


@router.get("/b/{token}")
def public_binder(request: Request, token: str, q: str = "", set: str = "", rarity: str = "", language: str = "",
                  price: str = "", tag: str = "", printing: str = "", sort: str = "price_desc", view: str = "",
                  page: int = 0, find: str = ""):
    binder, code_id, refusal = _visitor(request, token)
    if refusal:
        return refusal
    if code_id is None:
        return _public(request, "public_code.html", {"token": token, "error": None})
    every = _binder_rows(binder)
    # The owner's pages, in their layout and order, unless the grid is asked for (or its search or filters)
    if view == "pages" or (view != "grid" and not any((q, set, rarity, language, price, tag, printing)) and sort == "price_desc"):
        from .binders import dex_of, pages_for
        kept = binder_pages.settings(conn, binder["collection_id"], binder["id"])
        raw = collection.cards(conn, binder["collection_id"], binder=str(binder["id"]))["rows"]
        ordered = shared.public_rows(binder_pages.ordered(raw, kept["sort"], dex_of))  # sorted before the details go
        return _public(request, "public_binder_pages.html", {
            "token": token, "binder": binder, "total": sum(r["value"] for r in every),
            "count": sum(r["quantity"] for r in every), "updated": prices.last_updated(conn),
            "sorted_by": binder_pages.SORTED_BY[kept["sort"]],
            **pages_for(ordered, {"layout": kept["layout"], "sort": None}, find, page,
                        lambda r: _public_pictures(token, r["card_id"]))})
    sort = sort if sort in collection.SORTS else "price_desc"
    price = price if price in collection.PRICE_RANGES else ""
    language = language if language in collection.LANGUAGE_FILTERS else ""
    # Only automatic tags: your own tags ("selling", "for Mike") stay private, and can't be probed either.
    card_tags = {cid: {**t, "tags": [x for x in t["tags"] if x[1] == "auto"]}
                 for cid, t in tags.for_cards(conn, [r["card_id"] for r in every]).items()}
    tag_options = collection.tag_choices(card_tags, [r["card_id"] for r in every])
    if tag and tag not in {value for value, _ in tag_options}:
        tag = ""
    rows = _binder_rows(binder, q=q, set_id=set, rarity=rarity, language=language, price=price, tag=tag, sort=sort)
    if printing:
        rows = [r for r in rows if r["variant"] == printing]
    for r in rows:
        r["pics"] = _public_pictures(token, r["card_id"])
    return _public(request, "public_binder.html", {
        "token": token, "binder": binder, "rows": rows, "total": sum(r["value"] for r in every),
        "count": sum(r["quantity"] for r in every), "shown": sum(r["quantity"] for r in rows),
        "updated": prices.last_updated(conn),
        "q": q, "set": set, "rarity": rarity, "language": language, "price": price, "tag": tag, "printing": printing,
        "sort": sort, "sorts": collection.SORTS, "price_ranges": collection.price_ranges(),
        "set_options": sorted({(r["set_id"], r["set_name"], r["release_date"] or "") for r in every}, key=lambda x: x[2], reverse=True),
        "rarity_options": sorted({r["rarity"] for r in every if r["rarity"]}),
        "language_options": [(k, collection.LANGUAGE_FILTERS[k]) for k in collection.LANGUAGE_FILTERS
                             if any(collection.language_group(r["language"]) == k for r in every)],
        "printing_options": [(v, inventory.VARIANTS[v]) for v in dict.fromkeys(r["variant"] for r in every)],
        "tag_options": tag_options,
        "filtered": any((q, set, rarity, language, price, tag, printing)),
    })


@router.post("/b/{token}/code")
def public_binder_code(request: Request, token: str, code: str = Form("")):
    binder, _, refusal = _visitor(request, token)
    if refusal:
        return refusal
    address = request.client.host if request.client else ""
    code_id = shared.check_code(conn, binder["id"], code, SECRET_KEY)  # a locked address never gets here (_visitor)
    if code_id is None:
        share_limit.failed(_SHARE_KEY, address)  # wrong codes count; right ones don't, so one busy Wi-Fi isn't locked out
        return _public(request, "public_code.html", {"token": token, "error": "That code doesn't work. Check it with whoever sent you the link."}, 401)
    share_limit.succeeded(_SHARE_KEY, address)
    response = RedirectResponse(f"/b/{token}", status_code=303)
    response.set_cookie(shared.PASS_COOKIE, shared.make_pass(SECRET_KEY, binder["id"], code_id), max_age=shared.PASS_DAYS * 86400,
                        path=f"/b/{token}", httponly=True, samesite="lax",
                        secure=settings.on("HTTPS_ONLY"))
    return response


def _in_binder(binder: dict, card_id: str) -> bool:
    return conn.execute("""SELECT 1 FROM binder_copies bc JOIN inventory i ON i.id = bc.inventory_id
                           WHERE bc.binder_id = ? AND i.card_id = ? AND i.user_id = ? AND bc.qty > 0 LIMIT 1""",
                        (binder["id"], card_id, binder["collection_id"])).fetchone() is not None


@router.get("/b/{token}/card/{card_id}")
def public_binder_card(request: Request, token: str, card_id: str, printing: str = ""):
    binder, code_id, refusal = _visitor(request, token)
    if refusal:
        return refusal
    rows = [r for r in _binder_rows(binder) if r["card_id"] == card_id] if code_id else []
    if not rows:
        raise HTTPException(404)
    row = next((r for r in rows if r["variant"] == printing), rows[0])
    card = conn.execute("SELECT c.*, s.name AS set_name, s.language FROM cards c JOIN sets s ON s.set_id = c.set_id WHERE c.id = ?",
                        (card_id,)).fetchone()
    links = _links(card, card["language"], card["pricing_json"], extras.get(conn, card_id), card_id)
    return _public(request, "_public_card.html", {
        "token": token, "r": row, "card": card, "pics": card_pictures.large(_public_pictures(token, card_id)), "links": links,
        "tcgdex_links": _tcgdex_links(card, card["language"], catalog.tcgdex_set_id(card_id), catalog.tcgdex_set_id(card["set_id"])),
        "updated": prices.last_updated(conn)})


@router.get("/b/{token}/picture/{card_id}")
async def public_binder_picture(request: Request, token: str, card_id: str, sprite: str = "", chs: str = ""):
    found = await _in_thread(_public_picture, request, token, card_id, sprite, chs)
    if not isinstance(found, tuple):
        return found
    (collection_id, number), headers = found
    try:  # The card's mainland Chinese dataset picture (kept, as it's in a binder)
        found = await _chs_download(collection_id, number, True)
    except Exception as e:
        log.warning("Chinese picture %s not downloaded for a shared binder: %s: %s", card_id, e.__class__.__name__, e)
        raise HTTPException(404)
    if isinstance(found, bytes):
        return Response(found, media_type="image/jpeg", headers=headers)
    return await run_in_threadpool(_picture_reply, found, headers)


def _public_picture(request: Request, token: str, card_id: str, sprite: str, chs: str):
    """The shared binder picture as a response, or ((collection, number), headers) for a Chinese one to download."""
    binder, code_id, refusal = _visitor(request, token)
    if refusal or code_id is None or not _in_binder(binder, card_id):
        raise HTTPException(404)
    card = conn.execute("SELECT name, twin_name, image_url, saved_picture, saved_from, saved_src FROM cards WHERE id = ?",
                        (card_id,)).fetchone()
    headers = {"Cache-Control": "private, max-age=86400", "X-Robots-Tag": "noindex"}
    if not sprite and not chs and card["saved_picture"] and not _private_saved(card):
        path = card_pictures.FOLDER / card["saved_picture"]
        if path.exists():
            return _picture_reply(path, headers)
    chs_pic = re.fullmatch(r"/chs-img/(\d+)/(\d+)", card["image_url"] or "")
    if chs and chs_pic:
        return (chs_pic.group(1), chs_pic.group(2)), headers
    if chs:
        raise HTTPException(404)
    url = _sprite_for(card["twin_name"] or card["name"])
    if url:
        return _sprite(int(url.rsplit("/", 1)[1].split(".")[0]), False)
    raise HTTPException(404)
