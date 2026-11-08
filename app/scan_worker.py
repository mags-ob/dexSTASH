"""The scanner container: `python -m app.scan_worker`.

Everything heavy that used to run inside the web app runs here instead, at low priority with one CPU (see
docker-compose.yml): reading scan photos (RapidOCR), Enhanced Search (the picture model), fingerprinting
card pictures, grouping a followed Pokémon's cards by artwork, and looking up pictures for cards that have
none. The web app only queues work: scans by their status, the rest in jobs (runs_in 'scanner') and picture_lookups.
Both share the database (SQLite in WAL mode) in the data folder. A heartbeat in app_meta tells the app
whether the scanner is running.
"""

import logging
import os
import threading
import time

from . import backup, card_pictures, clock, chase, db, jobs, pictures, scans, settings
from .names import Names
from .tcgdex import TCGdex

log = logging.getLogger("dexstash.scanner")

POLL = 2           # seconds between looks for new work
HEARTBEAT = 15     # seconds between "still here" notes for the web app
STATS_EVERY = 600  # seconds between picture counts for Data sources
FOLLOWED_EVERY = 30  # Seconds between small batches of followed cards' pictures


class Scanner:
    def __init__(self, conn, api_for=None, names=None, reader=None, encoder=None, lookups_get=None, pause: float = 2.0):
        self.conn = conn
        self._apis: dict[str, TCGdex] = {}
        self.api_for = api_for or self._api_for
        self.names = names or Names(db.DB_PATH.parent / "pokemon_species_names.csv")
        self.worker = scans.Worker(self.api_for, reader=reader)
        self.encoder = encoder or pictures.Encoder()
        self.picture_worker = scans.PictureWorker(self.encoder, ready=self._pictures_ready)
        # This process's one job runner (jobs.py): the app's rows by their kind (self.run), picture lookups its own
        self.jobs = jobs.Jobs(conn, "scanner", run_kind=self.run, idle=POLL)
        self.lookups = card_pictures.LookupWorker(self.api_for, self._english_of, get=lookups_get, pause=pause,
                                                  jobs=self.jobs)
        self._beat = self._stats = self._followed_at = 0.0
        self._data_version = None
        self._followed: threading.Thread | None = None
        self._learned = None

    def _api_for(self, lang: str) -> TCGdex:
        return self._apis.setdefault(lang, TCGdex(lang))

    def _english_of(self, name: str | None, lang: str) -> str | None:
        if lang == "en" or not name:
            return name
        try:
            return self.names.to_english(name)
        except Exception:
            return None

    def _pictures_ready(self) -> bool:
        return pictures.is_on(self.conn) and pictures.available() and pictures.has_model()

    # --- one round of looking for work -------------------------------------------------

    def start(self) -> None:
        """After a (re)start: photos that were being read or matched go back in their queue (scans carry on); a job
        that was running when the scanner stopped is stopped. Ones the app asked for meanwhile wait their turn."""
        scans.resume(self.conn, self.worker, picture_worker=self.picture_worker)
        self.conn.execute("UPDATE jobs SET status = 'stopped', error = ?, finished_at = ? WHERE runs_in = 'scanner' "
                          "AND status = 'running'", (jobs.RESTARTED, clock.now()))
        self.conn.commit()
        self.jobs.start_runner()

    def tick(self, now: float | None = None) -> None:
        now = now or time.time()
        if now - self._beat >= HEARTBEAT:
            self._beat = now
            self._meta("scanner_seen", clock.from_unix(now))
            self._meta("scanner_model_loaded", "1" if self.encoder.loaded else "0")
        if not self._changed():
            return  # E9: nothing in the database changed since the last look (the heartbeat above is ours)
        if self.conn.execute("SELECT 1 FROM scans WHERE status = 'waiting' LIMIT 1").fetchone() and not self.worker.running():
            self.worker.start()
        if self.conn.execute("SELECT 1 FROM scans WHERE status = 'picture_waiting' LIMIT 1").fetchone() \
                and not self.picture_worker.running():
            self.picture_worker.start()
        if card_pictures.lookups_due(self.conn, 1):
            self.lookups.poke()
        self._followed_pictures(now)
        self._sync_learned()
        if self._pictures_ready() and now - self._stats >= STATS_EVERY:
            self._stats = now
            pictures.save_stats(self.conn)

    def _changed(self) -> bool:
        """SQLite's own count of changes other connections made (PRAGMA data_version): the full look only when it
        moved. Every FOLLOWED_EVERY seconds it looks anyway, for followed cards whose time came."""
        version = self.conn.execute("PRAGMA data_version").fetchone()[0]
        due = time.time() - self._followed_at >= FOLLOWED_EVERY
        if version == self._data_version and not due:
            return False
        self._data_version = version
        return True

    def _followed_pictures(self, now: float, wait: bool = False) -> None:
        """A few followed cards' pictures at a time, only while no photo is waiting."""
        if now - self._followed_at < FOLLOWED_EVERY or (self._followed and self._followed.is_alive()):
            return
        if self.conn.execute("SELECT 1 FROM scans WHERE status IN ('waiting', 'reading', 'picture_waiting', 'picture_searching') "
                             "LIMIT 1").fetchone():
            return
        self._followed_at = now
        due = card_pictures.followed_due(self.conn)
        if not due:
            return

        def run():
            conn = db.connect()
            try:
                for card_id in due:
                    try:
                        card_pictures.check_followed(conn, card_id)
                    except Exception:
                        log.exception("followed picture for %s failed", card_id)
                    time.sleep(card_pictures.PAUSE)
            finally:
                conn.close()
        self._followed = threading.Thread(target=run, daemon=True, name="followed-pictures")
        self._followed.start()
        if wait:
            self._followed.join()

    def _meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO app_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                          (key, value))
        self.conn.commit()

    def _sync_learned(self) -> None:
        """What your confirmed scans taught Enhanced Search goes into its index (and forgotten ones out)."""
        signature = tuple(self.conn.execute("SELECT COUNT(*), MAX(at), group_concat(key) FROM picture_learned").fetchone())
        if signature != self._learned and self._pictures_ready():
            self._learned = signature
            if pictures.sync_learned(self.conn):
                pictures.save_stats(self.conn)

    # --- jobs the web app asked for (by kind; this process's runner calls run) -------------------------

    def run(self, conn, kind: str, arg: str | None, job) -> str:
        if kind == "pictures-on":
            pictures.download_model(job)
            kind = "pictures-index"
        if kind == "pictures-index":
            text = pictures.index_sets(conn, self.encoder, self.api_for, job)
            pictures.save_stats(conn)
            return text
        if kind == "recheck":
            dex = int(arg)
            return chase.run_recheck(conn, dex, self.encoder, job)
        raise ValueError(f"Unknown job {kind!r}")


def _wait_for_the_app(path, code: str, sleep=time.sleep) -> None:
    """The web app makes and upgrades the database (and renames the old database file the first time): wait for it."""
    while True:
        if path.exists():
            try:
                conn = db.connect(path)
                try:
                    row = conn.execute("SELECT value FROM app_meta WHERE key = 'code_version'").fetchone()
                finally:
                    conn.close()
                if row and row[0] == code:
                    return
            except Exception as e:
                log.info("database not ready: %s", e)
        log.info("waiting for the web app to set up the database")
        sleep(5)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from . import activity
    activity.setup("scanner")  # Data sources › Activity log
    try:
        os.nice(10)  # the web app (searches, pages) comes first on a busy NAS
    except OSError:
        pass
    _wait_for_the_app(db.DB_PATH, backup.code_version())
    conn = db.connect()
    scanner = Scanner(conn)
    scanner.start()
    log.info("scanner running (%s)", settings.app_name())
    while True:
        try:
            scanner.tick()
        except Exception:
            log.exception("scanner round failed")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
