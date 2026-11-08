"""Pictures pages."""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .. import card_pictures, chinese, handmade, sites
from ..web import (_Route, _chs_download, _in_thread, _picture_reply, conn, current_user, log)

router = APIRouter(route_class=_Route)


PICTURE_ASK_CAP = 24  # Blank tiles asked about per page


@router.post("/pictures/ask")
async def pictures_ask(request: Request, user=Depends(current_user)):
    """Find and scan tiles that showed only a sprite ask for a one-off picture lookup after the
    page has loaded (never on the page's own request). The scanner container looks them up."""
    try:
        ids = (await request.json()).get("ids") or []
    except Exception:
        ids = []
    ids = [i for i in ids if isinstance(i, str) and 0 < len(i) < 80][:PICTURE_ASK_CAP]
    return JSONResponse(await _in_thread(_ask, ids))  # it writes: off the loop that answers every page


def _ask(ids: list[str]) -> dict:
    asked = card_pictures.ask(conn, ids, failed=True) if ids else 0
    return {"asked": asked, "waiting": [i for i in ids if card_pictures.waiting(conn, i)]}


@router.get("/pictures/found")
def pictures_found(ids: str = "", user=Depends(current_user)):
    """The pictures found so far for those tiles, and which are still being looked up."""
    wanted = [i for i in ids.split(",") if i][:PICTURE_ASK_CAP]
    found = {}
    for card_id in wanted:
        row = conn.execute("SELECT url FROM picture_lookups WHERE card_id = ? AND found = 1", (card_id,)).fetchone()
        if row:
            found[card_id] = row["url"]
    return JSONResponse({"found": found, "waiting": [i for i in wanted if i not in found and card_pictures.waiting(conn, i)]})


@router.get("/pictures/lookup/{card_id:path}")
def picture_lookup_result(card_id: str, user=Depends(current_user)):
    """A card page waiting on a picture lookup asks every few seconds."""
    pics = card_pictures.known(card_pictures.more(conn, card_id))
    return JSONResponse({"pics": pics, "waiting": card_pictures.waiting(conn, card_id)})


@router.get("/chs-img/{collection_id}/{number}")
async def chs_picture(collection_id: str, number: str, user=Depends(current_user)):
    """A mainland Chinese card's picture from GitHub. Kept only for cards someone has."""
    try:
        found = await _chs_download(collection_id, number, chinese.is_owned_picture(conn, collection_id, number))
    except sites.Off:  # Switched off in Data sources; the tile tries its next picture
        raise HTTPException(404)
    except Exception as e:  # Shows under Problems in the activity log, with the reason
        log.warning("Chinese picture %s/%s not downloaded from GitHub: %s: %s", collection_id, number, e.__class__.__name__, e)
        raise HTTPException(404)
    headers = {"Cache-Control": "private, max-age=86400"}
    if isinstance(found, bytes):
        return Response(found, media_type="image/jpeg", headers=headers)
    return await run_in_threadpool(_picture_reply, found, headers)


@router.get("/card-pictures/{name}")
def saved_card_picture(name: str, user=Depends(current_user)):
    """A saved card picture. Each file has its own name, so it can be cached for good."""
    if not re.fullmatch(r"[0-9a-f]{32}\.jpg", name):
        raise HTTPException(404)
    path = card_pictures.FOLDER / name
    if not path.exists():
        raise HTTPException(404)
    return _picture_reply(path, {"Cache-Control": "private, max-age=31536000, immutable"})


@router.get("/card-photos/{name}")
def card_photo(name: str, user=Depends(current_user)):
    path = handmade.photo_path(name)
    if path is None:
        raise HTTPException(404)
    return _picture_reply(path, {"Cache-Control": "private, max-age=86400"})
