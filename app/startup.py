"""Starting the app, in three phases, from main.py's start-up hook (never just by importing the app):

1. settings: the activity log first, so every message below reaches Data sources › Activity log; then a check
   of the setting names.
2. before the first page (must succeed): a backup before an upgrade, the database steps (db.STEPS), the quick
   after-upgrade tasks (upgrades.py), time zone and currency, the first account or its setup code.
3. after the first page, in the background: slow after-upgrade tasks ("Finishing the upgrade" shows meanwhile),
   and scans left from before the restart.

If phase 2 fails, the app still answers: every page is a plain "The upgrade didn't finish" page with the error and
the backup to put back (main.py), and nothing else starts. Each phase logs how long it took."""
import logging
import sys
import threading
import time
from pathlib import Path

from . import __version__, activity, backup, db, fx, region, settings, upgrades

log = logging.getLogger("dexstash")

# What pages need to know: failed (None, or {"error", "backup"}), and finishing (slow upgrade work running).
STATE: dict = {"failed": None, "finishing": False, "backup": None}


def _took(phase: str, since: float, what: str = "") -> None:
    log.info("startup: %s%s, %.1f s", phase, f" ({what})" if what else "", time.monotonic() - since)


def settings_phase() -> None:
    activity.setup("app")  # Also written to data/logs/app.log for Data sources › Activity log
    log.info("dexSTASH %s starting", __version__)  # Which version is running, in the activity log
    print(f"  dexSTASH {__version__}", file=sys.stderr, flush=True)  # and in docker logs
    settings.check_names()  # A setting with another prefix is named in the log, not silently ignored


def _stop_left_over(conn) -> None:
    """Your rule (30 Oct): after a restart scans carry on (after_first_page), every other job that was running or
    waiting stops ("Stopped by a restart") and you start it again yourself. An import that was matching too."""
    from . import importer, jobs
    for runs_in in ("app", "scanner"):
        jobs.stop_left_over(conn, runs_in)
    importer.stop_left_over(conn)


def before_first_page(conn, backups: Path) -> None:
    started = time.monotonic()
    from . import web
    new = db.is_new(conn)
    backup.adopt_old_names(backups)  # Copies from before the rename keep showing
    STATE["backup"] = backup.before_upgrade(conn, backups)  # a copy before new code changes any tables
    steps_before = db.version(conn)
    with conn.request():  # the table changes land together
        db.init(conn)
    backup.mark_version(conn)
    _stop_left_over(conn)
    if new:
        upgrades.mark_all(conn)
    done = upgrades.run(conn, slow=False)
    region.load(conn)  # The time zone setting (saved from .env on the first start)
    fx.load(conn)  # The currency setting and the last exchange rates
    web.read_scanning_switch()  # the in-app scanning switch, now that the database is ready
    web._first_account()
    steps = db.version(conn) - steps_before
    _took("before the first page", started, f"{steps} database step{'' if steps == 1 else 's'}, "
                                            f"{len(done)} upgrade task{'' if len(done) == 1 else 's'}" if steps or done else "")


def after_first_page(conn) -> threading.Thread:
    """Slow upgrade work and leftovers, on a background thread at the lowest priority."""
    from . import scans, web

    def run():
        started = time.monotonic()
        try:
            import os
            os.nice(19)
        except (AttributeError, OSError):
            pass
        work_conn = db.connect()
        try:
            if upgrades.pending(work_conn, slow=True):
                STATE["finishing"] = True
                try:
                    upgrades.run(work_conn, slow=True)
                except Exception:
                    log.exception("the upgrade's last part didn't finish; it's tried again at the next start")
                finally:
                    STATE["finishing"] = False
            scans.drop_chinese_batches(work_conn)
            web.picture_saver.poke_if_changed(work_conn)  # notes what's kept now, so a change after this is seen
            if web.SCANNER_MODE == "threads":  # the scanner does this when it starts
                scans.resume(work_conn, web.scan_worker, picture_worker=web.picture_worker)
        except Exception:
            log.exception("startup: background part stopped")
        finally:
            work_conn.close()
            _took("after the first page", started)

    thread = threading.Thread(target=run, daemon=True, name="startup-after")
    thread.start()
    return thread


def start(conn, backups: Path) -> bool:
    """All three phases. False when phase 2 failed: the app then shows only the failure page."""
    settings_phase()
    try:
        before_first_page(conn, backups)
    except Exception as e:
        log.exception("startup: the upgrade didn't finish")
        STATE["failed"] = {"error": str(e) or type(e).__name__, "backup": STATE["backup"]}
        return False
    STATE["failed"] = None
    after_first_page(conn)
    return True
