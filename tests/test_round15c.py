"""Round 15c (0.15.2): set-up for one-container hosts like PikaPods, and the critique's fixes."""
import sys
import pytest
import re
from pathlib import Path

from app import main
from test_app import JPEG, _scan_setup, client  # noqa: F401

APP = Path(__file__).resolve().parent.parent / "app"


# --- A new scan never shows an older photo ---------------------------------------------------------------

def test_a_reused_scan_number_gets_a_new_photo_address(client, monkeypatch):
    # Deleting a batch frees its scan numbers, and the phone kept the old photo for an hour under the same address.
    _, batch_id, _, _ = _scan_setup(client, monkeypatch)

    def photo_src(batch):
        r = client.post(f"/scans/{batch}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg"))], headers={"X-Scan-Upload": "1"})
        return re.search(r'src="(/scans/photo/\d+\?v=\w+)"', r.text).group(1)  # the tile, while it's read

    first = photo_src(batch_id)
    client.post(f"/scans/{batch_id}/delete")
    batch_id = int(client.post("/scans", follow_redirects=False).headers["location"].split("?")[0].rsplit("/", 1)[1])
    second = photo_src(batch_id)
    assert first.split("?")[0] == second.split("?")[0]  # the same scan number again
    assert first != second  # but not the same address
    assert client.get(second).status_code == 200


# --- Behind PikaPods' HTTPS proxy --------------------------------------------------------------------------

def test_behind_a_proxy_on_another_domain(client):
    # A plain `docker run` (no compose file) behind a proxy at a private address, on the host's own domain.
    from fastapi.testclient import TestClient
    from app import web
    from test_app import _setup_with_set
    _setup_with_set(client)
    client.get("/logout")
    pod = TestClient(main.app, base_url="http://10.0.4.7:8000", client=("10.0.4.2", 40000))
    head = {"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https", "Host": "misty.example.org",
            "Origin": "https://misty.example.org"}
    wrong = pod.post("/login", data={"username": "misty", "password": "nope"}, headers=head)
    assert wrong.status_code == 401  # a wrong password, not refused: the same-site check passed: the form came from the proxy's domain
    assert web.lockout._failures.get(("misty", "203.0.113.9"))  # counted against the visitor, not the proxy
    assert not web.lockout._failures.get(("misty", "10.0.4.2"))
    ok = pod.post("/login", data={"username": "misty", "password": "pikachu123"}, headers=head, follow_redirects=False)
    assert ok.status_code == 303 and "secure" in ok.headers["set-cookie"].lower()
    # From an address that isn't a proxy, the forwarded headers are ignored.
    outsider = TestClient(main.app, base_url="http://10.0.4.7:8000", client=("203.0.113.50", 40000))
    outsider.post("/login", data={"username": "misty", "password": "nope"}, headers={**head, "X-Forwarded-For": "198.51.100.1"})
    assert web.lockout._failures.get(("misty", "203.0.113.50")) and not web.lockout._failures.get(("misty", "198.51.100.1"))


# --- First admin without reading logs ----------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="Linux only")
def test_setup_code_is_also_a_file_until_the_first_account(client, monkeypatch):
    from app import web
    monkeypatch.setattr(web, "_setup_code", None)
    web.SETUP_FILE.unlink(missing_ok=True)
    assert "setup-code.txt" in client.get("/setup").text
    code = web.SETUP_FILE.read_text().strip()
    assert code == web.setup_code() and oct(web.SETUP_FILE.stat().st_mode)[-3:] == "600"
    client.post("/setup", data={"code": code, "username": "misty", "password": "pikachu123"})
    assert not web.SETUP_FILE.exists()


# --- Database step 7: what removed features left behind ----------------------------------------------------

def test_step_7_drops_page_reading_leftovers_and_keeps_what_matters(tmp_path):
    from app import db
    from test_round15b import round14_db
    conn = round14_db(tmp_path / "r14.db")
    conn.execute("INSERT INTO sets (set_id, name, printed_total, total) VALUES ('ja:SV1', 'x', 9, 9)")
    for n in (1, 2):
        conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name) VALUES (?, 'ja:SV1', ?, ?, 1, 'x')",
                     (f"ja:SV1-{n}", str(n), n))
    conn.execute("INSERT INTO found_links (card_id, site, status, url, name, found_at) VALUES "
                 "('ja:SV1-1', 'english', 'confirmed', 'sv4-185', 'Iono · Paldea Evolved #185', '2026')")
    conn.execute("INSERT INTO card_extras (card_id, pricecharting_url, pc_price, pc_checked_at, updated_at) "
                 "VALUES ('ja:SV1-2', 'https://www.pricecharting.com/game/p/x', 5.0, '2026-10-01', '2026')")
    conn.executemany("INSERT INTO prices (card_id, variant, source, price, currency, updated_at) VALUES (?, ?, 'tcgplayer', 1, ?, '2026')",
                     [("ja:SV1-1", "normal", "USD-found"), ("ja:SV1-2", "normal", "USD-en"), ("ja:SV1-2", "holo", "USD-print")])
    conn.commit()
    db.init(conn)  # steps 6 to 9
    assert db.version(conn) == db.LATEST
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not tables & set(db.DEAD_TABLES)
    assert not {c for _, c in db.DEAD_COLUMNS} & db._columns(conn, "card_extras")
    assert conn.execute("SELECT english_name FROM cards WHERE id = 'ja:SV1-1'").fetchone()[0] == "Iono"
    assert conn.execute("SELECT pc_typed, pc_typed_read FROM card_extras WHERE card_id = 'ja:SV1-2'").fetchone()[:] == (5.0, 1)
    assert [r[0] for r in conn.execute("SELECT origin FROM prices")] == ["printing"]  # step 9's name for USD-print
    db.init(conn)  # a restart: schema.sql doesn't bring any of it back
    assert not {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")} & set(db.DEAD_TABLES)


def test_a_new_database_runs_every_step(tmp_path):
    from app import db
    conn = db.connect(tmp_path / "new.db")
    db.init(conn)
    assert db.version(conn) == db.LATEST


# --- Downloaded data in a file of its own (A3) -------------------------------------------------------------

def _tables(conn, schema):
    return {r[0] for r in conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table'")}


def _old_db(path):
    """A database as 15b left it: the downloaded data in the database itself, at step 7."""
    from app import db
    from test_round15b import OLD_CT_PRICES, OLD_PRICES
    conn = db.connect(path)
    db.init(conn)
    sql = "\n".join(line for line in db.CATALOG_SCHEMA.read_text().splitlines() if not line.startswith("--"))
    conn.executescript(sql.replace("catalog.", "main.") + "DROP TABLE main.price_quotes; DROP TABLE catalog.price_quotes;"
                       "DROP TABLE main.index_sets; DROP TABLE main.index_cards; DROP TABLE main.tcgcsv_product_prices;"
                       + OLD_PRICES + OLD_CT_PRICES.format(schema="main") + "PRAGMA user_version = 7;")
    return conn


def test_a_new_database_keeps_downloads_in_the_catalog_file(tmp_path):
    from app import db
    conn = db.connect(tmp_path / "dexstash.db")
    db.init(conn)
    assert (tmp_path / "dexstash-catalog.db").exists()
    assert set(db.CATALOG_TABLES) <= _tables(conn, "catalog")
    assert not set(db.CATALOG_TABLES) & _tables(conn, "main")
    assert "ct_sets" in _tables(conn, "main")  # sets you picked on Data sources are yours
    conn.execute("INSERT INTO tcgcsv_prices (card_id, variant, market, updated_at) VALUES ('a', 'normal', 1.5, '2026')")
    other = db.connect(tmp_path / "dexstash.db")  # every connection sees it by name
    assert other.execute("SELECT market FROM tcgcsv_prices").fetchone()[0] == 1.5


def test_step_8_moves_the_downloads_and_the_catalog_wins(tmp_path):
    from app import db
    conn = _old_db(tmp_path / "dexstash.db")
    conn.execute("INSERT INTO catalog.tcgcsv_prices (card_id, variant, market, updated_at) VALUES ('a', 'normal', 9, 'new')")
    conn.executemany("INSERT INTO main.tcgcsv_prices (card_id, variant, market, updated_at) VALUES (?, 'normal', ?, 'old')",
                     [("a", 1), ("b", 2)])
    conn.execute("INSERT INTO main.ct_blueprints (blueprint_id, expansion_id, number, fetched_at) VALUES (5, 1, '7', '2026')")
    conn.execute("INSERT INTO main.tcgdex_cache (url, body, fetched_at) VALUES ('u', '{}', 1)")
    conn.commit()
    db.init(conn)
    assert db.version(conn) == db.LATEST
    assert not set(db.CATALOG_TABLES) & _tables(conn, "main")
    assert dict(conn.execute("SELECT card_id, market FROM tcgcsv_prices").fetchall()) == {"a": 9, "b": 2}
    assert conn.execute("SELECT number FROM ct_blueprints WHERE blueprint_id = 5").fetchone()[0] == "7"
    assert conn.execute("SELECT COUNT(*) FROM tcgdex_cache").fetchone()[0] == 1
    db.init(conn)  # a restart doesn't make them in the database again
    assert not set(db.CATALOG_TABLES) & _tables(conn, "main")


def test_backups_hold_only_your_data_and_an_old_one_restores(tmp_path):
    import sqlite3
    from app import backup, db
    conn = db.connect(tmp_path / "dexstash.db")
    db.init(conn)
    conn.execute("INSERT INTO users (username, password_hash) VALUES ('misty', 'x')")
    conn.execute("INSERT INTO tcgdex_cache (url, body, fetched_at) VALUES ('u', '{}', 1)")
    made = backup.make(conn, tmp_path / "backups")
    saved = sqlite3.connect(made)
    assert not set(db.CATALOG_TABLES) & _tables(saved, "main")
    saved.close()
    old = _old_db(tmp_path / "old.db")  # a backup from before 15c
    old.execute("INSERT INTO users (username, password_hash) VALUES ('misty', 'x')")
    old.execute("INSERT INTO main.tcgdex_cache (url, body, fetched_at) VALUES ('v', '{}', 1)")
    old.commit()
    old_backup = backup.make(old, tmp_path / "backups", "manual")
    backup.restore(conn, tmp_path / "backups", old_backup.name)
    db.init(conn)
    assert not set(db.CATALOG_TABLES) & _tables(conn, "main")
    assert {r[0] for r in conn.execute("SELECT url FROM tcgdex_cache")} == {"u", "v"}


def test_rebuild_downloaded_data(client, monkeypatch):
    from app import db, web
    from test_app import _setup_with_set
    _setup_with_set(client)
    conn = main.conn
    conn.execute("INSERT INTO tcgcsv_prices (card_id, variant, market, updated_at) VALUES ('a', 'normal', 1, '2026')")
    conn.execute("INSERT INTO ct_expansions (id, code, name, fetched_at) VALUES (1, 'x', 'X', '2026')")
    conn.execute("INSERT INTO pokemon_palette (species_id, shiny, swatches, light, dark, extracted_at) VALUES (1, 0, '[]', '{}', '{}', '2026')")
    conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('tcgcsv_groups_at', '2026'), ('cardtrader_expansions_at', '2026')")
    conn.commit()
    started = []
    monkeypatch.setattr(web.updater, "start", lambda *a, **k: started.append(1) or True)
    assert "Rebuild downloaded data" in client.get("/account/data").text
    r = client.post("/account/data/catalog/rebuild", follow_redirects=False)
    assert r.status_code == 303 and started == [1]
    assert conn.execute("SELECT COUNT(*) FROM tcgcsv_prices").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ct_expansions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM pokemon_palette").fetchone()[0] == 1  # worked out here, not downloaded
    assert not conn.execute("SELECT 1 FROM app_meta WHERE key IN ('tcgcsv_groups_at', 'cardtrader_expansions_at')").fetchone()


def test_no_code_uses_the_dropped_tables_and_columns():
    from app import db
    # (pc_price is still the name of the PriceCharting price box on forms, typed by you, so it isn't checked)
    names = [*db.DEAD_TABLES, *(c for _, c in db.DEAD_COLUMNS if c != "pc_price"), "save_english", "USD-found", "USD-en"]
    for path in [*APP.rglob("*.py"), *APP.rglob("*.html"), *APP.rglob("*.js"), APP / "schema.sql", APP / "catalog.sql"]:
        if path.name == "db.py":  # the steps that read them before dropping them
            continue
        text = path.read_text()
        hits = [n for n in names if re.search(rf"\b{re.escape(n)}\b", text)]
        assert not hits, f"{path.relative_to(APP)}: {hits}"


# --- Scanner modes -------------------------------------------------------------------------------------------

def test_scanner_modes_and_their_old_names(caplog):
    from app import settings
    assert settings.scanner_mode({}) == "off"  # a plain `docker run`: one small container
    assert [settings.scanner_mode({"DEXSTASH_SCANNER": m}) for m in ("off", "inside", "separate", "threads")] == \
        ["off", "inside", "separate", "threads"]
    assert settings.scanner_mode({"DEXSTASH_SCANNER": "external"}) == "separate"
    assert settings.scanner_mode({"DEXSTASH_SCANNER": "inline"}) == "inside"
    assert "now called separate" in caplog.text
    assert settings.scanner_mode({"DEXSTASH_SCANNER": "sometimes"}) == "off"


def test_scanning_off_never_loads_the_reader_or_the_picture_model(tmp_path):
    import subprocess
    import sys
    code = ("import sys; import app.main; from app import web; "
            "heavy = sorted(m for m in sys.modules if m.split('.')[0] in ('onnxruntime', 'cv2', 'rapidocr', 'rapidocr_onnxruntime')); "
            "print(web.SCANNER_MODE, type(web.scan_worker).__name__, type(web.picture_encoder).__name__, heavy)")
    env = {"PATH": "/usr/bin:/bin", "DEXSTASH_DB": str(tmp_path / "x.db"), "DEXSTASH_SCANNER": "off", "DEXSTASH_TCGCSV": "0",
           "DEXSTASH_EURO_RATE": "0", "DEXSTASH_PICTURE_CHECK": "0", "HOME": str(tmp_path)}
    out = subprocess.run([sys.executable, "-c", code], cwd=APP.parent, env=env, capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().splitlines()[-1] == "off _InScanner _InScanner []", out.stderr[-2000:]


def test_scanning_off_says_so(client, monkeypatch):
    from app import web
    from test_app import _setup_with_set
    _setup_with_set(client)
    monkeypatch.setattr(web, "SCANNER_MODE", "off")
    monkeypatch.setattr(web, "SCANNER_EXTERNAL", False)
    assert web._scan_problem() == (web.SCANNING_OFF_ENV if web.SCANNER_LOCKED else web.SCANNING_OFF) and not web._pictures_ready()
    assert "Scanning is off on this server." in client.get("/account/data").text


def test_memory_limit_from_the_container(tmp_path):
    from app import scanning
    v2, v1 = tmp_path / "memory.max", tmp_path / "limit"
    assert scanning.memory_limit((v2, v1)) is None  # not in a container
    v2.write_text("max\n")
    assert scanning.memory_limit((v2, v1)) is None
    v2.write_text("1073741824\n")
    assert scanning.memory_limit((v2, v1)) == 1 << 30
    v2.unlink()
    v1.write_text("9223372036854771712\n")  # cgroup v1's "no limit"
    assert scanning.memory_limit((v2, v1)) is None


def test_inside_mode_needs_the_memory(caplog):
    from app import scanning
    child = scanning.Child(command=["true"], limit=lambda: 1 << 30)
    child.start()
    assert child.refused and "1.0 GB" in child.refused and child.process is None


@pytest.mark.skipif(sys.platform == "win32", reason="Linux only")
def test_inside_mode_child_runs_lowest_priority_restarts_and_stops_with_the_app(monkeypatch):
    import os
    import sys
    import time
    from app import scanning
    monkeypatch.setattr(scanning, "FIRST_WAIT", 0.05)
    # One that keeps stopping is started again, waiting longer each time.
    quitter = scanning.Child(command=[sys.executable, "-c", "raise SystemExit(1)"], limit=lambda: None)
    quitter.start()
    deadline = time.monotonic() + 10
    while quitter.restarts < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    quitter.stop()
    assert quitter.restarts >= 2
    # One that runs: at the lowest priority, and stopped with the app.
    child = scanning.Child(command=[sys.executable, "-c", "import time; time.sleep(60)"], limit=lambda: 4 << 30)
    child.start()
    while not child.running() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert child.running() and os.getpriority(os.PRIO_PROCESS, child.process.pid) == 19
    child.stop(timeout=5)
    assert child.process.poll() is not None and not child.running()


def test_inside_mode_reader_never_gets_the_cardtrader_token(monkeypatch, tmp_path):
    import sys
    import time
    from app import scanning
    monkeypatch.setenv("DEXSTASH_CARDTRADER_TOKEN", "test-token-not-a-real-one")
    out = tmp_path / "env.txt"
    script = f"import os; open({str(out)!r}, 'w').write(os.environ.get('DEXSTASH_CARDTRADER_TOKEN', 'none'))"
    child = scanning.Child(command=[sys.executable, "-c", script + "; import time; time.sleep(60)"], limit=lambda: None)
    child.start()
    deadline = time.monotonic() + 10
    while not (out.exists() and out.read_text()) and time.monotonic() < deadline:
        time.sleep(0.05)
    child.stop(timeout=5)
    assert out.read_text() == "none"


@pytest.mark.skipif(sys.platform == "win32", reason="Linux only")
def test_light_work_runs_at_the_lowest_priority(monkeypatch):
    import os
    import time
    from app import card_pictures, scanning
    seen = []
    monkeypatch.setattr(card_pictures, "followed_due", lambda conn: ["t1-1", "t1-2"])
    monkeypatch.setattr(card_pictures, "check_followed", lambda conn, card_id: seen.append(card_id))
    work = scanning.LightWork(every=0.05, pause=0)
    work.start()
    deadline = time.monotonic() + 5
    while len(seen) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    priority = os.getpriority(os.PRIO_PROCESS, work.native_id)
    work.stop()
    assert seen[:2] == ["t1-1", "t1-2"] and priority == 19
    assert os.getpriority(os.PRIO_PROCESS, 0) < 19  # the app's own threads keep theirs


# --- CardTrader link on the card page ---------------------------------------------------------------------------

def test_card_page_links_to_the_card_on_cardtrader(client):
    from test_app import _setup_with_set
    _setup_with_set(client)
    assert "cardtrader.com/en/cards/" not in client.get("/cards/t1-2").text  # no match, no link
    main.conn.execute("INSERT INTO price_quotes (card_id, variant, source, price, currency, basis, ref, updated_at) "
                      "VALUES ('t1-2', '', 'cardtrader', 3.5, 'USD', 'nm', 242345, '2026-10-26')")
    main.conn.commit()
    page = client.get("/cards/t1-2").text
    assert 'href="https://www.cardtrader.com/en/cards/242345"' in page


def test_version_is_0152():
    from app import __version__
    assert tuple(map(int, __version__.split("."))) >= (0, 15, 2)


def test_classic_collection_card_matches_its_first_printing_number():
    # 30th Celebration's Classic Collection on CardTrader: N is 'NVI 101' in the main expansion; ours is 30th-c 021.
    from app import cardtrader
    blueprints = [{"blueprint_id": 406682, "number": "021", "name": "Greninja ex", "tcgplayer_id": None},
                  {"blueprint_id": 406762, "number": "101", "name": "Galarian Meowth", "tcgplayer_id": None},
                  {"blueprint_id": 413213, "number": "NVI 101", "name": "N", "tcgplayer_id": 716202},
                  {"blueprint_id": 500001, "number": "XY 76", "name": "Pikachu", "tcgplayer_id": None},
                  {"blueprint_id": 500002, "number": "SM 30", "name": "Pikachu", "tcgplayer_id": None}]
    assert cardtrader._blueprint_for(blueprints, {"number": "021", "name": "N", "subset": True}) == 413213
    assert cardtrader._blueprint_for(blueprints, {"number": "021", "name": "Greninja ex", "subset": True}) == 406682
    assert cardtrader._blueprint_for(blueprints, {"number": "009", "name": "Pikachu", "subset": True}) is None  # two: unsure
    assert cardtrader._blueprint_for(blueprints, {"number": "021", "name": "N"}) == 406682  # a main-set card: number first, as before


def test_promo_numbered_with_its_set_code_matches():
    from app import cardtrader
    blueprints = [{"blueprint_id": 397592, "number": "MEP 101", "name": "Nidorina", "tcgplayer_id": None},
                  {"blueprint_id": 397593, "number": "MEP 102", "name": "Nidoking", "tcgplayer_id": None}]
    assert cardtrader._blueprint_for(blueprints, {"number": "101", "name": "Nidorina"}) == 397592
    assert cardtrader._blueprint_for(blueprints, {"number": "101", "name": "Pikachu"}) is None  # the name must agree


def test_a_card_added_by_hand_is_priced_when_its_name_agrees():
    # Nidorina MEP 101 (the 30th Celebration stamp) was added by hand before TCGdex had it.
    from app import cardtrader
    blueprints = [{"blueprint_id": 397592, "number": "101", "name": "Nidorina", "tcgplayer_id": None}]
    assert cardtrader._blueprint_for(blueprints, {"number": "101", "name": "Nidorina", "by_hand": 1}) == 397592
    assert cardtrader._blueprint_for(blueprints, {"number": "101", "name": "Nidoqueen", "by_hand": 1}) is None
    main.conn.execute("INSERT OR IGNORE INTO sets (set_id, name, printed_total, total) VALUES ('mep', 'MEP', 0, 0)")
    main.conn.execute("INSERT OR IGNORE INTO cards (id, set_id, number, sort_number, is_main_set, name, hand_added_by) "
                      "VALUES ('mep-101', 'mep', '101', 101, 1, 'Nidorina', 1)")
    main.conn.execute("INSERT OR IGNORE INTO card_extras (card_id, updated_at) VALUES ('mep-101', '2026')")
    main.conn.commit()
    try:
        kept = cardtrader._kept_by_set(main.conn)
        assert any(c["id"] == "mep-101" and c["by_hand"] for cards in kept.values() for c in cards)
    finally:
        main.conn.execute("DELETE FROM card_extras WHERE card_id = 'mep-101'")
        main.conn.execute("DELETE FROM cards WHERE id = 'mep-101'")
        main.conn.commit()
