"""Imports pages."""
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse

from .. import catalog, chinese, importer, inventory, search
from .. import binders, tcgdex
from ..tcgdex import LANGUAGES
from .. import web
from ..web import (_Route, _binder_choice, _ensure_card, _in_thread, _index_pictures, _pictures_ready, conn, current_user,
                   matcher, templates, updater)

router = APIRouter(route_class=_Route)


@router.get("/imports")
def imports_view(request: Request, user=Depends(current_user)):
    return templates.TemplateResponse(request, "imports.html", {
        "user": user, "batches": importer.batches(conn, user["collection_id"]),
        "error": request.query_params.get("error"),
    })


@router.post("/imports")
async def imports_upload(request: Request, file: UploadFile = File(...), user=Depends(current_user)):
    data = await file.read(importer.MAX_BYTES + 1)

    def work():  # Off the event loop, as one transaction
        try:
            batch_id = importer.create_batch(conn, user["collection_id"], user["id"], file.filename or "export.csv", data)
        except ValueError as e:
            return templates.TemplateResponse(request, "imports.html", {
                "user": user, "batches": importer.batches(conn, user["collection_id"]), "error": str(e),
            }, status_code=400)
        matcher.start(batch_id)
        return RedirectResponse(f"/imports/{batch_id}", status_code=303)
    return await _in_thread(work)


def _set_options(lang: str) -> list[dict] | None:
    """Sets to pick from when matching a whole set by hand; None if TCGdex can't be reached.
    For Chinese, ids carry their language ('zh-tw:SV8a')."""
    # Mainland sets come from the Chinese sets stored here; TCGdex lists them without cards.
    mainland = [{"id": f"{chinese.LANG}:{catalog.tcgdex_set_id(r['set_id'])}",
                 "name": f"{r['name']} · mainland"} for r in conn.execute(
        "SELECT set_id, name FROM sets WHERE language = ? ORDER BY release_date DESC, set_id DESC", (chinese.LANG,))]
    try:
        if lang == "zh":
            return mainland + [dict(x, id=f"zh-tw:{x['id']}", name=f"{x['name']} · Taiwan & Hong Kong")
                               for x in search.set_choices(web.api_for("zh-tw"))]
        return search.set_choices(web.api_for(lang))
    except Exception:
        return (mainland or None) if lang == "zh" else None


@router.get("/imports/set-options")
def import_set_options(request: Request, lang: str = "en", user=Depends(current_user)):
    sets = _set_options(lang) or []
    return templates.TemplateResponse(request, "_set_options.html", {"sets": sets})


def _batch_or_404(user, batch_id: int) -> dict:
    batch = importer.get_batch(conn, user["collection_id"], batch_id)
    if batch is None:
        raise HTTPException(404)
    return batch


@router.get("/imports/{batch_id}")
def import_view(request: Request, batch_id: int, user=Depends(current_user)):
    batch = _batch_or_404(user, batch_id)
    ctx = {"user": user, "batch": batch, "languages": tcgdex.CHOICES, "variants": inventory.VARIANTS,
           "binders": binders.all_for(conn, user["collection_id"])}
    if batch["status"] in ("ready", "applied", "failed"):
        rows = importer.rows(conn, batch_id)
        groups = importer.unmatched_sets(rows)
        for g in groups:
            g["sets"] = _set_options(g["lang"])
        ctx.update(rows=rows, unmatched=groups, review=importer.review(conn, user["collection_id"], batch_id),
                   result=json.loads(batch["message"]) if batch["status"] == "applied" and batch["message"] else None)
    return templates.TemplateResponse(request, "import.html", ctx)


@router.post("/imports/{batch_id}/set")
def import_map_set(batch_id: int, set_name: str = Form(...), lang: str = Form("en"), set_id: str = Form(...),
                   user=Depends(current_user)):
    batch = _batch_or_404(user, batch_id)
    if batch["status"] != "ready":
        raise HTTPException(400, "Wait for matching to finish first.")
    if lang == "zh" and ":" in set_id:
        lang, _, set_id = set_id.partition(":")
    if lang not in LANGUAGES or not set_id.strip():
        raise HTTPException(400, "Pick a language and a set.")
    importer.map_set(conn, user["collection_id"], batch_id, set_name, lang, set_id.strip())
    matcher.start(batch_id)
    return RedirectResponse(f"/imports/{batch_id}", status_code=303)


def _row_or_404(user, row_id: int) -> dict:
    row = importer.get_row(conn, user["collection_id"], row_id)
    if row is None:
        raise HTTPException(404)
    return row


@router.post("/imports/rows/{row_id}/match")
def import_match_row(row_id: int, card_id: str = Form(...), set_id: str = Form(...), lang: str = Form("en"),
                     user=Depends(current_user)):
    """card_id and set_id are TCGdex ids in lang, as on a card page or preview."""
    row = _row_or_404(user, row_id)
    our_card_id = _ensure_card(lang, set_id, card_id)
    importer.match_row(conn, user["collection_id"], row, our_card_id)
    return RedirectResponse(f"/imports/{row['batch_id']}#row-{row_id}", status_code=303)


@router.post("/imports/rows/{row_id}/ignore")
def import_ignore_row(row_id: int, undo: str = Form(""), user=Depends(current_user)):
    row = _row_or_404(user, row_id)
    if undo:
        importer.unignore_row(conn, user["collection_id"], row)
    else:
        importer.ignore_row(conn, user["collection_id"], row)
    return RedirectResponse(f"/imports/{row['batch_id']}#unmatched", status_code=303)


@router.post("/imports/{batch_id}/apply")
def import_apply(request: Request, batch_id: int, lower: str = Form(""), remove_missing: str = Form(""),
                 to_binder: str = Form(""), new_binder: str = Form(""), user=Depends(current_user)):
    _batch_or_404(user, batch_id)
    binder_id = None
    if to_binder:  # optional "Put these in binder"; the default is Not in a binder
        try:
            binder_id, _ = _binder_choice(user, to_binder, new_binder)
        except ValueError as e:
            raise HTTPException(400, str(e))
    try:
        done = importer.apply(conn, user["collection_id"], batch_id, user["id"], lower=bool(lower),
                              remove_missing=bool(remove_missing))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if binder_id is not None:
        wanted = {}
        for (card_id, variant, condition), added in done["added_lines"]:
            line = conn.execute("SELECT id FROM inventory WHERE user_id = ? AND card_id = ? AND variant = ? AND condition = ?",
                                (user["collection_id"], card_id, variant, condition)).fetchone()
            if line:
                wanted[line["id"]] = added
        binders.put_some(conn, user["collection_id"], wanted, binder_id)
    ids = sorted({card_id for (card_id, _, _), _ in done["added_lines"]})
    if ids:  # a short run of the imported cards' sets prices them in the background
        marks = ",".join("?" * len(ids))
        updater.start_sets([r[0] for r in conn.execute(f"SELECT DISTINCT set_id FROM cards WHERE id IN ({marks})", ids)])
    if _pictures_ready():
        _index_pictures()
    return RedirectResponse(f"/imports/{batch_id}", status_code=303)


@router.post("/imports/{batch_id}/match")
def import_match_again(batch_id: int, user=Depends(current_user)):
    """Start matching again: a restart stopped it."""
    batch = _batch_or_404(user, batch_id)
    if batch["status"] in ("stopped", "failed") and not matcher.running(batch_id):
        matcher.start(batch_id)
    return RedirectResponse(f"/imports/{batch_id}", status_code=303)


@router.post("/imports/{batch_id}/again")
def import_again(batch_id: int, user=Depends(current_user)):
    batch = _batch_or_404(user, batch_id)
    new_id = importer.again(conn, user["collection_id"], batch, user["id"])
    matcher.start(new_id)
    return RedirectResponse(f"/imports/{new_id}", status_code=303)


@router.post("/imports/{batch_id}/delete")
def import_delete(batch_id: int, user=Depends(current_user)):
    _batch_or_404(user, batch_id)
    importer.delete_batch(conn, user["collection_id"], batch_id)
    return RedirectResponse("/imports", status_code=303)
