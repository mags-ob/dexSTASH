"""Round 13 (23 Oct 2026)."""
import sys
import re

import pytest

from app import auth, main
from app import web
from app.routes import search as search_pages
from tests.test_app import client  # noqa: F401  (the test app, with no accounts yet)


# --- 2: the secret key is made for you ----------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="Linux only")
def test_with_no_key_set_one_is_made_once_and_kept(tmp_path, monkeypatch):
    monkeypatch.delenv("DEXSTASH_SECRET_KEY", raising=False)
    first = web._secret_key(tmp_path)
    assert len(first) >= 32
    assert (tmp_path / "secret_key").stat().st_mode & 0o077 == 0  # only the app's user can read it
    assert web._secret_key(tmp_path) == first  # the next start uses the same key


def test_a_key_in_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("DEXSTASH_SECRET_KEY", "k" * 40)
    assert web._secret_key(tmp_path) == "k" * 40
    assert not (tmp_path / "secret_key").exists()


@pytest.mark.parametrize("bad", ["short", "replace-me-with-a-long-random-value-please"])
def test_a_short_or_example_key_stops_the_app(tmp_path, monkeypatch, bad):
    monkeypatch.setenv("DEXSTASH_SECRET_KEY", bad)
    with pytest.raises(RuntimeError):
        web._secret_key(tmp_path)


# --- 3: the setup code ----------------------------------------------------------------------------------

def test_setup_needs_the_code_from_the_log(client):
    page = client.get("/setup").text
    assert 'name="code"' in page and "docker logs dexstash" in page
    r = client.post("/setup", data={"username": "misty", "password": "pikachu123"})
    assert r.status_code == 400 and auth.user_count(main.conn) == 0
    r = client.post("/setup", data={"code": "WRONGONE", "username": "misty", "password": "pikachu123"})
    assert r.status_code == 400 and "setup code isn" in r.text and auth.user_count(main.conn) == 0
    code = web.setup_code()
    assert len(code) == 8 and set(code) <= set(auth.SETUP_CODE_LETTERS)
    r = client.post("/setup", data={"code": code.lower()[:4] + " " + code.lower()[4:], "username": "misty",
                                    "password": "pikachu123"}, follow_redirects=False)
    assert r.status_code == 303 and auth.user_count(main.conn) == 1
    assert web.setup_code() is None  # once there's an account there's no code any more
    r = client.post("/setup", data={"code": code, "username": "rocket", "password": "pikachu123"})
    assert r.status_code == 403


def test_the_setup_code_stays_the_same_until_used(client):
    assert web.setup_code() == web.setup_code()


def test_wrong_setup_codes_lock_out_for_a_while(client):
    tries = [client.post("/setup", data={"code": "WRONGONE", "username": "misty", "password": "pikachu123"})
             for _ in range(10)]
    assert tries[-1].status_code == 429 and "Too many wrong codes" in tries[-1].text
    r = client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    assert r.status_code == 429 and auth.user_count(main.conn) == 0  # even the right code waits


def test_env_names_the_first_admin_once(client, monkeypatch):
    monkeypatch.setenv("DEXSTASH_ADMIN_USER", "misty")
    monkeypatch.setenv("DEXSTASH_ADMIN_PASSWORD", "pikachu123")
    web._first_account()
    user = auth.authenticate(main.conn, "misty", "pikachu123")
    assert user and user["is_admin"]
    monkeypatch.setenv("DEXSTASH_ADMIN_PASSWORD", "something-else")
    web._first_account()  # a later start never changes an existing account
    assert auth.authenticate(main.conn, "misty", "pikachu123") and auth.user_count(main.conn) == 1


def test_a_bad_env_admin_falls_back_to_the_setup_code(client, monkeypatch):
    monkeypatch.setenv("DEXSTASH_ADMIN_USER", "misty")
    monkeypatch.setenv("DEXSTASH_ADMIN_PASSWORD", "short")
    web._first_account()
    assert auth.user_count(main.conn) == 0 and web.setup_code()


# --- 1: ask for the link after a search -------------------------------------------------------------------

def test_a_search_tap_offers_the_link_box_on_a_card_with_no_link(client, monkeypatch):
    from test_app import _setup_with_set
    _setup_with_set(client)
    page = client.get("/cards/t1-1").text
    assert 'data-search-site="pricecharting"' in page and 'id="search-pricecharting"' in page
    assert "Found it on PriceCharting?" in page and "Paste a price link for this card" in page
    assert ">Not now</button>" in page and 'inputmode="url"' in page and "Paste</button>" not in page
    assert 'id="search-pricecharting" data-card="t1-1" data-open' not in page  # it opens on return, not on load
    r = client.post("/cards/t1-1/link/pricecharting", data={"url": "https://www.tcgplayer.com/product/5"},
                    follow_redirects=False)
    # Round 14: one box for any site, so a TCGplayer link pasted after a PriceCharting search is a TCGplayer link.
    assert "Saved%20the%20TCGplayer%20link." in r.headers["location"]
    assert web.extras.get(web.conn, "t1-1")["tcgplayer_url"] == "https://www.tcgplayer.com/product/5"
    r = client.post("/cards/t1-1/link/pricecharting", data={"url": "  "}, follow_redirects=False)
    assert "ask=pricecharting" in r.headers["location"] and "paste_error=" in r.headers["location"]
    page = client.get(r.headers["location"]).text
    assert 'id="search-pricecharting" data-card="t1-1" data-open' in page and "Paste the card&#39;s link first." in page
    r = client.post("/cards/t1-1/link/pricecharting",
                    data={"url": "https://www.pricecharting.com/game/pokemon-test-set/bulbasaur-1"})
    assert "Saved the PriceCharting link." in r.text
    assert 'id="search-pricecharting"' not in client.get("/cards/t1-1").text  # your own link: never asks
    assert 'name="pc_price"' in page  # round 15b: the price you saw there, optional


def test_a_link_saved_on_a_find_preview_keeps_the_card_not_an_inventory_row(client, monkeypatch):
    from app import catalog
    from test_app import FakeDetailAPI
    monkeypatch.setattr(web, "api", FakeDetailAPI())
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    page = client.get("/preview/en/old-1").text
    assert 'id="search-pricecharting"' in page and 'action="/preview/en/old-1/link/pricecharting"' in page
    assert 'id="search-tcgplayer"' not in page  # TCGdex gives this card's TCGplayer page
    r = client.post("/preview/en/old-1/link/pricecharting",
                    data={"set_id": "old", "url": "https://www.pricecharting.com/game/pokemon-base-set/alakazam-1"})
    assert "Saved the PriceCharting link." in r.text
    assert web.extras.get(main.conn, "old-1")["pricecharting_url"].endswith("alakazam-1")
    assert main.conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0] == 0
    uid = main.conn.execute("SELECT id FROM users").fetchone()[0]
    catalog.remove_set(main.conn, "old", uid)  # a set clean-up keeps a card with your link
    assert main.conn.execute("SELECT 1 FROM cards WHERE id = 'old-1'").fetchone()


# --- 4: time zone setting -----------------------------------------------------------------------------------

def _fresh(tmp_path):
    from app import db
    c = db.connect(tmp_path / "z.db")
    db.init(c)
    return c


@pytest.fixture
def zone_kept(monkeypatch):
    from app import display
    monkeypatch.setattr(display, "_zone", display.zone())  # whatever a test sets is undone after it
    for name in ("DISPLAY_TZ", "TZ"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_first_start_takes_the_zone_from_env_in_order(tmp_path, zone_kept):
    from app import display, region
    assert region.load(_fresh(tmp_path / "a")) == "UTC"
    zone_kept.setenv("TZ", "Europe/London")
    assert region.load(_fresh(tmp_path / "b")) == "Europe/London"
    zone_kept.setenv("DISPLAY_TZ", "Asia/Tokyo")
    c = _fresh(tmp_path / "c")
    assert region.load(c) == "Asia/Tokyo" and display.zone().key == "Asia/Tokyo"
    zone_kept.setenv("DISPLAY_TZ", "Australia/Sydney")
    assert region.load(c) == "Asia/Tokyo"  # after the first start, the setting counts
    assert "Australia/Sydney" in region.zone_note()


def test_an_existing_install_keeps_eastern(tmp_path, zone_kept):
    from app import region
    zone_kept.setenv("TZ", "Etc/UTC")
    c = _fresh(tmp_path)
    auth.create_user(c, "misty", "pikachu123", is_admin=True)
    assert region.load(c) == "America/New_York"


def test_the_zone_is_saved_from_data_sources(client, zone_kept):
    from app import display, region
    from test_app import _setup_with_set
    _setup_with_set(client)
    page = client.get("/account/data").text
    assert 'action="/account/data/zone"' in page and '<option value="Europe/London">' in page
    r = client.post("/account/data/zone", data={"zone": "Nowhere/Atlantis"})
    assert "Pick a time zone" in r.text
    client.post("/account/data/zone", data={"zone": "Europe/Berlin"})
    assert display.zone().key == "Europe/Berlin" and region._meta(main.conn, region.ZONE_KEY) == "Europe/Berlin"
    assert "Europe/Berlin time" in client.get("/account/data").text


def test_the_nightly_run_moves_with_the_zone(zone_kept, monkeypatch):
    from datetime import datetime, timezone
    from app import display, sync
    display.set_zone("America/New_York")
    utc = lambda day, h, m=0: datetime(2026, 10, day, h, m, tzinfo=timezone.utc)  # noqa: E731
    clock = [utc(9, 3)]  # 23:00 Eastern; tonight's 04:00 Eastern is 08:00 UTC
    runs = []
    nightly = sync.Nightly(None, now=lambda: clock[0].astimezone(display.zone()))
    monkeypatch.setattr(nightly, "run_once", lambda conn: runs.append(clock[0]))
    steps = iter([lambda: display.set_zone("Asia/Tokyo"),  # 12:00 in Tokyo: 04:00 Tokyo is 19:00 UTC
                  lambda: clock.__setitem__(0, utc(9, 8, 1)),  # Eastern's 04:00 passes: nothing runs
                  lambda: clock.__setitem__(0, utc(9, 19, 1))])

    def wait(seconds):
        step = next(steps, None)
        if step is None:
            return True
        step()
        return False
    monkeypatch.setattr(nightly._stop, "wait", wait)
    monkeypatch.setattr(sync, "nightly_settings", lambda conn: {"on": True, "time": "04:00", "zone_key": display.zone().key})
    monkeypatch.setattr(sync.db, "connect", lambda: type("C", (), {"close": lambda self: None})())
    nightly._loop()
    assert runs == [utc(9, 19, 1)]


# --- 5: currency ------------------------------------------------------------------------------------------------

ECB = """<Cube><Cube time='2026-10-22'><Cube currency='USD' rate='1.1000'/><Cube currency='JPY' rate='165.00'/>
<Cube currency='GBP' rate='0.8500'/><Cube currency='CAD' rate='1.5000'/></Cube></Cube>"""


@pytest.fixture
def money(client):
    from app import fx
    main.conn.execute("DELETE FROM app_meta WHERE key IN ('currency', 'ecb_rates')")
    main.conn.commit()
    fx.refresh(main.conn, get=lambda url: ECB, force=True)
    yield fx
    main.conn.execute("DELETE FROM app_meta WHERE key IN ('currency', 'ecb_rates', 'eur_usd', 'eur_usd_at')")
    main.conn.commit()
    fx.load(main.conn)


def test_every_currency_in_the_ecb_file_is_kept():
    from app import fx
    rates = fx.parse_all(ECB)
    assert rates == {"USD": 1.1, "JPY": 165.0, "GBP": 0.85, "CAD": 1.5} and fx.parse(ECB) == 1.1


def test_prices_convert_with_the_right_symbol_and_decimals(money):
    from app import display
    assert display.money(10) == "$10.00" and display.money(-3.1) == "-$3.10"
    money.save_currency(main.conn, "JPY")
    assert display.money(10) == "¥1,500" and display.money(10, "USD") == "$10.00"  # typed amounts show as typed
    money.save_currency(main.conn, "GBP")
    assert display.money(10) == "£7.73"
    assert money.to_usd(8.5, "GBP") == pytest.approx(11.0) and money.typed(11, "USD") == pytest.approx(8.5)


def test_with_no_rate_prices_stay_in_dollars(money):
    from app import display
    money.save_currency(main.conn, "SEK")  # not in this ECB file
    assert money.setting() == "SEK" and money.shown() == "USD" and display.money(10) == "$10.00"


def test_every_page_shows_the_chosen_currency(client, money):
    from test_app import _setup_with_set
    _setup_with_set(client)
    client.post("/cards/t1-1/adjust", data={"variant": "normal", "condition": "NM", "delta": "1"})
    main.conn.execute("INSERT OR REPLACE INTO prices (card_id, variant, origin, price, updated_at) "
                      "VALUES ('t1-1', 'normal', 'tcgplayer', 10.0, '2026-10-22')")
    main.conn.commit()
    pages = ["/", "/collection", "/value", "/chases", "/account", "/account/data", "/scans", "/search?q=bulba",
             "/cards/t1-1", "/sets/t1", "/spending", "/collection/binders"]
    for code, sign in (("EUR", "€"), ("JPY", "¥")):
        money.save_currency(main.conn, code)
        for page in pages:
            r = client.get(page)
            assert r.status_code == 200, (code, page)
        card = client.get("/cards/t1-1").text
        assert ("€9.09" if code == "EUR" else "¥1,500") in card and "$10.00" not in card
    assert "¥" in client.get("/collection").text


def test_typed_amounts_keep_their_currency_and_totals_convert(client, money):
    from app import spending
    from test_app import _setup_with_set
    _setup_with_set(client)
    cid = main.conn.execute("SELECT collection_id FROM users").fetchone()[0]
    client.post("/cards/t1-1/adjust", data={"variant": "normal", "condition": "NM", "delta": "1"})
    money.save_currency(main.conn, "GBP")
    spending.set_paid(main.conn, cid, "t1-1", "normal", "NM", 8.5)
    spending.create_purchase(main.conn, cid, "Booster", 11.0, currency="USD")
    money.save_currency(main.conn, "USD")
    assert "paid £8.50" in client.get("/cards/t1-1").text  # as typed
    totals = spending.totals(main.conn, cid)
    assert totals["singles"] == pytest.approx(11.0) and totals["total"] == pytest.approx(22.0)  # each converted
    page = client.get("/spending").text
    assert "£8.50" in page and "$22.00" in page
    spending.set_paid(main.conn, cid, "t1-1", "normal", "NM", 8.5)  # saved again unchanged: still pounds
    assert main.conn.execute("SELECT acquired_currency FROM inventory").fetchone()[0] == "GBP"
    spending.set_paid(main.conn, cid, "t1-1", "normal", "NM", 9)  # a new amount is in the currency now
    assert main.conn.execute("SELECT acquired_currency FROM inventory").fetchone()[0] == "USD"


def test_a_typed_card_price_counts_in_dollars(client, money):
    from app import extras
    from test_app import _setup_with_set
    uid = _setup_with_set(client)
    money.save_currency(main.conn, "JPY")
    extras.save(main.conn, "t1-1", uid, manual_price=1650, variant="normal")
    [typed] = extras.typed_rows(main.conn, "t1-1")
    assert (typed["variant"], typed["price"], typed["currency"]) == ("normal", 1650, "JPY")
    row = next(r for r in web.prices.card_prices(main.conn, "t1-1") if r["variant"] == "normal")
    assert (row["price"], row["origin"]) == (11.0, "manual")  # 1650 yen at 150 a dollar


def test_the_price_filter_follows_the_currency(money):
    from app import collection
    assert collection.price_ranges()["5"][0] == "$5–9.99"
    money.save_currency(main.conn, "JPY")
    ranges = collection.price_ranges()
    assert [ranges[k][0] for k in ("lt5", "5", "10", "25", "50")] == \
        ["Under ¥1,000", "¥1,000–¥1,999", "¥2,000–¥4,999", "¥5,000–¥9,999", "¥10,000+"]
    assert ranges["5"][1] == pytest.approx(1000 / 150) and collection.in_price_range(7.0, "5")


def test_the_currency_is_picked_in_data_sources(client, money):
    from test_app import _setup_with_set
    _setup_with_set(client)
    page = client.get("/account/data").text
    assert 'action="/account/data/currency"' in page and '<option value="CAD">' not in page or "Canadian dollar (CAD)" in page
    client.post("/account/data/currency", data={"currency": "CAD"})
    assert money.setting() == "CAD" and money.shown() == "CAD"
    assert "Pick a currency" in client.post("/account/data/currency", data={"currency": "XXX"}).text


def test_older_databases_get_the_currency_columns(tmp_path):
    from app import db
    c = db.connect(tmp_path / "old.db")
    db.init(c)
    for table, column in (("inventory", "acquired_currency"), ("purchases", "currency"), ("scans", "paid_currency"),
                          ("import_rows", "cost_currency"), ("card_extras", "manual_currency")):
        assert column in {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


# --- 6: licence and credits -------------------------------------------------------------------------------------

def test_licence_and_credits(client):
    from pathlib import Path
    from test_app import _setup_with_set
    root = Path(main.__file__).parent.parent
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in (root / "LICENSE").read_text()
    notice = (root / "NOTICE.md").read_text()
    for name in ("TCGdex", "PokéAPI", "PTCG-card-data", "CLIP", "Nunito", "htmx", "European Central Bank", "Bulbapedia"):
        assert name in notice
    assert "not affiliated" in (root / "README.md").read_text()
    _setup_with_set(client)
    page = client.get("/account").text
    assert 'id="about"' in page and "Affero" in page and "not affiliated" in page


# --- 8: My cards in chunks ----------------------------------------------------------------------------------------

def _many_cards(client, n):
    from test_app import _setup_with_set
    uid = _setup_with_set(client)
    cid = main.conn.execute("SELECT collection_id FROM users").fetchone()[0]
    main.conn.executemany("INSERT INTO cards (id, set_id, name, number, sort_number, is_main_set) VALUES (?, 't1', ?, ?, ?, 0)",
                          [(f"t1-x{i}", f"Card {i:03d}", f"x{i}", 1000 + i) for i in range(n)])
    main.conn.executemany("INSERT INTO inventory (user_id, card_id, variant, condition, quantity) VALUES (?, ?, 'normal', 'NM', 1)",
                          [(cid, f"t1-x{i}") for i in range(n)])
    main.conn.commit()
    return uid, cid


def test_my_cards_loads_in_chunks_and_select_all_means_every_match(client, monkeypatch):
    monkeypatch.setattr(web, "CARDS_CHUNK", 50)
    uid, cid = _many_cards(client, 120)
    page = client.get("/collection?sort=name").text
    assert page.count('class="mc-check"') == 50 and "Select all 120</a>" in page and "120 cards" in page
    more = re.search(r'hx-get="([^"]+)" hx-trigger="revealed"', page).group(1).replace("&amp;", "&")
    assert "sort=name" in more and "from=50" in more
    part = client.get(more).text
    assert part.count('class="mc-check"') == 50 and "<html" not in part and "from=100" in part
    last = client.get(re.search(r'hx-get="([^"]+)"', part).group(1).replace("&amp;", "&")).text
    assert last.count('class="mc-check"') == 20 and 'hx-trigger="revealed"' not in last
    assert "Card 000" in page and "Card 050" in part  # filters and sort carry on in later chunks
    r = client.post("/collection/bulk", data={"action": "note", "note": "binder 2", "all": "1", "back": "sort=name&q=Card"})
    assert r.status_code == 200
    assert main.conn.execute("SELECT COUNT(DISTINCT card_id) FROM notes").fetchone()[0] == 120


def test_select_all_inside_a_filter_only_takes_the_matches(client):
    uid, cid = _many_cards(client, 5)
    client.post("/collection/bulk", data={"action": "note", "note": "two", "all": "1", "back": "q=Card 002"})
    assert [r[0] for r in main.conn.execute("SELECT card_id FROM notes")] == ["t1-x2"]



# --- 9: tidy-ups ------------------------------------------------------------------------------------------------

def test_a_missed_night_waits_for_the_next_one(tmp_path, monkeypatch):
    """Round 23 (her pick, 30 Oct): no catch-up after a restart; the update runs at the next nightly time."""
    from datetime import datetime, timedelta, timezone
    from app import db, sync
    c = db.connect(tmp_path / "n.db")
    db.init(c)
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    sync._set_meta(c, sync.NIGHTLY_LAST_AT, sync.clock.stamp(now - timedelta(days=3)))
    assert not hasattr(sync, "missed")
    runs = []
    nightly = sync.Nightly(None, now=lambda: now)
    monkeypatch.setattr(nightly, "run_once", lambda conn: runs.append(1))
    waits = iter([False, False, True])
    monkeypatch.setattr(nightly._stop, "wait", lambda seconds: next(waits))
    monkeypatch.setattr(sync.db, "connect", lambda: c)
    nightly._loop()
    assert runs == []


def test_new_indexes_and_find_pages_lock():
    from app import db
    names = {r[0] for r in main.conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"scans_rev", "binder_copies_binder"} <= names and db.LATEST >= 4
    assert hasattr(search_pages._find_pages, "_lock")
