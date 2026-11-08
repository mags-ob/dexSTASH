import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from . import clock, settings

DB_PATH = Path(settings.get("DB", "data/dexstash.db"))
SCHEMA = Path(__file__).with_name("schema.sql")
CATALOG_SCHEMA = Path(__file__).with_name("catalog.sql")


def catalog_path(path: Path) -> Path:
    """The downloaded data that's the same for everyone, in a file next to the database."""
    return path.with_name(f"{path.stem}-catalog{path.suffix or '.db'}")


BUSY_MS = 20000  # how long a writer waits for another to finish before "database is locked"


def connect(path: Path | None = None, autocommit: bool = True) -> sqlite3.Connection:
    """A connection of its own, for a background thread, a job or the scanner container.

    These commit every statement as it runs (autocommit). The web app and the scanner are two
    processes sharing the file, so a write left uncommitted (say, an error between a write and its commit)
    held SQLite's one write lock until that thread ended, and every upload got "database is locked".
    Several statements that must land together go in `with transaction(conn):`, which opens a real one.
    The web app's own shared connection is made with autocommit=False and committed after every request."""
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=BUSY_MS / 1000,
                           isolation_level=None if autocommit else "")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # The web app and the scanner container share the file; a writer waits its turn.
    conn.execute(f"PRAGMA busy_timeout = {BUSY_MS}")
    # WAL keeps reads working during the price sync and is safer for backups.
    conn.execute("PRAGMA journal_mode = WAL")
    # With WAL this is still safe from corruption, and a commit no longer waits on the disk each time.
    conn.execute("PRAGMA synchronous = NORMAL")
    # The catalog (catalog.sql) is attached to every connection, so queries find its tables by name.
    conn.execute("ATTACH DATABASE ? AS catalog", (str(catalog_path(Path(path))),))
    conn.execute("PRAGMA catalog.journal_mode = WAL")
    conn.execute("PRAGMA catalog.synchronous = NORMAL")
    return conn


def settle(conn, where: str = "") -> bool:
    """Commit what a request or job left open, so it can't keep the write lock. True if anything was."""
    if not conn.in_transaction:
        return False
    import logging
    logging.getLogger("dexstash").warning("a write was left uncommitted%s; committing it", f" ({where})" if where else "")
    try:
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
    return True


class _Result:
    """A statement's rows, read in full while the connection was held, used like a cursor."""

    def __init__(self, cursor: sqlite3.Cursor):
        self.rows = cursor.fetchall()
        self.rowcount, self.lastrowid, self.description = cursor.rowcount, cursor.lastrowid, cursor.description
        self._at = 0

    def fetchone(self):
        if self._at >= len(self.rows):
            return None
        self._at += 1
        return self.rows[self._at - 1]

    def fetchall(self):
        rest, self._at = self.rows[self._at:], len(self.rows)
        return rest

    def __iter__(self):
        return iter(self.fetchall())


class SharedConnection:
    """A connection used by many threads at once: each statement runs under a lock and its rows are read
    before the lock is let go (sqlite3 connections mustn't run two statements at the same moment).
    Tools and tests use this; the web app uses ThreadConnection."""

    def __init__(self, conn: sqlite3.Connection):
        self.raw = conn
        self.lock = threading.RLock()

    def execute(self, sql: str, params=()):
        with self.lock:
            return _Result(self.raw.execute(sql, params))

    def executemany(self, sql: str, rows):
        with self.lock:
            return _Result(self.raw.executemany(sql, rows))

    def executescript(self, script: str):
        with self.lock:
            return _Result(self.raw.executescript(script))

    def commit(self) -> None:
        with self.lock:
            self.raw.commit()

    def rollback(self) -> None:
        with self.lock:
            self.raw.rollback()

    def backup(self, target, **kwargs) -> None:
        with self.lock:
            self.raw.backup(target, **kwargs)

    def close(self) -> None:
        with self.lock:
            self.raw.close()

    @property
    def row_factory(self):
        return self.raw.row_factory

    @property
    def isolation_level(self):
        return self.raw.isolation_level

    @property
    def in_transaction(self) -> bool:
        return self.raw.in_transaction


class ThreadConnection:
    """The web app's database: each thread gets a connection of its own.

    Until now every request shared one connection and so one open transaction: one request's rollback could
    undo another's writes, and a failed request's half-done writes were committed by the next one.
    Outside a request (startup, background jobs, async pages on the event loop) every statement saves on
    its own, like connect(). Inside request() (each sync page, see main.py) writes collect in the thread's
    own transaction: saved when the page finishes, undone if it fails. `with transaction(conn):` works in
    both, for statements that must land together."""

    def __init__(self, path: Path | None = None):
        self.path = path or DB_PATH
        self.busy_ms = BUSY_MS
        self.local = threading.local()
        self._opened_lock = threading.Lock()
        self.opened: list[tuple[threading.Thread, sqlite3.Connection]] = []

    @property
    def raw(self) -> sqlite3.Connection:
        raw = getattr(self.local, "raw", None)
        if raw is None:
            raw = connect(self.path)
            self.local.raw, self.local.busy = raw, BUSY_MS
            with self._opened_lock:
                # A thread that has ended can't use its connection again, so it's closed here.
                # Keeping every one open ran a long-running app out of open files ("unable to open database
                # file"), as each background job and picture check runs on a thread of its own.
                alive = []
                for thread, old in self.opened:
                    if thread.is_alive():
                        alive.append((thread, old))
                    else:
                        try:
                            old.close()
                        except sqlite3.Error:
                            pass
                self.opened = alive + [(threading.current_thread(), raw)]
        if self.local.busy != self.busy_ms:
            raw.execute(f"PRAGMA busy_timeout = {int(self.busy_ms)}")
            self.local.busy = self.busy_ms
        return raw

    @contextmanager
    def request(self):
        """One page's writes: kept together, saved when it ends, undone if it fails."""
        raw = self.raw
        if raw.in_transaction or raw.isolation_level is not None:
            yield  # already inside one (a page called from another); the outer one decides
            return
        raw.isolation_level = ""  # Python opens the transaction just before the first write
        try:
            yield
        except BaseException:
            if raw.in_transaction:
                raw.rollback()
            raise
        else:
            if raw.in_transaction:
                try:
                    raw.commit()
                except sqlite3.Error:
                    raw.rollback()
                    raise
        finally:
            raw.isolation_level = None

    # The thread's own cursor, read as you go: no other thread can use this connection, so rows needn't be
    # copied first (SharedConnection still does).
    def execute(self, sql: str, params=()):
        return self.raw.execute(sql, params)

    def executemany(self, sql: str, rows):
        return self.raw.executemany(sql, rows)

    def executescript(self, script: str):
        return self.raw.executescript(script)

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def backup(self, target, **kwargs) -> None:
        self.raw.backup(target, **kwargs)

    def close(self) -> None:
        with self._opened_lock:
            for _, raw in self.opened:
                try:
                    raw.close()
                except sqlite3.Error:
                    pass
            self.opened.clear()
        self.local = threading.local()

    @property
    def row_factory(self):
        return sqlite3.Row

    @property
    def isolation_level(self):
        return self.raw.isolation_level

    @property
    def in_transaction(self) -> bool:
        return self.raw.in_transaction


class NewerDatabase(RuntimeError):
    """The database was last used by a newer version of the app than this one."""


def version(conn) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def is_new(conn) -> bool:
    """No tables yet: a new install (call before init)."""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone() is None


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _add_columns(conn, *columns: tuple[str, str, str]) -> None:
    for table, column, kind in columns:
        have = _columns(conn, table)
        if have and column not in have:  # (no columns: a table a later step drops, which a new database never has)
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def _step_1(conn) -> None:
    """Everything up to the reliability round (21 Oct 2026): the column checks every start used to run."""
    _migrate(conn)
    _drop_unused(conn)


# Numbered database changes. PRAGMA user_version is the last step a database has had.
# schema.sql makes the tables; then a database runs the steps it hasn't had, in order, each in its own
# transaction (after the backup main.py makes before an upgrade). A new database runs them all, so a step
# adds a column only when it's missing. A step is never undone: going back to older code means restoring
# a backup from before the update. To change the database, add a new step here.
def _step_2(conn) -> None:
    """Each scan's own language and when each PriceCharting link was last tried."""
    _add_columns(conn, ("scans", "language", "TEXT"), ("scans", "language_sure", "INTEGER NOT NULL DEFAULT 1"),
                 ("card_extras", "pc_tried_at", "TEXT"))
    conn.execute("""UPDATE scans SET language = (SELECT b.language FROM scan_batches b WHERE b.id = scans.batch_id)
                    WHERE language IS NULL""")


def _step_3(conn) -> None:
    """An amount you type keeps the currency it was typed in. Everything before is dollars.
    A pull's own cost is in its purchase's currency."""
    _add_columns(conn, ("inventory", "acquired_currency", "TEXT NOT NULL DEFAULT 'USD'"),
                 ("purchases", "currency", "TEXT NOT NULL DEFAULT 'USD'"),
                 ("scans", "paid_currency", "TEXT NOT NULL DEFAULT 'USD'"),
                 ("import_rows", "cost_currency", "TEXT NOT NULL DEFAULT 'USD'"),
                 ("card_extras", "manual_currency", "TEXT NOT NULL DEFAULT 'USD'"))


def _step_4(conn) -> None:
    """Indexes for looking up a scan by its change number and a binder's cards."""
    conn.execute("CREATE INDEX IF NOT EXISTS scans_rev ON scans(rev)")
    conn.execute("CREATE INDEX IF NOT EXISTS binder_copies_binder ON binder_copies(binder_id)")


def _step_5(conn) -> None:
    """A switch for each price and picture site, and a card's link to another site. An install that already has accounts keeps
    using them all, as before; a new one starts with them off. Later updates never change a switch."""
    from .sites import SITES
    _add_columns(conn, ("card_extras", "other_url", "TEXT"))  # the paste box keeps a link to any other site
    value = "1" if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() else "0"
    for site in SITES:
        conn.execute("INSERT OR IGNORE INTO app_meta (key, value) VALUES (?, ?)", (f"site_{site}", value))


def _step_6(conn) -> None:
    """DexSTASH no longer reads other sites' pages. The price you saw on PriceCharting gets its own
    columns (typed in a currency, with the date). Prices read from PriceCharting before are copied into them with
    the date they were last read, from pasted links and from links the app found; only new columns (and an empty
    PriceCharting link) are filled, so nothing already there changes. CardTrader's tables are in schema.sql.
    Pictures looked up on PriceCharting are forgotten, so those cards are looked up again elsewhere."""
    _add_columns(conn, ("card_extras", "pc_typed", "REAL"), ("card_extras", "pc_typed_currency", "TEXT"),
                 ("card_extras", "pc_typed_at", "TEXT"), ("card_extras", "pc_typed_read", "INTEGER NOT NULL DEFAULT 0"))
    # A new database (since step 7) never had the read prices or found links, so there's nothing to copy.
    if "link_price" in _columns(conn, "card_extras"):
        conn.execute("""UPDATE card_extras SET pc_typed_currency = 'USD', pc_typed_read = 1,
                          pc_typed_at = CASE WHEN pc_price IS NOT NULL THEN pc_checked_at ELSE link_checked_at END,
                          pc_typed = COALESCE(pc_price, link_price)
                        WHERE pc_typed IS NULL AND pricecharting_url IS NOT NULL
                          AND (pc_price IS NOT NULL OR (link_source = 'pricecharting' AND link_price IS NOT NULL))""")
    found = conn.execute("""SELECT card_id, url, price, COALESCE(read_at, found_at) AS at FROM found_links
                            WHERE site = 'pricecharting' AND status IN ('sure', 'confirmed', 'guess')
                              AND price IS NOT NULL AND url IS NOT NULL""").fetchall() if _columns(conn, "found_links") else []
    for row in found:
        conn.execute("INSERT OR IGNORE INTO card_extras (card_id, updated_at) VALUES (?, ?)", (row["card_id"], row["at"]))
        conn.execute("""UPDATE card_extras SET pricecharting_url = COALESCE(pricecharting_url, ?), pc_typed = ?,
                          pc_typed_currency = 'USD', pc_typed_at = ?, pc_typed_read = 1
                        WHERE card_id = ? AND pc_typed IS NULL""", (row["url"], row["price"], row["at"], row["card_id"]))
    # A picture looked up on PriceCharting is never shown now: look the card up again (TCGdex, then TCGCSV).
    conn.execute("DELETE FROM picture_lookups WHERE url LIKE '%pricecharting.com%'")


# What the features removed in 0.15.1 (and the ≈ English price) left behind. Their data went
# where 0.15.1 put it (the PriceCharting price columns), so nothing you'd see is lost.
DEAD_TABLES = ("found_links", "english_prices", "english_misses", "pc_consoles")
DEAD_COLUMNS = (("card_extras", "link_price"), ("card_extras", "link_source"), ("card_extras", "link_checked_at"),
                ("card_extras", "link_error"), ("card_extras", "link_image"), ("card_extras", "pc_image"),
                ("card_extras", "pc_price"), ("card_extras", "pc_checked_at"), ("card_extras", "pc_tried_at"))


def _step_7(conn) -> None:
    """Drop the tables and columns of page reading and the ≈ English price, so nothing in the database
    (or the code) still looks like it reads other sites. An English name found for a Japanese card is kept on
    the card first; old found-link and ≈ English prices go (no price update has used them since 0.15.1)."""
    if _columns(conn, "found_links"):
        conn.execute("""UPDATE cards SET english_name = (
                          SELECT TRIM(SUBSTR(f.name, 1, CASE WHEN INSTR(f.name, ' · ') > 0 THEN INSTR(f.name, ' · ') - 1
                                                              ELSE LENGTH(f.name) END))
                          FROM found_links f WHERE f.card_id = cards.id AND f.site = 'english'
                            AND f.status IN ('guess', 'confirmed') AND f.name IS NOT NULL LIMIT 1)
                        WHERE english_name IS NULL AND id IN (SELECT card_id FROM found_links WHERE site = 'english'
                          AND status IN ('guess', 'confirmed') AND name IS NOT NULL)""")
    if "currency" in _columns(conn, "prices"):  # (a new database has step 9's prices table, without the marks)
        conn.execute("DELETE FROM prices WHERE currency IN ('USD-found', 'USD-en')")
    for table in DEAD_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    for table, column in DEAD_COLUMNS:
        if column in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


# The tables step 8 moves into the catalog, which older databases (and backups made before 0.15.2) keep
# in the database itself. CATALOG_TABLES is what catalog.sql has now (price_quotes in place of ct_prices).
MOVED_TABLES = ("pokemon_palette", "tcgdex_cache", "tcgcsv_groups", "tcgcsv_sets", "tcgcsv_cards", "tcgcsv_products",
                "tcgcsv_group_reads", "tcgcsv_prices", "ct_expansions", "ct_blueprints", "ct_prices")
CATALOG_TABLES = tuple(t for t in MOVED_TABLES if t != "ct_prices") + ("price_quotes", "index_sets", "index_cards",
                                                                     "tcgcsv_product_prices")


def _main_columns(conn, schema: str, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA {schema}.table_info({table})")]


def _step_8(conn) -> list[str]:
    """Move the downloaded data into the catalog. Rows already in the catalog win (restoring an
    older backup brings its old copies back here, and they're moved again). Returns the tables moved."""
    moved = []
    for table in MOVED_TABLES:
        have = _main_columns(conn, "main", table)
        if not have:
            continue
        if not _main_columns(conn, "catalog", table):  # one catalog.sql no longer makes (ct_prices): step 9 reads it
            conn.execute(f"CREATE TABLE catalog.{table} AS SELECT * FROM main.{table}")
            conn.execute(f"DROP TABLE main.{table}")
            moved.append(table)
            continue
        both = ", ".join(c for c in have if c in set(_main_columns(conn, "catalog", table)))
        conn.execute(f"INSERT OR IGNORE INTO catalog.{table} ({both}) SELECT {both} FROM main.{table}")
        conn.execute(f"DROP TABLE main.{table}")
        moved.append(table)
    return moved


# The shown price used to be marked in prices.currency (USD-ct, USD-extra, USD-print). The table
# that had them, as step 9 finds it.
_OLD_ORIGIN = """CASE p.currency WHEN 'USD' THEN 'tcgplayer' WHEN 'USD-ct' THEN 'cardtrader' WHEN 'USD-print' THEN 'printing'
                 WHEN 'USD-extra' THEN CASE WHEN EXISTS (SELECT 1 FROM card_extras e WHERE e.card_id = p.card_id
                   AND e.manual_price IS NULL AND e.pc_typed IS NOT NULL) THEN 'pricecharting' ELSE 'manual' END END"""


def _step_9(conn) -> None:
    """One price table. TCGplayer's prices and CardTrader's lowest listings go into
    catalog.price_quotes, with the prices each card showed kept as they were (now saying where each came from)."""
    if "currency" not in _columns(conn, "prices"):
        return  # a new database: schema.sql made the new table
    conn.execute("""INSERT OR IGNORE INTO catalog.price_quotes (card_id, variant, source, price, currency, low, mid, basis,
                      updated_at)
                    SELECT card_id, variant, 'tcgplayer', price, 'USD', low, mid, basis, updated_at FROM main.prices
                    WHERE source = 'tcgplayer' AND currency = 'USD'""")
    if _main_columns(conn, "catalog", "ct_prices"):
        conn.execute("""INSERT OR IGNORE INTO catalog.price_quotes (card_id, variant, source, price, currency, basis, ref,
                          updated_at)
                        SELECT card_id, variant, 'cardtrader', price, currency,
                               CASE WHEN any_condition THEN 'any' ELSE 'nm' END, blueprint_id, updated_at
                        FROM catalog.ct_prices""")
        conn.execute("DROP TABLE catalog.ct_prices")
    conn.execute("ALTER TABLE main.prices RENAME TO prices_before_15d")
    conn.execute("""CREATE TABLE main.prices (
                      card_id TEXT NOT NULL REFERENCES cards(id), variant TEXT NOT NULL, price REAL NOT NULL,
                      origin TEXT NOT NULL, basis TEXT, low REAL, mid REAL, updated_at TEXT NOT NULL,
                      PRIMARY KEY (card_id, variant))""")
    conn.execute(f"""INSERT OR IGNORE INTO main.prices (card_id, variant, price, origin, basis, low, mid, updated_at)
                     SELECT p.card_id, p.variant, p.price, {_OLD_ORIGIN}, p.basis, p.low, p.mid, p.updated_at
                     FROM main.prices_before_15d p WHERE p.source = 'tcgplayer'
                       AND p.currency IN ('USD', 'USD-ct', 'USD-print', 'USD-extra')""")
    conn.execute("DROP TABLE main.prices_before_15d")


def _step_10(conn) -> None:
    """The first-run wizard. An install already in use (cards, chases or scans, or more than one login)
    has done its setup, so it never sees the wizard."""
    in_use = any(conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone() for t in ("inventory", "wants", "scans"))
    if in_use or conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 1:
        conn.execute("INSERT OR IGNORE INTO app_meta (key, value) VALUES ('setup_done', '1')")


def _step_11(conn) -> None:
    """Binder pages. Each binder has a layout (4, 9 or 12 pockets a page) and a default sort."""
    _add_columns(conn, ("binders", "layout", "INTEGER NOT NULL DEFAULT 9"), ("binders", "sort", "TEXT NOT NULL DEFAULT 'set'"))


def _step_12(conn) -> None:
    """A printing's price is the average of every source, with their range and the sources counted. The
    start after this step works every card's prices out again (prices_round, web.py)."""
    _add_columns(conn, ("prices", "range_low", "REAL"), ("prices", "range_high", "REAL"), ("prices", "sources", "TEXT"))


def _step_13(conn) -> None:
    """Each price is also saved in the install's currency, with the rate used (prices, price history). The start
    after this step works every card's prices out again (prices_round, web.py)."""
    _add_columns(conn, ("prices", "local_currency", "TEXT"), ("prices", "local_rate", "REAL"),
                 ("prices", "price_local", "REAL"), ("prices", "range_low_local", "REAL"),
                 ("prices", "range_high_local", "REAL"),
                 ("price_snapshots", "local", "REAL"), ("price_snapshots", "local_currency", "TEXT"))


def _step_15(conn) -> None:
    """Prices per printing, from a list of sources.
    - Each source price keeps the source's own updated date and a link to its product.
    - Each day's exchange rates are kept (fx_rates), starting with the ones the app has now.
    - A history point can keep its low, high and how many days it stands for; a price run its summary.
    - TCGCSV's card table keys a card by printing too (Poké Ball and Master Ball products are listed apart).
      The catalog is downloaded data, so that table is made again and filled at the next price update.
    - Prices you typed move to typed_prices, each for one printing: the printing you have when you have one,
      else the card's first printing (those cards are listed on Data sources, app_meta 'typed_guessed')."""
    for column, kind in (("source_updated_at", "TEXT"), ("url", "TEXT")):
        if column not in _main_columns(conn, "catalog", "price_quotes"):
            conn.execute(f"ALTER TABLE catalog.price_quotes ADD COLUMN {column} {kind}")
    _add_columns(conn, ("price_snapshots", "low", "REAL"), ("price_snapshots", "low_source", "TEXT"),
                 ("price_snapshots", "high", "REAL"), ("price_snapshots", "high_source", "TEXT"),
                 ("price_snapshots", "samples", "INTEGER NOT NULL DEFAULT 1"),
                 ("price_runs", "finished_at", "TEXT"), ("price_runs", "status", "TEXT"),
                 ("price_runs", "fx_date", "TEXT"), ("price_runs", "summary_json", "TEXT"))
    if "variant" not in _main_columns(conn, "catalog", "tcgcsv_cards"):
        conn.execute("DROP TABLE catalog.tcgcsv_cards")
        made = CATALOG_SCHEMA.read_text().split("CREATE TABLE IF NOT EXISTS catalog.tcgcsv_cards", 1)[1].split(";", 1)[0]
        conn.execute("CREATE TABLE IF NOT EXISTS catalog.tcgcsv_cards" + made)
        conn.execute("UPDATE catalog.tcgcsv_sets SET products_at = NULL")  # products read again at the next update
    meta = dict(conn.execute("SELECT key, value FROM app_meta WHERE key IN ('ecb_rates', 'eur_usd', 'eur_usd_at')").fetchall())
    if meta.get("eur_usd_at"):
        import json
        try:
            rates = json.loads(meta.get("ecb_rates") or "{}")
        except ValueError:
            rates = {}
        if "USD" not in rates and meta.get("eur_usd"):
            rates["USD"] = float(meta["eur_usd"])
        rates["EUR"] = 1.0
        conn.executemany("INSERT OR IGNORE INTO fx_rates (rate_date, currency, per_eur) VALUES (?, ?, ?)",
                         [(meta["eur_usd_at"][:10], code, float(v)) for code, v in rates.items()])
    _typed_prices_per_printing(conn)


def _typed_prices_per_printing(conn) -> None:
    from . import inventory
    have = set(_columns(conn, "card_extras"))
    if "manual_price" not in have or conn.execute("SELECT 1 FROM typed_prices LIMIT 1").fetchone():
        return
    pc = "pc_typed" in have
    guessed = []
    for e in conn.execute(f"""SELECT e.* FROM card_extras e
                              WHERE e.manual_price IS NOT NULL{" OR e.pc_typed IS NOT NULL" if pc else ""}""").fetchall():
        owned = [r[0] for r in conn.execute("SELECT DISTINCT variant FROM inventory WHERE card_id = ? ORDER BY variant",
                                            (e["card_id"],))]
        if len(owned) == 1:
            variant = owned[0]
        else:
            card = conn.execute("SELECT * FROM cards WHERE id = ?", (e["card_id"],)).fetchone()
            quoted = {r[0] for r in conn.execute("SELECT variant FROM catalog.price_quotes WHERE card_id = ? AND variant <> ''",
                                                 (e["card_id"],))}
            mine = [v for v in inventory.VARIANTS if v in owned]
            variant = (mine or inventory.offered_variants(card, quoted))[0]
            guessed.append(e["card_id"])
        at = e["updated_at"] or "2026-01-01T00:00:00Z"
        if e["manual_price"] is not None:
            conn.execute("INSERT OR IGNORE INTO typed_prices (card_id, variant, kind, price, currency, typed_at, typed_by) "
                         "VALUES (?, ?, 'manual', ?, ?, ?, ?)",
                         (e["card_id"], variant, e["manual_price"], e["manual_currency"] or "USD", at, e["updated_by"]))
        if pc and e["pc_typed"] is not None:
            conn.execute("INSERT OR IGNORE INTO typed_prices (card_id, variant, kind, price, currency, typed_at, typed_by, was_read) "
                         "VALUES (?, ?, 'pricecharting', ?, ?, ?, ?, ?)",
                         (e["card_id"], variant, e["pc_typed"], e["pc_typed_currency"] or "USD", e["pc_typed_at"] or at,
                          e["updated_by"], 1 if e["pc_typed_read"] else 0))
    if guessed:
        import json
        conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('typed_guessed', ?)", (json.dumps(sorted(set(guessed))),))


# A time stored the way older versions wrote it: a space or a T, then the time, maybe with parts of a second or +00:00.
_OLD_TIME = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9][ T][0-9][0-9]:[0-9][0-9]*"
_STAMP = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z"
# Times older versions kept as seconds since 1970.
_UNIX_TIMES = (("main", "worker_jobs", "created_at"), ("main", "worker_jobs", "finished_at"),
               ("catalog", "tcgdex_cache", "fetched_at"))


def _tables(conn, schema: str) -> list[str]:
    return [r[0] for r in conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table' "
                                       "AND name NOT LIKE 'sqlite_%'").fetchall()]


def _step_14(conn) -> None:
    """One time format everywhere ('2026-10-28T19:56:42Z', clock.py): every stored time written another way is
    rewritten once, in both files. Dates without a time (a purchase date, a price history day) stay as they are."""
    for schema in ("main", "catalog"):
        for table in _tables(conn, schema):
            for column in conn.execute(f'PRAGMA {schema}.table_info("{table}")').fetchall():
                c = f'"{column[1]}"'
                conn.execute(f'UPDATE {schema}."{table}" SET {c} = strftime(?, {c}) WHERE typeof({c}) = \'text\' '
                             f"AND {c} GLOB ? AND {c} NOT GLOB ? AND strftime(?, {c}) IS NOT NULL",
                             (clock.FORMAT, _OLD_TIME, _STAMP, clock.FORMAT))
    for schema, table, column in _UNIX_TIMES:
        if table not in _tables(conn, schema):
            continue
        conn.execute(f"UPDATE {schema}.{table} SET {column} = strftime(?, CAST({column} AS REAL), 'unixepoch') "
                     f"WHERE typeof({column}) IN ('integer', 'real') OR ({column} GLOB '[0-9]*' "
                     f"AND {column} NOT GLOB '*[^0-9.]*')", (clock.FORMAT,))
    conn.execute("UPDATE app_meta SET value = strftime(?, CAST(value AS REAL), 'unixepoch') "
                 "WHERE key = 'scanner_seen' AND value GLOB '[0-9]*' AND value NOT GLOB '*-*'", (clock.FORMAT,))


def _step_16(conn) -> None:
    """Speed (round 21):
    - An index on the stash by card and printing, the way prices and stash pages look cards up.
    - A followed Pokémon's card that is stored in cards reads its prices from prices only: its second copy goes."""
    conn.execute("CREATE INDEX IF NOT EXISTS inventory_by_card ON inventory(card_id, variant)")
    conn.execute("UPDATE pokemon_cards SET prices_json = NULL WHERE card_id IN (SELECT id FROM cards)")


def _step_17(conn) -> None:
    """One job system (round 23): the scanner's worker_jobs become rows of jobs (run in the scanner). A job that
    was waiting or running stopped with the restart, as every job does now."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'worker_jobs'").fetchone():
        return
    conn.execute("""INSERT INTO jobs (id, key, label, kind, arg, runs_in, priority, status, done, total, step, result,
                                      error, created_at, finished_at)
                    SELECT id, key, label, kind, arg, 'scanner', 0,
                           CASE WHEN status IN ('waiting', 'running') THEN 'stopped' ELSE status END, done, total, step,
                           result, CASE WHEN status IN ('waiting', 'running') THEN 'Stopped by a restart' ELSE error END,
                           created_at, COALESCE(finished_at, CASE WHEN status IN ('waiting', 'running')
                                                                   THEN strftime(?, 'now') END)
                    FROM worker_jobs""", (clock.FORMAT,))
    conn.execute("DROP TABLE worker_jobs")


def _step_18(conn) -> None:
    """A deleted copy leaves its binders (round 26). SQLite can give a deleted copy's id to the next copy added,
    so a binder slot left behind would pass to that new card. (schema.sql has the same trigger for new installs.)"""
    conn.execute("CREATE TRIGGER IF NOT EXISTS inventory_gone AFTER DELETE ON inventory BEGIN "
                 "DELETE FROM binder_copies WHERE inventory_id = OLD.id; END")
    conn.execute("DELETE FROM binder_copies WHERE inventory_id NOT IN (SELECT id FROM inventory)")


SCAN_MATCHES = """CREATE TABLE IF NOT EXISTS scan_matches (
    scan_id  INTEGER NOT NULL,
    card_id  TEXT NOT NULL,
    PRIMARY KEY (scan_id, card_id)
)"""


def _step_19(conn) -> None:
    """Prices in scan review before Add (round 27): a matched photo's card is kept, like a chased card, while the
    photo waits to be added. And each set's English name (Japanese and Chinese sets), worked out from the
    set lists already downloaded."""
    conn.execute(SCAN_MATCHES)
    _add_columns(conn, ("sets", "english_name", "TEXT"))


def _time_defaults(conn) -> None:
    """A table made before 0.19 keeps its old default time (datetime('now'): a space, no Z), as SQLite can't change
    a default. A trigger rewrites a time filled in that way as it's saved."""
    for table in _tables(conn, "main"):
        for column in conn.execute(f'PRAGMA main.table_info("{table}")').fetchall():
            if "datetime('now')" not in (column[4] or ""):
                continue
            name = column[1]
            conn.execute(f'CREATE TRIGGER IF NOT EXISTS "{table}_{name}_stamp" AFTER INSERT ON "{table}" '
                         f"WHEN NEW.{name} NOT GLOB '{_STAMP}' AND strftime('{clock.FORMAT}', NEW.{name}) IS NOT NULL BEGIN "
                         f"UPDATE \"{table}\" SET {name} = strftime('{clock.FORMAT}', NEW.{name}) WHERE rowid = NEW.rowid; END")


STEPS: list = [(1, _step_1), (2, _step_2), (3, _step_3), (4, _step_4), (5, _step_5), (6, _step_6), (7, _step_7),
               (8, _step_8), (9, _step_9), (10, _step_10), (11, _step_11), (12, _step_12), (13, _step_13),
               (14, _step_14), (15, _step_15), (16, _step_16), (17, _step_17), (18, _step_18), (19, _step_19)]
LATEST = 19


def init(conn: sqlite3.Connection) -> None:
    from . import english
    english.changed()  # set names shown are read from this database again
    have = version(conn)
    if have > LATEST:
        raise NewerDatabase(f"This database was last used by a newer version of the app (database step {have}, "
                            f"this version knows {LATEST}). Install that version again, or restore a backup "
                            "made before it.")
    conn.executescript(SCHEMA.read_text())
    conn.executescript(CATALOG_SCHEMA.read_text())
    moved = None
    for number, step in STEPS:
        if number <= have:
            continue
        with transaction(conn):
            done = step(conn)
            conn.execute(f"PRAGMA user_version = {number}")
        if number == 8:
            moved = done
        have = number
    _time_defaults(conn)
    conn.commit()
    # A database that has more pages free after its downloads moved out gives them back (backups copy them all).
    if moved:
        try:
            conn.execute("VACUUM main")
        except sqlite3.OperationalError:  # another connection is mid-read: it's smaller next time it's tidied
            pass


def rebuild_catalog(conn) -> None:
    """Data sources › Rebuild downloaded data: empty the catalog and forget when it was last fetched, so the next
    price update fetches it all again. The colours worked out from the sprites aren't downloads, so they stay."""
    with transaction(conn):
        for table in CATALOG_TABLES:
            if table != "pokemon_palette":
                conn.execute(f"DELETE FROM catalog.{table}")
        if _main_columns(conn, "catalog", "index_names"):  # the card catalog's names (card_index.make_names)
            conn.execute("DELETE FROM catalog.index_names")
        conn.execute("DELETE FROM app_meta WHERE key IN ('tcgcsv_groups_at', 'cardtrader_expansions_at')")


# Columns nothing reads any more. SQLite 3.35+ can drop a column.
UNUSED_COLUMNS = (("prices", "source_updated"), ("wants", "max_price"))


def _drop_unused(conn) -> None:
    for table, column in UNUSED_COLUMNS:
        if any(c[1] == column for c in conn.execute(f"PRAGMA table_info({table})")):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")  # inside step 1's transaction


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring databases made by older versions up to the current schema."""
    set_columns = {r["name"] for r in conn.execute("PRAGMA table_info(sets)")}
    if "language" not in set_columns:
        conn.execute("ALTER TABLE sets ADD COLUMN language TEXT NOT NULL DEFAULT 'en'")
    card_columns = {r["name"] for r in conn.execute("PRAGMA table_info(cards)")}
    if "pricing_json" not in card_columns:
        conn.execute("ALTER TABLE cards ADD COLUMN pricing_json TEXT")
    inventory_columns = {r["name"] for r in conn.execute("PRAGMA table_info(inventory)")}
    if "note" not in inventory_columns:
        conn.execute("ALTER TABLE inventory ADD COLUMN note TEXT")
    for table, column, kind in (("users", "collection_id", "INTEGER"), ("users", "session_key", "TEXT"),
                                ("purchases", "created_by", "INTEGER"), ("wants", "added_by", "INTEGER"),
                                ("inventory", "origin", "TEXT"), ("inventory_log", "origin", "TEXT"),
                                ("cards", "hand_added_by", "INTEGER"),
                                ("scan_batches", "language", "TEXT NOT NULL DEFAULT 'en'"),
                                ("scans", "rev", "INTEGER NOT NULL DEFAULT 0"), ("scans", "picture_json", "TEXT"),
                                ("scans", "paid", "REAL"), ("scans", "prev_status", "TEXT"),
                                ("users", "mode", "TEXT NOT NULL DEFAULT 'dark'"),
                                ("cards", "has_star", "INTEGER NOT NULL DEFAULT 0"),
                                ("cards", "has_star2", "INTEGER NOT NULL DEFAULT 0"),
                                ("cards", "source", "TEXT"), ("cards", "twin_code", "TEXT"),
                                ("cards", "twin_number", "TEXT"), ("cards", "twin_name", "TEXT"),
                                ("prices", "basis", "TEXT"), ("cards", "types", "TEXT"),
                                ("purchases", "kind", "TEXT NOT NULL DEFAULT 'packs'"),
                                # Card pictures (card_pictures.py) and the PriceCharting link's picture
                                ("cards", "tcgplayer_id", "INTEGER"), ("cards", "saved_picture", "TEXT"),
                                ("cards", "saved_from", "TEXT"), ("cards", "saved_src", "TEXT"),
                                ("cards", "picture_checked_at", "TEXT"), ("cards", "twin_picture", "TEXT"),
                                ("cards", "twin_label", "TEXT"), ("card_extras", "pc_image", "TEXT"),
                                # One batch screen (checked in the card view, per-card binder, Cancel's snapshot,
                                # and the batch's From · Binder choice)
                                ("scans", "checked", "INTEGER NOT NULL DEFAULT 0"),
                                ("scans", "in_binder", "INTEGER NOT NULL DEFAULT 1"), ("scans", "undo_json", "TEXT"),
                                ("scan_batches", "came", "TEXT"), ("scan_batches", "purchase_id", "INTEGER"),
                                ("scan_batches", "new_label", "TEXT"), ("scan_batches", "new_cost", "REAL"),
                                ("scan_batches", "binder", "TEXT"), ("scan_batches", "new_binder", "TEXT"),
                                # The picture of a link found for you (finder.py); guess = shown after twins
                                ("cards", "found_image", "TEXT"), ("cards", "found_image_guess", "INTEGER"),
                                # An English name typed once on Links to confirm, for link searches
                                ("cards", "english_name", "TEXT"),
                                # The price range (TCGplayer low and mid) and Cardmarket's price in euros
                                ("prices", "low", "REAL"), ("prices", "mid", "REAL"),
                                ("cards", "cardmarket_eur", "REAL"), ("cards", "cardmarket_at", "TEXT"),
                                ("card_extras", "pc_price", "REAL"), ("card_extras", "pc_checked_at", "TEXT"),
                                # Which feed a history price came from ('tcgcsv'), so the switch isn't a move
                                ("price_snapshots", "basis", "TEXT"),
                                # When a guessed link was tapped, so "Was this the right card?" asks
                                ("found_links", "visited_at", "TEXT"),
                                # The fingerprint Enhanced Search made of a scan's photo (picture_learned)
                                ("scans", "picture_vector", "BLOB"),
                                # The phone's id for a photo, so a resent photo never makes a second scan
                                ("scans", "client_id", "TEXT"), ("scan_batches", "name", "TEXT"),
                                # Share my binder (shared.py)
                                ("binders", "share_token", "TEXT"), ("binders", "share_on", "INTEGER NOT NULL DEFAULT 0"),
                                ("binders", "shared_at", "TEXT"), ("binder_codes", "code_tag", "TEXT")):
        _add_columns(conn, (table, column, kind))
    # History rows written by TCGCSV runs before the basis column existed (from its first run on).
    first_tcgcsv = conn.execute("SELECT value FROM app_meta WHERE key = 'tcgcsv_groups_at'").fetchone()
    if first_tcgcsv and not conn.execute("SELECT 1 FROM app_meta WHERE key = 'tcgcsv_basis_marked'").fetchone():
        conn.execute("""UPDATE price_snapshots SET basis = 'tcgcsv' WHERE source = 'tcgplayer' AND basis IS NULL
                        AND captured_at >= ? AND EXISTS (SELECT 1 FROM tcgcsv_prices t WHERE t.card_id = price_snapshots.card_id
                        AND t.variant = price_snapshots.variant)""", (first_tcgcsv[0],))
        conn.execute("INSERT INTO app_meta (key, value) VALUES ('tcgcsv_basis_marked', '1')")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS binders_share_token ON binders(share_token)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS scans_client_id ON scans(batch_id, client_id) WHERE client_id IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS binder_codes_tag ON binder_codes(binder_id, code_tag)")
    # Price updates are logged in price_runs now; start the log from the history we already have.
    if conn.execute("SELECT 1 FROM price_runs LIMIT 1").fetchone() is None:
        conn.execute("INSERT OR IGNORE INTO price_runs (ran_at) SELECT DISTINCT captured_at FROM price_snapshots")
    # Every new or changed scan gets the next rev, so the batch page can ask for just what changed.
    # One statement each (executescript would commit step 1's transaction half way).
    conn.execute("""CREATE TRIGGER IF NOT EXISTS scans_rev_insert AFTER INSERT ON scans BEGIN
                        UPDATE scans SET rev = (SELECT MAX(rev) FROM scans) + 1 WHERE id = NEW.id;
                    END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS scans_rev_update AFTER UPDATE ON scans WHEN NEW.rev = OLD.rev BEGIN
                        UPDATE scans SET rev = (SELECT MAX(rev) FROM scans) + 1 WHERE id = NEW.id;
                    END""")
    # Accounts from before sharing: everyone shares the first account's collection.
    first = conn.execute("SELECT MIN(id) FROM users").fetchone()[0]
    if first is not None:
        conn.execute("UPDATE users SET collection_id = ? WHERE collection_id IS NULL", (first,))
    # Notes used to be one per inventory row; move them to the notes table, written by the collection's owner.
    conn.execute(
        """INSERT INTO notes (collection_id, card_id, variant, condition, author_id, text)
           SELECT user_id, card_id, variant, condition, user_id, note FROM inventory WHERE note IS NOT NULL""")
    conn.execute("UPDATE inventory SET note = NULL WHERE note IS NOT NULL")
    # Notes and chases are about the whole card (8 Oct 2026): no printing, no "most I'd pay".
    conn.execute("UPDATE notes SET variant = NULL, condition = NULL WHERE variant IS NOT NULL OR condition IS NOT NULL")
    conn.execute("DELETE FROM wants WHERE id NOT IN (SELECT MIN(id) FROM wants GROUP BY user_id, card_id)")
    conn.execute("UPDATE wants SET variant = 'any' WHERE variant <> 'any'")
    # Start the history with what's already there, as added by the collection's owner.
    if conn.execute("SELECT 1 FROM inventory_log LIMIT 1").fetchone() is None:
        conn.execute(
            """INSERT INTO inventory_log (collection_id, card_id, variant, condition, delta, user_id, source, at)
               SELECT user_id, card_id, variant, condition, quantity, user_id, 'manual',
                      COALESCE(acquired_date, datetime('now')) FROM inventory""")


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Statements that land together or not at all. On an autocommit connection (see connect) this opens
    the transaction itself; the web app's shared connection is already in one."""
    began = getattr(conn, "isolation_level", "") is None and not conn.in_transaction
    if began:
        conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
