"""Refreshing prices of the cards you have (and cards with a link or price
you added). Other cards' prices aren't kept; they're looked up live when shown.

Runs when asked (the 'Update prices' button, or by hand with
`python -m app.sync`) and once a night when the nightly update is on (Nightly).
"""

import json
import logging
import threading
import time
from datetime import datetime, timedelta


from . import catalog, clock, db, display, jobs, prices

log = logging.getLogger("dexstash.sync")
MISS_DAYS = 30  # a card TCGdex has no English version of is asked again after this many days


def _kept_cards(conn) -> list:
    return conn.execute(
        f"""SELECT c.id, s.language FROM cards c JOIN sets s ON s.set_id = c.set_id
            WHERE c.id IN ({prices.KEPT_SQL}) AND c.hand_added_by IS NULL AND c.source IS NULL ORDER BY c.id""").fetchall()


TCGDEX_ALL_EVERY = timedelta(days=7)  # B6: every kept card is asked once a week, for new pictures and types
TCGDEX_ALL_AT = "tcgdex_all_at"


def tcgdex_due(conn) -> tuple[list, bool]:
    """B6: the kept cards the nightly run asks TCGdex about: ones with no picture, and ones TCGCSV doesn't price (no
    TCGCSV price at all, or none for a printing you have). Once a week, every kept card. Returns (rows, every one)."""
    rows = _kept_cards(conn)
    last = _meta(conn, TCGDEX_ALL_AT)
    if last is None or last < clock.since(TCGDEX_ALL_EVERY):
        return rows, True
    need = {r[0] for r in conn.execute(
        f"""SELECT c.id FROM cards c WHERE c.id IN ({prices.KEPT_SQL}) AND (c.image_url IS NULL
              OR NOT EXISTS (SELECT 1 FROM price_quotes q WHERE q.card_id = c.id AND q.source = 'tcgplayer'
                             AND q.basis = 'tcgcsv')
              OR EXISTS (SELECT 1 FROM inventory i WHERE i.card_id = c.id AND NOT EXISTS (
                  SELECT 1 FROM price_quotes q WHERE q.card_id = i.card_id AND q.variant = i.variant
                  AND q.source = 'tcgplayer' AND q.basis = 'tcgcsv')))""")}
    return [r for r in rows if r["id"] in need], False


def tcgdex_all_done(conn) -> None:
    _set_meta(conn, TCGDEX_ALL_AT, clock.now())


def refresh_cards(conn, api_for, rows, seen: dict | None = None, resolve: bool = True) -> tuple[int, list[str]]:
    """Fetch and store the prices of the given cards (rows of id, language).
    seen, when given, collects each fetched card's detail by our card id, so a later step can reuse it.
    resolve False: the caller works the cards' prices out once, at its end (E3)."""
    def fetch(row):
        jobs.pause_for_pages()
        try:
            return row, api_for(row["language"]).get_card(catalog.tcgdex_set_id(row["id"])), None
        except Exception as e:  # one bad card shouldn't stop the rest
            return row, None, e

    with jobs.pool(8) as pool:
        results = list(pool.map(fetch, rows))
    errors = [f"{row['id']}: {e}" for row, _, e in results if e is not None]
    for row, _, e in results:
        if e is not None:
            log.warning("price update failed for %s: %s", row["id"], e)
    with db.transaction(conn):
        for row, detail, _ in results:
            if detail is not None:
                if seen is not None:
                    seen[row["id"]] = detail
                prices.store(conn, row["id"], detail, resolve)
                catalog.store_types(conn, row["id"], detail)  # the type comes with the same fetch
    return len(results) - len(errors), errors


def refresh_all(conn, api_for) -> tuple[int, list[str]]:
    """Re-fetch the prices of every kept card, and forget the prices of the
    rest. Returns (cards updated, errors)."""
    started = clock.now()
    from . import fx, settings
    if settings.on("EURO_RATE"):
        fx.refresh(conn)  # the day's exchange rates first, so every price below is converted at them
        if fx.backfill(conn):  # once: past days' rates (tried again if it fails), then history re-saved at them
            with db.transaction(conn):
                prices.localize_history(conn)
    # Each source in prices.SOURCES in its order: TCGCSV's TCGplayer prices per set first, TCGdex's for
    # what it doesn't have, then CardTrader's Near Mint median (when the host gave a token). Links you
    # pasted are never read.
    run: dict = {"seen": {}, "counts": {}}
    errors: list[str] = []
    by_source: dict[str, list[str]] = {}
    try:
        for source in prices.SOURCES:
            if source.fetch is not None:
                found = source.fetch(conn, api_for, run)
                by_source[source.name] = found
                errors += found
        from . import chase
        by_source["followed"] = chase.refresh_prices(conn, api_for, run["seen"])[1]  # cards of Pokémon you follow (each card fetched once)
        errors += by_source["followed"]
        from . import card_index
        by_source["catalog"] = card_index.refresh(conn, api_for)  # New and grown sets for Find's local catalog
        errors += by_source["catalog"]
        with db.transaction(conn):
            prices.apply_all(conn)
            prices.localize_all(conn)  # today's history in the install's currency too
        prices.prune(conn)
        _english_names(conn)
    except Exception as e:
        record_run(conn, started, "failed", {"kind": "full", "error": str(e)[:300],
                                             "sources": _sources_summary(run, by_source)})
        raise
    record_run(conn, started, "partial" if errors else "ok", {"kind": "full", "sources": _sources_summary(run, by_source)})
    return run.get("updated", 0), errors


RUNS_KEPT = 30  # run summaries kept (price_runs rows stay: they mark the days prices were updated)


def _sources_summary(run: dict, by_source: dict[str, list[str]]) -> dict:
    out = {}
    for name in dict.fromkeys(list(run.get("counts", {})) + list(by_source)):
        errs = by_source.get(name, [])
        if name in ("followed", "catalog") and not errs:
            continue  # listed only when they had a problem
        out[name] = {**run.get("counts", {}).get(name, {}), "errors": len(errs), "first_errors": [e[:200] for e in errs[:3]]}
    return out


def record_run(conn, started: str, status: str, summary: dict) -> None:
    """A price run's summary for Data sources (price_runs): when, how it went ('ok', 'partial' a source had problems,
    'failed' it stopped), the exchange rates' date and per source what it did. A short run (summary kind 'short')
    is kept in app_meta instead, so price_runs keeps marking only the days every price was updated."""
    row = conn.execute("SELECT value FROM app_meta WHERE key = 'eur_usd_at'").fetchone()
    fx_date = row[0][:10] if row and row[0] else None
    record = {"ran_at": started, "finished_at": clock.now(), "status": status, "fx_date": fx_date, **summary}
    if summary.get("kind") == "short":
        _set_meta(conn, LAST_SHORT, json.dumps(record))
        return
    with db.transaction(conn):
        conn.execute("""INSERT INTO price_runs (ran_at, finished_at, status, fx_date, summary_json) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(ran_at) DO UPDATE SET finished_at = excluded.finished_at, status = excluded.status,
                          fx_date = excluded.fx_date, summary_json = excluded.summary_json""",
                     (started, record["finished_at"], status, fx_date, json.dumps(summary)))
        conn.execute("""UPDATE price_runs SET summary_json = NULL WHERE summary_json IS NOT NULL AND ran_at NOT IN
                        (SELECT ran_at FROM price_runs WHERE summary_json IS NOT NULL ORDER BY ran_at DESC LIMIT ?)""",
                     (RUNS_KEPT,))


def runs(conn, limit: int = 6) -> list[dict]:
    """The last price runs with a summary, newest first, and the last short run (for Data sources)."""
    out = []
    for r in conn.execute("SELECT * FROM price_runs WHERE summary_json IS NOT NULL ORDER BY ran_at DESC LIMIT ?", (limit,)):
        out.append({**json.loads(r["summary_json"]), "ran_at": r["ran_at"], "finished_at": r["finished_at"],
                    "status": r["status"], "fx_date": r["fx_date"]})
    return out


def last_short_run(conn) -> dict | None:
    row = _meta(conn, LAST_SHORT)
    return json.loads(row) if row else None


LAST_SHORT = "price_short_run_last"


def refresh_sets(conn, api_for, set_ids, get=None, sleep=time.sleep) -> tuple[int, list[str]]:
    """The short run after you add or import cards: only those cards' sets. TCGCSV's prices for each set
    (matched to TCGplayer first, products read when new), TCGdex's for the printings it has no price for, then
    each kept card of those sets worked out again. Round 27: CardTrader's prices for those sets too (with a token;
    one request per set, as in the full update). Returns (cards updated, errors)."""
    from . import settings, tcgcsv
    set_ids = sorted({s for s in set_ids if s})
    if not set_ids:
        return 0, []
    started = clock.now()
    errors: list[str] = []
    if settings.on("TCGCSV"):
        try:
            get = get or tcgcsv.fetch
            tcgcsv.ensure_groups(conn, get, sleep)
            tcgcsv.match_sets(conn)
            errors += tcgcsv.read_sets(conn, set_ids, get, sleep, resolve=False)[1]
        except Exception as e:
            log.warning("TCGCSV not read for the new cards: %s", e)
            errors.append(f"TCGCSV: {e}")
    try:
        from . import cardtrader
        errors += cardtrader.run(conn, sleep=sleep, only=set(set_ids))[1]
    except Exception as e:
        log.warning("CardTrader not read for the new cards: %s", e)
        errors.append(f"CardTrader: {e}")
    try:  # round 28: PriceCharting's API, with the host's token
        from . import pricecharting
        errors += pricecharting.run(conn, sleep=sleep, only=set(set_ids))[1]
    except Exception as e:
        log.warning("PriceCharting not read for the new cards: %s", e)
        errors.append(f"PriceCharting: {e}")
    marks = ",".join("?" * len(set_ids))
    rows = [r for r in _kept_cards(conn) if r["id"] in {c[0] for c in conn.execute(
        f"SELECT id FROM cards WHERE set_id IN ({marks})", set_ids)}]
    updated, card_errors = refresh_cards(conn, api_for, rows, resolve=False)
    errors += card_errors
    with db.transaction(conn):
        for (card_id,) in conn.execute(f"SELECT id FROM cards WHERE set_id IN ({marks}) AND id IN ({prices.KEPT_SQL})",
                                       set_ids).fetchall():
            prices.resolve(conn, card_id)
    _english_names(conn)
    record_run(conn, started, "partial" if errors else "ok",
               {"kind": "short", "sets": len(set_ids), "cards": updated, "errors": len(errors),
                "first_errors": [e[:200] for e in errors[:3]]})
    return updated, errors


def _english_names(conn) -> None:
    """Round 27: the sets just matched to TCGCSV's and CardTrader's lists get their English names."""
    try:
        from . import english
        english.refresh(conn)
    except Exception:
        log.exception("English set names not worked out")


def reload_hand_sets(conn, api_for) -> int:
    """Sets with cards added by hand are loaded again when TCGdex lists more cards than we have,
    in case it has your cards by now. One call per set; the full reload only when it grew. Returns sets reloaded."""
    reloaded = 0
    for row in conn.execute("""SELECT s.set_id, s.language, SUM(c.hand_added_by IS NULL) AS stored FROM sets s
                               JOIN cards c ON c.set_id = s.set_id GROUP BY s.set_id
                               HAVING SUM(c.hand_added_by IS NOT NULL) > 0""").fetchall():
        api, tcgdex_id = api_for(row["language"]), catalog.tcgdex_set_id(row["set_id"])
        try:
            data = api.get_set(tcgdex_id)
            listed = (data.get("cardCount") or {}).get("total") or len(data.get("cards") or [])
            if max(listed, len(data.get("cards") or [])) > (row["stored"] or 0):
                catalog.cache_set(conn, api, tcgdex_id, data)
                reloaded += 1
        except Exception as e:  # expected for sets TCGdex doesn't list
            log.info("set %s not reloaded: %s", row["set_id"], e)
    return reloaded


def fill_missing(conn, api_for) -> tuple[int, list[str]]:
    """Prices for cards someone has that don't have one yet (after an import, say): a short run of their sets."""
    ids = [r["id"] for r in prices.missing(conn)]
    if not ids:
        return 0, []
    marks = ",".join("?" * len(ids))
    sets = [r[0] for r in conn.execute(f"SELECT DISTINCT set_id FROM cards WHERE id IN ({marks})", ids)]
    return refresh_sets(conn, api_for, sets)


PENDING = "pending_price_sets"  # sets waiting for a short run, as JSON


def queue_sets(conn, set_ids) -> None:
    """Remember sets for the next short run (an add during a run is picked up when it ends)."""
    import json
    have = set(json.loads(_meta(conn, PENDING) or "[]"))
    new = have | {s for s in set_ids if s}
    if new != have:
        _set_meta(conn, PENDING, json.dumps(sorted(new)))


def take_sets(conn) -> list[str]:
    import json
    sets = json.loads(_meta(conn, PENDING) or "[]")
    if sets:
        _set_meta(conn, PENDING, None)
    return sets


class PriceUpdater:
    """Price runs are jobs (round 23): the full update ('prices': Update prices, the nightly run) and the short run
    after you add or import cards ('prices-short'). The runner runs one job at a time, so they never overlap."""
    short_runs = True  # the tests turn the short run after an add off and run refresh_sets themselves
    inline = None  # tests: a function giving the connection to run a short run on at once, on the caller's thread
    FULL, SHORT = "prices", "prices-short"

    def __init__(self, api_for, jobs=None):
        self.api_for = api_for
        self.jobs = jobs  # the app's jobs.Jobs
        self.last_result: str | None = None
        self.last_failed = False

    @property
    def running(self) -> bool:
        return self.jobs is not None and (self.jobs.running(self.FULL) or self.jobs.running(self.SHORT))

    def start(self, only_missing: bool = False, then=None, priority: int = 0, wait: bool = False) -> bool:
        """The full update (or, only_missing, prices for cards with none) as a job. then: run after the prices, in
        the same job ("Update prices and links"). False if one is already waiting or running."""
        def run(conn, job):
            result = self.run(conn, only_missing, job)
            if then is not None:
                try:
                    then()
                except Exception as e:
                    log.warning("after the price update: %s", e)
            return result
        return self.jobs.start(self.FULL, "Updating prices", run, priority=priority, wait=wait)

    def start_sets(self, set_ids) -> bool:
        """A short run for these sets (refresh_sets). They're queued first, so a run already going picks them up
        when it ends; ten quick adds make one run. Returns whether a new run was added."""
        if not self.short_runs:
            return False
        if self.inline is not None:
            refresh_sets(self.inline(), self.api_for, set_ids)
            return False
        conn = db.connect()
        try:
            queue_sets(conn, set_ids)
        finally:
            conn.close()
        return self.jobs.start(self.SHORT, "Pricing the cards you just added", lambda c, job: self.run(c, None, job),
                               again=True)

    def run(self, conn, only_missing: bool | None = False, job=None) -> str | None:
        """only_missing: True fills cards with no price, False is the full update, None only the queued sets.
        Returns what Data sources and Home say about it (None for a quiet run with nothing to say)."""
        self.last_failed = False
        try:
            started = time.monotonic()
            if only_missing is None:
                updated, errors = 0, []
            else:
                updated, errors = (fill_missing if only_missing else refresh_all)(conn, self.api_for)
            quiet = only_missing is not False
            while True:  # sets queued by adds and imports meanwhile: a short run each round
                sets = take_sets(conn)
                if not sets:
                    break
                more, more_errors = refresh_sets(conn, self.api_for, sets)
                updated += more
                errors += more_errors
            if quiet and not errors:
                return None  # quiet: nothing to report on the home page
            took = round(time.monotonic() - started)
            self.last_result = f"Updated prices for {updated} card{'' if updated == 1 else 's'} in {took}s."
            if errors:
                self.last_result += f" {len(errors)} couldn't be updated: " + "; ".join(errors[:5])
        except Exception as e:
            self.last_result = f"Price update failed: {e}"
            self.last_failed = True
            raise
        return self.last_result


# --- the nightly update --------------------------------------------------------------
# Approved 9 Oct 2026: Update prices runs by itself once a night, on by default
# at 04:00 in the app's time zone (display.zone()). Settings live in app_meta.
NIGHTLY_ON, NIGHTLY_TIME, NIGHTLY_LAST, NIGHTLY_ERROR = "nightly_prices", "nightly_time", "nightly_last", "nightly_error"
NIGHTLY_LAST_AT = "nightly_last_at"  # When it last ran, in UTC
DEFAULT_TIME = "04:00"


def _meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn, key: str, value: str | None) -> None:
    if value is None:
        conn.execute("DELETE FROM app_meta WHERE key = ?", (key,))
    else:
        conn.execute("INSERT INTO app_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, value))
    conn.commit()


def parse_time(text: str) -> str:
    """'4:00', '04:00' or '4' -> '04:00'. Raises ValueError."""
    text = (text or "").strip()
    hours, _, minutes = text.partition(":")
    h, m = int(hours), int(minutes or 0)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError("Use a time like 04:00.")
    return f"{h:02d}:{m:02d}"


def nightly_settings(conn) -> dict:
    return {"on": _meta(conn, NIGHTLY_ON) != "off", "time": _meta(conn, NIGHTLY_TIME) or DEFAULT_TIME,
            "last": _meta(conn, NIGHTLY_LAST), "error": _meta(conn, NIGHTLY_ERROR),
            "zone": display.now().tzname(), "zone_key": display.zone().key}


def save_nightly(conn, on: bool, at: str) -> None:
    _set_meta(conn, NIGHTLY_ON, "on" if on else "off")
    _set_meta(conn, NIGHTLY_TIME, parse_time(at))


def next_run(now: datetime, at: str) -> datetime:
    """The next time after now that the clock shows at (in now's time zone, the app's)."""
    h, m = (int(x) for x in at.split(":"))
    run = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return run if run > now else run + timedelta(days=1)


class Nightly:
    """The scheduler (round 23): at the nightly time it adds the nightly update as a job, and its chores (backup,
    picture tidy and weekly recheck, history roll-up, Value over time) as the next job. A night it's off, or one
    already waiting or running, is skipped. A night missed while the server was off isn't caught up: prices update
    the next night, or when you press Update prices. A failed run is tried again the next night."""
    KEY, CHORES = "nightly", "nightly-chores"

    def __init__(self, updater: PriceUpdater, now=display.now, after=None, jobs=None):
        self.updater = updater
        self.now = now
        self.after = after  # the chores
        self.jobs = jobs
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="nightly-schedule").start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        conn = db.connect()
        try:
            settings = nightly_settings(conn)
            due, zone = next_run(self.now(), settings["time"]), settings["zone_key"]
            while not self._stop.wait(30):
                settings = nightly_settings(conn)
                now = self.now()
                if now < due:
                    if settings["zone_key"] != zone:  # A new time zone moves tonight's run with it
                        due, zone = next_run(now, settings["time"]), settings["zone_key"]
                    due = min(due, next_run(now, settings["time"]))  # a new time takes effect tonight
                    continue
                if settings["on"]:
                    self.run_once(conn)
                due, zone = next_run(self.now(), settings["time"]), settings["zone_key"]
        finally:
            conn.close()

    def run_night(self, conn, job=None) -> str | None:
        """The nightly update itself (the job's work): prices, then what Data sources says about it."""
        try:
            result = self.updater.run(conn, False, job)
        except Exception:
            result = None  # last_result says why
        stamp = display.when(self.now().isoformat())
        _set_meta(conn, NIGHTLY_LAST, f"{stamp}: {self.updater.last_result or result or 'done'}")
        _set_meta(conn, NIGHTLY_LAST_AT, clock.stamp(self.now()))
        _set_meta(conn, NIGHTLY_ERROR, self.updater.last_result if self.updater.last_failed else None)
        return self.updater.last_result

    def run_once(self, conn) -> bool:
        """Add tonight's update and its chores (with no runner, as in some tests: run them now). False if a price
        update or tonight's run is already waiting or running."""
        if self.jobs is None:
            self.run_night(conn)
            if self.after:
                self.after()
            return True
        if self.jobs.running(self.KEY) or self.updater.running:
            log.info("nightly price update skipped: one is already running")
            return False
        self.jobs.start(self.KEY, "Nightly price update", lambda c, job: self.run_night(c, job), priority=1)
        if self.after:
            def chores(c, job):
                self.after()
                return "Nightly backup and tidy-up done."
            self.jobs.start(self.CHORES, "Nightly backup and tidy-up", chores, priority=1)
        return True


if __name__ == "__main__":
    from .tcgdex import TCGdex

    logging.basicConfig(level=logging.INFO)
    clients: dict[str, TCGdex] = {}
    conn = db.connect()
    db.init(conn)
    from . import fx
    fx.load(conn)  # the saved rates, so CardTrader's euro prices are converted (0.20.1)
    count, errs = refresh_all(conn, lambda lang: clients.setdefault(lang, TCGdex(lang)))
    print(f"Updated {count} cards." + (f" Errors: {errs}" if errs else ""))
