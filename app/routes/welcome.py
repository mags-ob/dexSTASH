"""The first-run wizard: /welcome, once, for the first admin after their first sign-in."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import auth, card_index, cardtrader, db, display, fx, pictures, region, settings, sites, sync, tcgcsv, themes, welcome
from .. import web
from ..web import _Route, _is_owner, background, conn, log, signed_in, templates
from .data_sources import _load_all_chinese, fetch_rates_soon, start_enhanced_search, start_pokemon_data

router = APIRouter(route_class=_Route)

FIRST_PAGES = {"collectr": "/imports"}  # round 26: Import a Collectr CSV, or Not now (an older "search" or "scan" is Not now)


def _scanning() -> bool:
    return web.SCANNER_MODE != "off"


def _downloads(choices: dict) -> list[dict]:
    """Step 4's rows: each download with its estimated size, ticked or not."""
    site_on = sites.states()
    langs = welcome.collected(conn)
    rows = [{"key": f"catalog-{lang}", "label": f"Card catalog for Find: {welcome.LANGUAGES[lang]}", "fixed": True,
             "note": "Starts with the languages you picked, so Find answers from your own server."}
            for lang in langs if lang in welcome.CATALOG_LANGUAGES and card_index.enabled()]
    all_here = web.describer.ready() and themes.counts(conn)["normal"] >= themes.SPECIES
    rows.append({"key": "pokemon", "label": "Pokémon data", "ticked": choices["pokemon"], "here": all_here,
                 "note": "The Pokédex (Describe a Pokémon and evolution lines) and the sprites (themes and Home)."})
    if site_on.get("bulbapedia"):
        rows.append({"key": "descriptions", "label": "Bulbapedia descriptions", "ticked": choices["descriptions"],
                     "needs": "pokemon", "note": "Each Pokémon's Origin text, so Describe finds it by more words."})
    if "zh-cn" in langs and site_on.get("chinese"):
        rows.append({"key": "chinese", "label": "Mainland Chinese sets", "fixed": True,
                     "note": "Loads because you picked Chinese (mainland) and the Chinese datasets."})
    if _scanning() and pictures.available():
        rows.append({"key": "enhanced", "label": "Enhanced Search", "ticked": choices["enhanced"],
                     "note": "Matches scan photos by their picture as well as their text. The first run is long: about "
                             "10 minutes for Chinese, a couple of hours each for English and Japanese."})
    for row in rows:
        row["size"] = welcome.size_text(row["key"])
        row["down"], row["kept"] = welcome.SIZES[row["key"]][:2]
    return rows


def _page(request: Request, user, number: int, error: str | None = None, status_code: int = 200, **extra):
    choices = welcome.choices(conn)
    context = {
        "user": None, "me": user, "step": number, "furthest": welcome.step(conn), "steps": welcome.STEPS,
        "error": error, "choices": choices, "min_password": auth.MIN_PASSWORD,
        "from_env": bool(settings.get("ADMIN_PASSWORD")),
        "zone": display.zone().key, "zones": region.zone_names(), "currencies": fx.CURRENCIES, "currency": fx.setting(),
        "languages": welcome.LANGUAGES, "collected": welcome.collected(conn),
        "sites": [(key, label, use) for key, (label, use) in sites.SITES.items()], "site_on": sites.states(),
        "nightly": sync.nightly_settings(conn), "cardtrader_token": bool(cardtrader.token()),
        "scanning": _scanning(), "first_pages": FIRST_PAGES, **extra,
    }
    if number == 4:
        rows = _downloads(choices)
        down, kept = welcome.total([r["key"] for r in rows if (r.get("fixed") or r.get("ticked")) and not r.get("here")])
        context.update(downloads=rows, total_down=welcome.mb(down), total_kept=welcome.mb(kept))
    return templates.TemplateResponse(request, "welcome.html", context, status_code=status_code)


def _next(number: int) -> RedirectResponse:
    welcome.reached(conn, number + 1)
    return RedirectResponse(f"/welcome?step={number + 1}", status_code=303)


def _wizard_user(request: Request) -> dict:
    user = signed_in(request)
    user["owner"] = _is_owner(user)
    return user


@router.get("/welcome")
def welcome_page(request: Request, step: int = 0, user=Depends(_wizard_user)):
    if welcome.done(conn):
        return RedirectResponse("/", status_code=303)
    if not user["owner"]:  # someone else signed in before the admin finished
        return templates.TemplateResponse(request, "welcome.html", {"user": None, "me": user, "waiting": True})
    if welcome.preparing(conn) is not None:  # Finish was pressed: Getting your stash ready
        return templates.TemplateResponse(request, "welcome.html", {"user": None, "me": user, "step": 7,
                                                                    "steps": welcome.STEPS, **_ready_context()})
    furthest = welcome.step(conn)
    # Back goes as far as step 2: the password step is only ever the first.
    number = step if 2 <= step <= furthest else furthest
    return _page(request, user, number)


def _owner(user) -> None:
    if not user["owner"] or welcome.done(conn):
        raise HTTPException(403, "Only the admin setting up can do that.")


def _check_step(number: int) -> None:
    if welcome.preparing(conn) is not None:
        raise HTTPException(409, "Finish was already pressed.")
    furthest = welcome.step(conn)
    if number > furthest or (number == 1 and furthest > 1):
        raise HTTPException(409, "That step isn't open.")


@router.post("/welcome/1")
def welcome_password(request: Request, current: str = Form(...), new: str = Form(...), again: str = Form(...),
                     user=Depends(_wizard_user)):
    """Step 1: change your password, always first."""
    _owner(user)
    _check_step(1)
    if not auth.verify_password(current, user["password_hash"]):
        return _page(request, user, 1, "Your current password isn't right.", 400)
    if new != again:
        return _page(request, user, 1, "The new passwords don't match.", 400)
    if new == current:
        return _page(request, user, 1, "Pick a new password, not the one you have now.", 400)
    try:
        request.session["key"] = auth.set_password(conn, user["id"], new)
    except ValueError as e:
        return _page(request, user, 1, str(e), 400)
    log.info("first-run wizard: %s changed their password", user["username"])
    return _next(1)


@router.post("/welcome/2")
def welcome_basics(request: Request, zone: str = Form(...), currency: str = Form(...), user=Depends(_wizard_user)):
    """Step 2: time zone and currency, as Data sources › Settings saves them."""
    _owner(user)
    _check_step(2)
    try:
        region.save_zone(conn, zone)
    except ValueError:
        return _page(request, user, 2, "Pick a time zone from the list.", 400)
    try:
        fx.save_currency(conn, currency)
    except ValueError as e:
        return _page(request, user, 2, str(e), 400)
    fetch_rates_soon(fetch=fx.shown() != currency and settings.on("EURO_RATE"))
    return _next(2)


@router.post("/welcome/3")
async def welcome_cards(request: Request, user=Depends(_wizard_user)):
    """Step 3: the languages collected, the extra sites and the nightly price update."""
    _owner(user)
    _check_step(3)
    form = await request.form()
    langs = [lang for lang in welcome.LANGUAGES if form.get(f"lang-{lang}") == "1"]
    if not langs:
        return _page(request, user, 3, "Pick at least one language.", 400)
    try:
        with db.transaction(conn):
            for site in sites.SITES:
                sites.set_on(conn, site, form.get(f"site-{site}") == "1")
            sync.save_nightly(conn, form.get("nightly") == "1", str(form.get("at") or sync.DEFAULT_TIME))
            welcome.save_choices(conn, languages=langs)
            welcome.reached(conn, 4)
    except ValueError:
        return _page(request, user, 3, "Use a time like 04:00.", 400)
    return RedirectResponse("/welcome?step=4", status_code=303)


@router.post("/welcome/4")
def welcome_downloads(pokemon: str = Form(""), descriptions: str = Form(""), enhanced: str = Form(""),
                      user=Depends(_wizard_user)):
    """Step 4: what to download at Finish."""
    _owner(user)
    _check_step(4)
    welcome.save_choices(conn, pokemon=bool(pokemon), descriptions=bool(pokemon and descriptions), enhanced=bool(enhanced))
    return _next(4)


@router.post("/welcome/5")
def welcome_first(first: str = Form(""), user=Depends(_wizard_user)):
    """Step 5: what opens after Finish."""
    _owner(user)
    _check_step(5)
    welcome.save_choices(conn, first=first if first == "collectr" else "")  # Import a Collectr CSV, or Not now
    return _next(5)


def _basics_job(job_conn, job) -> str:
    """The first of the basics: the day's exchange rates and TCGplayer's set list (TCGCSV), which prices need."""
    done = []
    if settings.on("EURO_RATE"):
        job.progress(0, 2, "exchange rates")
        fx.refresh(job_conn, force=True)
        done.append("exchange rates")
    if settings.on("TCGCSV"):
        job.progress(1, 2, "TCGplayer's set list")
        tcgcsv.ensure_groups(job_conn)
        tcgcsv.match_sets(job_conn)
        done.append("TCGplayer's set list")
    return ("Downloaded " + " and ".join(done) + ".") if done else "Nothing to download."


def start_basics() -> list[str]:
    """W2: the basics, as jobs one after another in this order: exchange rates and TCGplayer's set list, Pokémon names
    and sprites, Find's card catalogs (English first, Japanese last), then mainland Chinese sets if picked. Returns
    the job keys, which the Getting your stash ready step shows."""
    choices = welcome.choices(conn)
    keys = []
    if background.start("first-basics", "Exchange rates and TCGplayer's set list", _basics_job) or \
            background.running("first-basics"):
        keys.append("first-basics")
    if choices["pokemon"]:
        start_pokemon_data(with_descriptions=False)  # Bulbapedia's descriptions are long: after Continue
        keys.append("pokemon-data")
    langs = [lang for lang in welcome.collected(conn) if lang in welcome.CATALOG_LANGUAGES]
    for lang in sorted(langs, key=lambda lang: lang == "ja"):  # Japanese is the biggest: last
        card_index.start(web.api_for(lang))
        if card_index.enabled():
            keys.append(f"card-index:{lang}")
    if "zh-cn" in choices["languages"] and sites.on("chinese"):
        background.start("chs", "Loading all Chinese sets", _load_all_chinese)
        keys.append("chs")
    return keys


def _ready_items() -> list[dict]:
    items = []
    for key in welcome.preparing(conn) or []:
        job = background.last(key)
        state = "waiting" if job is None or job.waiting else "running" if job.running else \
            "failed" if job.error else "done"
        items.append({"key": key, "job": job, "state": state, "label": job.label if job else key,
                      "progress": job.describe() if job and state == "running" else None,
                      "error": job.error if job else None})
    return items


def _ready_context() -> dict:
    items = _ready_items()
    return {"items": items, "ready": all(i["state"] in ("done", "failed") for i in items),
            "failed": any(i["state"] == "failed" for i in items)}


@router.post("/welcome/6")
def welcome_finish(request: Request, username: str = Form(""), password: str = Form(""), is_admin: str = Form(""),
                   user=Depends(_wizard_user)):
    """The last step: maybe a login for someone you collect with, then Finish starts the basics (W2): the wizard
    shows them downloading, one at a time, and opens the app once they're done (Continue)."""
    _owner(user)
    _check_step(6)
    if username.strip():
        try:
            auth.create_user(conn, username, password, is_admin=bool(is_admin), collection_id=user["collection_id"])
        except ValueError as e:
            return _page(request, user, 6, str(e), 400, partner=username.strip())
    if welcome.preparing(conn) is None:
        keys = start_basics()
        welcome.start_preparing(conn, keys)
        log.info("first-run wizard: %s pressed Finish; downloading %s first", user["username"], ", ".join(keys) or "nothing")
    return RedirectResponse("/welcome?step=7", status_code=303)


@router.get("/welcome/ready")
def welcome_ready(request: Request, user=Depends(_wizard_user)):
    """The Getting your stash ready list, refreshed every 2 seconds until Continue opens (a status check: it doesn't
    count as browsing, so the downloads don't wait for it)."""
    if welcome.done(conn) or welcome.preparing(conn) is None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "_welcome_ready.html", _ready_context())


@router.post("/welcome/7/again")
def welcome_try_again(user=Depends(_wizard_user)):
    """Try again: the basics that failed or were stopped by a restart."""
    _owner(user)
    if welcome.preparing(conn) is not None:
        start_basics()  # one per key: those that finished aren't fetched again by their own checks
    return RedirectResponse("/welcome?step=7", status_code=303)


@router.post("/welcome/7")
def welcome_continue(user=Depends(_wizard_user)):
    """Continue: the wizard is finished. The first price update, Bulbapedia's descriptions and Enhanced Search carry on
    in the background."""
    _owner(user)
    if welcome.preparing(conn) is None:
        raise HTTPException(409, "That step isn't open.")
    if not _ready_context()["ready"]:
        return RedirectResponse("/welcome?step=7", status_code=303)
    choices = welcome.choices(conn)
    started = []
    if web.updater.start():
        started.append("price update")
    if choices["pokemon"] and choices["descriptions"] and sites.on("bulbapedia") and \
            start_pokemon_data(with_descriptions=True):
        started.append("Bulbapedia descriptions")
    if choices["enhanced"] and _scanning() and pictures.available() and start_enhanced_search():
        started.append("Enhanced Search")
    welcome.finish(conn)
    log.info("first-run wizard finished by %s; in the background: %s", user["username"], ", ".join(started) or "nothing")
    return RedirectResponse(FIRST_PAGES.get(choices["first"], "/"), status_code=303)


@router.post("/welcome/hide")
def welcome_hide(user=Depends(web.current_user)):
    """Home's Getting started card: Hide, for good."""
    if user["owner"]:
        welcome.hide(conn)
    return RedirectResponse("/", status_code=303)
