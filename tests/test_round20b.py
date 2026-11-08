"""Rounds 20b, 20c and 20d (0.20.1): reliable runs, history and currency, flags."""
from datetime import date

from app import cardtrader, db, fx, home, main, prices, sync, value, web
from test_app import _entry, _setup_with_set, client  # noqa: F401
from test_round15b import TOKEN, FakeCardTrader, _listing
from test_round15d import _quote


# --- 20b: CardTrader -----------------------------------------------------------------------------------------

def test_cardtrader_takes_the_near_mint_median_in_the_cards_language():
    eur = [_listing(100, currency="EUR"), _listing(200, currency="EUR"), _listing(900, currency="EUR"),
           _listing(50, "Played", currency="EUR")]
    assert cardtrader.near_mint(eur, "en") == (2.0, "EUR", False)  # €1, €2, €9 Near Mint; the played one doesn't count
    assert cardtrader.near_mint(eur[:2], "en") == (1.5, "EUR", False)  # an even count: the middle two
    assert cardtrader.near_mint([_listing(50, "Played")], "en") is None  # no Near Mint: no price
    assert cardtrader.near_mint([_listing(1000), _listing(1200), _listing(400, language="jp")], "en")[0] == 11.0
    assert cardtrader.near_mint([_listing(400, language="jp"), _listing(600, language="jp")], "ja")[0] == 5.0
    skipped = {}
    nameless = dict(_listing(300), properties_hash={"condition": "Near Mint"})
    assert cardtrader.near_mint([nameless, _listing(100)], "en", skipped=skipped)[0] == 1.0
    assert skipped == {"no_language": 1}


def test_the_far_too_high_check_compares_dollars(client):
    _setup_with_set(client)
    _entry(client, "t1", "2", "holo")
    main.conn.execute("DELETE FROM price_quotes")
    _quote("t1-2", "holo", "tcgplayer", 1.0)
    main.conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('ecb_rates', '{\"USD\": 2.0}')")
    fx.load(main.conn)
    assert cardtrader._believable(main.conn, "t1-2", "holo", 10.0, "EUR")  # $20: within 20× TCGplayer's $1
    assert not cardtrader._believable(main.conn, "t1-2", "holo", 15.0, "EUR")  # $30: over 20× and over $20 more
    assert cardtrader._believable(main.conn, "t1-2", "holo", 15.0, "USD")  # $15 is fine
    main.conn.execute("DELETE FROM app_meta WHERE key = 'ecb_rates'")
    fx.load(main.conn)


def test_a_slowdown_keeps_last_nights_cardtrader_prices(client, monkeypatch):
    _setup_with_set(client)
    _entry(client, "t1", "1", "normal")
    _entry(client, "t1", "1", "pokeball")
    monkeypatch.setenv(cardtrader.TOKEN_ENV, TOKEN)
    get = FakeCardTrader()
    cardtrader.run(main.conn, get=get, sleep=lambda s: None)
    before = {r[0]: r[1] for r in main.conn.execute(
        "SELECT variant, price FROM price_quotes WHERE card_id = 't1-1' AND source = 'cardtrader'")}
    assert before["pokeball"] == 8.0 and before[""] == 1.5
    url = main.conn.execute("SELECT url FROM price_quotes WHERE card_id = 't1-1' AND source = 'cardtrader' AND variant = ''").fetchone()[0]
    assert url == "https://www.cardtrader.com/en/cards/501"

    class SlowPokeBall(FakeCardTrader):  # CardTrader says slow down on the Poké Ball expansion only
        def __call__(self, path, params=None):
            if path == "/marketplace/products" and params["expansion_id"] == 11:
                raise cardtrader.Busy("429")
            return super().__call__(path, params)
    cardtrader.run(main.conn, get=SlowPokeBall(), sleep=lambda s: None)
    after = {r[0]: r[1] for r in main.conn.execute(
        "SELECT variant, price FROM price_quotes WHERE card_id = 't1-1' AND source = 'cardtrader'")}
    assert after["pokeball"] == 8.0  # not read: kept
    assert cardtrader.LAST_RUN.get("slowed") == 1


# --- 20b: the run summary ----------------------------------------------------------------------------------------

def test_each_full_run_records_a_summary(client):
    _setup_with_set(client)
    _entry(client, "t1", "1")
    main.conn.execute("DELETE FROM price_runs")
    main.conn.commit()
    sync.refresh_all(main.conn, lambda lang: web.api)
    [run] = sync.runs(main.conn)
    assert run["status"] == "ok" and run["kind"] == "full" and run["finished_at"] and "tcgdex" in run["sources"]
    page = client.get("/account/data").text
    assert "Last price updates" in page and "all fine" in page


def test_a_failed_run_says_so_and_frees_the_update(client, monkeypatch):
    _setup_with_set(client)
    main.conn.execute("DELETE FROM price_runs")
    main.conn.commit()

    def broken(conn, api_for, run):
        raise RuntimeError("disk full")
    monkeypatch.setattr(prices, "SOURCES", (prices.Source("broken", "Broken", "broken", fetch=broken),))
    try:
        sync.refresh_all(main.conn, lambda lang: web.api)
    except RuntimeError:
        pass
    [run] = sync.runs(main.conn)
    assert run["status"] == "failed" and "disk full" in run["error"]
    # A failed run isn't a day prices were updated.
    main.conn.execute("UPDATE price_runs SET ran_at = '2000-01-05T04:00:00Z'")
    main.conn.commit()
    assert "2000-01-05" not in value._days(main.conn, date(2000, 1, 1), date(2000, 1, 9))


def test_a_source_with_problems_makes_the_run_partial(client, monkeypatch):
    _setup_with_set(client)
    main.conn.execute("DELETE FROM price_runs")
    main.conn.commit()

    def grumpy(conn, api_for, run):
        run["counts"]["grumpy"] = {"cards": 0}
        return ["Grumpy: no answer"]
    monkeypatch.setattr(prices, "SOURCES", (prices.Source("grumpy", "Grumpy", "grumpy", fetch=grumpy),))
    sync.refresh_all(main.conn, lambda lang: web.api)
    [run] = sync.runs(main.conn)
    assert run["status"] == "partial" and run["sources"]["grumpy"]["errors"] == 1
    assert run["sources"]["grumpy"]["first_errors"] == ["Grumpy: no answer"]


def test_a_catalog_problem_is_named_in_the_summary(client, monkeypatch):
    from app import card_index
    _setup_with_set(client)
    main.conn.execute("DELETE FROM price_runs")
    main.conn.commit()
    monkeypatch.setattr(card_index, "refresh", lambda conn, api_for: ["TCGdex set ja:M9: no answer"])
    sync.refresh_all(main.conn, lambda lang: web.api)
    [run] = sync.runs(main.conn)
    assert run["status"] == "partial" and "followed" not in run["sources"]
    assert run["sources"]["catalog"]["first_errors"] == ["TCGdex set ja:M9: no answer"]
    page = client.get("/account/data").text
    assert "Find&#39;s card catalog: 1 problem" in page and "ja:M9: no answer" in page


def test_a_short_run_keeps_its_own_summary(client):
    _setup_with_set(client)
    _entry(client, "t1", "1")
    sync.refresh_sets(main.conn, lambda lang: web.api, ["t1"])
    short = sync.last_short_run(main.conn)
    assert short["kind"] == "short" and short["sets"] == 1 and short["status"] == "ok"


# --- 20b: pages read stored prices only --------------------------------------------------------------------------

def test_a_card_page_never_asks_tcgdex_for_a_price(client, monkeypatch):
    _setup_with_set(client)
    main.conn.execute("DELETE FROM price_quotes")
    main.conn.execute("DELETE FROM prices")
    main.conn.commit()
    asked = []
    monkeypatch.setattr(web, "_live_card", lambda card_id: asked.append(card_id))
    read = []
    monkeypatch.setattr(web, "_read_set_later", lambda *a: read.append(a))
    client.get("/cards/t1-2")
    _entry(client, "t1", "2")  # adding doesn't wait on TCGdex either
    assert asked == []
    from app import tile_prices
    found, _ = tile_prices.for_cards(main.conn, [{"key": "x-1", "id": "x-1", "set_id": "x", "set_name": "X", "number": "1"}])
    assert found == {}  # Find: stored prices only


def test_a_card_in_a_browsed_set_shows_tcgcsvs_price_with_no_request(client, monkeypatch):
    from app import tcgcsv
    _setup_with_set(client)
    main.conn.execute("INSERT INTO tcgcsv_groups (category, group_id, name, abbreviation) VALUES (3, 100, 'Test Set', 'TS1')")
    main.conn.execute("INSERT INTO tcgcsv_group_reads (group_id, category, prices_at) VALUES (100, 3, '2026-10-29T00:00:00Z')")
    main.conn.execute("INSERT INTO tcgcsv_sets (set_id, category, group_id, matched_by) VALUES ('t1', 3, 100, 'code')")
    main.conn.execute("INSERT INTO tcgcsv_products (category, group_id, product_id, number, name) VALUES (3, 100, 502, '2', 'Ivysaur')")
    main.conn.execute("INSERT INTO tcgcsv_product_prices (group_id, product_id, subtype, market, low) VALUES "
                      "(100, 502, 'holofoil', 4.25, 4.0)")
    main.conn.commit()
    monkeypatch.setattr(tcgcsv, "fetch", lambda url: (_ for _ in ()).throw(AssertionError("asked TCGCSV")))
    rows = web._browsed_prices("t1-2")
    assert [(r["variant"], r["price"]) for r in rows] == [("holo", 4.25)]


# --- 20c: exchange rates kept by day ---------------------------------------------------------------------------

DAYS = ("<Cube><Cube time='2026-10-28'><Cube currency='USD' rate='1.08'/><Cube currency='CAD' rate='1.50'/></Cube>"
        "<Cube time='2026-10-27'><Cube currency='USD' rate='1.07'/><Cube currency='CAD' rate='1.49'/></Cube></Cube>")


def test_each_days_rates_are_kept_and_looked_up_on_or_before_a_date(client):
    main.conn.execute("DELETE FROM fx_rates")
    assert fx.save_days(main.conn, fx.parse_days(DAYS)) == 2
    assert fx.save_days(main.conn, fx.parse_days(DAYS)) == 0  # a second time changes nothing
    assert fx.rate_on(main.conn, "2026-10-28", "CAD") == 1.5
    assert fx.rate_on(main.conn, "2026-10-30", "CAD") == 1.5  # the newest day before
    assert fx.rate_on(main.conn, "2026-10-01", "CAD") is None
    assert fx.rate_on(main.conn, "2026-10-27", "EUR") == 1.0
    assert fx.usd_rate_on(main.conn, "2026-10-27", "CAD") == round(1.49 / 1.07, 6)


def test_past_rates_are_read_once_and_tried_again_after_a_failure(client):
    main.conn.execute("DELETE FROM fx_rates")
    main.conn.execute("DELETE FROM app_meta WHERE key = ?", (fx.HISTORY_DONE,))
    assert fx.backfill(main.conn, get=lambda url: 1 / 0) == 0
    asked = []
    assert fx.backfill(main.conn, get=lambda url: asked.append(url) or DAYS) == 2
    assert asked == [fx.HISTORY_URL]
    assert fx.backfill(main.conn, get=lambda url: 1 / 0) == 0  # done: not asked again


def test_a_by_hand_update_loads_the_saved_rates_first():
    src = open(sync.__file__).read()
    main_block = src[src.index('if __name__ == "__main__":'):]
    assert main_block.index("fx.load(conn)") < main_block.index("refresh_all(")


# --- 20c: history with low, high and count -----------------------------------------------------------------------

def test_the_average_history_keeps_its_low_and_high(client):
    _setup_with_set(client)
    _entry(client, "t1", "2", "holo")
    main.conn.execute("DELETE FROM price_quotes")
    main.conn.execute("DELETE FROM price_snapshots")
    _quote("t1-2", "holo", "tcgplayer", 6.0)
    _quote("t1-2", "", "cardtrader", 10.0, basis="nm")
    with db.transaction(main.conn):
        prices.resolve(main.conn, "t1-2")
    row = main.conn.execute("SELECT price, low, low_source, high, high_source, samples FROM price_snapshots "
                            "WHERE card_id = 't1-2' AND source = 'average'").fetchone()
    assert tuple(row) == (8.0, 6.0, "tcgplayer", 10.0, "cardtrader", 1)
    pts = value.card_series(main.conn, "t1-2", "holo")["points"]
    assert pts[-1][3:] == (6.0, 10.0)
    chart = value.svg_path([("2026-10-27", 8.0, "Average", 6.0, 10.0), ("2026-10-28", 9.0, "Average", 7.0, 11.0)])
    assert chart["band"] and chart["lo"] == 6.0 and chart["hi"] == 11.0


def test_thinning_a_week_keeps_its_stats_and_the_saved_currency():
    week = [(f"2026-09-0{d}T08:00:00Z", 10.0 + d, None, 9.0 + d, "tcgplayer", 12.0 + d, "cardtrader", 1, 20.0 + d, "CAD")
            for d in range(7, 10)]  # Mon 7, Tue 8, Wed 9 Sep 2026; Thu-Sun carry Wed's
    thin = value._thin(week, date(2026, 10, 1), date(2026, 9, 1))
    [(at, price, basis, low, low_source, high, high_source, samples, local, currency)] = thin
    assert at == "2026-09-07T00:00:00Z" and samples == 7
    assert price == round((17 + 18 + 19 * 5) / 7, 2) and (low, low_source) == (16.0, "tcgplayer")
    assert (high, high_source) == (21.0, "cardtrader") and currency == "CAD" and local == round((27 + 28 + 29 * 5) / 7, 2)
    assert value._thin(thin, date(2026, 10, 1), date(2026, 9, 1)) == thin  # twice changes nothing
    old = [(r[0], r[1], r[2]) for r in week]  # the three-column shape older callers use still works
    assert len(value._thin(old, date(2026, 10, 1), date(2026, 9, 1))[0]) == 3


# --- 20c: a currency change redraws history ----------------------------------------------------------------------

def test_a_currency_change_resaves_every_point_at_its_own_days_rate(client):
    _setup_with_set(client)
    _entry(client, "t1", "2", "holo")
    main.conn.execute("DELETE FROM price_snapshots")
    main.conn.execute("DELETE FROM fx_rates")
    fx.save_days(main.conn, fx.parse_days(DAYS))
    for day, price in (("2026-10-27", 10.0), ("2026-10-28", 12.0), ("2026-09-01", 5.0)):
        main.conn.execute("INSERT INTO price_snapshots (card_id, variant, source, price, captured_at, local, local_currency) "
                          "VALUES ('t1-2', 'holo', 'average', ?, ?, ?, 'USD')", (price, f"{day}T08:00:00Z", price))
    main.conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('ecb_rates', '{\"USD\": 1.08, \"CAD\": 1.5}')")
    fx.load(main.conn)
    fx.save_currency(main.conn, "CAD")
    try:
        with db.transaction(main.conn):
            done, kept = prices.localize_history(main.conn)
        assert (done, kept) == (2, 1)  # 1 Sep is older than every rate: kept as it was
        got = dict(main.conn.execute("SELECT substr(captured_at, 1, 10), local FROM price_snapshots WHERE local_currency = 'CAD'"))
        assert got == {"2026-10-27": round(10 * round(1.49 / 1.07, 6), 2), "2026-10-28": round(12 * round(1.5 / 1.08, 6), 2)}
        fx.save_currency(main.conn, "USD")
        with db.transaction(main.conn):
            prices.localize_history(main.conn)
        assert {r[0] for r in main.conn.execute("SELECT local - price FROM price_snapshots")} == {0.0}  # back to dollars
    finally:
        fx.save_currency(main.conn, "USD")
        main.conn.execute("DELETE FROM app_meta WHERE key = 'ecb_rates'")
        fx.load(main.conn)


# --- 20d: flags and coverage ------------------------------------------------------------------------------------

def test_no_price_and_stale_flags_for_cards_you_have_or_chase(client):
    uid = _setup_with_set(client)
    _entry(client, "t1", "1", "normal")
    _entry(client, "t1", "2", "holo")
    main.conn.execute("DELETE FROM price_quotes")
    _quote("t1-2", "holo", "tcgplayer", 3.0, at="2026-01-01T00:00:00Z")  # months old
    main.conn.execute("INSERT INTO wants (user_id, card_id, variant) VALUES (?, 't1-TG01', 'any')", (uid,))
    for card_id in ("t1-1", "t1-2", "t1-TG01"):
        with db.transaction(main.conn):
            prices.resolve(main.conn, card_id)
    states = prices.printing_states(main.conn, uid)
    assert states[("t1-1", "normal")] == {"no_price"} and states[("t1-2", "holo")] == {"tcgplayer", "stale"}
    assert states[("t1-TG01", "")] == {"no_price"}
    assert ("t1-3", "normal") not in states  # a card you don't have or chase is never flagged
    review = home.to_review(main.conn, uid)
    assert {(r["id"], r["key"]) for r in review} == {("t1-1", "no_price"), ("t1-TG01", "no_price"), ("t1-2", "stale")}
    assert [r["id"] for r in home.to_review(main.conn, uid, "stale")] == ["t1-2"]
    page = client.get("/review?show=stale").text
    assert "Stale price" in page
    cov = prices.coverage(main.conn, uid)
    assert cov["total"] == 3 and dict((k, n) for k, _, n in cov["parts"])["no_price"] == 2
    assert "Your 3 printings" in client.get("/account/data").text


def test_the_stale_setting(client):
    _setup_with_set(client)
    client.post("/account/data/stale", data={"days": "30"})
    assert prices.stale_days(main.conn) == 30
    client.post("/account/data/stale", data={"days": "0"})
    assert prices.stale_days(main.conn) == 1
    main.conn.execute("DELETE FROM app_meta WHERE key = ?", (prices.STALE_KEY,))
    main.conn.commit()
    assert prices.stale_days(main.conn) == 7


def test_card_history_carries_on_from_chase_to_stash(client):
    uid = _setup_with_set(client)
    main.conn.execute("DELETE FROM price_snapshots")
    main.conn.execute("DELETE FROM price_quotes")
    main.conn.execute("INSERT INTO wants (user_id, card_id, variant) VALUES (?, 't1-2', 'holo')", (uid,))
    _quote("t1-2", "holo", "tcgplayer", 3.0)
    with db.transaction(main.conn):
        prices.resolve(main.conn, "t1-2")
    _entry(client, "t1", "2", "holo")
    _quote("t1-2", "holo", "tcgplayer", 4.0)
    with db.transaction(main.conn):
        prices.resolve(main.conn, "t1-2")
    got = [r[0] for r in main.conn.execute("SELECT price FROM price_snapshots WHERE card_id = 't1-2' AND variant = 'holo' "
                                            "AND source = 'average' ORDER BY captured_at, rowid")]
    assert got == [3.0, 4.0]  # one series, chased then owned
