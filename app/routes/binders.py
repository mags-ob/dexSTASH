"""Binders pages."""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import collection, extras
from .. import binder_pages, binders, shared, web
from ..web import (_Route, _cards_page, _new_code, conn, current_user, templates)

router = APIRouter(route_class=_Route)


@router.get("/collection/binders")
def binders_view(request: Request, user=Depends(current_user)):
    """My Stash > Binders: each binder with its cover card, count and value, then Not in a binder."""
    data = collection.cards(conn, user["collection_id"])
    return templates.TemplateResponse(request, "collection_binders.html", {
        "user": user, "binders": binders.summaries(conn, user["collection_id"], data["rows"]),
        "shared_ids": shared.shared_ids(conn, user["collection_id"]),
        "extras_map": extras.all_extras(conn), "error": request.query_params.get("error"),
        "done": request.query_params.get("done"),
    })


@router.post("/collection/binders")
def binder_create(name: str = Form(""), back: str = Form(""), user=Depends(current_user)):
    try:
        binder_id = binders.create(conn, user["collection_id"], name, user["id"])
    except ValueError as e:
        return RedirectResponse(f"/collection/binders?error={quote(str(e))}", status_code=303)
    return RedirectResponse(f"/collection/binders/{binder_id}", status_code=303)


def _binder_or_404(user, binder_id: str) -> dict:
    if binder_id == "none":
        return {"id": None, "name": "Not in a binder"}
    found = binders.get(conn, user["collection_id"], int(binder_id)) if binder_id.isdigit() else None
    if found is None:
        raise HTTPException(404)
    return found


def dex_of(row: dict) -> int | None:
    return web.names.species_id(row.get("twin_name") or row["name"])


def pages_for(rows: list[dict], settings: dict, find: str, page: int, pictures) -> dict:
    """The pages, the page to open and the pocket a search found (Pages view, owner's and shared)."""
    pockets = binder_pages.pockets(rows, settings["sort"], dex_of)
    pics = {}
    for r in pockets:
        if r["card_id"] not in pics:
            pics[r["card_id"]] = pictures(r)
    hit = None
    if find.strip():
        also = web.other_names(find)
        hit = binder_pages.page_of(pockets, settings["layout"],
                                   lambda r: collection.name_matches(find, r["name"], r.get("twin_name"), r["number"], also))
    pages = binder_pages.pages(pockets, settings["layout"])
    page = hit[0] if hit else max(1, min(page or 1, len(pages)))
    return {"pages": pages, "page": page, "hit": hit[1] if hit else None, "find": find.strip(), "find_page": hit[0] if hit else None,
            "pics": pics, "layout": settings["layout"]}


def _head(user, in_binder: dict, view: str, request: Request, find: str = "") -> dict:
    cid = user["collection_id"]
    data = collection.cards(conn, cid, binder="none" if in_binder["id"] is None else str(in_binder["id"]))
    return {"binder_view": view, "binder_set": binder_pages.settings(conn, cid, in_binder["id"]),
            "binder_count": data["count"], "binder_value": data["value"], "binder_sorts": binder_pages.SORTS,
            "binder_layouts": binder_pages.LAYOUTS, "binder_sorted_by": binder_pages.SORTED_BY, "find": find,
            "binder_rows": data["rows"]}


@router.get("/collection/binders/{binder_id}")
def binder_view(request: Request, binder_id: str, q: str = "", rarity: str = "", set: str = "", sort: str = "price_desc",
                method: str = "", by: str = "", origin: str = "", price: str = "", language: str = "", tag: str = "",
                added: str = "", view: str = "", page: int = 0, find: str = "", user=Depends(current_user)):
    """One binder: its pages (the default), or the usual grid with Filter, sort and bulk edit.
    'none' is Not in a binder. Each login's last view is remembered."""
    in_binder = _binder_or_404(user, binder_id)
    shown = binder_pages.view(conn, user["id"], view)
    head = _head(user, in_binder, shown, request, find)
    if shown == "grid":
        head.pop("binder_rows")
        return _cards_page(request, user, f"/collection/binders/{binder_id}", in_binder, q=q, rarity=rarity, set=set, sort=sort,
                           method=method, by=by, origin=origin, price=price, language=language, tag=tag, added=added,
                           extra=head)
    rows = head.pop("binder_rows")
    return templates.TemplateResponse(request, "binder_pages.html", {
        "user": user, "in_binder": in_binder, **head,
        **pages_for(rows, head["binder_set"], find, page,
                    lambda r: web.card_pictures.for_id(conn, r["card_id"], r.get("image_url"))),
        "share": shared.info(conn, user["collection_id"], in_binder["id"]) if in_binder["id"] else None,
        "share_open": request.query_params.get("share") == "1", "new_code": _new_code(request, in_binder),
        "done": request.query_params.get("done"),
    })


@router.post("/collection/binders/{binder_id}/settings")
def binder_settings(binder_id: str, name: str = Form(""), layout: int = Form(9), sort: str = Form("set"),
                    user=Depends(current_user)):
    """The gear: name, pockets a page and order (Not in a binder: just the last two)."""
    in_binder = _binder_or_404(user, binder_id)
    try:
        if in_binder["id"] is not None and name.strip() and name.strip() != in_binder["name"]:
            binders.rename(conn, user["collection_id"], in_binder["id"], name)
        binder_pages.save(conn, user["collection_id"], in_binder["id"], layout, sort)
    except ValueError as e:
        return RedirectResponse(f"/collection/binders/{binder_id}?done={quote(str(e))}", status_code=303)
    return RedirectResponse(f"/collection/binders/{binder_id}", status_code=303)


@router.post("/collection/binders/{binder_id}/rename")
def binder_rename(binder_id: int, name: str = Form(""), user=Depends(current_user)):
    try:
        binders.rename(conn, user["collection_id"], binder_id, name)
    except ValueError as e:
        return RedirectResponse(f"/collection/binders/{binder_id}?done={quote(str(e))}", status_code=303)
    return RedirectResponse(f"/collection/binders/{binder_id}", status_code=303)


@router.post("/collection/binders/{binder_id}/delete")
def binder_delete(binder_id: int, user=Depends(current_user)):
    """Deleting a binder moves its cards to Not in a binder."""
    binders.delete(conn, user["collection_id"], binder_id)
    return RedirectResponse(f"/collection/binders?done={quote('Binder deleted. Its cards are now Not in a binder.')}",
                            status_code=303)
