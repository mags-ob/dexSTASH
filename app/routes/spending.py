"""Spending pages."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import catalog, inventory, spending
from ..tcgdex import LANGUAGES
from ..web import (_Route, _price, _store_price, conn, current_user, templates)

router = APIRouter(route_class=_Route)


@router.post("/spending/before")
def spending_before(cost: str = Form(""), purchased_on: str = Form(""), user=Depends(current_user)):
    """The one "Spent before we started tracking" line."""
    spending.set_before_tracking(conn, user["collection_id"], _price(cost), purchased_on, user["id"])
    return RedirectResponse("/spending", status_code=303)


@router.post("/purchases")
def purchase_create(
    label: str = Form(...), cost: str = Form(...), packs: int = Form(1), set_id: str = Form(""),
    purchased_on: str = Form(""), kind: str = Form("packs"), user=Depends(current_user),
):
    if set_id and conn.execute("SELECT 1 FROM sets WHERE set_id = ?", (set_id,)).fetchone() is None:
        raise HTTPException(400, "Unknown set.")
    try:
        purchase_id = spending.create_purchase(conn, user["collection_id"], label, _price(cost) or 0, packs, set_id,
                                               purchased_on, by=user["id"], kind=kind if kind in spending.KINDS else "other")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return RedirectResponse(f"/purchases/{purchase_id}", status_code=303)


def _purchase_or_404(user, purchase_id: int) -> dict:
    purchase = spending.get_purchase(conn, user["collection_id"], purchase_id)
    if purchase is None:
        raise HTTPException(404)
    return purchase


def _purchase_context(request: Request, user, purchase: dict, message: str | None = None, error: bool = False) -> dict:
    pulls = spending.pulls(conn, purchase)
    return {
        "user": user, "purchase": purchase, "pulls": pulls,
        "pulled": sum(r["quantity"] for r in pulls), "value": sum((r["price"] or 0) * r["quantity"] for r in pulls),
        "sets": catalog.set_completion(conn, user["collection_id"]), "languages": LANGUAGES,
        "entry_set": request.session.get("pull_set") or purchase["set_id"],
        "variants": inventory.VARIANTS, "conditions": inventory.CONDITIONS,
        "variant": request.session.get("entry_variant", "normal"),
        "condition": request.session.get("entry_condition", "NM"),
        "origins": inventory.ORIGINS, "origin": request.session.get("entry_origin", ""),
        "message": message, "error": error,
    }


@router.get("/purchases/{purchase_id}")
def purchase_view(request: Request, purchase_id: int, user=Depends(current_user)):
    purchase = _purchase_or_404(user, purchase_id)
    return templates.TemplateResponse(request, "purchase.html", _purchase_context(request, user, purchase))


@router.post("/purchases/{purchase_id}/pulls")
def purchase_pull(
    request: Request, purchase_id: int, entry: str = Form(""), set_id: str = Form(""),
    variant: str = Form("normal"), condition: str = Form("NM"), user=Depends(current_user),
):
    """Typed like set-mode entry: '123', '123 x2', '-123' to take one back out."""
    purchase = _purchase_or_404(user, purchase_id)
    variant = variant if variant in inventory.VARIANTS else "normal"
    condition = condition if condition in inventory.CONDITIONS else "NM"
    request.session["entry_variant"], request.session["entry_condition"] = variant, condition
    set_id = set_id or purchase["set_id"] or ""
    request.session["pull_set"] = set_id
    parsed = inventory.parse_entry(entry)
    card = inventory.find_card(conn, set_id, parsed.number) if parsed and set_id else None
    if not set_id:
        message, error = "Pick the set these cards are from.", True
    elif parsed is None:
        message, error = f"Couldn't read '{entry}'. Type a card number, like 123 or 123 x2.", True
    elif card is None:
        message, error = f"No card #{parsed.number} in that set.", True
    else:
        delta = -parsed.quantity if parsed.remove else parsed.quantity
        qty = spending.add_pull(conn, user["collection_id"], purchase_id, card["id"], variant, condition, delta,
                                by=user["id"])
        _store_price(card["id"])
        verb = "Took out" if parsed.remove else "Pulled"
        message = f"{verb} #{card['number']} {card['name']} ({inventory.VARIANTS[variant]}). {qty} from this purchase."
        error = False
    ctx = _purchase_context(request, user, purchase, message, error)
    return templates.TemplateResponse(request, "purchase.html", ctx)


@router.post("/purchases/{purchase_id}/pulls/{pull_id}/cost")
def purchase_pull_cost(purchase_id: int, pull_id: int, cost_each: str = Form(""), user=Depends(current_user)):
    _purchase_or_404(user, purchase_id)
    spending.set_pull_cost(conn, purchase_id, pull_id, _price(cost_each))
    return RedirectResponse(f"/purchases/{purchase_id}", status_code=303)


@router.post("/purchases/{purchase_id}/delete")
def purchase_delete(purchase_id: int, user=Depends(current_user)):
    _purchase_or_404(user, purchase_id)
    spending.delete_purchase(conn, user["collection_id"], purchase_id)
    return RedirectResponse("/spending", status_code=303)
