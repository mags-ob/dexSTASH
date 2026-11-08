"""Round 15b: no page reading, CardTrader's official API, and the PriceCharting price you type."""
import json
import logging
import re
from pathlib import Path

import httpx
import pytest

from app import cardtrader, db, extras, home, main, prices, sites, web
from test_app import _entry, _setup_with_set, client  # noqa: F401

APP = Path(__file__).resolve().parent.parent / "app"
TOKEN = "test-token-not-a-real-one"  # never a real token in a test

# Shapes as CardTrader sent them (26 Oct 2026, from a Chinese set): sealed items have no collector number.
EXPANSIONS = [{"id": 10, "game_id": 5, "code": "t1", "name": "Test Set"},
              {"id": 11, "game_id": 5, "code": "p-t1", "name": "Test Set - Poké Ball Reverse Holo"},
              {"id": 99, "game_id": 1, "code": "t1", "name": "A Magic set"},
              {"id": 12, "game_id": 5, "code": "oth", "name": "Other Set"}]
BLUEPRINTS = {
    10: [{"id": 500, "name": "Test Set Booster", "category_id": 66, "fixed_properties": {}, "tcg_player_id": None},
         {"id": 501, "name": "Bulbasaur", "category_id": 73, "tcg_player_id": None,
          "fixed_properties": {"collector_number": "001", "pokemon_rarity": "Common"}},
         {"id": 502, "name": "Ivysaur", "category_id": 73, "tcg_player_id": 77,
          "fixed_properties": {"collector_number": "2", "pokemon_rarity": "Uncommon"}}],
    11: [{"id": 601, "name": "Bulbasaur", "category_id": 73, "fixed_properties": {"collector_number": "1"}}],
}


def _listing(cents, condition="Near Mint", language="en", currency="USD", graded=False, sealed=False, reverse=False):
    props = {"condition": condition, "pokemon_language": language, "pokemon_reverse": reverse, "first_edition": False}
    if sealed:
        props = {"sealed": True, "pokemon_language": language}
    return {"price": {"cents": cents, "currency": currency}, "properties_hash": props, "graded": graded}


LISTINGS = {
    10: {"500": [_listing(2895, sealed=True)],
         "501": [_listing(90, "Played"), _listing(150), _listing(120, language="fr"), _listing(100, graded=True),
                 _listing(110, reverse=True), _listing(400, "Played", reverse=True)],
         "502": [_listing(300, "Slightly Played"), _listing(250, "Moderately Played")]},
    11: {"601": [_listing(800)]},
}


class FakeCardTrader:
    def __init__(self, refuse=False):
        self.asked, self.refuse = [], refuse

    def __call__(self, path, params=None):
        self.asked.append((path, dict(params or {})))
        if self.refuse:
            raise cardtrader.Refused("CardTrader answered 401")
        if path == "/expansions":
            return EXPANSIONS
        if path == "/blueprints/export":
            return BLUEPRINTS.get(params["expansion_id"], [])
        if path == "/marketplace/products":
            return LISTINGS.get(params["expansion_id"], {})
        raise AssertionError(path)


# --- no page reading -----------------------------------------------------------------------------------

def test_tcgplayer_and_pricecharting_pages_are_never_requested():
    sent = []
    transport = httpx.MockTransport(lambda request: sent.append(str(request.url)) or httpx.Response(200))
    with sites.client(transport=transport, follow_redirects=True) as c:
        for url in ("https://www.pricecharting.com/game/pokemon-x/y", "https://pricecharting.com/console/x",
                    "https://storage.googleapis.com/images.pricecharting.com/a/240.jpg",
                    "https://www.tcgplayer.com/product/5", "https://mp-search-api.tcgplayer.com/v1/search/request"):
            with pytest.raises(sites.Blocked):
                c.get(url)
        c.get("https://tcgplayer-cdn.tcgplayer.com/product/5_in_400x400.jpg")  # product pictures, from TCGCSV
    assert sent == ["https://tcgplayer-cdn.tcgplayer.com/product/5_in_400x400.jpg"]
    assert "tcgplayer" not in sites.SITES and "pricecharting" not in sites.SITES


def test_the_page_reading_code_is_gone():
    assert not (APP / "finder.py").exists() and not (APP / "pacing.py").exists()
    src = "".join(p.read_text() for p in APP.rglob("*.py"))
    for name in ("read_pricecharting", "read_tcgplayer", "check_link", "refresh_links", "mp-search-api", "link_finder"):
        assert name not in src, name
    templates = "".join(p.read_text() for p in (APP / "templates").glob("*.html"))
    assert "Links to confirm" not in templates and "Possible match found" not in templates
    assert "Update prices and links" not in templates


def test_old_pricecharting_pictures_are_not_shown_but_tcgplayer_ones_are():
    pics = [("https://storage.googleapis.com/images.pricecharting.com/a/240.jpg", "Picture from your link"),
            ("https://tcgplayer-cdn.tcgplayer.com/product/1_in_400x400.jpg", "TCGplayer picture")]
    assert sites.keep(pics) == pics[1:]


def test_search_buttons_always_show_and_only_open_the_site(client):
    _setup_with_set(client)
    page = client.get("/cards/t1-2").text
    assert "Search PriceCharting &#8599;" in page and "off in Data sources" not in page
    assert "never reads the page" in page


# --- the PriceCharting price you type --------------------------------------------------------------------

def test_pasting_a_pricecharting_link_can_bring_its_price(client):
    user_id = _setup_with_set(client)
    _entry(client, "t1", "2")
    page = client.get("/cards/t1-2").text
    assert 'id="search-pricecharting"' in page and 'name="pc_price"' in page
    r = client.post("/cards/t1-2/link/pricecharting",
                    data={"url": "https://www.pricecharting.com/game/pokemon-test-set/ivysaur-2", "pc_price": "$12.50"})
    assert "Saved the PriceCharting link." in r.text
    [typed] = extras.typed_rows(main.conn, "t1-2")  # round 20: for the printing you own
    assert (typed["variant"], typed["kind"], typed["price"], typed["currency"], typed["was_read"]) == \
        ("normal", "pricecharting", 12.5, "USD", 0)
    # It shows beside the other prices, and is the price only when there's nothing better.
    page = client.get("/cards/t1-2").text
    assert "PriceCharting" in page and "$12.50" in page and "typed by you" in page
    assert 'name="pc_price" value="12.50"' in page
    # A bad amount is refused; empty removes it.
    r = client.post("/cards/t1-2/extras", data={"pc_form": "1", "pc_price": "lots"})
    assert "isn&#39;t a price" in r.text
    client.post("/cards/t1-2/extras", data={"pc_form": "1", "pc_price": ""})
    assert extras.typed_rows(main.conn, "t1-2") == []
    assert user_id


def test_a_price_without_a_pricecharting_link_is_ignored(client):
    _setup_with_set(client)
    client.post("/cards/t1-1/link/tcgplayer", data={"url": "https://www.tcgplayer.com/product/5", "pc_price": "9"})
    assert extras.typed_rows(main.conn, "t1-1") == []


def test_a_pricecharting_price_over_90_days_old_is_listed_to_review(client):
    user_id = _setup_with_set(client)
    _entry(client, "t1", "1", "masterball")  # no TCGdex price for this printing
    extras.save(main.conn, "t1-1", user_id, pricecharting_url="https://www.pricecharting.com/game/p/bulbasaur-1", pc_price=3)
    assert not [r for r in home.to_review(main.conn, user_id) if r["id"] == "t1-1"]
    main.conn.execute("UPDATE typed_prices SET typed_at = '2026-01-01T00:00:00Z' WHERE card_id = 't1-1'")
    main.conn.commit()
    assert [r["reason"] for r in home.to_review(main.conn, user_id) if r["id"] == "t1-1"] == ["Price over 90 days old"]
    assert "Price over 90 days old" in client.get("/review").text


# --- database step 6 -----------------------------------------------------------------------------------

# Round 15d: the prices table before database step 9 (the shown price marked in currency) and CardTrader's own.
OLD_PRICES = """DROP TABLE main.prices;
    CREATE TABLE main.prices (card_id TEXT NOT NULL REFERENCES cards(id), variant TEXT NOT NULL, source TEXT NOT NULL,
        price REAL NOT NULL, currency TEXT NOT NULL, updated_at TEXT NOT NULL, basis TEXT, low REAL, mid REAL,
        PRIMARY KEY (card_id, variant, source));"""
OLD_CT_PRICES = """CREATE TABLE {schema}.ct_prices (card_id TEXT NOT NULL, variant TEXT NOT NULL, price REAL NOT NULL,
        currency TEXT NOT NULL, any_condition INTEGER NOT NULL DEFAULT 0, blueprint_id INTEGER, updated_at TEXT NOT NULL,
        PRIMARY KEY (card_id, variant));"""


def round14_db(path):
    """A database as round 14 left it: today's tables, plus what database step 7 drops, at step 5."""
    conn = db.connect(path)
    db.init(conn)
    conn.executescript(OLD_PRICES + "DROP TABLE catalog.price_quotes;" + OLD_CT_PRICES.format(schema="main"))
    for column, kind in (("link_price", "REAL"), ("link_source", "TEXT"), ("link_checked_at", "TEXT"), ("link_error", "TEXT"),
                         ("link_image", "TEXT"), ("pc_image", "TEXT"), ("pc_price", "REAL"), ("pc_checked_at", "TEXT"),
                         ("pc_tried_at", "TEXT")):
        conn.execute(f"ALTER TABLE card_extras ADD COLUMN {column} {kind}")
    conn.executescript("""
        CREATE TABLE found_links (card_id TEXT NOT NULL, site TEXT NOT NULL, status TEXT NOT NULL, url TEXT, product_id INTEGER,
            price REAL, image TEXT, name TEXT, found_at TEXT NOT NULL, read_at TEXT, asked_at TEXT, visited_at TEXT,
            PRIMARY KEY (card_id, site));
        CREATE TABLE english_prices (card_id TEXT NOT NULL, variant TEXT NOT NULL, price REAL NOT NULL, english_id TEXT NOT NULL,
            updated_at TEXT NOT NULL, PRIMARY KEY (card_id, variant));
        CREATE TABLE english_misses (card_id TEXT PRIMARY KEY, english_id TEXT NOT NULL, checked_at TEXT NOT NULL);
        CREATE TABLE pc_consoles (set_id TEXT PRIMARY KEY, console TEXT NOT NULL, ok INTEGER NOT NULL, checked_at TEXT NOT NULL);
        PRAGMA user_version = 5;""")
    return conn


def test_step_6_copies_prices_read_before_with_their_date(tmp_path):
    conn = round14_db(tmp_path / "r14.db")
    conn.execute("INSERT INTO sets (set_id, name, printed_total, total) VALUES ('s', 'S', 9, 9)")
    for n in range(1, 6):
        conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name) VALUES (?, 's', ?, ?, 1, 'x')",
                     (f"s-{n}", str(n), n))
    pc = "https://www.pricecharting.com/game/p/x-"
    conn.executemany(
        "INSERT INTO card_extras (card_id, pricecharting_url, pc_price, pc_checked_at, link_price, link_source, "
        "link_checked_at, manual_price, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-10-01')",
        [("s-1", pc + "1", 7.5, "2026-10-12T05:00:00+00:00", 2.0, "tcgplayer", "2026-10-11T05:00:00+00:00", None),
         ("s-2", pc + "2", None, None, 4.25, "pricecharting", "2026-10-10T05:00:00+00:00", 3.0),
         ("s-3", None, None, None, 1.0, "tcgplayer", "2026-10-10T05:00:00+00:00", None)])
    conn.executemany(
        "INSERT INTO found_links (card_id, site, status, url, price, found_at, read_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [("s-4", "pricecharting", "sure", pc + "4", 6.0, "2026-09-01", "2026-10-09T05:00:00+00:00"),
         ("s-5", "pricecharting", "rejected", pc + "5", 9.0, "2026-09-01", None),
         ("s-1", "pricecharting", "sure", pc + "1b", 99.0, "2026-09-01", None)])
    conn.executemany("INSERT INTO picture_lookups (card_id, asked_at, looked_at, url, source, found) VALUES (?, '2026', '2026', ?, ?, 1)",
                     [("s-1", "https://storage.googleapis.com/images.pricecharting.com/a/240.jpg", "PriceCharting picture"),
                      ("s-2", "https://tcgplayer-cdn.tcgplayer.com/product/1_in_400x400.jpg", "TCGplayer picture")])
    before = {r["card_id"]: dict(r) for r in conn.execute("SELECT * FROM card_extras")}
    conn.commit()
    with db.transaction(conn):  # step 6 alone (step 7 drops the old columns this test compares)
        db._step_6(conn)
    rows = {r["card_id"]: dict(r) for r in conn.execute("SELECT * FROM card_extras")}
    pick = lambda r: (r["pc_typed"], r["pc_typed_at"], r["pc_typed_read"], r["pricecharting_url"])  # noqa: E731
    assert pick(rows["s-1"]) == (7.5, "2026-10-12T05:00:00+00:00", 1, pc + "1")  # your link wins over a found one
    assert pick(rows["s-2"]) == (4.25, "2026-10-10T05:00:00+00:00", 1, pc + "2")
    assert rows["s-3"]["pc_typed"] is None  # a TCGplayer price read before isn't PriceCharting's
    assert pick(rows["s-4"]) == (6.0, "2026-10-09T05:00:00+00:00", 1, pc + "4")
    assert "s-5" not in rows  # a rejected guess isn't copied
    for card_id, old in before.items():  # the old columns are left as they were
        assert {k: rows[card_id][k] for k in old if not k.startswith("pc_typed")} == \
            {k: v for k, v in old.items() if not k.startswith("pc_typed")}
    assert [r[0] for r in conn.execute("SELECT card_id FROM picture_lookups")] == ["s-2"]  # looked up again
    # Round 20: step 15 moves it to typed_prices; a price last read shows its date.
    line = {"origin": prices.PRICECHARTING, "basis": "read", "updated_at": rows["s-4"]["pc_typed_at"]}
    assert prices.source_of({"origin": prices.PRICECHARTING, "sources": [line]})["label"] == \
        "PriceCharting · last read Oct 9, 2026"


# --- CardTrader -----------------------------------------------------------------------------------------

def _priced_setup(client, monkeypatch):
    user_id = _setup_with_set(client)
    _entry(client, "t1", "1", "pokeball")
    _entry(client, "t1", "2", "normal")
    monkeypatch.setenv(cardtrader.TOKEN_ENV, TOKEN)
    return user_id


def test_no_token_means_no_request(client, monkeypatch):
    _setup_with_set(client)
    monkeypatch.delenv(cardtrader.TOKEN_ENV, raising=False)
    monkeypatch.setattr(sites, "client", lambda **kw: (_ for _ in ()).throw(AssertionError("asked CardTrader")))
    assert cardtrader.run(main.conn) == (0, [])
    assert cardtrader.status(main.conn)["token"] is False
    assert "no key. Add one under" in client.get("/account/data").text  # round 28: API keys on Data sources


def test_cardtrader_lowest_near_mint_in_the_cards_language(client, monkeypatch):
    _priced_setup(client, monkeypatch)
    get = FakeCardTrader()
    count, errors = cardtrader.run(main.conn, get=get, sleep=lambda s: None)
    assert (count, errors) == (1, [])
    rows = {(r["card_id"], r["variant"]): (r["price"], int(r["basis"] == "any"))
            for r in main.conn.execute("SELECT * FROM price_quotes WHERE source = 'cardtrader'")}
    # Bulbasaur: $1.50 Near Mint (the $0.90 is Played, the French and graded ones don't count); its Poké Ball
    # printing is its own expansion. Round 20b: Ivysaur has no Near Mint copy, so no CardTrader price.
    # Its reverse holo listings are their own printing (CardTrader marks them pokemon_reverse).
    assert rows == {("t1-1", ""): (1.5, 0), ("t1-1", "reverse"): (1.1, 0), ("t1-1", "pokeball"): (8.0, 0)}
    assert all(p[0] != "/marketplace/products" or p[1].get("language") == "en" for p in get.asked)
    with db.transaction(main.conn):  # the price update does this next
        prices.apply_all(main.conn)
    # The prices fill the printings TCGplayer has none for.
    p = {(r["card_id"], r["variant"]): (r["price"], r["origin"], r["basis"])
         for r in main.conn.execute("SELECT * FROM prices WHERE origin = ?", (prices.CARDTRADER,))}
    assert p[("t1-1", "pokeball")] == (8.0, prices.CARDTRADER, "nm") and ("t1-2", "normal") not in p
    # The second run asks for listings only: expansions and cards are kept a week.
    get.asked.clear()
    cardtrader.run(main.conn, get=get, sleep=lambda s: None)
    assert {p for p, _ in get.asked} == {"/marketplace/products"}


def test_cardtrader_shows_beside_tcgplayer_and_doesnt_replace_it(client, monkeypatch):
    from test_app import FakePricedAPI
    monkeypatch.setattr(web, "api", FakePricedAPI())
    _setup_with_set(client)
    _entry(client, "t1", "1")  # $0.18 on TCGplayer (FakeAPI)
    monkeypatch.setenv(cardtrader.TOKEN_ENV, TOKEN)
    cardtrader.run(main.conn, get=FakeCardTrader(), sleep=lambda s: None)
    with db.transaction(main.conn):
        prices.apply_all(main.conn)
    row = main.conn.execute("SELECT price, origin FROM prices WHERE card_id = 't1-1' AND variant = 'normal'").fetchone()
    assert (row["price"], row["origin"]) == (0.84, "tcgplayer")  # round 18: the average of $0.18 and $1.50
    page = client.get("/cards/t1-1").text
    assert "$1.50" in page and "Near Mint median" in page and "$0.18 – $1.50" in page


def test_a_refused_token_says_so_and_other_prices_keep_working(client, monkeypatch, caplog):
    _priced_setup(client, monkeypatch)
    caplog.set_level(logging.INFO)
    count, errors = cardtrader.run(main.conn, get=FakeCardTrader(refuse=True), sleep=lambda s: None)
    assert count == 0 and errors == ["CardTrader refused the token. Make a new one on CardTrader and put it in .env."]
    assert cardtrader.status(main.conn)["refused"]
    assert "CardTrader refused your token" in client.get("/account/data").text
    assert TOKEN not in caplog.text and TOKEN not in json.dumps(errors)


def test_the_token_only_goes_in_the_header(monkeypatch):
    monkeypatch.setenv(cardtrader.TOKEN_ENV, TOKEN)
    seen = []

    def answer(request):
        seen.append(request)
        return httpx.Response(401 if len(seen) == 1 else 200, json=[])
    real = sites.client
    monkeypatch.setattr(sites, "client", lambda **kw: real(transport=httpx.MockTransport(answer), **kw))
    with pytest.raises(cardtrader.Refused) as e:
        cardtrader.fetch("/info")
    assert TOKEN not in str(e.value)
    assert cardtrader.fetch("/expansions") == []
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}" and TOKEN not in str(seen[0].url)
    assert str(seen[0].url).startswith("https://api.cardtrader.com/api/v2/")


def test_unmatched_sets_can_be_picked(client, monkeypatch):
    _priced_setup(client, monkeypatch)
    main.conn.execute("UPDATE sets SET name = 'Renamed' WHERE set_id = 't1'")
    other = [dict(e, code="zz") if e["id"] == 10 else e for e in EXPANSIONS]
    cardtrader.load_expansions(main.conn, lambda path, params=None: other)
    main.conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('cardtrader_expansions_at', '2999-01-01')")
    assert cardtrader.match_set(main.conn, "t1", "Renamed") is None
    main.conn.commit()
    page = client.get("/account/data").text
    assert "1 set not matched on CardTrader" in page and '<option value="10">Test Set (zz)</option>' in page
    r = client.post("/account/data/cardtrader/pick", data={"set_id": "t1", "expansion": "10"})
    assert "Its CardTrader prices come with the next price update." in r.text
    assert cardtrader.match_set(main.conn, "t1", "Renamed") == 10  # a pick is kept


def test_a_reverse_holo_without_its_own_listing_gets_no_cardtrader_price(client, monkeypatch):
    _setup_with_set(client)
    _entry(client, "t1", "2", "reverse")  # Ivysaur has no reverse holo listing
    monkeypatch.setenv(cardtrader.TOKEN_ENV, TOKEN)
    cardtrader.run(main.conn, get=FakeCardTrader(), sleep=lambda s: None)
    with db.transaction(main.conn):
        prices.apply_all(main.conn)
    assert not main.conn.execute("SELECT 1 FROM prices WHERE card_id = 't1-2' AND variant = 'reverse' AND origin = ?",
                                 (prices.CARDTRADER,)).fetchone()
    assert cardtrader.side_line(main.conn, "t1-2", "reverse") is None
    assert cardtrader.side_line(main.conn, "t1-2", "normal") is None  # round 20b: no Near Mint copy, no price


def test_a_tcgplayer_product_id_matches_before_the_number():
    blueprints = [{"blueprint_id": 1, "number": "5", "name": "A", "tcgplayer_id": None},
                  {"blueprint_id": 2, "number": "105", "name": "A", "tcgplayer_id": 589939}]
    assert cardtrader._blueprint_for(blueprints, {"number": "5", "name": "A", "tcgplayer_id": 589939}) == 2
    assert cardtrader._blueprint_for(blueprints, {"number": "005", "name": "A", "tcgplayer_id": None}) == 1


def test_codes_for_chinese_and_japanese_sets():
    assert cardtrader._codes("chs:CBB4C") == ["cbb4c", "cbb4"]
    assert cardtrader._codes("chs:CSV7C") == ["csv7c", "csv7"]
    assert cardtrader._codes("ja:SV8a") == ["sv8a"]
    assert cardtrader._codes("zh-cn:CSV7C") == ["csv7c", "csv7"]  # the ids Chinese sets really have
    assert cardtrader._codes("zh-cn:CS2.5C") == ["cs2.5c", "cs2.5", "cs25"]  # CardTrader: cs25, but csv9.5
    assert cardtrader._codes("zh-cn:CSVE1C1") == ["csve1c1", "csve1"]  # a half of Battle Party
    assert cardtrader._codes("svp") == ["svpromo", "svp"]
    assert cardtrader._number("245/204") == "245" and cardtrader._number("001") == "1" and cardtrader._number("TG01") == "tg01"
    assert cardtrader.near_mint([_listing(500, language="zh-CN")], "zh-cn") == (5.0, "USD", False)
    assert cardtrader.near_mint([_listing(500, language="zh-CN")], "en") is None


def test_a_euro_listing_is_turned_into_dollars(client, monkeypatch):
    _priced_setup(client, monkeypatch)
    main.conn.execute("INSERT INTO price_quotes (card_id, variant, source, price, currency, basis, updated_at) "
                      "VALUES ('t1-2', '', 'cardtrader', 2.0, 'EUR', 'nm', '2026-10-26')")
    from app import fx
    monkeypatch.setattr(fx, "to_usd", lambda amount, currency: amount * 1.1 if currency == "EUR" else amount)
    monkeypatch.setattr(fx, "in_usd", lambda amount, currency: (round(amount * 1.1, 2), 1.1) if currency == "EUR" else (amount, 1.0))
    with db.transaction(main.conn):
        prices.resolve(main.conn, "t1-2")
    assert {(r["variant"], r["price"], r["origin"]) for r in prices.card_prices(main.conn, "t1-2")} == {("normal", 2.2, "cardtrader")}
    assert cardtrader.side_line(main.conn, "t1-2")["price"] == 2.2


def test_the_scanner_container_doesnt_get_the_token():
    compose = (APP.parent / "docker-compose.yml").read_text()
    scanner = compose[compose.index("container_name: dexstash-scanner"):]
    assert re.search(r"DEXSTASH_CARDTRADER_TOKEN: \"\"", scanner)
    # Round 28: keys are entered in the app, not .env (an old .env token still works and is moved in once).
    assert "DEXSTASH_CARDTRADER_TOKEN=" not in (APP.parent / ".env.example").read_text()


def test_version_is_0151_or_later():
    from app import __version__
    assert tuple(map(int, __version__.split("."))) >= (0, 15, 1)


def test_page_fills_the_screen_so_bottom_bars_stay_down():
    # iPhone home-screen app: on a short page, fixed bars floated up to the end of the content.
    css = (APP / "static" / "css" / "base.css").read_text()
    assert "html { -webkit-text-size-adjust: 100%; min-height:100%; }" in css
    assert "body { min-height:100vh; }" in css


def test_cardtrader_skips_a_parked_listing_far_above_tcgplayer(client):
    # Her first run: a Slowpoke at $12,648.90 on CardTrader, $0.49 on TCGplayer.
    _setup_with_set(client)
    with db.transaction(main.conn):
        main.conn.execute("INSERT INTO price_quotes (card_id, variant, source, price, currency, updated_at) "
                          "VALUES ('t1-1', 'normal', 'tcgplayer', 0.49, 'USD', '2026-10-26')")
    assert not cardtrader._believable(main.conn, "t1-1", "", 12648.90)
    assert cardtrader._believable(main.conn, "t1-1", "", 5.0)  # 10x: kept
    assert cardtrader._believable(main.conn, "t1-1", "", 15.0)  # 30x but only $14.51 more: kept
    assert cardtrader._believable(main.conn, "t1-1", "reverse", 500.0)  # no TCGplayer price for that printing
    assert cardtrader._believable(main.conn, "t1-2", "", 500.0)  # nor for that card


def test_cardtrader_matches_ex_sets_galleries_and_retries_unmatched(client):
    # From her first run: sets CardTrader names its own way, and galleries kept in the main set there.
    _setup_with_set(client)
    conn = main.conn
    with db.transaction(conn):
        for set_id, name in (("ex11", "Delta Species"), ("swsh9", "Brilliant Stars"), ("swsh9tg", "Brilliant Stars Trainer Gallery"),
                             ("swsh10.5", "Pokémon GO")):
            conn.execute("INSERT INTO sets (set_id, name, printed_total, total) VALUES (?, ?, 1, 1)", (set_id, name))
        conn.executemany("INSERT INTO ct_expansions (id, code, name, fetched_at) VALUES (?, ?, ?, 'x')",
                         [(1508, "ds", "EX Delta Species"), (2911, "brs", "Brilliant Stars"), (3058, "pkmgo", "Pokémon TCG: Pokémon GO")])
    assert cardtrader.match_set(conn, "ex11", "Delta Species") == 1508
    assert cardtrader.match_set(conn, "swsh9tg", "Brilliant Stars Trainer Gallery") == 2911
    assert cardtrader.match_set(conn, "swsh10.5", "Pokémon GO") == 3058
    # A set left unmatched is looked for again next time (CardTrader may add it), not after a week.
    assert cardtrader.match_set(conn, "t1", "Test Set") is None
    with db.transaction(conn):
        conn.execute("INSERT INTO ct_expansions (id, code, name, fetched_at) VALUES (9, 't1', 'Test Set', 'x')")
    assert cardtrader.match_set(conn, "t1", "Test Set") == 9
    # In the main set's expansion, a gallery card needs its name too: TG05 is only Pikachu if it's called Pikachu.
    blueprints = [{"blueprint_id": 1, "number": "TG05", "name": "Zeraora", "tcgplayer_id": None}]
    assert cardtrader._blueprint_for(blueprints, {"number": "TG05", "name": "Pikachu", "subset": True}) is None
    assert cardtrader._blueprint_for(blueprints, {"number": "TG05", "name": "Zeraora", "subset": True}) == 1
