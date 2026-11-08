"""Round 12 (21 Oct 2026)."""
import sys
import sqlite3

import pytest

from app import db, main
from app import web
from tests.test_app import client  # noqa: F401  (the signed-in test app)


# --- 6: numbered database steps --------------------------------------------------------------------

def test_a_new_database_starts_at_the_latest_step(tmp_path):
    conn = db.connect(tmp_path / "new.db")
    db.init(conn)
    assert db.version(conn) == db.LATEST
    db.init(conn)  # running again changes nothing
    assert db.version(conn) == db.LATEST


def test_an_older_database_runs_the_steps_it_has_not_had(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "old.db")
    db.init(conn)
    conn.execute("PRAGMA user_version = 0")  # as the reliability round left it
    ran = []
    monkeypatch.setattr(db, "STEPS", [(1, lambda c: ran.append(1)), (2, lambda c: ran.append(2))])
    monkeypatch.setattr(db, "LATEST", 2)
    db.init(conn)
    assert ran == [1, 2] and db.version(conn) == 2
    db.init(conn)
    assert ran == [1, 2]  # nothing runs twice


def test_a_failed_step_leaves_the_database_at_the_step_before(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "fail.db")
    db.init(conn)

    def bad(c):
        c.execute("INSERT INTO app_meta (key, value) VALUES ('half', '1')")
        raise RuntimeError("stop")
    monkeypatch.setattr(db, "STEPS", db.STEPS + [(db.LATEST + 1, bad)])
    monkeypatch.setattr(db, "LATEST", db.LATEST + 1)
    with pytest.raises(RuntimeError):
        db.init(conn)
    assert db.version(conn) == db.LATEST - 1
    assert conn.execute("SELECT 1 FROM app_meta WHERE key = 'half'").fetchone() is None


def test_a_database_from_newer_code_is_refused(tmp_path):
    conn = db.connect(tmp_path / "newer.db")
    db.init(conn)
    conn.execute(f"PRAGMA user_version = {db.LATEST + 1}")
    with pytest.raises(db.NewerDatabase, match="newer version of the app"):
        db.init(conn)
    from app import backup
    conn.close()
    with pytest.raises(ValueError, match="newer version"):
        backup.check(tmp_path / "newer.db")


# --- 1: each photo's own language --------------------------------------------------------------------

from app import scans  # noqa: E402
from tests.test_app import (FARIGIRAF_ZH, GEM_PACK_ZH, IVYSAUR, JPEG, LOPUNNY_JA, _scan_setup)  # noqa: E402


def _lines(boxes):
    return [b.text for b in boxes]


def test_each_photo_language_is_worked_out_from_its_text():
    assert scans.detect_language(_lines(IVYSAUR)) == ("en", True)
    assert scans.detect_language(_lines(LOPUNNY_JA)) == ("ja", True)
    assert scans.detect_language(_lines(FARIGIRAF_ZH)) == ("zh-cn", True)  # simplified characters
    assert scans.detect_language(_lines(GEM_PACK_ZH)) == ("zh-cn", True)
    assert scans.detect_language(["奇麒麟", "寶可夢", "1階進化"]) == ("zh-tw", True)  # traditional characters
    assert scans.detect_language(["CSV8C 209/207", "皮卡丘"]) == ("zh-cn", True)  # a mainland set code
    assert scans.detect_language(["피카츄", "HP 60"]) == ("ko", True)
    assert scans.detect_language(["皮卡丘"]) == ("zh", False)  # Chinese, but which?
    assert scans.detect_language(["060/078", "HP"]) == (None, False)


def test_an_unsure_photo_takes_the_batch_usual_language(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    batch = scans.get_batch(main.conn, owner, batch_id)
    first = scans.add_photo(main.conn, batch, owner, JPEG)
    blank = {"id": 999999, "batch_id": batch_id, "language": None}
    assert scans._languages_to_try(main.conn, blank, ["060/078"]) == (["en"], False)  # nothing to go on: English
    main.conn.execute("UPDATE scans SET language = 'ja', language_sure = 1 WHERE id = ?", (first,))
    assert scans._languages_to_try(main.conn, blank, ["060/078"]) == (["ja", "zh-tw", "en"], False)  # Japanese, or a Taiwan twin, then English (round 26)
    assert scans._languages_to_try(main.conn, blank, ["皮卡丘"]) == (["zh-cn", "zh-tw", "ja", "en"], False)  # then the usual, then English
    picked = dict(blank, language="ko", language_sure=1)
    assert scans._languages_to_try(main.conn, picked, _lines(LOPUNNY_JA)) == (["ko"], True)  # the tag wins


def test_a_new_batch_has_one_button_and_rows_get_a_language_tag(client, monkeypatch):
    owner, batch_id, worker, reader = _scan_setup(client, monkeypatch)
    page = client.get("/scans").text
    assert "lang_chips" not in page and 'name="lang"' not in page and "tap the photo's language tag" in page
    assert scans.get_batch(main.conn, owner, batch_id)["language"] == "auto"
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg"))])
    worker.start(wait=True)
    [scan_id] = [r[0] for r in main.conn.execute("SELECT id FROM scans WHERE batch_id = ?", (batch_id,))]
    scan = scans.get_scan(main.conn, owner, scan_id)
    assert (scan["language"], scan["language_sure"], scan["status"]) == ("en", 1, "ready")
    page = client.get(f"/scans/{batch_id}").text
    assert 'class="bt-lang"' in page and '<option value="en" selected>EN</option>' in page and "Scanning cards" in page
    # Picking another language looks the card up again in it.
    r = client.post(f"/scans/item/{scan_id}/language", data={"lang": "ja"}, headers={"HX-Request": "true"})
    assert r.status_code == 200
    worker.start(wait=True)  # read again, in Japanese this time
    scan = scans.get_scan(main.conn, owner, scan_id)
    assert (scan["language"], scan["language_sure"]) == ("ja", 1) and scan["status"] in ("ready", "nomatch")
    assert client.post(f"/scans/item/{scan_id}/language", data={"lang": "xx"}).status_code in (200, 303)
    assert scans.get_scan(main.conn, owner, scan_id)["language"] == "ja"  # an unknown language is ignored


def test_an_unsure_tag_shows_a_question_mark(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch, boxes=scans_boxes_unsure())
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg"))])
    worker.start(wait=True)
    page = client.get(f"/scans/{batch_id}").text
    assert 'class="bt-lang unsure"' in page and ">EN ?</option>" in page


def scans_boxes_unsure():
    from tests.test_app import _boxes
    return _boxes([[300, 1150, 30, "002/002"]])


def test_old_batches_rows_keep_their_batch_language(tmp_path):
    conn = db.connect(tmp_path / "old.db")
    db.init(conn)
    conn.execute("INSERT INTO users (username, password_hash, is_admin) VALUES ('misty', 'x', 1)")
    conn.execute("INSERT INTO scan_batches (id, collection_id, created_by, language) VALUES (1, 1, 1, 'ja')")
    conn.execute("INSERT INTO scans (batch_id, collection_id, status) VALUES (1, 1, 'ready')")
    conn.execute("UPDATE scans SET language = NULL")
    conn.execute("PRAGMA user_version = 1")  # from the reliability round
    db.init(conn)
    assert tuple(conn.execute("SELECT language, language_sure FROM scans").fetchone()) == ("ja", 1)


def test_a_mixed_batch_looks_each_card_up_in_its_own_language(client, monkeypatch, tmp_path):
    asked = []

    class Lookup:
        def codes(self, lang):
            asked.append(lang)
            return None if lang == "en" else {}

        def sets(self, lang):
            return []

        def cards_of(self, set_id, lang):
            return []

        def search_name(self, name, lang):
            return [{"id": f"{lang}-1", "set_id": lang, "number": "1", "name": name, "image": None}]

    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    batch = scans.get_batch(main.conn, owner, batch_id)
    for boxes in (IVYSAUR, LOPUNNY_JA, FARIGIRAF_ZH):
        scan_id = scans.add_photo(main.conn, batch, owner, JPEG)
        scan = scans.get_scan(main.conn, owner, scan_id)

        class Reader:
            def read(self, path, boxes=boxes):
                return boxes
        scans.read_scan(main.conn, scan, Reader(), Lookup())
    assert [r[0] for r in main.conn.execute("SELECT language FROM scans WHERE batch_id = ? ORDER BY id", (batch_id,))] \
        == ["en", "ja", "zh-cn"]
    assert asked == ["en", "ja", "zh-cn"]



# --- 3: set Value sort and the price each tile shows ------------------------------------------------

def test_set_value_sort_shows_the_price_it_sorted_by():
    from app import display
    cards = [{"number": "1", "name": "A", "price": None, "value": None},   # no price anywhere
             {"number": "2", "name": "B", "price": None, "value": 80.0},   # not yours: the set's price list
             {"number": "3", "name": "C", "price": 5.0, "value": 9.0},     # yours: its own price
             {"number": "4", "name": "D", "price": None, "value": None}]
    view = display.set_grid_view(cards, "", "value")
    assert [c["number"] for c in view["cards"]] == ["2", "3", "1", "4"]  # dearest first, unpriced last in number order
    assert [c["price"] for c in view["cards"]] == [80.0, 5.0, None, None] and view["from_list"]


# --- 4: a whole Pokémon name finds only that Pokémon --------------------------------------------

GHOSTS = ("pokemon_species_id,local_language_id,name,genus\n"
          "92,9,Gastly,x\n92,1,ゴース,x\n92,12,鬼斯,x\n93,9,Haunter,x\n93,1,ゴースト,x\n93,12,鬼斯通,x\n"
          "94,9,Gengar,x\n94,1,ゲンガー,x\n94,12,耿鬼,x\n25,9,Pikachu,x\n151,9,Mew,x\n150,9,Mewtwo,x\n")


class _NameAPI:
    def __init__(self, names):
        self.names = names

    def list_sets(self):
        return [{"id": "s1", "name": "S"}]

    def search_cards(self, name=None, **kw):
        return [{"id": f"s1-{i}", "localId": str(i), "name": n} for i, n in enumerate(self.names, 1) if (name or "") in n]


def test_a_whole_pokemon_name_leaves_out_pokemon_whose_names_contain_it(tmp_path):
    from app import search
    from app.names import Names
    csv = tmp_path / "names.csv"
    csv.write_text(GHOSTS, encoding="utf-8")
    names = Names(csv, url="http://unused")
    find = lambda api, q: [c["name"] for c in search.find_cards(api, q, longer=names.longer_names)]  # noqa: E731
    assert find(_NameAPI(["ゴース", "ゴースト", "ロケット団のゴース"]), "ゴース") == ["ゴース", "ロケット団のゴース"]
    assert find(_NameAPI(["鬼斯", "鬼斯通"]), "鬼斯") == ["鬼斯"]
    assert find(_NameAPI(["ゴースト", "ゴーストex"]), "ゴースト") == ["ゴースト", "ゴーストex"]  # Haunter is unchanged
    assert find(_NameAPI(["ゲンガー", "メガゲンガーex"]), "ゲンガー") == ["ゲンガー", "メガゲンガーex"]
    assert find(_NameAPI(["Pikachu", "Ash's Pikachu", "Pikachu ex"]), "Pikachu") == ["Pikachu", "Ash's Pikachu", "Pikachu ex"]
    assert find(_NameAPI(["Mew", "Mewtwo ex", "Mewtwo & Mew GX"]), "Mew") == ["Mew", "Mewtwo & Mew GX"]
    assert find(_NameAPI(["Gastly", "Haunter"]), "Gas") == ["Gastly"]  # part of a name still matches loosely
    assert find(_NameAPI(["ゴース", "ゴースト"]), "ゴ") == ["ゴース", "ゴースト"]


# --- 5: top movers from any price source -----------------------------------------------------------

def _mover_setup(client):
    from test_round8 import _own
    from tests.test_round11b import _setup_with_set
    user_id = _setup_with_set(client)
    _own(client, "1", "2")
    for table in ("price_snapshots", "prices"):
        main.conn.execute(f"DELETE FROM {table}")
    return user_id


def _now(card_id, price, origin="tcgplayer", variant="normal"):
    main.conn.execute("INSERT OR REPLACE INTO prices (card_id, variant, origin, price, updated_at) "
                      "VALUES (?, ?, ?, ?, '2026-10-14')", (card_id, variant, origin, price))


def test_movers_read_the_source_the_price_comes_from(client):
    from app import value
    from test_round8 import TODAY
    from tests.test_round11b import _snap
    user_id = _mover_setup(client)
    _now("t1-1", 9.0, web.prices.CARDTRADER)  # round 15c: was a found link's price, gone since 15b
    main.conn.execute("UPDATE prices SET basis = 'nm' WHERE card_id = 't1-1'")
    _snap("t1-1", "normal", 50.0, 20)  # an old TCGplayer price isn't what CardTrader's 9.00 moved from
    _snap("t1-1", "normal", 6.0, 3, source="cardtrader")  # only 3 days of history in a 3-month range
    _now("t1-2", 4.0)
    _snap("t1-2", "normal", 4.0, 10)  # flat: not a mover
    main.conn.commit()
    m = value.movers(main.conn, user_id, "3m", today=TODAY)
    [row] = m["up"]
    assert (row["card_id"], row["then"], row["now"], row["source"]) == ("t1-1", 6.0, 9.0, "CardTrader")
    assert row["since"] and not m["down"]
    assert m["thin"]  # most history starts inside the range
    page = client.get("/value?range=3m").text
    assert "Normal &middot; CardTrader" in page and "moves fill in day by day" in page


def test_movers_leave_out_estimates_and_cardmarket(client):
    """Round 20: a card with only Cardmarket's price (gone) has no move; ≈ another printing is an estimate."""
    from app import value
    from test_round8 import TODAY
    from tests.test_round11b import _snap
    user_id = _mover_setup(client)
    _now("t1-1", 8.0, web.prices.PRINTING)  # ≈ another printing: an estimate
    _snap("t1-1", "normal", 5.0, 20, source="printing")
    main.conn.execute("UPDATE cards SET cardmarket_eur = 10 WHERE id = 't1-2'")
    _snap("t1-2", "", 8.0, 20, source="cardmarket")
    main.conn.commit()
    m = value.movers(main.conn, user_id, "1m", today=TODAY)
    assert m["up"] == [] and m["down"] == []


# --- 7 and 8: Value over time is kept; how much price history is kept --------------------------------

def test_value_chart_is_kept_until_something_changes(client, monkeypatch):
    from app import value
    from test_round8 import TODAY
    from tests.test_round11b import _snap
    user_id = _mover_setup(client)
    _now("t1-1", 5.0)
    _snap("t1-1", "normal", 4.0, 10)
    main.conn.commit()
    value.forget()
    first = value.series(main.conn, user_id, "1m", today=TODAY)
    worked = []
    real = value._series
    monkeypatch.setattr(value, "_series", lambda *a: worked.append(1) or real(*a))
    assert value.series(main.conn, user_id, "1m", today=TODAY) == first and not worked  # kept
    _snap("t1-1", "normal", 4.5, 5)  # new history
    main.conn.commit()
    value.series(main.conn, user_id, "1m", today=TODAY)
    _now("t1-2", 2.0)  # a card's price today changes the lines
    main.conn.commit()
    value.series(main.conn, user_id, "1m", today=TODAY)
    client.post("/inventory/remove", data={"card_id": "t1-2"})  # whatever happens, a changed inventory is seen
    main.conn.execute("UPDATE inventory SET quantity = quantity + 1 WHERE card_id = 't1-1'")
    main.conn.commit()
    after = value.series(main.conn, user_id, "1m", today=TODAY)
    assert len(worked) == 3 and after["points"][-1][1] == 2 * first["points"][-1][1] + 2.0


def test_weeks_and_months_keep_the_average_daily_price():
    from datetime import date
    from app import value
    # Mon 6 Jan 2025 at 10, Fri 10 Jan to 17: the week's days are 10,10,10,10,17,17,17 -> 13.0
    rows = [("2025-01-06 08:00:00", 10.0, None), ("2025-01-10 08:00:00", 17.0, None), ("2025-03-03 08:00:00", 20.0, None)]
    thin = value._thin(rows, daily_cut=date(2025, 2, 1), weekly_cut=date(2024, 1, 1))
    assert thin[0] == ("2025-01-06T00:00:00Z", 13.0, None) and thin[-1] == rows[-1]
    assert value._thin(thin, date(2025, 2, 1), date(2024, 1, 1)) == thin  # running again changes nothing
    # The switch to TCGCSV inside a week isn't blended
    rows = [("2025-01-06 08:00:00", 10.0, None), ("2025-01-09 08:00:00", 5.0, "tcgcsv"), ("2025-01-11 08:00:00", 6.0, "tcgcsv")]
    assert value._thin(rows, date(2025, 2, 1), date(2024, 1, 1)) == [
        ("2025-01-06T00:00:00Z", 10.0, None), ("2025-01-09T00:00:00Z", 5.5, "tcgcsv")]
    # January as a month: 6-9 Jan at 10 (4 days), 10-31 Jan at 17 (22 days)
    rows = [("2025-01-06 08:00:00", 10.0, None), ("2025-01-10 08:00:00", 17.0, None)]
    assert value._thin(rows, date(2025, 6, 1), date(2025, 3, 1)) == [("2025-01-06T00:00:00Z", round((40 + 22 * 17) / 26, 2), None)]


@pytest.fixture
def default_keep():
    yield
    main.conn.execute("DELETE FROM app_meta WHERE key LIKE 'history_%'")
    main.conn.commit()


def test_history_settings_thin_now_only_when_shorter(client, default_keep):
    from datetime import timedelta
    from app import display, value
    from tests.test_round11b import _snap
    _mover_setup(client)
    today = display.today()
    for days_ago in range(0, 200):
        _snap("t1-1", "normal", 1 + days_ago / 10, days_ago, today=today)
    main.conn.commit()
    page = client.get("/account/data").text
    assert "Price history" in page and "200 rows" in page and "Keep all" in page
    r = client.post("/account/data/history", data={"daily": "182", "weekly": "365", "monthly": "0"})
    assert "More history is kept" in r.text  # (the weeks before 6 months ago still become averages)
    assert main.conn.execute("SELECT COUNT(*) FROM price_snapshots WHERE captured_at >= date('now', '-180 days')").fetchone()[0] >= 180
    r = client.post("/account/data/history", data={"daily": "30", "weekly": "30", "monthly": "182"})
    assert "turned into averages" in r.text
    left = [r[0] for r in main.conn.execute("SELECT date(captured_at) FROM price_snapshots ORDER BY captured_at")]
    assert len(left) < 70 and min(left) >= (today - timedelta(days=30 + 30 + 182)).isoformat()
    assert value.keep_settings(main.conn) == {"daily": 30, "weekly": 30, "monthly": 182}
    assert "Pick one" in client.post("/account/data/history", data={"daily": "31", "weekly": "30", "monthly": "0"}).text


def test_clear_price_history_needs_clear_typed(client):
    from tests.test_round11b import _snap
    _mover_setup(client)
    _now("t1-1", 5.0)
    _snap("t1-1", "normal", 4.0, 3)
    main.conn.commit()
    assert "Type CLEAR" in client.post("/account/data/history/clear", data={"confirm": "clear"}).text
    assert main.conn.execute("SELECT COUNT(*) FROM price_snapshots").fetchone()[0] == 1
    assert "history cleared" in client.post("/account/data/history/clear", data={"confirm": "CLEAR"}).text
    assert main.conn.execute("SELECT COUNT(*) FROM price_snapshots").fetchone()[0] == 0
    assert main.conn.execute("SELECT price FROM prices WHERE card_id = 't1-1'").fetchone()[0] == 5.0


# --- 12a: pictures are read whole before the reply starts --------------------------------------------

def test_a_picture_deleted_before_it_is_sent_is_a_404_not_a_broken_reply(tmp_path):
    from fastapi import HTTPException
    pic = tmp_path / "1.jpg"
    pic.write_bytes(b"\xff\xd8 picture")
    r = web._picture_reply(pic, {"Cache-Control": "private"})
    assert r.body == b"\xff\xd8 picture" and r.headers["content-length"] == str(len(b"\xff\xd8 picture"))
    pic.unlink()
    with pytest.raises(HTTPException) as e:
        web._picture_reply(pic, {})
    assert e.value.status_code == 404


# --- 12b: connections of finished threads are closed ----------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="Linux only")
def test_finished_threads_do_not_keep_database_files_open(tmp_path):
    import os
    import threading
    conn = db.ThreadConnection(tmp_path / "t.db")
    conn.execute("CREATE TABLE IF NOT EXISTS x (a)")

    def touch():
        conn.execute("SELECT COUNT(*) FROM x").fetchone()
    files = lambda: len(os.listdir("/proc/self/fd"))  # noqa: E731
    for _ in range(5):
        t = threading.Thread(target=touch)
        t.start()
        t.join()
    before = files()
    for _ in range(200):  # as many jobs, picture checks and link checks as a long day brings
        t = threading.Thread(target=touch)
        t.start()
        t.join()
    assert files() - before < 10
    assert len(conn.opened) <= 3
    conn.close()
