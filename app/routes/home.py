"""Home pages."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from .. import pwa
from .. import db, home as home_page, prices
from .. import value as worth, display, facts, themes, welcome
from ..tcgdex import LANGUAGES
from .. import web
from ..web import (_Route, _account_page, _en_name, _sprite, _sprite_for, conn, current_user, log, templates, updater)

router = APIRouter(route_class=_Route)


@router.get("/")
def home(request: Request, user=Depends(current_user)):
    collection_id = user["collection_id"]
    theme = _theme_for(user)
    return templates.TemplateResponse(request, "home.html", {  # Your sets, then Your Pokémon (round 24), left Home
        "user": user, "languages": LANGUAGES, "cheaper": worth.cheaper_chases(conn, collection_id), "stats": home_page.stats(conn, collection_id),
        "movers": home_page.movers(conn, collection_id),
        "recent": home_page.recently_added(conn, collection_id), "review": home_page.to_review(conn, collection_id),
        "prices_updated": prices.last_updated(conn), "updating": updater.running, "update_result": updater.last_result, "update_failed": updater.last_failed,
        "home_theme": theme, "fact": _fact(theme), "pokedoku": home_page.POKEDOKU_URL,
        "getting_started": _getting_started(user),
    })


def _fact(theme: dict | None) -> str | None:
    """The fun fact of the day about the Home tile's Pokémon. Never breaks the page."""
    try:
        return facts.for_day(theme["species_id"], display.today()) if theme and theme.get("sprite") else None
    except Exception:
        log.exception("fact of the day")
        return None


def _getting_started(user) -> dict | None:
    try:
        return welcome.getting_started(conn, user, web.describer.ready())
    except Exception:
        log.exception("getting started")
        return None


def _theme_for(user) -> dict | None:
    """The signed-in login's Pokémon theme, for base.html. Never breaks a page."""
    try:
        return themes.for_user(conn, user["id"], web.names)
    except Exception:
        log.exception("theme")
        return None


templates.env.globals["theme_for"] = _theme_for


templates.env.globals["en_name"] = _en_name


templates.env.globals["sprite_for"] = _sprite_for


@router.get("/sprites/{species_id}.png")
def sprite(species_id: int, user=Depends(current_user)):
    return _sprite(species_id, False)


@router.get("/sprites/shiny/{species_id}.png")
def sprite_shiny(species_id: int, user=Depends(current_user)):
    return _sprite(species_id, True)


def _species_from(text: str) -> int | None:
    text = (text or "").strip()
    number = text.lstrip("#").split(" ")[0]
    if number.isdigit():
        return int(number)
    try:
        return web.names.species_id(text)
    except Exception:
        return None


@router.post("/account/theme")
def account_theme(request: Request, mode: str = Form("fixed"), pokemon: str = Form(""), only_owned: str = Form(""),
                  shiny_sometimes: str = Form(""), user=Depends(current_user)):
    current = themes.user_setting(conn, user["id"])
    species = _species_from(pokemon) if pokemon.strip() else current["species_id"]
    if species is None:
        return _account_page(request, user, f"Couldn't find a Pokémon called '{pokemon}'. Try its number.", error=True)
    try:
        themes.save_user_setting(conn, user["id"], mode, species)
    except ValueError as e:
        return _account_page(request, user, str(e), error=True)
    if mode == "daily":
        themes.save_shared_settings(conn, user["id"], bool(only_owned), bool(shiny_sometimes))
    return RedirectResponse("/account?theme=saved#theme", status_code=303)


@router.post("/account/mode")
def account_mode(mode: str = Form("dark"), user=Depends(current_user)):
    """Light or Dark for this login only."""
    if mode not in ("light", "dark"):
        raise HTTPException(400, "Pick Light or Dark.")
    with db.transaction(conn):
        conn.execute("UPDATE users SET mode = ? WHERE id = ?", (mode, user["id"]))
    return RedirectResponse("/account?theme=saved#theme", status_code=303)


@router.get("/themes/palette/{species_id}")
def theme_palette(species_id: int, user=Depends(current_user)):
    """A Pokémon's colours, for the live preview in Account > Theme."""
    colours = themes.get_palette(conn, species_id)
    if colours is None:
        raise HTTPException(404)
    return JSONResponse({"light": themes.with_roles(colours, "light"), "dark": themes.with_roles(colours, "dark"),
                         "swatches": colours["swatches"]})


@router.get("/review")
def review_page(request: Request, show: str = "", user=Depends(current_user)):
    show = show if show in home_page.REASONS else ""
    return templates.TemplateResponse(request, "review.html", {
        "user": user, "review": home_page.to_review(conn, user["collection_id"], show or None), "show": show,
        "reasons": home_page.REASONS, "stale_days": prices.stale_days(conn)})


@router.post("/prices/refresh")
def refresh_prices(user=Depends(current_user)):
    updater.start()
    return RedirectResponse("/", status_code=303)


@router.get("/prices/status")
def prices_status(user=Depends(current_user)):
    """For Home's $↻: whether an update is still running."""
    return {"running": updater.running}


@router.get("/manifest.webmanifest")
def manifest(mode: str = "dark"):
    """The Home Screen app: named from APP_NAME, with the box logo (tools/make_logo.py). Icon addresses carry
    their content hash, so a new logo is picked up when the app is added again. ?mode=light gives the Light
    look's colours (the page asks with the person's own mode)."""
    return JSONResponse(pwa.manifest(web.asset, web.SCANNER_MODE != "off", "light" if mode == "light" else "dark"),
                        media_type="application/manifest+json")


@router.get("/sw.js")
def service_worker():
    """The service worker for the offline page (app/pwa.py). Checked for a new version on every launch."""
    return Response(pwa.service_worker(web.asset), media_type="application/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@router.get("/offline")
def offline_page(request: Request):
    """The page the service worker shows when the server can't be reached. Needs no sign-in."""
    return templates.TemplateResponse(request, "offline.html", {})


@router.get("/healthz")
def healthz():
    return {"ok": True}
