"""Round 20a (0.20.0): correct prices. Each printing's price from the sources that price that printing."""
import json

from app import db, main, prices, sync, tcgcsv, upgrades, web
from test_app import _entry, _setup_with_set, _typed, client  # noqa: F401
from test_round15d import _quote, _shown


# --- database step 15 ---------------------------------------------------------------------------------

def _step_14_db(path):
    """A database as 0.19.2 left it: typed prices on the card, TCGCSV's card table without printings."""
    conn = db.connect(path)
    db.init(conn)
    conn.executescript("""
        DROP TABLE catalog.tcgcsv_cards;
        CREATE TABLE catalog.tcgcsv_cards (card_id TEXT PRIMARY KEY, product_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
            image_url TEXT);
        INSERT INTO catalog.tcgcsv_cards VALUES ('s-1', 501, 100, NULL);
        PRAGMA user_version = 14;""")
    conn.execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'misty', 'x')")
    conn.execute("INSERT INTO sets (set_id, name, printed_total, total) VALUES ('s', 'S', 9, 9)")
    for n in range(1, 4):
        conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name) VALUES (?, 's', ?, ?, 1, 'x')",
                     (f"s-{n}", str(n), n))
    conn.executemany("INSERT INTO inventory (user_id, card_id, variant, condition, quantity) VALUES (1, ?, ?, 'NM', 1)",
                     [("s-1", "reverse"), ("s-2", "holo"), ("s-2", "normal")])
    conn.executemany(
        "INSERT INTO card_extras (card_id, manual_price, manual_currency, pc_typed, pc_typed_currency, pc_typed_at, "
        "pc_typed_read, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, '2026-10-20T00:00:00Z')",
        [("s-1", 4.0, "GBP", 6.0, "USD", "2026-09-01T00:00:00Z", 1),
         ("s-2", 7.0, "USD", None, None, None, 0),
         ("s-3", None, "USD", 2.5, "USD", "2026-10-01T00:00:00Z", 0)])
    conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('eur_usd', '1.08'), ('eur_usd_at', '2026-10-28T05:00:00Z'), "
                 "('ecb_rates', '{\"USD\": 1.08, \"GBP\": 0.86}')")
    conn.commit()
    return conn


def test_step_15_moves_typed_prices_to_a_printing_and_rebuilds_the_tcgcsv_cards(tmp_path):
    conn = _step_14_db(tmp_path / "dexstash.db")
    db.init(conn)
    assert db.version(conn) == db.LATEST >= 15
    typed = {(r["card_id"], r["variant"], r["kind"]): (r["price"], r["currency"], r["was_read"])
             for r in conn.execute("SELECT * FROM typed_prices")}
    assert typed == {("s-1", "reverse", "manual"): (4.0, "GBP", 0),  # the one printing you have
                     ("s-1", "reverse", "pricecharting"): (6.0, "USD", 1),
                     ("s-2", "normal", "manual"): (7.0, "USD", 0),  # two: the first in the usual order
                     ("s-3", "normal", "pricecharting"): (2.5, "USD", 0)}  # none: the card's first printing
    assert json.loads(conn.execute("SELECT value FROM app_meta WHERE key = 'typed_guessed'").fetchone()[0]) == ["s-2", "s-3"]
    cols = [c[1] for c in conn.execute("PRAGMA catalog.table_info(tcgcsv_cards)")]
    assert "variant" in cols and "url" in cols and conn.execute("SELECT COUNT(*) FROM tcgcsv_cards").fetchone()[0] == 0
    rates = dict(conn.execute("SELECT currency, per_eur FROM fx_rates WHERE rate_date = '2026-10-28'").fetchall())
    assert rates == {"USD": 1.08, "GBP": 0.86, "EUR": 1.0}
    for table, column in (("price_quotes", "source_updated_at"), ("price_snapshots", "samples"), ("price_runs", "summary_json")):
        assert column in [c[1] for c in conn.execute(f"PRAGMA table_info({table})")]
    db.init(conn)  # a restart changes nothing
    assert conn.execute("SELECT COUNT(*) FROM typed_prices").fetchone()[0] == 4


def test_a_new_database_is_at_step_15(tmp_path):
    conn = db.connect(tmp_path / "new.db")
    db.init(conn)
    assert db.version(conn) == db.LATEST and conn.execute("SELECT COUNT(*) FROM typed_prices").fetchone()[0] == 0


def test_the_start_up_rerun_keeps_average_history_after_19_2(client):
    _setup_with_set(client)
    _entry(client, "t1", "2", "holo")
    keep = ("t1-2", "holo", "average", 5.0, "2026-10-28T00:00:00Z")
    main.conn.execute("INSERT INTO price_snapshots (card_id, variant, source, price, captured_at) VALUES (?, ?, ?, ?, ?)", keep)
    main.conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('prices_round', '19.2')")
    main.conn.execute("DELETE FROM app_meta WHERE key = 'after_upgrade:3'")  # round 22: the after-upgrade task
    main.conn.commit()
    assert upgrades.run(main.conn, slow=True) == ["Work every card's prices out again"]
    assert main.conn.execute("SELECT COUNT(*) FROM price_snapshots WHERE source = 'average' AND price = 5.0").fetchone()[0] == 1
    assert upgrades.run(main.conn, slow=True) == []  # once
    # An older database loses Cardmarket's history and the averages it was in.
    main.conn.execute("UPDATE app_meta SET value = '19.1' WHERE key = 'prices_round'")
    main.conn.execute("DELETE FROM app_meta WHERE key = 'after_upgrade:3'")
    main.conn.commit()
    assert upgrades.run(main.conn, slow=True) == ["Work every card's prices out again"]
    assert main.conn.execute("SELECT COUNT(*) FROM price_snapshots WHERE source = 'average' AND price = 5.0").fetchone()[0] == 0
    # An install that ran it the old way (0.20) doesn't again.
    main.conn.execute("UPDATE app_meta SET value = '20.0' WHERE key = 'prices_round'")
    main.conn.execute("DELETE FROM app_meta WHERE key = 'after_upgrade:3'")
    main.conn.commit()
    assert upgrades.run(main.conn, slow=True) == []


# --- the price math ---------------------------------------------------------------------------------

def test_a_typed_price_is_for_one_printing(client):
    _setup_with_set(client)
    _entry(client, "t1", "2", "normal")
    _entry(client, "t1", "2", "reverse")
    main.conn.execute("DELETE FROM price_quotes")
    main.conn.commit()
    page = client.get("/cards/t1-2").text
    assert 'name="variant" id="mp-variant"' in page
    client.post("/cards/t1-2/extras", data={"manual_price": "8", "variant": "reverse"})
    assert _typed("t1-2") == {"reverse": 8.0}
    shown = _shown("t1-2")
    assert shown["reverse"] == (8.0, "manual", None) and shown["normal"][1] == "printing"  # ≈, not spread
    client.post("/cards/t1-2/extras", data={"manual_price": "4", "variant": "normal"})
    assert _typed("t1-2") == {"reverse": 8.0, "normal": 4.0}
    page = client.get("/cards/t1-2").text
    assert page.count("Remove</button></form>") >= 2  # each printing's with its Remove
    client.post("/cards/t1-2/extras", data={"manual_price": "", "variant": "reverse"})
    assert _typed("t1-2") == {"normal": 4.0}
    client.post("/cards/t1-2/extras", data={"manual_price": "1", "variant": "nonsense"})
    assert _typed("t1-2") == {"normal": 4.0}


def test_an_ex_card_saved_as_normal_gets_tcgplayers_holofoil(client):
    _setup_with_set(client)
    _entry(client, "t1", "2", "normal")
    main.conn.execute("DELETE FROM price_quotes")
    _quote("t1-2", "holo", "tcgplayer", 12.0, basis="tcgcsv")
    assert _shown("t1-2")["normal"] == (12.0, "tcgplayer", "tcgcsv")
    # With both printings yours, each keeps its own.
    _entry(client, "t1", "2", "holo")
    shown = _shown("t1-2")
    assert shown["holo"] == (12.0, "tcgplayer", "tcgcsv") and shown["normal"][1] == "printing"
    assert prices.printing_swap({"holo", "reverse"}, ["normal"]) == {}  # two listed: nothing swapped
    assert prices.printing_swap({"reverse"}, ["normal"]) == {}  # only Normal and Holo swap


def test_a_cardtrader_price_for_the_card_goes_on_its_one_base_printing(client):
    _setup_with_set(client)
    _entry(client, "t1", "2", "normal")
    _entry(client, "t1", "2", "reverse")
    main.conn.execute("DELETE FROM price_quotes")
    _quote("t1-2", "", "cardtrader", 3.0, basis="nm")
    shown = _shown("t1-2")
    assert shown["normal"] == (3.0, "cardtrader", "nm") and shown["reverse"][1] == "printing"
    # Two base printings: it's for neither.
    _entry(client, "t1", "2", "holo")
    assert _shown("t1-2") == {}


def test_a_printing_you_chase_borrows_a_price_too(client):
    uid = _setup_with_set(client)
    _entry(client, "t1", "2", "normal")
    main.conn.execute("DELETE FROM price_quotes")
    _quote("t1-2", "normal", "tcgplayer", 2.0)
    main.conn.execute("INSERT INTO wants (user_id, card_id, variant) VALUES (?, 't1-2', 'reverse')", (uid,))
    shown = _shown("t1-2")
    assert shown["reverse"] == (2.0, "printing", "normal")


def test_no_cardmarket_price_but_the_search_link_stays(client):
    _setup_with_set(client)
    _entry(client, "t1", "2", "holo")
    main.conn.execute("DELETE FROM price_quotes")
    main.conn.execute("UPDATE cards SET cardmarket_eur = 50.0 WHERE id = 't1-2'")
    _quote("t1-2", "holo", "tcgplayer", 6.0)
    assert _shown("t1-2") == {"holo": (6.0, "tcgplayer", None)}
    page = client.get("/cards/t1-2").text
    assert "Search Cardmarket" in page and "Europe" not in page
    src = "".join(open(f"app/{name}.py").read() for name in ("prices", "value", "web"))
    assert "CARDMARKET" not in src and "cardmarket_eur" not in src.replace("SET cardmarket_eur", "")


def test_a_fifth_source_needs_only_a_line_in_the_list(client, monkeypatch):
    _setup_with_set(client)
    _entry(client, "t1", "2", "holo")
    main.conn.execute("DELETE FROM price_quotes")
    main.conn.commit()
    ran = []

    def fetch(conn, api_for, run):
        ran.append(1)
        conn.execute("INSERT OR REPLACE INTO price_quotes (card_id, variant, source, price, currency, updated_at) "
                     "VALUES ('t1-2', 'holo', 'misty_market', 4.0, 'USD', '2026-10-29T00:00:00Z')")
        return []
    monkeypatch.setattr(prices, "SOURCES", prices.SOURCES + (prices.Source("misty", "Misty's Market", "misty_market",
                                                                           fetch=fetch),))
    monkeypatch.setattr(prices, "HISTORY_SOURCES", prices.HISTORY_SOURCES + ("misty_market",))
    sync.refresh_all(main.conn, lambda lang: web.api)
    assert ran == [1]
    row = prices.card_prices(main.conn, "t1-2")[0]
    assert "misty_market" in [f["origin"] for f in row["sources"]]
    assert prices.source_label("misty_market") == "Misty's Market"
    assert "Misty&#39;s Market" in client.get("/cards/t1-2").text or "Misty's Market" in client.get("/cards/t1-2").text


# --- TCGCSV write rules -------------------------------------------------------------------------------

GROUPS = {"https://tcgcsv.com/tcgplayer/3/groups": {"results": [{"groupId": 100, "name": "SV01: Test Set", "abbreviation": "TS1"}]},
          "https://tcgcsv.com/tcgplayer/85/groups": {"results": []}}
PRODUCTS = {"results": [
    {"productId": 501, "name": "Bulbasaur", "url": "https://www.tcgplayer.com/product/501/bulbasaur",
     "modifiedOn": "2026-10-20T10:00:00", "extendedData": [{"name": "Number", "value": "001/002"}]},
    {"productId": 511, "name": "Bulbasaur (Poke Ball Pattern)", "extendedData": [{"name": "Number", "value": "001/002"}]},
    {"productId": 512, "name": "Bulbasaur (Master Ball Pattern)", "extendedData": [{"name": "Number", "value": "001/002"}]},
    {"productId": 502, "name": "Ivysaur", "extendedData": [{"name": "Number", "value": "002/002"}]}]}
PRICES = {"results": [
    {"productId": 501, "subTypeName": "Normal", "marketPrice": 0.5},
    {"productId": 501, "subTypeName": "Reverse Holofoil", "marketPrice": 1.5, "lowPrice": 1.0, "midPrice": 2.0},
    {"productId": 511, "subTypeName": "Holofoil", "marketPrice": 9.0},
    {"productId": 512, "subTypeName": "Holofoil", "marketPrice": 40.0},
    {"productId": 502, "subTypeName": "Holofoil", "marketPrice": 3.0},
    {"productId": 502, "subTypeName": "Shiny Sparkle", "marketPrice": 99.0}]}


def _get(prices_reply):
    def get(url):
        if url.endswith("/products"):
            return PRODUCTS
        if url.endswith("/prices"):
            if isinstance(prices_reply, Exception):
                raise prices_reply
            return prices_reply
        return GROUPS[url]
    return get


def test_tcgcsv_prices_poke_ball_and_master_ball_printings_and_keeps_its_link(client):
    _setup_with_set(client)
    for variant in ("normal", "reverse", "pokeball", "masterball"):
        _entry(client, "t1", "1", variant)
    main.conn.execute("DELETE FROM price_quotes")
    tcgcsv.run(main.conn, _get(PRICES), sleep=lambda s: None)
    shown = {r["variant"]: (r["price"], r["origin"], r["basis"]) for r in prices.card_prices(main.conn, "t1-1")}
    assert shown == {"normal": (0.5, "tcgplayer", "tcgcsv"), "reverse": (1.5, "tcgplayer", "tcgcsv"),
                     "pokeball": (9.0, "tcgplayer", "tcgcsv"), "masterball": (40.0, "tcgplayer", "tcgcsv")}
    q = main.conn.execute("SELECT url, source_updated_at FROM price_quotes WHERE card_id = 't1-1' AND variant = 'normal'").fetchone()
    assert tuple(q) == ("https://www.tcgplayer.com/product/501/bulbasaur", "2026-10-20T10:00:00")
    assert main.conn.execute("SELECT COUNT(*) FROM tcgcsv_product_prices").fetchone()[0] == 0  # only for browsed sets
    assert tcgcsv.UNKNOWN_SUBTYPES == {}  # counted, logged and cleared


def test_a_good_read_drops_a_printing_tcgplayer_stopped_listing_and_a_failed_read_keeps_them(client):
    _setup_with_set(client)
    _entry(client, "t1", "1", "normal")
    _entry(client, "t1", "1", "reverse")
    main.conn.execute("DELETE FROM price_quotes")
    tcgcsv.run(main.conn, _get(PRICES), sleep=lambda s: None)
    mine = lambda: {r["variant"] for r in prices.card_prices(main.conn, "t1-1")  # noqa: E731
                    if r["origin"] == "tcgplayer" and r["variant"] in ("normal", "reverse")}
    assert mine() == {"normal", "reverse"}
    # A failed read keeps yesterday's.
    main.conn.execute("UPDATE tcgcsv_sets SET prices_at = NULL")
    _, errors = tcgcsv.run(main.conn, _get(OSError("no answer")), sleep=lambda s: None)
    assert errors and mine() == {"normal", "reverse"}
    # An empty reply is treated the same.
    tcgcsv.run(main.conn, _get({"results": []}), sleep=lambda s: None)
    assert main.conn.execute("SELECT COUNT(*) FROM tcgcsv_prices WHERE card_id = 't1-1'").fetchone()[0] == 4
    # A good read without the reverse: its price goes, and it borrows Normal's.
    dropped = {"results": [p for p in PRICES["results"] if p.get("subTypeName") != "Reverse Holofoil"]}
    tcgcsv.run(main.conn, _get(dropped), sleep=lambda s: None)
    shown = {r["variant"]: (r["price"], r["origin"]) for r in prices.card_prices(main.conn, "t1-1")}
    assert shown["normal"] == (0.5, "tcgplayer") and shown["reverse"][1] == "printing"


def test_tcgdex_only_fills_printings_tcgcsv_has_no_price_for(client):
    _setup_with_set(client)
    _entry(client, "t1", "1", "normal")
    _entry(client, "t1", "1", "holo")
    main.conn.execute("DELETE FROM price_quotes")
    tcgcsv.run(main.conn, _get(PRICES), sleep=lambda s: None)
    with db.transaction(main.conn):
        prices.store(main.conn, "t1-1", {"pricing": {"tcgplayer": {"normal": {"marketPrice": 7.0},
                                                                    "holofoil": {"marketPrice": 2.0}}}})
    got = {r["variant"]: (r["price"], r["basis"]) for r in main.conn.execute(
        "SELECT * FROM price_quotes WHERE card_id = 't1-1' AND source = 'tcgplayer'")}
    assert got["normal"] == (0.5, "tcgcsv") and got["holo"] == (2.0, None)


# --- the short run -------------------------------------------------------------------------------------

def test_a_card_from_a_set_never_read_is_priced_by_the_short_run(client, monkeypatch):
    from app import settings
    _setup_with_set(client)
    started = []
    monkeypatch.setattr(sync.PriceUpdater, "short_runs", True)
    monkeypatch.setattr(web.updater, "start_sets", lambda sets: started.append(sorted(sets)) or True)
    _entry(client, "t1", "1", "normal")
    assert started == [["t1"]]
    # The short run itself: only that set, matched to TCGplayer first.
    monkeypatch.setenv("DEXSTASH_TCGCSV", "1")
    assert settings.on("TCGCSV")
    main.conn.execute("DELETE FROM price_quotes")
    main.conn.commit()
    updated, errors = sync.refresh_sets(main.conn, lambda lang: web.api, ["t1"], get=_get(PRICES), sleep=lambda s: None)
    assert errors == []
    assert {r["variant"]: r["price"] for r in prices.card_prices(main.conn, "t1-1")}["normal"] == 0.5


def test_sets_queue_for_one_short_run(client):
    main.conn.execute("DELETE FROM app_meta WHERE key = ?", (sync.PENDING,))
    sync.queue_sets(main.conn, ["a", "b"])
    sync.queue_sets(main.conn, ["b", "c", None])
    assert sync.take_sets(main.conn) == ["a", "b", "c"] and sync.take_sets(main.conn) == []


def test_an_import_starts_a_short_run_and_the_wizard_one_full_update():
    from pathlib import Path
    app = Path(main.__file__).parent
    assert "updater.start_sets(" in (app / "routes" / "imports.py").read_text()
    assert "web.updater.start()" in (app / "routes" / "welcome.py").read_text()  # round 23: at Continue


def test_other_ball_and_energy_patterns_dont_hide_the_card_itself(client):
    # Japanese sets list "(Quick Ball Pattern)", "(Energy Symbol Pattern)" and so on beside the card.
    _setup_with_set(client)
    _entry(client, "t1", "2", "normal")
    main.conn.execute("DELETE FROM price_quotes")
    products = {"results": PRODUCTS["results"] + [
        {"productId": 521, "name": "Ivysaur (Quick Ball Pattern)", "extendedData": [{"name": "Number", "value": "002/002"}]},
        {"productId": 522, "name": "Ivysaur (Energy Symbol Pattern)", "extendedData": [{"name": "Number", "value": "002/002"}]}]}
    reply = {"results": [{"productId": 502, "subTypeName": "Normal", "marketPrice": 0.25},
                         {"productId": 521, "subTypeName": "Holofoil", "marketPrice": 7.0}]}

    def get(url):
        return products if url.endswith("/products") else reply if url.endswith("/prices") else GROUPS[url]
    tcgcsv.run(main.conn, get, sleep=lambda s: None)
    assert {r["variant"]: r["price"] for r in prices.card_prices(main.conn, "t1-2")} == {"normal": 0.25}
    assert not tcgcsv._patterned("Beautifly - 006/193") and tcgcsv._patterned("Entei (Quick Ball Pattern)")
