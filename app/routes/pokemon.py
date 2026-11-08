"""Pokemon pages."""
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .. import chase, chase_pages, pokemon_words, tcgdex, themes
from .. import web
from ..web import (_Route, _artist_matches, _evolution, _following, _group_in_scanner, _in_thread, _pictures_ready,
                   _pokemon_name, background, chase_api, conn, current_user, templates)

router = APIRouter(route_class=_Route)


def _dex_of(text: str) -> int | None:
    """A Pokédex number from a number or a name in any language."""
    text = (text or "").strip().lstrip("#")
    if text.isdigit():
        return int(text) if 1 <= int(text) <= themes.SPECIES else None
    try:
        return web.names.species_id(text) or web.names.species_id(web.names.to_english(text) or "")
    except Exception:
        return None


def _start_follow(dex: int, only_new: bool = False) -> bool:
    def run(job_conn, job):
        text = chase.run_follow(job_conn, dex, web.names, chase_api,
                                web.picture_encoder if _pictures_ready() and not web.SCANNER_EXTERNAL else None, job, only_new=only_new)
        _group_in_scanner(dex)
        return text
    return background.start(f"follow:{dex}", f"Getting {_pokemon_name(dex)}'s cards", run)


def _regroup(dex: int) -> None:
    """After one of your fixes: group again with the fingerprints already made (a second)."""
    chase.regroup(conn, dex, chase.fingerprints(conn, dex, None))


@router.get("/pokemon")
def pokemon_list():
    """Followed Pokémon are on My Stash › Chases now."""
    return RedirectResponse("/chases", status_code=301)


@router.post("/pokemon/follow")
def pokemon_follow(q: str = Form(""), dex: str = Form(""), kind: str = Form("pokemon"), artist: str = Form(""),
                   user=Depends(current_user)):
    if kind == "artist":  # The follow sheet's Artist side
        return artist_follow(artist, user)
    dex = (int(dex) if dex.isdigit() else 0) or _dex_of(q)
    if not dex:
        return RedirectResponse(f"/chases?error={quote('No Pokémon by that name or number.')}", status_code=303)
    if chase.follow(conn, user["collection_id"], dex, _pokemon_name(dex), user["id"]):
        _start_follow(dex)
    return RedirectResponse(f"/pokemon/{dex}", status_code=303)


@router.post("/artists/follow")
def artist_follow(name: str = Form(""), user=Depends(current_user)):
    """Follow an artist (by the name TCGdex gives them, or as typed)."""
    known = {chase.artist_norm(n): n for n in tcgdex.artist_names(web.api)}
    name = known.get(chase.artist_norm(name)) or " ".join(name.split())
    if not chase.artist_norm(name):
        return RedirectResponse(f"/chases?error={quote('Type an artist name.')}", status_code=303)
    key = chase.artist_key(conn, name)
    if chase.follow(conn, user["collection_id"], key, name, user["id"]):
        _start_follow(key)
    return RedirectResponse(f"/artists/{quote(chase.artist_norm(name))}", status_code=303)


@router.get("/artists/suggest")
def artist_suggest(q: str = "", user=Depends(current_user)):
    """Names for the follow sheet's Artist box: TCGdex's list, then names on stored cards."""
    return JSONResponse({"names": _artist_matches(q, 12)})


@router.get("/artists/{norm}")
def artist_page(request: Request, norm: str, show: str = "", sort: str = "",
                open: str = "", user=Depends(current_user)):
    """An artist's cards grouped by art, like a Pokémon's set; a preview of their English cards until followed."""
    cid = user["collection_id"]
    found = chase.artist(conn, norm)
    if found is None:
        names_known = {chase.artist_norm(n): n for n in tcgdex.artist_names(web.api)}
        if chase.artist_norm(norm) not in names_known:
            raise HTTPException(404)
        found = {"key": None, "norm": chase.artist_norm(norm), "name": names_known[chase.artist_norm(norm)]}
    key = found["key"]
    following = key is not None and chase.is_followed(conn, cid, key)
    context = {"user": user, "dex": key or 0, "name": found["name"], "following": following, "sprite": None,
               "artist": found, "running": key is not None and (background.running(f"follow:{key}") or background.running(f"recheck:{key}")),
               "message": request.query_params.get("message"), "evo": None, "ptags": None}
    if not following:
        try:
            briefs = chase.list_artist_briefs(web.api, found["name"])
        except Exception:
            briefs = None
        return templates.TemplateResponse(request, "pokemon_set.html", {
            **context, "preview": True,
            "briefs": [dict(b, image_url=tcgdex.image_url(b.get("image"))) for b in (briefs or [])][:300],
            "unreachable": briefs is None})
    return _arts_page(request, context, user, key, show, sort, open)


@router.post("/pokemon/{dex}/star")
def pokemon_star(request: Request, dex: int, on: str = Form(""), user=Depends(current_user)):
    """Find's ★ follows or stops following a Pokémon in place (no page, no Back step)."""
    if not 1 <= dex <= themes.SPECIES:
        raise HTTPException(404)
    if on:
        if chase.follow(conn, user["collection_id"], dex, _pokemon_name(dex), user["id"]):
            _start_follow(dex)
    else:
        chase.unfollow(conn, user["collection_id"], dex)
    return templates.TemplateResponse(request, "_find_star.html", {
        "dex": dex, "name": _pokemon_name(dex), "following": _following(user["collection_id"], dex)})


@router.post("/pokemon/{dex}/unfollow")
def pokemon_unfollow(dex: int, user=Depends(current_user)):
    chase.unfollow(conn, user["collection_id"], dex)
    return RedirectResponse("/chases", status_code=303)


def _sort(user, sort: str) -> str:
    """Pokémon and artist pages' sort: Value to start, then whichever you picked last, kept per login."""
    if sort in chase_pages.SORTS:
        conn.execute("INSERT INTO user_prefs (user_id, key, value) VALUES (?, 'pokemon_sort', ?) "
                     "ON CONFLICT (user_id, key) DO UPDATE SET value = excluded.value", (user["id"], sort))
        conn.commit()
        return sort
    row = conn.execute("SELECT value FROM user_prefs WHERE user_id = ? AND key = 'pokemon_sort'", (user["id"],)).fetchone()
    return row[0] if row and row[0] in chase_pages.SORTS else "value"


def _arts_page(request: Request, context: dict, user, key: int, show: str, sort: str, open: str):
    """A followed Pokémon's or artist's page: every art in one grid, every language counted."""
    cid = user["collection_id"]
    show = show if show in chase_pages.SHOW else ""
    sort = _sort(user, sort)
    return templates.TemplateResponse(request, "pokemon_set.html", {
        **context, "preview": False, "show": show, "sort": sort,
        "summary": chase_pages.summary(conn, cid, key), "tiles": chase_pages.art_tiles(conn, cid, key, show, sort),
        "sorts": chase_pages.SORTS, "shows": chase_pages.SHOW,
        "open": open, "open_art": next((a["id"] for a in chase_pages.arts(conn, key) if open in a["card_ids"]), None) if open else None,
        "checked": conn.execute("SELECT MAX(checked_at) FROM followed_pokemon WHERE dex_id = ?", (key,)).fetchone()[0],
    })


@router.get("/pokemon/{dex}")
def pokemon_set(request: Request, dex: int, show: str = "", sort: str = "",
                open: str = "", user=Depends(current_user)):
    if not 1 <= dex <= themes.SPECIES:
        raise HTTPException(404)
    cid = user["collection_id"]
    following = chase.is_followed(conn, cid, dex)
    context = {"user": user, "dex": dex, "name": _pokemon_name(dex), "following": following,
               "sprite": f"/sprites/{dex}.png" if themes.sprite_path(dex, False).exists() else None,
               "running": background.running(f"follow:{dex}") or background.running(f"recheck:{dex}"),
               "message": request.query_params.get("message"), "evo": _evolution(cid, dex),
               "ptags": pokemon_words.for_pokemon(conn, web.describer, dex) if web.describer.ready() else None}
    if not following:  # a preview: the English cards as a plain list, nothing kept
        try:
            briefs = chase.list_briefs(web.api, dex, [_pokemon_name(dex)])
        except Exception:
            briefs = None
        return templates.TemplateResponse(request, "pokemon_set.html", {
            **context, "preview": True,
            "briefs": [dict(b, image_url=tcgdex.image_url(b.get("image"))) for b in (briefs or [])][:300],
            "unreachable": briefs is None})
    return _arts_page(request, context, user, dex, show, sort, open)


@router.get("/pokemon/{dex}/art")
def pokemon_art(request: Request, dex: int, card: str, user=Depends(current_user)):
    """The Edit art sheet's insides: the art's cards, Add a card to this art, and what was taken out."""
    detail = chase_pages.art_detail(conn, user["collection_id"], dex, card)
    if detail is None:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "_pokemon_art.html", {
        "user": user, "dex": dex, "d": detail, "tags": chase.LANG_TAGS,
        "candidates": chase_pages.candidates(conn, dex, card, chase.fingerprints(conn, dex, None)),
        "sprite": f"/sprites/{dex}.png" if themes.sprite_path(dex, False).exists() else None})


def _back_to_art(dex: int, card: str, message: str | None = None) -> RedirectResponse:
    query = urlencode({"open": card, **({"message": message} if message else {})})
    page = f"/artists/{quote((chase.artist(conn, dex) or {}).get('norm', ''))}" if dex < 0 else f"/pokemon/{dex}"
    return RedirectResponse(f"{page}?{query}", status_code=303)


@router.post("/pokemon/{dex}/fix/out")
async def pokemon_fix_out(request: Request, dex: int, user=Depends(current_user)):
    """✕: take a print (all its languages) or one language out of the art. They stay together."""
    form = await request.form()

    def work():  # Off the event loop, as one transaction
        taken = [c for c in form.getlist("card") if c]
        back = form.get("back", "")
        if taken:
            chase.take_out(conn, dex, taken[0], user["id"])
            for other in taken[1:]:
                chase.take_out(conn, dex, other, user["id"])
                chase.put_with(conn, dex, other, taken[0], user["id"])
            _regroup(dex)
        return _back_to_art(dex, back if back not in taken else (form.get("stay") or back))
    return await _in_thread(work)


@router.post("/pokemon/{dex}/fix/add")
async def pokemon_fix_add(request: Request, dex: int, user=Depends(current_user)):
    """＋ Add a version: the ticked cards join this art."""
    form = await request.form()

    def work():  # Off the event loop, as one transaction
        to = form.get("to", "")
        for card in form.getlist("card"):
            chase.put_with(conn, dex, card, to, user["id"])
        _regroup(dex)
        return _back_to_art(dex, to)
    return await _in_thread(work)


@router.post("/pokemon/{dex}/fix/undo")
def pokemon_fix_undo(dex: int, card: str = Form(...), back: str = Form(...), user=Depends(current_user)):
    chase.undo_fix(conn, dex, card)
    _regroup(dex)
    return _back_to_art(dex, back)


@router.post("/pokemon/{dex}/recheck")
def pokemon_recheck(dex: int, back: str = Form(""), user=Depends(current_user)):
    """↻ Re-check: compare the pictures again in the background. Your fixes stay."""
    if not _pictures_ready():
        return _back_to_art(dex, back, "Turn on Enhanced Search under Data sources to compare pictures.")

    def run(job_conn, job):
        return chase.run_recheck(job_conn, dex, web.picture_encoder, job)
    if web.SCANNER_EXTERNAL:
        _group_in_scanner(dex)
    else:
        background.start(f"recheck:{dex}", f"Comparing {_pokemon_name(dex)}'s pictures", run)
    return _back_to_art(dex, back, "Comparing the pictures again. Refresh in a minute.")


@router.post("/cards/{card_id}/pokemon-sets")
async def card_pokemon_sets(request: Request, card_id: str, user=Depends(current_user)):
    """Add to a Pokémon set…: tick the followed Pokémon whose set this card belongs in."""
    form = await request.form()

    def work():  # Off the event loop, as one transaction
        ticked = {int(d) for d in form.getlist("dex") if str(d).isdigit()}
        row = chase.row_from_cards(conn, card_id)
        if row is None:
            raise HTTPException(404)
        for f in chase.followed(conn, user["collection_id"]):
            dex = f["dex_id"]
            inside = conn.execute("SELECT how FROM pokemon_cards WHERE dex_id = ? AND card_id = ?", (dex, card_id)).fetchone()
            if dex in ticked and (inside is None or inside["how"] == "removed"):
                if inside is not None:
                    conn.execute("UPDATE pokemon_cards SET how = 'auto' WHERE dex_id = ? AND card_id = ?", (dex, card_id))
                    conn.commit()
                else:
                    chase.add_card(conn, dex, row, user["id"])
                _regroup(dex)
            elif dex not in ticked and inside is not None and inside["how"] != "removed":
                chase.remove_card(conn, dex, card_id)
                _regroup(dex)
        return RedirectResponse(f"/cards/{card_id}?extra_saved={quote('Saved your Pokémon sets.')}#fix", status_code=303)
    return await _in_thread(work)


@router.get("/wants")
def wants_view():
    return RedirectResponse("/chases", status_code=301)
