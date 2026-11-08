"""After-upgrade tasks: work an update needs done once on an existing database, beyond changing its tables (those
are the numbered database steps, db.STEPS). Each task has a number, runs once, and is recorded in app_meta
('after_upgrade:N'). A new database never had the old ways to fix, so it starts with every task marked done.

A quick task runs before the first page, right after the database steps. A slow one (working every card's prices
out again) runs after the first page, in the background, while pages say the upgrade is finishing.

Before round 22 these were a flag each (prices_round, tcgcsv_match_round) or ran on every start (TCG Pocket sets,
English-version pictures, Enhanced Search's kept pictures). done_before reads the old flag, so an install that
already did a task doesn't do it again."""
import logging
import sqlite3
from typing import Callable, NamedTuple

from . import clock, db

log = logging.getLogger("dexstash")
KEY = "after_upgrade:"


class Task(NamedTuple):
    number: int
    name: str
    run: Callable[[sqlite3.Connection], object]
    slow: bool = False
    done_before: Callable[[sqlite3.Connection], bool] = lambda conn: False


def _meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _prices_round(conn) -> tuple[int, ...]:
    was = _meta(conn, "prices_round") or ""
    return tuple(int(x) for x in was.split(".")) if was.replace(".", "").isdigit() else (0,)


def prices_again(conn: sqlite3.Connection) -> None:
    """0.20: every card's prices worked out again with the new rules. A database from before 0.19.2 also loses its
    Cardmarket history and the averages it was counted in."""
    from . import prices
    with db.transaction(conn):
        if _prices_round(conn) < (19, 2):
            conn.execute("DELETE FROM price_snapshots WHERE source IN ('cardmarket', 'average')")
        prices.apply_all(conn)


def products_again(conn: sqlite3.Connection) -> None:
    """0.20.2: TCGplayer products matched differently, so every set's are read at the next price update."""
    conn.execute("UPDATE tcgcsv_sets SET products_at = NULL")


def no_pocket(conn: sqlite3.Connection) -> None:
    from . import catalog
    catalog.drop_pocket(conn)  # TCG Pocket sets are digital only


def no_english_pictures(conn: sqlite3.Connection) -> None:
    from . import card_pictures
    card_pictures.drop_english(conn)  # no saved copies from a card's English version


def no_kept_images(conn: sqlite3.Connection) -> None:
    from . import pictures
    pictures.drop_kept_images()  # Enhanced Search keeps fingerprints only


def chase_languages(conn: sqlite3.Connection) -> None:
    """Pokémon sets keep English, Japanese and Chinese only (a check that ran at every start before round 22)."""
    from . import chase
    for dex in chase.purge_languages(conn):
        chase.regroup(conn, dex, chase.fingerprints(conn, dex, None))


def english_names(conn: sqlite3.Connection) -> None:
    """0.27: Japanese and Chinese sets' English names, from the set lists already downloaded."""
    from . import english
    english.refresh(conn)


def tcgplayer_matches(conn: sqlite3.Connection) -> None:
    """0.28: TCGplayer sets matched with the new rules (a set code with its zeros, the name over a printed code).
    A set matched to another TCGplayer set loses the prices the old one gave its cards, and those cards' prices are
    worked out again; every set's products are read again at the next price update, checked against TCGdex's
    product ids."""
    from . import prices, tcgcsv
    if not conn.execute("SELECT 1 FROM tcgcsv_groups LIMIT 1").fetchone():
        return
    changed = tcgcsv.match_sets(conn)["changed"]
    conn.execute("UPDATE tcgcsv_sets SET products_at = NULL")
    conn.commit()
    if changed:
        marks = ",".join("?" * len(changed))
        with db.transaction(conn):
            for (card_id,) in conn.execute(f"SELECT id FROM cards WHERE set_id IN ({marks}) AND id IN ({prices.KEPT_SQL})",
                                           changed).fetchall():
                prices.apply_tcgcsv(conn, card_id)


def move_keys(conn: sqlite3.Connection) -> None:
    """0.28: a CardTrader token in .env moves to Data sources › Prices › API keys (keys.py)."""
    from . import keys
    keys.move_env_tokens()


TASKS: list[Task] = [
    Task(1, "Remove TCG Pocket sets", no_pocket),
    Task(2, "Remove pictures saved from English versions", no_english_pictures),
    Task(3, "Work every card's prices out again", prices_again, slow=True,
         done_before=lambda conn: _prices_round(conn) >= (20, 0)),
    Task(4, "Read every set's TCGplayer products again", products_again,
         done_before=lambda conn: _meta(conn, "tcgcsv_match_round") == "20.2"),
    Task(5, "Remove card pictures earlier versions kept for Enhanced Search", no_kept_images),
    Task(6, "Keep English, Japanese and Chinese only in Pokémon sets", chase_languages, slow=True),
    Task(7, "Find English names for Japanese and Chinese sets", english_names),
    Task(8, "Match sets to TCGplayer again", tcgplayer_matches),
    Task(9, "Move the CardTrader token from .env into the app", move_keys),
]


def _done(conn, task: Task) -> bool:
    return _meta(conn, f"{KEY}{task.number}") is not None


def _mark(conn, task: Task) -> None:
    conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)", (f"{KEY}{task.number}", clock.now()))
    conn.commit()


def mark_all(conn: sqlite3.Connection) -> None:
    """A new database: nothing to catch up on."""
    for task in TASKS:
        _mark(conn, task)


def pending(conn: sqlite3.Connection, slow: bool | None = None) -> list[Task]:
    """Tasks not done yet (only slow or only quick ones when asked). One an install did the old way is marked done."""
    out = []
    for task in TASKS:
        if _done(conn, task):
            continue
        if task.done_before(conn):
            _mark(conn, task)
            continue
        if slow is None or task.slow == slow:
            out.append(task)
    return out


def run(conn: sqlite3.Connection, slow: bool) -> list[str]:
    """Run the quick (or slow) tasks not done yet, in order. A task that fails is logged and tried again at the next
    start; a quick one also stops the start (the caller shows it). Returns the names of those done."""
    ran = []
    for task in pending(conn, slow=slow):
        task.run(conn)
        conn.commit()
        _mark(conn, task)
        log.info("after-upgrade task %d done: %s", task.number, task.name)
        ran.append(task.name)
    return ran
