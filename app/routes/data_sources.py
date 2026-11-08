"""Data sources pages."""
import json
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import english, keys, pricecharting, scanning, settings, sites
from .. import card_index, catalog, chinese, db, display, handmade, pictures, prices, sync
from .. import (activity, region, bulbapedia, card_pictures, cardtrader, fx, tcgcsv, value as worth, chase,
                pokemon_words, scanner, smart, tags, tcgdex, themes)
from ..tcgdex import LANGUAGES
from .. import clock, facts, jobs, web
from ..web import (_Route, _build_describe, _group_in_scanner, _index_pictures, _pictures_ready, _scanner_state,
                   background, chase_api, conn, descriptions, log, owner_user, picture_saver,
                   templates, updater)

router = APIRouter(route_class=_Route)


def _data_context(request: Request, user, message: str | None = None, error: bool = False) -> dict:
    languages = conn.execute(
        """SELECT s.language, COUNT(DISTINCT s.set_id) AS sets, COUNT(c.id) AS cards FROM sets s
           LEFT JOIN cards c ON c.set_id = s.set_id
           WHERE NOT EXISTS (SELECT 1 FROM cards x WHERE x.set_id = s.set_id AND (x.source IS NOT NULL OR x.hand_added_by IS NOT NULL))
           GROUP BY s.language ORDER BY COUNT(c.id) DESC""").fetchall()
    chs = conn.execute(
        """SELECT COUNT(*) AS sets, MIN(loaded_at) AS loaded_at, MAX(checked_at) AS checked_at,
                  (SELECT COUNT(*) FROM cards c JOIN sets s ON s.set_id = c.set_id WHERE s.language = 'zh-cn') AS cards,
                  (SELECT COUNT(DISTINCT c.id) FROM cards c JOIN sets s ON s.set_id = c.set_id WHERE s.language = 'zh-cn'
                     AND c.id IN (SELECT card_id FROM inventory)) AS owned,
                  (SELECT COUNT(*) FROM cards c JOIN sets s ON s.set_id = c.set_id WHERE s.language = 'zh-cn'
                     AND c.twin_name IS NOT NULL) AS twins
           FROM datasets WHERE kind = 'chs'""").fetchone()
    chs_pictures, picture_bytes = chinese.pictures_size()
    priced = conn.execute("SELECT COUNT(DISTINCT card_id) FROM prices").fetchone()[0]
    return {
        "user": user, "message": message, "error": error, "languages_stored": languages,
        "language_names": {**LANGUAGES, **tcgdex.CHOICES}, "chs": chs,
        "set_codes": len(scanner.SET_CODES), "pictures": chs_pictures, "picture_bytes": picture_bytes,
        "hand_photos": len(list(handmade.FOLDER.glob("*.jpg"))) if handmade.FOLDER.exists() else 0,
        "priced": priced, "prices_updated": prices.last_updated(conn), "nightly": sync.nightly_settings(conn),
        "zone": display.zone().key, "zones": region.zone_names(), "zone_note": region.zone_note(),
        "currencies": fx.CURRENCIES, "currency": fx.setting(), "currency_shown": fx.shown(), "rates_at": fx.rates_at(conn),
        "vintage_years": smart.vintage_years(conn), "vintage_year": smart.vintage_year(smart.vintage_years(conn)),
        "running": background.current(), "restarted": jobs.RESTARTED, "sprite_counts": themes.counts(conn), "sprites_running": background.running("sprites"), "prices_running": updater.running, "price_result": updater.last_result,
        "catalog_terms": chinese.CATALOG_TERMS, "twins_terms": chinese.TWINS_TERMS,
        "pictures_on": pictures.is_on(conn), "pictures_possible": pictures.available(),
        "picture_model": pictures.has_model(), "picture_stats": (pictures.stats_saved(conn) if web.SCANNER_EXTERNAL else pictures.stats()) if pictures.is_on(conn) else None,
        "picture_loaded": _scanner_state()["model_loaded"], "scanner": _scanner_state(),
        "scanner_locked": web.SCANNER_LOCKED, "scanner_short": scanning.too_little_memory(),
        "picture_score": pictures.score(conn) if pictures.is_on(conn) else None,
        "picture_idle_minutes": pictures.IDLE_SECONDS // 60,
        "describe_ready": web.describer.ready(), "evolution_ready": web.lines.available(),
        "describe_running": background.running("describe"),
        "bulbapedia_credit": bulbapedia.CREDIT, "descriptions_count": sum(1 for t in descriptions.load().values() if t),
        "descriptions_running": background.running("descriptions"),
        "reset_tags_running": background.running("reset-tags"),
        "no_picture": len(card_pictures.missing(conn, True, user["collection_id"])),
        "saved_pictures": conn.execute("SELECT COUNT(*) FROM cards WHERE saved_picture IS NOT NULL").fetchone()[0],
        "picture_check_running": picture_saver.running,
        "unknown_rarities": display.unknown_rarities(r[0] for r in conn.execute("SELECT DISTINCT rarity FROM cards")),
        # The status lines of the 8 sections
        "updates_checked_at": conn.execute("SELECT value FROM app_meta WHERE key = 'updates_checked_at'").fetchone(),
        "tcgcsv": tcgcsv.status(conn), "tcgcsv_on": settings.on("TCGCSV"), "euro_rate": fx.rate(conn),
        "tcgcsv_unmatched": tcgcsv.unmatched_sets(conn, kept_only=True),
        "tcgcsv_groups": {lang: tcgcsv.groups(conn, lang) for lang in tcgcsv.CATEGORIES},
        "no_source": _no_price_source(),
        "api_keys": keys.rows(), "pc": pricecharting.status(conn),
        "typed_guessed": _typed_guessed(),
        "price_runs": sync.runs(conn), "short_run": sync.last_short_run(conn),
        "run_words": {"ok": "all fine", "partial": "some problems", "failed": "stopped", None: "done"},
        "source_names": {"tcgcsv": "TCGCSV (TCGplayer)", "tcgdex": "TCGdex (TCGplayer)", "cardtrader": "CardTrader",
                         "followed": "Cards of Pokémon you follow", "catalog": "Find's card catalog"},
        "coverage": prices.coverage(conn, user["collection_id"]), "stale_days": prices.stale_days(conn),
        "ebay_sites": prices.EBAY_SITES, "ebay_site": prices.ebay_site(conn),
        "names_choices": english.CHOICES, "names_shown": english.shown(conn),
        "cardtrader": cardtrader.status(conn), "card_index": card_index.status(conn) if card_index.enabled() else None, "ct_expansions": cardtrader.expansions(conn),
        "keep": worth.keep_settings(conn), "history": worth.history_size(conn),
        "keep_choices": {"daily": worth.DAILY_CHOICES, "weekly": worth.WEEKLY_CHOICES, "monthly": worth.MONTHLY_CHOICES},
        "picture_count": card_pictures.picture_score(conn, user["collection_id"]),
        "log_problems": activity.problems_count(1),
        "species_total": themes.SPECIES,
        "sites": [(key, label, use) for key, (label, use) in sites.SITES.items()], "site_on": sites.states(),
    }


def _data_page(request: Request, user, message: str | None = None, error: bool = False):
    return templates.TemplateResponse(request, "data_sources.html", _data_context(request, user, message, error),
                                      status_code=400 if error else 200)


@router.get("/account/data/no-pictures")
def data_no_pictures(request: Request, all: str = "", user=Depends(owner_user)):
    """Cards with no picture: view only. Each opens its card page, where Add photo and
    Paste link fill it in."""
    rows = card_pictures.missing(conn, not all, user["collection_id"])
    return templates.TemplateResponse(request, "no_pictures.html", {"user": user, "rows": rows, "all": bool(all),
                                                                    "language_names": card_pictures.LANG_NAMES})


@router.get("/account/data")
def data_sources(request: Request, user=Depends(owner_user)):
    return _data_page(request, user, request.query_params.get("message"))


@router.get("/account/data/log")
def activity_log(request: Request, h: int = 1, only: str = "", user=Depends(owner_user)):
    """The last hours of both containers' logs, newest first; Copy last hour for a chat."""
    hours = h if h in activity.HOURS else 1
    entries = activity.read(hours, problems=only == "problems")
    return templates.TemplateResponse(request, "activity_log.html", {
        "user": user, "entries": entries[:1000], "hours": hours, "only": only, "choices": activity.HOURS,
        "last_hour": activity.as_text(activity.read(1)),
    })


@router.get("/account/data/running")
def data_running(request: Request, user=Depends(owner_user)):
    return templates.TemplateResponse(request, "_running.html", _data_context(request, user))


def _started(started: bool, label: str) -> RedirectResponse:
    message = f"{label} started. It shows under Running now." if started else f"{label} is already running."
    return RedirectResponse(f"/account/data?message={quote(message)}", status_code=303)


def _typed_guessed() -> list[dict]:
    """Cards whose typed price 0.20.0 had to put on a printing by guess (you have several, or none), while it
    is still there: to open and move if it's on the wrong one."""
    row = conn.execute("SELECT value FROM app_meta WHERE key = 'typed_guessed'").fetchone()
    ids = json.loads(row[0]) if row else []
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return [dict(r) for r in conn.execute(
        f"""SELECT c.id, c.name, c.number, s.name AS set_name, GROUP_CONCAT(DISTINCT t.variant) AS variants
            FROM typed_prices t JOIN cards c ON c.id = t.card_id JOIN sets s ON s.set_id = c.set_id
            WHERE t.card_id IN ({marks}) GROUP BY c.id ORDER BY c.name""", ids)]


@router.post("/account/data/descriptions")
def data_descriptions(user=Depends(owner_user)):
    """Download Pokémon descriptions (Bulbapedia's Origin sections), then remake Describe's index with them."""
    def run(job_conn, job):
        web.describer.build()  # the Pokédex first: it lists the species
        species = {r["dex"]: r["name"] for r in web.describer.species()}
        result = descriptions.download(species, job)
        web.describer.build(rebuild=True)
        return result
    return _started(background.start("descriptions", "Downloading Pokémon descriptions from Bulbapedia", run),
                    "Downloading Pokémon descriptions")


@router.post("/account/data/reset-tags")
def data_reset_tags(user=Depends(owner_user)):
    """Reset tags: bring back every automatic tag and Pokémon word that was removed."""
    def run(job_conn, job):
        dexes = [r[0] for r in conn.execute("SELECT DISTINCT dex FROM pokemon_tag_hidden")]
        n = tags.reset(conn)
        own, _ = pokemon_words.own_and_hidden(conn)
        for dex in dexes:
            web.describer.update_words(dex, own.get(dex, []), [])
        return f"Brought back {n} tag{'s' if n != 1 else ''}." if n else "Nothing to bring back: no tags were removed."
    return _started(background.start("reset-tags", "Bringing back removed tags", run), "Bringing back removed tags")


@router.post("/account/data/describe")
def data_describe(user=Depends(owner_user)):
    """Fetch the PokéAPI files again and rebuild Describe a Pokémon (and the evolution lines)."""
    def run(job_conn, job):
        result = _build_describe(job_conn, job, again=True)
        web.lines.__init__()  # read the new species list next time
        facts.forget()  # and the new Pokédex entries
        return result
    return _started(background.start("describe", "Getting the Pokédex for Describe a Pokémon", run), "Getting the Pokédex")


@router.post("/account/data/sprites")
def data_sprites(user=Depends(owner_user)):
    """Download the Pokémon sprites not here yet and work out their colours."""
    return _started(background.start("sprites", "Downloading Pokémon sprites",
                                     lambda job_conn, job: themes.download(job_conn, job)), "Downloading Pokémon sprites")


def _load_all_chinese(job_conn, job):
    return chinese.load_all(job_conn, job)


@router.post("/account/data/chs/all")
def data_chs_all(back: str = Form(""), user=Depends(owner_user)):
    """Load every mainland Chinese set with its English versions."""
    started = background.start("chs", "Loading all Chinese sets", _load_all_chinese)
    if back.startswith("/scans/") or back.startswith("/cards/new"):  # from a scan or card that hit a set that isn't here
        return RedirectResponse(back, status_code=303)
    return _started(started, "Loading all Chinese sets")


def _refresh_tcgdex(job_conn, job) -> str:
    sets = job_conn.execute(
        """SELECT s.set_id, s.language FROM sets s WHERE NOT EXISTS (SELECT 1 FROM cards c WHERE c.set_id = s.set_id
           AND (c.source IS NOT NULL OR c.hand_added_by IS NOT NULL)) ORDER BY s.language, s.set_id""").fetchall()
    failed = []
    for i, row in enumerate(sets):
        job.progress(i, len(sets), f"TCGdex: {row['set_id']}")
        try:
            catalog.cache_set(job_conn, web.api_for(row["language"]), catalog.tcgdex_set_id(row["set_id"]))
        except Exception as e:
            failed.append(row["set_id"])
            log.warning("couldn't refresh %s: %s", row["set_id"], e)
    job.progress(len(sets))
    return f"Refreshed {len(sets) - len(failed)} TCGdex sets." + (f" Couldn't reach: {', '.join(failed[:5])}." if failed else "")


@router.post("/account/data/check")
def data_check(user=Depends(owner_user)):
    """Check for updates: TCGdex sets, the Chinese sets and their English versions, then new cards of followed Pokémon."""
    def run(job_conn, job):
        done = [_refresh_tcgdex(job_conn, job)]
        if chinese.loaded_codes(job_conn):
            try:
                done.append(chinese.load_all(job_conn, job, only_changed=True))
            except Exception as e:
                log.warning("Chinese sets not checked: %s", e)
                done.append(f"Couldn't check the Chinese sets ({e.__class__.__name__}).")
        tcgdex.artist_names(web.api, max_age=0)  # The artist list for search and follow
        for dex in sorted({r[0] for r in job_conn.execute("SELECT dex_id FROM followed_pokemon")}):
            try:
                done.append(chase.run_follow(job_conn, dex, web.names, chase_api, None if web.SCANNER_EXTERNAL else web.picture_encoder,
                                             job, only_new=True))
                _group_in_scanner(dex)
            except Exception as e:
                log.warning("followed Pokémon #%s not checked: %s", dex, e)
        if _pictures_ready():
            _index_pictures("Adding new sets to Enhanced Search")
            done.append("New sets are being added to Enhanced Search.")
        if picture_saver.enabled:
            picture_saver.poke(recheck=True)  # twins, then missing and borrowed pictures
            done.append("Card pictures are being checked.")
        if themes.counts(job_conn)["normal"]:  # New sprites too, when sprites are here
            try:
                themes.download(job_conn, job)
            except Exception as e:
                log.warning("sprites not checked: %s", e)
        job_conn.execute("INSERT INTO app_meta (key, value) VALUES ('updates_checked_at', ?) "
                         "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (clock.now(),))
        job_conn.commit()
        return " ".join(done)
    if background.running("chs"):
        return RedirectResponse(f"/account/data?message={quote('Wait for the Chinese sets to finish loading.')}", status_code=303)
    return _started(background.start("update", "Checking for updates", run), "Checking for updates")


@router.post("/account/data/tcgdex/clear")
def data_tcgdex_clear(user=Depends(owner_user)):
    """Forget stored sets nobody has, wants, pulled or bought cards from. They load again when needed."""
    in_use = ("SELECT card_id FROM inventory UNION SELECT card_id FROM wants UNION SELECT card_id FROM pulls "
              "UNION SELECT card_id FROM card_extras")
    sets = [r["set_id"] for r in conn.execute(
        f"""SELECT s.set_id FROM sets s WHERE NOT EXISTS (SELECT 1 FROM cards c WHERE c.set_id = s.set_id
              AND (c.id IN ({in_use}) OR c.source IS NOT NULL OR c.hand_added_by IS NOT NULL))
            AND s.set_id NOT IN (SELECT set_id FROM purchases WHERE set_id IS NOT NULL)""")]
    with db.transaction(conn):
        for set_id in sets:
            for table in ("notes", "prices", "price_snapshots"):
                conn.execute(f"DELETE FROM {table} WHERE card_id IN (SELECT id FROM cards WHERE set_id = ?)", (set_id,))
            conn.execute("DELETE FROM cards WHERE set_id = ?", (set_id,))
            conn.execute("DELETE FROM sets WHERE set_id = ?", (set_id,))
    message = f"Cleared {len(sets)} set{'' if len(sets) == 1 else 's'} nobody has cards from."
    return RedirectResponse(f"/account/data?message={quote(message)}", status_code=303)


@router.post("/account/data/nightly")
def data_nightly(on: str = Form(""), at: str = Form(sync.DEFAULT_TIME), user=Depends(owner_user)):
    """The nightly price update: on or off, and when."""
    try:
        sync.save_nightly(conn, bool(on), at)
    except ValueError:
        return RedirectResponse(f"/account/data?message={quote('Use a time like 04:00.')}#prices", status_code=303)
    s = sync.nightly_settings(conn)
    message = f"Prices update every night at {s['time']}." if s["on"] else "The nightly price update is off."
    return RedirectResponse(f"/account/data?message={quote(message)}#prices", status_code=303)


@router.post("/account/data/scanning")
def data_scanning(on: str = Form(""), user=Depends(owner_user)):
    """The Scanning switch, used when .env doesn't set DEXSTASH_SCANNER."""
    try:
        mode = web.set_scanning(on == "1")
    except ValueError as e:
        return RedirectResponse(f"/account/data?message={quote(str(e))}#scanner", status_code=303)
    message = ("Scanning is on: the photo reader starts inside the app." if mode == "inside"
               else "Scanning is off. Photos you take are kept, unread.")
    return RedirectResponse(f"/account/data?message={quote(message)}#scanner", status_code=303)


@router.post("/account/data/zone")
def data_zone(zone: str = Form(...), user=Depends(owner_user)):
    """The time zone for dates, times and the nightly update."""
    try:
        region.save_zone(conn, zone)
    except ValueError:
        return RedirectResponse(f"/account/data?message={quote('Pick a time zone from the list.')}#region", status_code=303)
    return RedirectResponse(f"/account/data?message={quote('Times now show in ' + display.zone().key + '.')}#region",
                            status_code=303)


def fetch_rates_soon(fetch: bool = True) -> None:
    """For a currency just picked, as a job: today's exchange rates (fetch), then every saved price and every
    history point again in that currency, each point at its own day's rate. A price update goes first if it's
    already waiting or running (one job at a time)."""
    def refresh(c, job):
        if fetch:
            fx.refresh(c, force=True)
        with db.transaction(c):
            n = prices.localize_all(c)
            points, kept = prices.localize_history(c)
        logging.getLogger("dexstash.fx").info("prices saved again in %s: %s rows, %s history points (%s older than "
                                              "every rate kept as they were)", fx.shown(), n, points, kept)
        return f"Prices saved again in {fx.shown()}."
    background.start("currency", "Saving prices in your currency", refresh, again=True)


@router.post("/account/data/ebay")
def data_ebay(site: str = Form(...), user=Depends(owner_user)):
    """The eBay site the card page's "Search eBay sold" link opens."""
    try:
        prices.set_ebay_site(conn, site)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return RedirectResponse(f"/account/data?message={quote(f'Search eBay sold opens {site}.')}#ebay", status_code=303)


@router.post("/account/data/names")
def data_names(how: str = Form(...), user=Depends(owner_user)):
    """How Japanese and Chinese set names show (round 27)."""
    try:
        english.set_shown(conn, how)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return RedirectResponse(f"/account/data?message={quote('Saved how Japanese and Chinese names show.')}#names", status_code=303)


@router.post("/account/data/stale")
def data_stale(days: int = Form(...), user=Depends(owner_user)):
    """How old a downloaded price is before Cards to review lists it."""
    days = max(1, min(int(days), 365))
    conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)", (prices.STALE_KEY, str(days)))
    conn.commit()
    return RedirectResponse(f"/account/data?message={quote(f'A price is stale after {days} days.')}#stale", status_code=303)


@router.post("/account/data/currency")
def data_currency(currency: str = Form(...), user=Depends(owner_user)):
    """The currency prices show in. Its rate comes from the ECB file the app already reads; every saved price is
    then saved again in it, in the background."""
    try:
        fx.save_currency(conn, currency)
    except ValueError as e:
        return RedirectResponse(f"/account/data?message={quote(str(e))}#currency", status_code=303)
    fetching = fx.shown() != currency and settings.on("EURO_RATE")
    fetch_rates_soon(fetch=fetching)
    if fetching:
        message = f"Saved. Prices show in {currency} once today's rate is downloaded, in a few seconds."
    elif fx.shown() != currency:
        message = "Saved, but prices stay in US dollars: rates come from the European Central Bank, which is off in .env."
    else:
        message = f"Prices now show in {fx.CURRENCIES[currency][0].lower() if currency != 'USD' else 'US dollars'}."
    return RedirectResponse(f"/account/data?message={quote(message)}#currency", status_code=303)


@router.post("/account/data/history")
def data_history(daily: int = Form(...), weekly: int = Form(...), monthly: int = Form(...), user=Depends(owner_user)):
    """How much price history is kept. A shorter setting thins the history now."""
    old = worth.keep_settings(conn)
    try:
        new = worth.save_keep(conn, daily, weekly, monthly)
    except ValueError:
        return RedirectResponse(f"/account/data?message={quote('Pick one of the listed choices.')}#history", status_code=303)
    message = "Saved. Older price history was turned into averages now." if worth.shorter(old, new) else \
        "Saved. More history is kept from now on."
    return RedirectResponse(f"/account/data?message={quote(message)}#history", status_code=303)


@router.post("/account/data/history/clear")
def data_history_clear(confirm: str = Form(""), user=Depends(owner_user)):
    """Clear price history (type CLEAR). Today's prices stay."""
    if confirm.strip() != "CLEAR":
        return RedirectResponse(f"/account/data?message={quote('Type CLEAR to clear the price history.')}#history", status_code=303)
    n = worth.clear_history(conn)
    log.info("price history cleared by %s: %d rows", user["username"], n)
    message = "Price history cleared. Today's prices stay, and history starts again with the next price update."
    return RedirectResponse(f"/account/data?message={quote(message)}#history", status_code=303)


@router.post("/account/data/sites")
async def data_sites(request: Request, user=Depends(owner_user)):
    """Which price and picture sites the app may contact. Turning one off deletes nothing."""
    form = await request.form()
    with db.transaction(conn):
        for site in sites.SITES:
            sites.set_on(conn, site, form.get(site) == "1")
    off = [sites.label(s) for s in sites.SITES if not sites.on(s)]
    message = ("Saved. Off: " + ", ".join(off) + ". Nothing was deleted.") if off else "Saved. Every site is on."
    return RedirectResponse(f"/account/data?message={quote(message)}#sites", status_code=303)


@router.post("/account/data/vintage")
def data_vintage(years: int = Form(...), user=Depends(owner_user)):
    """How old a set must be to count as vintage, 20 years by default."""
    smart.save_vintage_years(conn, years)
    message = f"Vintage now means sets from {smart.vintage_year(smart.vintage_years(conn)) - 1} and earlier."
    return RedirectResponse(f"/account/data?message={quote(message)}#vintage", status_code=303)


@router.post("/account/data/prices")
def data_prices(user=Depends(owner_user)):
    return _started(updater.start(), "Updating prices")


@router.post("/account/data/catalog/rebuild")
def data_catalog_rebuild(user=Depends(owner_user)):
    """Empty the downloaded card data and prices (the catalog) and fetch them again."""
    if updater.running:
        message = "A price update is running. Rebuild the downloaded data once it's done."
        return RedirectResponse(f"/account/data?message={quote(message)}#prices", status_code=303)
    db.rebuild_catalog(conn)
    card_index.forget()
    updater.start()
    return _started(True, "Rebuilding the downloaded data")


@router.post("/account/data/prices-and-links")
def data_prices_and_links(user=Depends(owner_user)):
    """The button's old address; There are no links to read, so it updates prices."""
    return _started(updater.start(), "Updating prices")


def _no_price_source() -> list[dict]:
    """Round 28: sets you keep cards from that neither TCGplayer (TCGCSV) nor CardTrader is matched to, so their cards
    get no downloaded price; each with Search links (link out only)."""
    out = []
    for r in conn.execute(f"""SELECT s.set_id, s.language, s.name, s.english_name FROM sets s
                               WHERE s.set_id IN (SELECT set_id FROM cards WHERE id IN ({prices.KEPT_SQL}))
                                 AND s.set_id NOT IN (SELECT set_id FROM tcgcsv_sets)
                                 AND s.set_id NOT IN (SELECT set_id FROM ct_sets WHERE expansion_id IS NOT NULL)
                               ORDER BY s.language, s.name""").fetchall():
        name = r["english_name"] or r["name"]
        words = name if name.isascii() else ""
        out.append({**dict(r), "tcgplayer": prices.tcgplayer_link(None, "", "", words, r["language"])[0] if words else None,
                    "cardtrader": prices.cardtrader_search_link("", words) if words else None,
                    "pricecharting": prices.pricecharting_link("", "", words, r["language"]) if words else None})
    return out


def _key_back(site: str, message: str, error: bool = False) -> RedirectResponse:
    """Back to the key's row; the message shows at the top, and a test's result on the row itself (red when it failed)."""
    return RedirectResponse(f"/account/data?message={quote(message)}#key-{site}", status_code=303)


@router.post("/account/data/keys/{site}")
def data_key_save(site: str, key: str = Form(""), user=Depends(owner_user)):
    """Round 28: an API key for a site (keys.py): stored encrypted, then tested once."""
    if site not in keys.SITES:
        raise HTTPException(404)
    try:
        keys.save(site, key)
    except ValueError as e:
        return _key_back(site, str(e), error=True)
    ok, said = keys.test(site)
    return _key_back(site, f"Saved. {said}", error=not ok)


@router.post("/account/data/keys/{site}/test")
def data_key_test(site: str, user=Depends(owner_user)):
    if site not in keys.SITES:
        raise HTTPException(404)
    ok, said = keys.test(site)
    return _key_back(site, said, error=not ok)


@router.post("/account/data/keys/{site}/remove")
def data_key_remove(site: str, user=Depends(owner_user)):
    if site not in keys.SITES:
        raise HTTPException(404)
    keys.remove(site)
    return _key_back(site, f"{keys.SITES[site]['label']} key removed.")


@router.post("/account/data/tcgcsv/pick")
def data_tcgcsv_pick(set_id: str = Form(...), group: str = Form(""), user=Depends(owner_user)):
    """Round 28: the TCGplayer set for one of yours that didn't match by code, name or its cards' products."""
    if group.isdigit() and tcgcsv.pick(conn, set_id, int(group)):
        message = "Saved. Its TCGplayer prices come with the next price update."
    else:
        message = "Pick one of TCGplayer's sets listed."
    return RedirectResponse(f"/account/data?message={quote(message)}#tcgcsv", status_code=303)


@router.post("/account/data/cardtrader/pick")
def data_cardtrader_pick(set_id: str = Form(...), expansion: str = Form(""), user=Depends(owner_user)):
    """The CardTrader expansion for a set that didn't match by code or name ('' = CardTrader hasn't it)."""
    if conn.execute("SELECT 1 FROM sets WHERE set_id = ?", (set_id,)).fetchone() is None:
        return RedirectResponse("/account/data#cardtrader", status_code=303)
    expansion_id = int(expansion) if expansion.isdigit() else None
    if expansion_id is not None and not conn.execute("SELECT 1 FROM ct_expansions WHERE id = ?", (expansion_id,)).fetchone():
        expansion_id = None
    cardtrader.pick(conn, set_id, expansion_id)
    message = "Saved. Its CardTrader prices come with the next price update." if expansion_id else "Saved. That set has no CardTrader prices."
    return RedirectResponse(f"/account/data?message={quote(message)}#cardtrader", status_code=303)


@router.post("/account/data/pokemon-missing")
def data_pokemon_missing(user=Depends(owner_user)):
    """Download missing: whatever Pokémon data isn't here yet, in order: the Pokédex (Describe and
    evolution lines), sprites, then Bulbapedia descriptions. What's here already isn't fetched again."""
    return _started(start_pokemon_data(), "Downloading missing Pokémon data")


def start_pokemon_data(with_descriptions: bool = True) -> bool:
    """The Download missing job (also started by the first-run wizard, which may leave out the descriptions).
    Bulbapedia's descriptions are only fetched while that site is on."""
    def run(job_conn, job):
        done = []
        if not web.describer.ready():
            done.append(_build_describe(job_conn, job))
            web.lines.__init__()
        if themes.counts(job_conn)["normal"] < themes.SPECIES:
            done.append(themes.download(job_conn, job))
        species = {r["dex"]: r["name"] for r in web.describer.species()}
        have = {dex for dex, text in descriptions.load().items() if text}
        if with_descriptions and sites.on("bulbapedia") and any(dex not in have for dex in species):
            done.append(descriptions.download(species, job))
            web.describer.build(rebuild=True)
        return " ".join(d for d in done if d) or "Nothing was missing."
    return background.start("pokemon-data", "Downloading missing Pokémon data", run)


@router.post("/account/data/pictures/score/reset")
def data_pictures_score_reset(user=Depends(owner_user)):
    conn.execute("DELETE FROM picture_scores")
    conn.commit()
    return RedirectResponse("/account/data#picture-score", status_code=303)


@router.post("/account/data/pictures/learned/forget")
def data_pictures_learned_forget(user=Depends(owner_user)):
    """Forget learned photos: the fingerprints go now; the index drops them on its next run."""
    conn.execute("DELETE FROM picture_learned")
    conn.commit()
    return RedirectResponse("/account/data#picture-score", status_code=303)


@router.post("/account/data/pictures/on")
def data_pictures_on(user=Depends(owner_user)):
    if not pictures.available():
        return RedirectResponse(f"/account/data?message={quote('Enhanced Search needs onnxruntime, numpy and Pillow.')}",
                                status_code=303)
    return _started(start_enhanced_search(), "Turning on Enhanced Search")


def start_enhanced_search() -> bool:
    """Turn Enhanced Search on: download the picture model, then fingerprint every set (also from the wizard)."""
    pictures.set_on(conn, True)

    def run(job_conn, job):
        pictures.download_model(job)
        return pictures.index_sets(job_conn, web.picture_encoder, web.api_for, job)
    if web.SCANNER_EXTERNAL:
        return background.queue("pictures", "Turning on Enhanced Search", "pictures-on")
    return background.start("pictures", "Turning on Enhanced Search", run)


@router.post("/account/data/pictures/off")
def data_pictures_off(user=Depends(owner_user)):
    pictures.set_on(conn, False)
    web.picture_encoder.unload_if_idle(now=float("inf"))
    message = "Enhanced Search is off. The model and fingerprints stay on the server, so turning it on again is quick."
    return RedirectResponse(f"/account/data?message={quote(message)}", status_code=303)
