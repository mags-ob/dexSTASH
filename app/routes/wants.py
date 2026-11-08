"""Wants pages."""
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import catalog, celebrate, display, handmade, prices, spending, wants
from .. import chase_box
from ..web import (_Route, _ensure_card, _in_thread, conn, current_user, templates)

router = APIRouter(route_class=_Route)


@router.post("/wants/add")
def wants_add(request: Request, card_id: str = Form(...), set_id: str = Form(...), lang: str = Form("en"),
              user=Depends(current_user)):
    """Chase a card. card_id and set_id are TCGdex ids in lang, so a card can be
    chased straight from a preview."""
    our_card_id = _ensure_card(lang, set_id, card_id)
    wants.add(conn, user["collection_id"], our_card_id, by=user["id"])
    return RedirectResponse(f"/cards/{our_card_id}", status_code=303)


@router.post("/wants/{want_id}/remove")
def wants_remove(request: Request, want_id: int, user=Depends(current_user)):
    wants.remove(conn, user["collection_id"], want_id)
    back = request.headers.get("referer", "")
    # Back to the page the button was on (a card or the chases list), only within this app.
    return RedirectResponse(back if back.startswith(str(request.base_url)) else "/chases", status_code=303)


@router.get("/got")
def got_card(request: Request, user=Depends(current_user)):
    """The "You got it!" pop-up for chased cards this person just added (nothing when there are none)."""
    got = celebrate.take(conn, user)
    if not got:
        return HTMLResponse("")
    return templates.TemplateResponse(request, "_got.html", {"user": user, "got": got})


@router.post("/chases/stop")
async def chases_stop(request: Request, user=Depends(current_user)):
    """Yes on the pop-up: stop chasing every card it listed."""
    form = await request.form()
    for want_id in form.getlist("want_id"):
        if str(want_id).isdigit():
            wants.remove(conn, user["collection_id"], int(want_id))
    back = request.headers.get("referer", "")
    return RedirectResponse(back if back.startswith(str(request.base_url)) else "/chases", status_code=303)


@router.post("/cards/{card_id}/chase/links")
def chase_link_add(card_id: str, url: str = Form(""), user=Depends(current_user)):
    """A link in the card's ★ Chase box: any website."""
    _chased_or_404(user, card_id)
    try:
        chase_box.add_link(conn, user["collection_id"], card_id, url, user["id"])
    except ValueError as e:
        return RedirectResponse(f"/cards/{card_id}?chase_error={quote(str(e))}#chase", status_code=303)
    return RedirectResponse(f"/cards/{card_id}#chase", status_code=303)


@router.post("/cards/{card_id}/chase/links/{link_id}/delete")
def chase_link_delete(card_id: str, link_id: int, user=Depends(current_user)):
    chase_box.remove_link(conn, user["collection_id"], link_id)
    return RedirectResponse(f"/cards/{card_id}#chase", status_code=303)


@router.post("/cards/{card_id}/chase/photos")
async def chase_photo_add(card_id: str, photo: UploadFile | None = File(None), user=Depends(current_user)):
    """A photo in the card's ★ Chase box (stored like a card's own photo)."""
    _chased_or_404(user, card_id)
    data = await photo.read(handmade.MAX_BYTES + 1) if photo is not None and photo.filename else b""

    def work():  # Off the event loop, as one transaction
        if not data:
            return RedirectResponse(f"/cards/{card_id}?chase_error={quote('Pick a photo first.')}#chase", status_code=303)
        try:
            chase_box.add_photo(conn, user["collection_id"], card_id, data, user["id"])
        except ValueError as e:
            return RedirectResponse(f"/cards/{card_id}?chase_error={quote(str(e))}#chase", status_code=303)
        return RedirectResponse(f"/cards/{card_id}#chase", status_code=303)
    return await _in_thread(work)


@router.post("/cards/{card_id}/chase/photos/{photo_id}/delete")
def chase_photo_delete(card_id: str, photo_id: int, user=Depends(current_user)):
    chase_box.remove_photo(conn, user["collection_id"], photo_id)
    return RedirectResponse(f"/cards/{card_id}#chase", status_code=303)


def _chased_or_404(user, card_id: str) -> None:
    if wants.for_card(conn, user["collection_id"], card_id) is None:
        raise HTTPException(404, "You're not chasing this card.")


@router.get("/spending")
def spending_view(request: Request, user=Depends(current_user)):
    collection_id = user["collection_id"]
    return templates.TemplateResponse(request, "spending.html", {
        "user": user, "months": spending.by_month(conn, collection_id), "before": spending.before_tracking(conn, collection_id),
        "totals": spending.totals(conn, collection_id), "value": prices.collection_value(conn, collection_id),
        "sets": catalog.set_completion(conn, collection_id, include_empty=False), "kinds": spending.KINDS,
        "today": display.today().isoformat(),
    })
