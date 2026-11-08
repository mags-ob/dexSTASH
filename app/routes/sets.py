"""Sets pages."""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import catalog, extras, inventory, prices, tile_prices, wants
from .. import tcgdex
from .. import web
from ..web import (_Route, _came_from_find, _set_sort, _set_values, conn, current_user, templates)

router = APIRouter(route_class=_Route)


@router.post("/sets")
def add_set(set_id: str = Form(...), lang: str = Form("en"), user=Depends(current_user)):
    set_id = set_id.strip()
    error = None
    for one in tcgdex.expand(lang):  # Chinese: Taiwan/Hong Kong sets first, then mainland
        try:
            catalog.cache_set(conn, web.api_for(one), set_id)
            return RedirectResponse(f"/sets/{catalog.local_id(one, set_id)}", status_code=303)
        except HTTPException:
            raise
        except Exception as e:  # network error or unknown set id
            error = e
    raise HTTPException(502, f"Could not load set '{set_id}' from TCGdex: {error}")


def _tile_prices(row, cards: list, user) -> dict[str, float]:
    """card id -> the price its tile shows (the card page's main price, round 27). A set with no TCGCSV prices
    stored is read in the background by _set_values for the next visit."""
    lang, tcgdex_set = row["language"], catalog.tcgdex_set_id(row["set_id"])
    found, _ = tile_prices.for_cards(conn, [
        {"key": c["id"], "id": c["id"], "lang": lang, "set_id": tcgdex_set, "set_name": row["name"], "number": c["number"],
         "english": c["english_name"] or (c["name"] if lang == "en" else None)} for c in cards], user["collection_id"])
    return found


def _set_context(request: Request, user, set_id: str, message: str | None = None, error: bool = False) -> dict:
    row = conn.execute("SELECT * FROM sets WHERE set_id = ?", (set_id,)).fetchone()
    if row is None:
        raise HTTPException(404)
    cards = catalog.set_cards(conn, set_id, user["collection_id"])
    summary = next((s for s in catalog.set_completion(conn, user["collection_id"]) if s["set_id"] == set_id), None)
    if summary:
        summary.update(prices.set_values(conn, user["collection_id"]).get(set_id, {}))
    return {
        "user": user, "set": row, "cards": cards, "summary": summary,
        "card_prices": _tile_prices(row, cards, user), "wanted": wants.wanted_ids(conn, user["collection_id"], set_id),
        "variants": inventory.VARIANTS, "conditions": inventory.CONDITIONS,
        "variant": request.session.get("entry_variant", "normal"),
        "condition": request.session.get("entry_condition", "NM"),
        "origins": inventory.ORIGINS, "origin": request.session.get("entry_origin", ""),
        "message": message, "error": error, "tcgdex_code": catalog.tcgdex_set_id(set_id),
        "set_sort": _set_sort(request, user),
        "set_values": _set_values(row["language"], catalog.tcgdex_set_id(set_id), row["name"], set_id),
        "extras_map": extras.all_extras(conn), "from_find": _came_from_find(request),
        "tcgdex_set_url": None if conn.execute("SELECT 1 FROM cards WHERE set_id = ? AND (source IS NOT NULL OR hand_added_by "
                                               "IS NOT NULL) LIMIT 1", (set_id,)).fetchone()
                          else f"{tcgdex.BASE_URL}/{row['language']}/sets/{quote(catalog.tcgdex_set_id(set_id))}",
    }


@router.post("/sets/{set_id}/remove")
def remove_set(set_id: str, user=Depends(current_user)):
    catalog.remove_set(conn, set_id, user["collection_id"])
    return RedirectResponse("/", status_code=303)


@router.get("/sets/{set_id}")
def set_view(request: Request, set_id: str, user=Depends(current_user)):
    return templates.TemplateResponse(request, "set.html", _set_context(request, user, set_id))
