"""Round 21 (0.21.0): speed fixes that change nothing on screen."""
import json
import sqlite3

from app import card_pictures, chase, collection, db, extras, main, web
from test_app import _chase_rows, _entry, _setup_with_set, client  # noqa: F401


def test_asking_for_pictures_runs_off_the_page_loop(client, monkeypatch):
    _setup_with_set(client)
    seen = []
    monkeypatch.setattr(card_pictures, "ask", lambda conn, ids, failed=False: seen.append(ids) or len(ids))
    r = client.post("/pictures/ask", json={"ids": ["t1-1", "", 5]})
    assert r.json()["asked"] == 1 and seen == [["t1-1"]]
    src = open("app/routes/pictures.py").read()
    body = src[src.index("async def pictures_ask"):src.index("def _ask")]
    assert "_in_thread(_ask" in body and "card_pictures.ask(" not in body


def test_the_stash_has_an_index_by_card_and_printing(client):
    names = {r[1] for r in main.conn.execute("PRAGMA index_list(inventory)")}
    assert "inventory_by_card" in names
    plan = " ".join(r[3] for r in main.conn.execute(
        "EXPLAIN QUERY PLAN SELECT 1 FROM inventory WHERE card_id = 't1-1' AND variant = 'normal'"))
    assert "inventory_by_card" in plan


def test_grid_tiles_use_the_extras_the_page_loaded(client, monkeypatch):
    _setup_with_set(client)
    _entry(client, "t1", "1")
    asked = []
    real = extras.get
    monkeypatch.setattr(extras, "get", lambda conn, card_id: asked.append(card_id) or real(conn, card_id))
    assert client.get("/sets/t1").status_code == 200
    assert client.get("/collection").status_code == 200
    assert asked == []  # every tile had its extras from extras_map
    card_pictures.for_id(main.conn, "t1-1")  # a lone picture still asks
    assert asked == ["t1-1"]


def test_scrolling_my_stash_reads_the_list_the_page_showed(client, monkeypatch):
    _setup_with_set(client)
    for number in ("1", "2", "TG01"):
        _entry(client, "t1", number)
    monkeypatch.setattr(web, "CARDS_CHUNK", 1)
    built = []
    real = collection.cards
    monkeypatch.setattr(collection, "cards", lambda *a, **k: built.append(1) or real(*a, **k))
    ids = ("/cards/t1-1\"", "/cards/t1-2\"", "/cards/t1-TG01\"")
    pages = [client.get("/collection?sort=name").text]
    assert built == [1] and "from=1" in pages[0]
    pages += [client.get("/collection?sort=name&from=1").text, client.get("/collection?sort=name&from=2").text]
    assert built == [1]  # not built again while scrolling
    assert [sum(i in p for i in ids) for p in pages] == [1, 1, 1] and all(any(i in p for p in pages) for i in ids)
    assert "from=3" not in pages[2]
    # Other filters, or nothing kept, build it as before.
    client.get("/collection?sort=number&from=1")
    assert built == [1, 1]
    web._scrolls.clear()
    client.get("/collection?sort=name&from=1")
    assert built == [1, 1, 1]


def test_a_followed_card_stored_in_cards_has_one_copy_of_its_prices(client):
    user_id = _setup_with_set(client)
    _entry(client, "t1", "1")
    chase.follow(main.conn, user_id, 1, "Bulbasaur", user_id)
    stored = dict(_chase_rows()[0], card_id="t1-1", lang="en", set_id="t1", prices_json=json.dumps({"normal": 9.0}))
    loose = dict(_chase_rows()[0], card_id="sv99-001", lang="en", set_id="sv99", prices_json=json.dumps({"normal": 2.0}))
    chase.save_cards(main.conn, 1, [stored, loose])
    got = dict(main.conn.execute("SELECT card_id, prices_json FROM pokemon_cards WHERE dex_id = 1"))
    assert got["t1-1"] is None and json.loads(got["sv99-001"]) == {"normal": 2.0}
    asked = []

    class API:
        def get_card(self, card_id):
            asked.append(card_id)
            return {"id": card_id, "pricing": {}}
    chase.refresh_prices(main.conn, lambda lang: API())
    assert asked == ["sv99-001"]  # the stored card is priced by the update itself


def test_step_16_drops_the_second_copy_and_adds_the_index(tmp_path):
    conn = db.connect(tmp_path / "old.db")
    db.init(conn)
    conn.execute("DROP INDEX inventory_by_card")
    conn.execute("INSERT INTO sets (set_id, name, language, printed_total, total) VALUES ('t1', 'Test', 'en', 3, 3)")
    conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name) VALUES ('t1-1', 't1', '1', 1, 1, 'Bulbasaur')")
    for card_id in ("t1-1", "sv99-001"):
        conn.execute("INSERT INTO pokemon_cards (dex_id, card_id, lang, set_id, prices_json) VALUES (1, ?, 'en', 'x', '{}')",
                     (card_id,))
    conn.execute("PRAGMA user_version = 15")
    conn.commit()
    db.init(conn)
    assert dict(conn.execute("SELECT card_id, prices_json FROM pokemon_cards")) == {"t1-1": None, "sv99-001": "{}"}
    assert "inventory_by_card" in {r[1] for r in conn.execute("PRAGMA index_list(inventory)")}
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.LATEST


def test_results_are_read_as_you_go_and_writes_in_between_are_safe(tmp_path):
    conn = db.ThreadConnection(tmp_path / "t.db")
    conn.execute("CREATE TABLE t (n INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(5)])
    cur = conn.execute("SELECT n FROM t ORDER BY n")
    assert isinstance(cur, sqlite3.Cursor)  # not copied into a list first
    seen = []
    for row in cur:
        seen.append(row[0])
        conn.execute("UPDATE t SET n = n + 100 WHERE n = ?", (row[0],))  # a write while reading
    assert seen == [0, 1, 2, 3, 4]
    assert [r[0] for r in conn.execute("SELECT n FROM t ORDER BY n")] == [100, 101, 102, 103, 104]
    # A page's transaction still undoes its writes when it fails, with a result left half read.
    try:
        with conn.request():
            half = conn.execute("SELECT n FROM t")
            half.fetchone()
            conn.execute("DELETE FROM t")
            raise RuntimeError("page failed")
    except RuntimeError:
        pass
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 5
    assert conn.execute("SELECT rowcount_test FROM (SELECT 1 AS rowcount_test)").fetchone()[0] == 1
    assert conn.execute("UPDATE t SET n = n").rowcount == 5
    conn.close()
