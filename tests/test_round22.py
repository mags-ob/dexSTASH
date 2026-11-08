"""Round 22 (0.22.0): startup in three phases, after-upgrade tasks, the upgrade failure page, the schema check."""
import ast
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, main, startup, upgrades, web
from test_app import _setup_with_set, client  # noqa: F401

APP = Path(__file__).resolve().parent.parent / "app"
OLD = Path(__file__).resolve().parent / "fixtures" / "db-0.11.sql"  # the tables 0.11 (round 11) made, no data


# --- B4: a new database and an upgraded one are the same --------------------------------------------------------

_NOW = ("datetime('now')", "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")
# Known and harmless: SQLite can't change a column's type, so these keep the type an old version gave them.
KNOWN: set = set()  # (round 22's worker_jobs times are gone with the table, round 23)


def _shape(conn) -> dict:
    out = {}
    for schema in ("main", "catalog"):
        triggers = {r[0] for r in conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type = 'trigger'")}
        for name, kind, table in conn.execute(f"SELECT name, type, tbl_name FROM {schema}.sqlite_master "
                                              "WHERE name NOT LIKE 'sqlite_%'").fetchall():
            if kind == "table":
                for c in conn.execute(f'PRAGMA {schema}.table_info("{name}")'):
                    default = c[4]
                    if default in _NOW:  # an old table's default, rewritten as it's saved by a trigger
                        assert default == _NOW[1] or f"{name}_{c[1]}_stamp" in triggers, (name, c[1])
                        default = "now"
                    kind_of = "?" if (schema, name, c[1]) in KNOWN else c[2].upper()
                    out[(schema, "column", name, c[1])] = (kind_of, c[3], default, c[5])
            elif kind == "index":
                out[(schema, "index", name)] = (table, tuple(r[2] for r in conn.execute(f'PRAGMA {schema}.index_info("{name}")')))
            elif kind == "trigger" and not name.endswith("_stamp"):
                out[(schema, "trigger", name)] = table
    return out


def test_an_upgraded_database_matches_a_new_one(tmp_path):
    new = db.connect(tmp_path / "new.db")
    db.init(new)
    old = db.connect(tmp_path / "old.db")
    old.executescript(OLD.read_text())
    assert db.version(old) == 0
    db.init(old)
    assert db.version(old) == db.LATEST
    a, b = _shape(new), _shape(old)
    assert {k: v for k, v in a.items() if b.get(k) != v} == {}
    assert {k: v for k, v in b.items() if a.get(k) != v} == {}


def test_step_1_is_one_transaction(tmp_path, monkeypatch):
    """A failure late in step 1 leaves an old database exactly as it was."""
    conn = db.connect(tmp_path / "old.db")
    conn.executescript(OLD.read_text())
    before = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    real = db._migrate

    def fails_late(c):
        real(c)
        raise RuntimeError("stop")
    monkeypatch.setattr(db, "_migrate", fails_late)
    with pytest.raises(RuntimeError):
        db.init(conn)
    assert db.version(conn) == 0
    # schema.sql's own new tables may stay (each start makes them); nothing that was there changed.
    after = {r[0] for r in conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()}
    assert {r[0] for r in before} <= after
    src = Path(db.__file__).read_text()
    step_1 = src[src.index("def _migrate"):src.index("def transaction")]
    assert "conn.executescript(" not in step_1 and "commit()" not in src[src.index("def _drop_unused"):src.index("def _migrate")]


# --- B1/E7: importing the app sets nothing up -------------------------------------------------------------------

def test_web_does_nothing_with_the_database_at_import():
    """Module-level statements of web.py and the pages: no database steps, tidies, first account or jobs."""
    for path in [APP / "web.py", *sorted((APP / "routes").glob("*.py"))]:
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
                continue
            text = ast.unparse(node)
            for word in ("db.init", "conn.execute", "prices.prune", "drop_pocket", "drop_english", "_first_account()",
                         "prices_round", "drop_chinese_batches", "scans.resume", "background.start", "backup.before_upgrade"):
                assert word not in text, f"{path.name}: {text[:80]}"


def test_startup_runs_the_phases_and_logs_how_long(tmp_path, monkeypatch, caplog):
    conn = db.ThreadConnection(tmp_path / "s.db")
    monkeypatch.setattr(web, "_first_account", lambda: None)
    monkeypatch.setattr(web, "conn", conn)
    caplog.set_level("INFO", logger="dexstash")
    startup.before_first_page(conn, tmp_path / "backups")
    assert db.version(conn) == db.LATEST
    assert all(conn.execute("SELECT 1 FROM app_meta WHERE key = ?", (f"{upgrades.KEY}{t.number}",)).fetchone()
               for t in upgrades.TASKS)  # a new database has nothing to catch up on
    assert "startup: before the first page" in caplog.text
    conn.close()


# --- after-upgrade tasks --------------------------------------------------------------------------------------

def test_after_upgrade_tasks_run_once_in_order_and_respect_the_old_flags(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "u.db")
    db.init(conn)
    ran = []
    monkeypatch.setattr(upgrades, "TASKS", [
        upgrades.Task(1, "One", lambda c: ran.append(1)),
        upgrades.Task(2, "Two", lambda c: ran.append(2), slow=True),
        upgrades.Task(3, "Three", lambda c: ran.append(3), done_before=lambda c: True)])
    assert upgrades.run(conn, slow=False) == ["One"] and ran == [1]
    assert upgrades.run(conn, slow=True) == ["Two"] and ran == [1, 2]
    assert upgrades.run(conn, slow=False) == [] and upgrades.run(conn, slow=True) == [] and ran == [1, 2]
    assert conn.execute("SELECT 1 FROM app_meta WHERE key = 'after_upgrade:3'").fetchone()  # done the old way


def test_old_flags_count_as_done():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.executemany("INSERT INTO app_meta VALUES (?, ?)", [("prices_round", "20.0"), ("tcgcsv_match_round", "20.2")])
    by_number = {t.number: t for t in upgrades.TASKS}
    assert by_number[3].done_before(conn) and by_number[4].done_before(conn)
    assert not by_number[1].done_before(conn)  # every-start tidies before round 22 run once more


def test_a_slow_task_shows_finishing_and_runs_after_the_first_page(client, monkeypatch):
    _setup_with_set(client)
    seen = []

    def slow(c):
        seen.append(web.templates.env.globals["upgrade_finishing"]())
    monkeypatch.setattr(upgrades, "TASKS", [upgrades.Task(99, "Slow one", slow, slow=True)])
    main.conn.execute("DELETE FROM app_meta WHERE key = 'after_upgrade:99'")
    main.conn.commit()
    startup.after_first_page(main.conn).join(30)
    assert seen == [True] and startup.STATE["finishing"] is False
    assert main.conn.execute("SELECT 1 FROM app_meta WHERE key = 'after_upgrade:99'").fetchone()
    assert "Finishing the upgrade" not in client.get("/").text
    monkeypatch.setitem(startup.STATE, "finishing", True)
    assert "Finishing the upgrade" in client.get("/").text
    main.conn.execute("DELETE FROM app_meta WHERE key = 'after_upgrade:99'")
    main.conn.commit()


# --- D1: the failure page --------------------------------------------------------------------------------------

def test_a_failed_upgrade_shows_a_page_with_the_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(startup, "before_first_page", lambda conn, backups: (_ for _ in ()).throw(
        db.NewerDatabase("This database was last used by a newer version of the app")))
    monkeypatch.setitem(startup.STATE, "backup", tmp_path / "dexstash-20261030-020000-upgrade.db")
    try:
        with TestClient(main.app) as c:
            page = c.get("/collection")
            assert page.status_code == 503 and "The upgrade didn't finish" in page.text
            assert "newer version of the app" in page.text and "dexstash-20261030-020000-upgrade.db" in page.text
            assert c.get("/healthz").status_code == 503  # the scanner keeps waiting
    finally:
        startup.STATE["failed"] = None
