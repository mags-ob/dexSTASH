"""Collection pages."""
from urllib.parse import parse_qsl, quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from .. import catalog, collection, display, export, inventory, notes, prices, spending, wants
from .. import binders, value as worth, chase_pages, themes
from .. import web
from ..web import (_Route, _binder_choice, _cards_data, _cards_page, _chart, _my_cards_filters, _NOT_FILTERS,
                   _price, _price_options, _species_choices, _species_swatches, background, conn, current_user,
                   templates)

router = APIRouter(route_class=_Route)


_CARD_FILTERS = {"q", "rarity", "set", "sort", "method", "by", "origin", "price", "language", "tag", "binder", "added"}


def _remembered_filters(request: Request, user, page: str) -> RedirectResponse | None:
    """My Stash and Chases keep their filters and sort per login until Clear filters.
    Opening the page bare brings the saved ones back; any filter in the address is saved."""
    key = f"filters:{page}"
    params = [(k, v) for k, v in request.query_params.multi_items() if k not in _NOT_FILTERS]
    if request.query_params.get("clear"):
        conn.execute("DELETE FROM user_prefs WHERE user_id = ? AND key = ?", (user["id"], key))
        conn.commit()
        return RedirectResponse(f"/{page}", status_code=303)
    if params:
        value = urlencode([(k, v) for k, v in params if v])
        conn.execute("INSERT INTO user_prefs (user_id, key, value) VALUES (?, ?, ?) "
                     "ON CONFLICT (user_id, key) DO UPDATE SET value = excluded.value", (user["id"], key, value))
        conn.commit()
        return None
    row = conn.execute("SELECT value FROM user_prefs WHERE user_id = ? AND key = ?", (user["id"], key)).fetchone()
    if row and row[0]:
        extra = urlencode([(k, v) for k, v in request.query_params.multi_items() if k in _NOT_FILTERS])
        return RedirectResponse(f"/{page}?{row[0]}" + (f"&{extra}" if extra else ""), status_code=303)
    return None


@router.get("/collection")
def collection_view(
    request: Request, q: str = "", rarity: str = "", set: str = "", sort: str = "price_desc",
    method: str = "", by: str = "", origin: str = "", price: str = "", language: str = "", tag: str = "",
    binder: str = "", added: str = "", user=Depends(current_user),
):
    if (back := _remembered_filters(request, user, "collection")) is not None:
        return back
    return _cards_page(request, user, "/collection", None, q=q, rarity=rarity, set=set, sort=sort, method=method, by=by,
                       origin=origin, price=price, language=language, tag=tag, binder=binder, added=added)


def _bulk_lines(keys: list[str]) -> list[tuple[str, str, str]]:
    """'card_id|variant|condition' keys from the Select checkboxes."""
    lines = []
    for key in keys:
        card_id, _, rest = key.partition("|")
        variant, _, condition = rest.partition("|")
        if card_id and variant in inventory.VARIANTS and condition in inventory.CONDITIONS:
            lines.append((card_id, variant, condition))
    return lines


@router.post("/collection/bulk")
def collection_bulk(
    request: Request, action: str = Form(...), lines: list[str] = Form([]), paid: str = Form(""), mode: str = Form("each"),
    note: str = Form(""), purchase_id: str = Form(""), new_label: str = Form(""), new_cost: str = Form(""),
    back: str = Form(""), back_path: str = Form(""), to_binder: str = Form(""), new_binder: str = Form(""),
    from_binder: str = Form(""), select_all: str = Form("", alias="all"), remove_from: str = Form("stash"),
    user=Depends(current_user),
):
    """Several cards at once, from My Stash' Select: set the price paid, add a
    note, put them in a spending entry, move them to a binder, or remove them."""
    collection_id = user["collection_id"]
    picked = _bulk_lines(lines)
    if select_all == "1":  # "Select all N" is every card the filters match, loaded or not
        query = dict(parse_qsl(back if not back.startswith(("/", "http")) else ""))
        in_binder = None
        if from_binder == "none" or from_binder.isdigit():
            in_binder = {"id": None if from_binder == "none" else int(from_binder), "name": ""}
        rows = _cards_data(user, in_binder, **{k: v for k, v in query.items() if k in _CARD_FILTERS})[0]["rows"]
        picked = [(r["card_id"], r["variant"], r["condition"]) for r in rows]
    if not picked:
        raise HTTPException(400, "Select at least one card.")
    if action == "paid":
        copies = spending.set_paid_many(conn, collection_id, picked, _price(paid), split=mode == "split")
        done = f"Set the price paid on {copies} cop{'y' if copies == 1 else 'ies'}."
    elif action == "note":
        if not note.strip():
            raise HTTPException(400, "Write the note first.")
        cards = sorted({card_id for card_id, _, _ in picked})
        for card_id in cards:
            notes.add(conn, collection_id, card_id, user["id"], note, commit=False)
        conn.commit()
        done = f"Added the note to {len(cards)} card{'' if len(cards) == 1 else 's'}."
    elif action == "spending":
        if purchase_id == "new" or not purchase_id:
            try:
                pid = spending.create_purchase(conn, collection_id, new_label, _price(new_cost) or 0, by=user["id"])
            except ValueError as e:
                raise HTTPException(400, str(e))
        elif purchase_id.isdigit():
            pid = int(purchase_id)
        else:
            raise HTTPException(400, "Pick a spending entry.")
        try:
            copies = spending.add_lines(conn, collection_id, pid, picked)
        except ValueError as e:
            raise HTTPException(400, str(e))
        label = spending.get_purchase(conn, collection_id, pid)["label"]
        done = f"Added {copies} cop{'y' if copies == 1 else 'ies'} to “{label}”."
    elif action == "binder":
        try:
            target, name = _binder_choice(user, to_binder, new_binder)
        except ValueError as e:
            raise HTTPException(400, str(e))
        ids = _inventory_ids(collection_id, picked)
        if from_binder:  # inside a binder: only the copies in it move
            source = None if from_binder == "none" else int(from_binder)
            spots = binders.placement(conn, collection_id)
            copies = sum(binders.move(conn, collection_id, i, spots.get(i, {}).get(source, 0), target, source) for i in ids)
        else:
            copies = binders.move_all(conn, collection_id, ids, target)
        done = f"Moved {copies} cop{'y' if copies == 1 else 'ies'} to {name}."
    elif action == "remove" and remove_from == "binder" and from_binder.isdigit():
        # Only out of this binder: the copies stay in your stash, Not in a binder.
        source, ids = int(from_binder), _inventory_ids(collection_id, picked)
        spots = binders.placement(conn, collection_id)
        copies = sum(binders.move(conn, collection_id, i, spots.get(i, {}).get(source, 0), None, source) for i in ids)
        done = f"Took {copies} cop{'y' if copies == 1 else 'ies'} out of the binder."
    elif action == "remove":
        copies, cards = 0, set()
        for card_id, variant, condition in picked:
            row = conn.execute("SELECT quantity FROM inventory WHERE user_id = ? AND card_id = ? AND variant = ? "
                               "AND condition = ?", (collection_id, card_id, variant, condition)).fetchone()
            if row:
                inventory.set_quantity(conn, collection_id, card_id, variant, condition, 0, by=user["id"],
                                       source="bulk", cheer=False)
                copies, cards = copies + row["quantity"], cards | {card_id}
        conn.commit()
        binders.tidy(conn, collection_id)
        done = (f"Removed {len(cards)} card{'' if len(cards) == 1 else 's'} "
                f"({copies} cop{'y' if copies == 1 else 'ies'}) from your stash.")
    else:
        raise HTTPException(400, "Unknown action.")
    query = back if back and not back.startswith(("/", "http")) else ""
    query = "&".join(part for part in query.split("&") if part and not part.startswith("done="))
    path = back_path if back_path.startswith("/collection") and "//" not in back_path else "/collection"
    return RedirectResponse(f"{path}?{query}{'&' if query else ''}done={quote(done)}", status_code=303)


def _inventory_ids(collection_id: int, picked: list[tuple[str, str, str]]) -> list[int]:
    out = []
    for card_id, variant, condition in picked:
        row = conn.execute("SELECT id FROM inventory WHERE user_id = ? AND card_id = ? AND variant = ? AND condition = ?",
                           (collection_id, card_id, variant, condition)).fetchone()
        if row:
            out.append(row["id"])
    return out


SET_SORTS = {"complete": "Most complete", "newest": "Newest", "name": "Name", "value": "Your value"}
SET_SHOW = {"complete": "Complete", "incomplete": "Not complete"}


@router.get("/collection/sets")
def collection_sets(request: Request, q: str = "", language: str = "", sort: str = "complete", show: str = "",
                    user=Depends(current_user)):
    """My Stash › Sets: the sets you have cards in, in Home's order, with a Filter panel remembered per login."""
    if (back := _remembered_filters(request, user, "collection/sets")) is not None:
        return back
    language = language if language in collection.LANGUAGE_FILTERS else ""
    sort = sort if sort in SET_SORTS else "complete"
    show = show if show in SET_SHOW else ""
    collection_id = user["collection_id"]
    sets = catalog.set_completion(conn, collection_id, include_empty=False)
    values = prices.set_values(conn, collection_id)
    for st in sets:
        st.update(values.get(st["set_id"], {}))
        st["shown_name"] = display.set_name(st["set_id"], st["name"])
    languages = [k for k in collection.LANGUAGE_FILTERS if any(collection.language_group(st["language"]) == k for st in sets)]
    if q.strip():
        folded = collection._fold(q.strip())
        sets = [st for st in sets if folded in collection._fold(st["shown_name"]) or folded in collection._fold(st["name"] or "")
                or folded in collection._fold(catalog.tcgdex_set_id(st["set_id"]))]
    if language:
        sets = [st for st in sets if collection.language_group(st["language"]) == language]
    if show:
        sets = [st for st in sets if (st["owned_main"] >= (st["printed_total"] or 0)) == (show == "complete")]
    if sort == "newest":
        sets.sort(key=lambda st: st["release_date"] or "", reverse=True)
    elif sort == "name":
        sets.sort(key=lambda st: st["shown_name"].casefold())
    elif sort == "value":
        sets.sort(key=lambda st: -(st.get("owned_value") or 0))
    active = [(f"“{q.strip()}”", "q")] if q.strip() else []
    if language:
        active.append((collection.LANGUAGE_FILTERS[language], "language"))
    if show:
        active.append((SET_SHOW[show], "show"))
    if sort != "complete":
        active.append((SET_SORTS[sort], "sort"))
    params = dict(request.query_params)
    return templates.TemplateResponse(request, "collection_sets.html", {
        "user": user, "sets": sets, "q": q, "language": language, "sort": sort, "show": show,
        "sorts": SET_SORTS, "shows": SET_SHOW, "language_options": [(k, collection.LANGUAGE_FILTERS[k]) for k in languages],
        "active": active, "filter_count": len(active),
        "clear": {key: "/collection/sets?" + ("&".join(f"{k}={quote(v)}" for k, v in params.items() if k != key and v and k not in _NOT_FILTERS) or "clear=1")
                  for _, key in active},
    })


def _followed_groups(collection_id: int) -> list[list[dict]]:
    """Followed Pokémon for Chases: one tile per evolution line, its Pokémon in evolution order,
    tiles by lowest Pokédex number."""
    followed = chase_pages.followed_list(conn, collection_id)
    use_lines = web.lines.available()
    groups: dict[int, list[dict]] = {}
    for f in followed:
        f["running"] = background.running(f"follow:{f['dex_id']}")
        try:
            key = web.lines.chain_of(f["dex_id"]) if use_lines else f["dex_id"]
            f["order"] = web.lines.members(f["dex_id"]).index(f["dex_id"]) if use_lines else 0
        except Exception:
            key, f["order"] = f["dex_id"], 0
        groups.setdefault(key, []).append(f)
    out = [sorted(g, key=lambda f: (f["order"], f["dex_id"])) for g in groups.values()]
    out.sort(key=lambda g: min(f["dex_id"] for f in g))
    return out


def _chases_context(request: Request, user, q: str, language: str, price: str) -> dict:
    items = wants.list_wants(conn, user["collection_id"])  # stored prices only (0.20.1)
    groups = _followed_groups(user["collection_id"])  # every single chase shows, followed Pokémon's too
    languages = [k for k in collection.LANGUAGE_FILTERS if any(collection.language_group(w["language"]) == k for w in items)]
    if q.strip():
        also = web.other_names(q)
        items = [w for w in items if collection.name_matches(q, w["name"], w.get("twin_name"), w["number"], also)]
    if language:
        items = [w for w in items if collection.language_group(w["language"]) == language]
    if price:
        items = [w for w in items if collection.in_price_range(w["price"], price)]
    active = _my_cards_filters(q, language, "", "", "", None, "", price, [], [])
    params = dict(request.query_params)
    return {
        "user": user, "wants": items, "groups": groups, "error": request.query_params.get("error"),
        "artists": [dict(a, running=background.running(f"follow:{a['dex_id']}"))
                    for a in chase_pages.followed_artist_list(conn, user["collection_id"])],
        "moves": worth.chase_moves(conn, user["collection_id"]), "chase_days": worth.CHASE_DAYS,
        "species_choices": _species_choices(), "sprites_ready": themes.counts(conn)["normal"] > 0,
        "species_swatches": _species_swatches(), "owned_species": sorted(themes.owned_species(conn, web.names)),
        "q": q, "language": language, "price": price, "price_options": _price_options(),
        "language_options": [(k, collection.LANGUAGE_FILTERS[k]) for k in languages],
        "language_names": collection.LANGUAGE_FILTERS, "price_ranges": collection.price_ranges(),
        "active": active, "filter_count": len(active),
        "clear": {key: "/chases?" + ("&".join(f"{k}={quote(v)}" for k, v in params.items() if k != key and v and k not in _NOT_FILTERS) or "clear=1")
                  for _, key in active},
    }


@router.get("/value")
def value_view(request: Request, range: str = worth.DEFAULT_RANGE, by: str = "usd", of: str = "cards", moves: str = "",
               user=Depends(current_user)):
    """Value over time: the chart, the change, and top movers (over the chart's range, or moves=1d: since the day
    before, from Home's Today's movers)."""
    by = by if by in ("usd", "pct") else "usd"
    of = of if of in ("cards", "chases") else "cards"
    moves = "1d" if moves == "1d" else ""
    data = worth.series(conn, user["collection_id"], range)
    return templates.TemplateResponse(request, "value.html", {
        "user": user, "data": data, "chart": _chart(data["points"]), "ranges": worth.RANGE_LABELS,
        "movers": worth.movers(conn, user["collection_id"], moves or data["range"], by, of), "by": by, "of": of, "moves": moves,
        "shown": worth.MOVERS_SHOWN, "min_price": worth.MIN_PRICE, "variants": inventory.VARIANTS,
        "stats": prices.collection_value(conn, user["collection_id"]),
        "fresh": worth.freshness(conn, user["collection_id"])})


@router.get("/cards/{card_id}/history")
def card_history(request: Request, card_id: str, variant: str = "normal", range: str = worth.DEFAULT_RANGE,
                 user=Depends(current_user)):
    """A card page's small price chart for one printing."""
    if variant not in inventory.VARIANTS:
        raise HTTPException(400)
    data = worth.card_series(conn, card_id, variant, range)
    return templates.TemplateResponse(request, "_price_chart.html", {
        "card_id": card_id, "variant": variant, "data": data, "chart": _chart(data["points"], 320, 90),
        "ranges": worth.RANGE_LABELS, "variant_label": inventory.VARIANTS[variant]})


def _csv_file(data: bytes, what: str) -> Response:
    return Response(data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{export.filename(what)}"'})


@router.get("/collection/export.csv")
def stash_csv(format: str = "dexstash", user=Depends(current_user)):
    """Download my stash: one row per printing and condition; format=collectr in Collectr's columns."""
    make = export.collectr if format == "collectr" else export.stash
    return _csv_file(make(conn, user["collection_id"]), "stash-collectr" if format == "collectr" else "stash")


@router.get("/collection/binders/{binder_id}/export.csv")
def binder_csv(binder_id: str, format: str = "dexstash", user=Depends(current_user)):
    """The same for one binder (or Not in a binder), with the copies in it."""
    try:
        which = export.known_binder(conn, user["collection_id"], binder_id)
    except LookupError:
        raise HTTPException(404, "No such binder.")
    make = export.collectr if format == "collectr" else export.stash
    return _csv_file(make(conn, user["collection_id"], which), "binder-collectr" if format == "collectr" else "binder")


@router.get("/chases/export.csv")
def chases_csv(user=Depends(current_user)):
    """Download my chases: single-card chases with their price and shop links, without prices paid."""
    return _csv_file(export.chases(conn, user["collection_id"]), "chases")


@router.get("/chases")
def chases_view(request: Request, q: str = "", language: str = "", price: str = "", user=Depends(current_user)):
    if (back := _remembered_filters(request, user, "chases")) is not None:
        return back
    language = language if language in collection.LANGUAGE_FILTERS else ""
    price = price if price in collection.PRICE_RANGES else ""
    return templates.TemplateResponse(request, "chases.html", _chases_context(request, user, q, language, price))
