import io
import json
import re
import sqlite3
import time
from conftest import template_with_assets, with_assets  # noqa: E402

import pytest
from fastapi.testclient import TestClient

from app import auth, backup, binders, catalog, chinese, collection, collectr, db, importer, inventory, main, notes, prices, scanner, scans, search, spending, sync, wants
from app import sites, web
from app.routes import collection as collection_pages, scans as scans_pages
from app.names import Names
from app import tcgdex as tcgdex_client
from app import home as home_page

tcgdex_client.RETRY_PAUSE = 0  # tests never wait for the retry pause
from app.numbers import parse_number


@pytest.mark.parametrize("number,expected", [
    ("125", (125, True)),
    ("001", (1, True)),
    ("197", (197, True)),
    ("198", (198, False)),     # numbered secret rare
    ("230", (230, False)),
    ("TG05", (10_005, False)), # Trainer Gallery
    ("GG01", (10_001, False)), # Galarian Gallery
    ("SV049", (10_049, False)),
    ("177a", (10_177, False)),
])
def test_parse_number(number, expected):
    assert parse_number(number, 197) == expected


class FakeAPI:
    """Stands in for TCGdex with a tiny 3-card set: two main cards and one secret."""

    def get_set(self, set_id):
        return {
            "id": set_id, "name": "Test Set", "releaseDate": "2026-01-01",
            "serie": {"name": "Test"}, "cardCount": {"official": 2, "total": 3},
            "cards": [
                {"id": f"{set_id}-1", "localId": "1", "name": "Bulbasaur", "image": None},
                {"id": f"{set_id}-2", "localId": "2", "name": "Ivysaur", "image": None},
                {"id": f"{set_id}-TG01", "localId": "TG01", "name": "Venusaur", "image": None},
            ],
        }

    def get_card(self, card_id):
        return {"id": card_id, "rarity": "Common", "variants": {"normal": True, "reverse": True}}

    def list_sets(self):
        return [{"id": "t1", "name": "Test Set", "cardCount": {"official": 2, "total": 3}}]

    def search_cards(self, local_id=None, name=None):
        return []


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(web.tcgdex, "_down_until", 0.0)  # reliability round: TCGdex counts as up again
    scans_pages.tile_prices.wait()  # a batch tile's price from the test before
    monkeypatch.setattr(scans_pages, "tile_prices", scans_pages.TilePrices())
    monkeypatch.setattr(web, "api", FakeAPI())
    monkeypatch.setattr(web, "lockout", auth.Lockout())
    # Round 23: one jobs table; the test before's jobs end, then go (a scanner job nobody runs here would stay)
    for _ in range(500):
        if not main.conn.execute("SELECT 1 FROM jobs WHERE runs_in = 'app' AND status IN ('waiting', 'running')").fetchone():
            break
        time.sleep(0.02)
    main.conn.execute("DELETE FROM jobs")
    main.conn.commit()
    web.background._live.clear()
    main.conn.executescript("DELETE FROM scan_matches; DELETE FROM app_meta WHERE key = 'names_shown'; "  # round 27
                            "DELETE FROM ct_sets; DELETE FROM ct_expansions;")
    __import__("app.english").english.changed()
    main.conn.executescript("DELETE FROM datasets; DELETE FROM scans; DELETE FROM scan_batches; DELETE FROM import_rows; DELETE FROM import_batches; DELETE FROM import_map; "
                            "DELETE FROM notes; DELETE FROM inventory_log; "
                            "DELETE FROM pulls; DELETE FROM purchases; DELETE FROM wants; DELETE FROM inventory; "
                            "DELETE FROM prices; DELETE FROM price_snapshots; "
                            "DELETE FROM cards; DELETE FROM sets; DELETE FROM users; "
                            "DELETE FROM pokemon_palette; DELETE FROM user_theme; DELETE FROM daily_pokemon; DELETE FROM theme_settings; DELETE FROM user_prefs; "
                            "DELETE FROM card_extras; DELETE FROM typed_prices; DELETE FROM app_meta WHERE key LIKE 'nightly%'; "
                            "DELETE FROM followed_pokemon; DELETE FROM pokemon_cards; DELETE FROM art_cards; DELETE FROM arts; "
                            "DELETE FROM art_fixes; DELETE FROM chase_links; DELETE FROM chase_photos; "
                            "DELETE FROM binder_copies; DELETE FROM binders; DELETE FROM card_views; DELETE FROM followed_pictures; DELETE FROM picture_lookups; "
                            "DELETE FROM tcgcsv_groups; DELETE FROM tcgcsv_sets; DELETE FROM tcgcsv_cards; DELETE FROM tcgcsv_prices; DELETE FROM tcgcsv_products; DELETE FROM tcgcsv_group_reads; "
                            "DELETE FROM tcgcsv_product_prices; "
                            "DELETE FROM ct_expansions; DELETE FROM ct_sets; DELETE FROM ct_blueprints; DELETE FROM price_quotes; "
                            "DELETE FROM app_meta WHERE key LIKE 'tcgcsv%' OR key LIKE 'eur_usd%' OR key LIKE 'cardtrader%' "
                            "OR key IN ('updates_checked_at', 'typed_guessed', 'pending_price_sets') "
                            "OR key IN ('setup_done', 'setup_step', 'setup_choices', 'getting_started');")
    return TestClient(main.app)


def test_redirects_to_setup_then_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.headers["location"] == "/setup"
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    client.post("/logout")
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"


def test_setup_only_once(client):
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    r = client.post("/setup", data={"code": web.setup_code(), "username": "intruder", "password": "pikachu123"})
    assert r.status_code == 403


def test_bad_password_rejected(client):
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    client.post("/logout")
    r = client.post("/login", data={"username": "misty", "password": "wrong-password"})
    assert r.status_code == 401


def test_completion_ignores_subset_numbers(client):
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    r = client.post("/sets", data={"set_id": "t1"})
    assert r.status_code == 200 and "Test Set" in r.text

    user_id = main.conn.execute("SELECT id FROM users").fetchone()[0]
    # Two copies of #1 in different conditions, plus the TG01 subset card.
    main.conn.executemany(
        "INSERT INTO inventory (user_id, card_id, variant, condition, quantity) VALUES (?, ?, ?, ?, ?)",
        [(user_id, "t1-1", "normal", "NM", 2), (user_id, "t1-1", "reverse", "LP", 1),
         (user_id, "t1-TG01", "normal", "NM", 1)],
    )
    [s] = catalog.set_completion(main.conn, user_id)
    assert (s["owned_main"], s["printed_total"], s["percent"]) == (1, 2, 50)
    assert (s["owned_extra"], s["extra_total"]) == (1, 1)


def test_password_hashing():
    stored = auth.hash_password("pikachu123")
    assert auth.verify_password("pikachu123", stored)
    assert not auth.verify_password("raichu123", stored)


@pytest.mark.parametrize("text,expected", [
    ("123", ("123", 1, False)),
    (" 045 x3 ", ("045", 3, False)),
    ("TG05", ("TG05", 1, False)),
    ("177a×2", ("177a", 2, False)),
    ("-12", ("12", 1, True)),
])
def test_parse_entry(text, expected):
    e = inventory.parse_entry(text)
    assert (e.number, e.quantity, e.remove) == expected


@pytest.mark.parametrize("text", ["", "abc", "12 x0", "12 x100", "12 34"])
def test_parse_entry_rejects(text):
    assert inventory.parse_entry(text) is None


def _entry(client, set_id, entry, variant="normal", condition="NM", origin=""):
    """Add or remove by number, the way the set page's quick add did (removed in round 5): '1 x2', '-1'."""
    parsed = inventory.parse_entry(entry)
    card = inventory.find_card(main.conn, set_id, parsed.number)
    return client.post(f"/cards/{card['id']}/adjust", data={
        "variant": variant, "condition": condition, "origin": origin,
        "delta": str(-parsed.quantity if parsed.remove else parsed.quantity)})


def _typed(card_id, kind="manual"):
    """Round 20: the prices typed for a card, by printing."""
    return {r[0]: r[1] for r in main.conn.execute("SELECT variant, price FROM typed_prices WHERE card_id = ? AND kind = ?",
                                                  (card_id, kind))}


def _setup_with_set(client):
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    client.post("/sets", data={"set_id": "t1"})
    return main.conn.execute("SELECT id FROM users").fetchone()[0]


def test_typed_entry_adds_and_removes(client):
    user_id = _setup_with_set(client)
    _entry(client, "t1", "1 x2", "reverse")
    assert [s["owned_main"] for s in catalog.set_completion(main.conn, user_id)] == [1]

    _entry(client, "t1", "01", "reverse")  # leading zero
    _entry(client, "t1", "tg01", "normal", "LP")  # lower case
    assert {(h["variant"], h["condition"], h["quantity"]) for h in inventory.holdings(main.conn, user_id, "t1-1")} \
        == {("reverse", "NM", 3)}
    assert inventory.holdings(main.conn, user_id, "t1-TG01")[0]["quantity"] == 1

    _entry(client, "t1", "-1 x3", "reverse")
    assert inventory.holdings(main.conn, user_id, "t1-1") == []


def test_card_page_adjust(client):
    user_id = _setup_with_set(client)
    client.post("/cards/t1-2/adjust", data={"variant": "pokeball", "condition": "NM", "delta": "1"})
    client.post("/cards/t1-2/adjust", data={"variant": "pokeball", "condition": "NM", "delta": "1"})
    page = client.get("/cards/t1-2").text
    assert "Poké Ball Reverse, Near Mint" in page
    client.post("/cards/t1-2/adjust", data={"variant": "pokeball", "condition": "NM", "delta": "-2"})
    assert inventory.holdings(main.conn, user_id, "t1-2") == []
    assert client.post("/cards/t1-2/adjust", data={"variant": "bogus", "condition": "NM", "delta": "1"}).status_code == 400


class FakeSearchAPI(FakeAPI):
    """Adds the whole-catalogue calls: two older sets and t1, each with a #1."""

    def list_sets(self):
        return [
            {"id": "old", "name": "Old Set", "cardCount": {"official": 102, "total": 102}},
            {"id": "sv03.5", "name": "Dotted Set", "cardCount": {"official": 165, "total": 207}},
            {"id": "t1", "name": "Test Set", "cardCount": {"official": 2, "total": 3}},
        ]

    def search_cards(self, local_id=None, name=None):
        cards = [
            {"id": "old-1", "localId": "1", "name": "Alakazam", "image": None},
            {"id": "sv03.5-001", "localId": "001", "name": "Bulbasaur", "image": None},
            {"id": "t1-1", "localId": "1", "name": "Bulbasaur", "image": None},
            {"id": "t1-TG01", "localId": "TG01", "name": "Venusaur", "image": None},
        ]
        if local_id:
            want = local_id if isinstance(local_id, list) else [local_id]
            cards = [c for c in cards if c["localId"] in want]
        if name:
            cards = [c for c in cards if name.lower() in c["name"].lower()]
        return cards


@pytest.mark.parametrize("q,expected", [
    ("1", ["t1-1", "sv03.5-001", "old-1"]),   # newest set first; '1' also finds '001'
    ("001", ["t1-1", "sv03.5-001", "old-1"]),
    ("1/102", ["old-1"]),                     # printed total narrows it
    ("bulbasaur 1", ["t1-1", "sv03.5-001"]),
    ("bulba", ["t1-1", "sv03.5-001"]),        # name only
    ("tg01", ["t1-TG01"]),
    ("999", []),
])
def test_find_cards(q, expected):
    results = search.find_cards(FakeSearchAPI(), q)
    assert [r["id"] for r in results] == expected


def test_set_id_with_dot_and_padded_number():
    [r] = search.find_cards(FakeSearchAPI(), "bulbasaur 1/165")
    assert (r["set_id"], r["set_name"], r["number"], r["printed_total"]) == ("sv03.5", "Dotted Set", "001", 165)


def test_search_add_loads_new_set(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakeSearchAPI())
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    page = client.get("/search", params={"q": "1"}).text
    assert "Old Set" in page and "/preview/en/t1-1" in page

    r = client.post("/search/add", data={"card_id": "t1-1", "set_id": "t1", "variant": "reverse", "condition": "LP"})
    assert r.status_code == 200 and "Added to your stash." in r.text and "Reverse Holo, Lightly Played" in r.text
    assert main.conn.execute("SELECT COUNT(*) FROM cards WHERE set_id = 't1'").fetchone()[0] == 3  # whole set cached

    # Adding again goes to the existing set and bumps the quantity.
    client.post("/search/add", data={"card_id": "t1-1", "set_id": "t1", "variant": "reverse", "condition": "LP"})
    user_id = main.conn.execute("SELECT id FROM users").fetchone()[0]
    assert inventory.holdings(main.conn, user_id, "t1-1")[0]["quantity"] == 2
    assert 'class="own-ck"' in client.get("/search", params={"q": "bulbasaur 1/2"}).text  # the owned ring and ✓


class FakeFrenchAPI(FakeSearchAPI):
    """Same ids as English (as TCGdex's European languages are), French names."""
    lang = "fr"

    def get_set(self, set_id):
        data = super().get_set(set_id)
        data["name"] = "Ensemble Test"
        for c in data["cards"]:
            c["name"] = c["name"] + " (FR)"
        return data


def test_set_filter_lists_whole_set_in_number_order():
    results = search.find_cards(FakeSearchAPI(), "", set_id="t1")
    assert [r["number"] for r in results] == ["1", "2", "TG01"]
    assert [r["id"] for r in search.find_cards(FakeSearchAPI(), "2", set_id="t1")] == ["t1-2"]
    assert [s["id"] for s in search.set_choices(FakeSearchAPI())] == ["t1", "sv03.5", "old"]  # newest first


def test_other_language_kept_separate(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakeSearchAPI())
    monkeypatch.setitem(web._other_apis, "fr", FakeFrenchAPI())
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    client.post("/search/add", data={"card_id": "t1-1", "set_id": "t1", "lang": "en", "variant": "normal", "condition": "NM"})
    r = client.post("/search/add", data={"card_id": "t1-1", "set_id": "t1", "lang": "fr", "variant": "normal", "condition": "NM"})
    assert "Bulbasaur (FR)" in r.text

    user_id = main.conn.execute("SELECT id FROM users").fetchone()[0]
    sets = {s["set_id"]: s for s in catalog.set_completion(main.conn, user_id)}
    assert set(sets) == {"t1", "fr:t1"} and sets["fr:t1"]["language"] == "fr"
    assert sets["t1"]["owned_main"] == sets["fr:t1"]["owned_main"] == 1
    assert "Ensemble Test" in client.get("/").text and "(fr)" in client.get("/").text

    # The French set page and typed entry work on the prefixed ids.
    _entry(client, "fr:t1", "2")
    assert "Ivysaur (FR)" in client.get("/sets/fr:t1").text


def test_search_language_sticks_and_drops_foreign_set(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakeSearchAPI())
    monkeypatch.setitem(web._other_apis, "ja", FakeFrenchAPI())
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    client.get("/search", params={"lang": "ja"})
    page = client.get("/search", params={"q": "1"}).text
    assert 'class="chip lc-ja on"' in page
    assert "lang=fr" not in page  # only English, Japanese and Chinese are buttons
    page = client.get("/search", params={"lang": "en", "set": "nope"}).text
    assert "No cards found" not in page and "Couldn't search" not in page
    assert 'class="chip lc-en on"' in client.get("/search", params={"lang": "xx"}).text


def test_chinese_searches_both_chinese_languages(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakeSearchAPI())
    monkeypatch.setitem(web._other_apis, "zh-tw", FakeFrenchAPI())
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    # Mainland sets are searched here, not on TCGdex (which lists them without cards).
    main.conn.execute("INSERT INTO sets (set_id, name, language, printed_total, total) VALUES ('zh-cn:CSV7C', '利刃猛醒', 'zh-cn', 204, 204)")
    main.conn.execute("INSERT INTO cards (id, set_id, number, name, twin_name, sort_number, is_main_set) VALUES "
                      "('zh-cn:CSV7C-001', 'zh-cn:CSV7C', '001', '妙蛙种子', 'Bulbasaur', 1, 1)")
    page = client.get("/search", params={"lang": "zh", "q": "1"}).text
    assert "/preview/zh-tw/t1-1" in page and "/cards/zh-cn:CSV7C-001" in page and "/preview/zh-cn/" not in page
    page = client.get("/search", params={"lang": "zh", "set": "zh-tw:t1"}).text
    assert "/preview/zh-tw/t1-1" in page and "zh-cn:CSV7C-001" not in page.split("Card type")[1]
    # Code and number, the English name, or the whole set.
    for q in ("CSV7C 1", "csv7c 001", "Bulbasaur", "妙蛙"):
        assert "/cards/zh-cn:CSV7C-001" in client.get("/search", params={"lang": "zh", "q": q}).text, q
    assert "/cards/zh-cn:CSV7C-001" in client.get("/search", params={"lang": "zh", "set": "zh-cn:CSV7C"}).text
    # The set itself shows at the top when its code is typed.
    page = client.get("/search", params={"lang": "zh", "q": "CSV7C"}).text
    assert "/sets/zh-cn:CSV7C?from=find" in page and "/cards/zh-cn:CSV7C-001" in page


def test_migration_adds_language_column(tmp_path):
    from app import db
    conn = db.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE sets (set_id TEXT PRIMARY KEY, name TEXT NOT NULL, series TEXT, release_date TEXT, "
                 "printed_total INTEGER NOT NULL, total INTEGER NOT NULL, logo_url TEXT, symbol_url TEXT, cached_at TEXT)")
    conn.execute("INSERT INTO sets (set_id, name, printed_total, total) VALUES ('sv03', 'Obsidian Flames', 197, 230)")
    db.init(conn)
    assert conn.execute("SELECT language FROM sets WHERE set_id = 'sv03'").fetchone()[0] == "en"


SAMPLE_NAMES = Names(__import__("pathlib").Path(__file__).with_name("species_sample.csv"), url="http://unused")


@pytest.mark.parametrize("text,lang,expected", [
    ("Charizard ex", "ja", ("リザードン", "Charizard")),
    ("charizard", "zh-tw", ("噴火龍", "Charizard")),
    ("Mega Charizard X ex", "ko", ("리자몽", "Charizard")),
    ("Porygon-Z", "ja", ("ポリゴンZ", "Porygon-Z")),  # longest match, and full-width Z made plain
    ("Porygon", "ja", ("ポリゴン", "Porygon")),
    ("Farfetch'd", "ja", ("カモネギ", "Farfetch'd")),  # straight or curly apostrophe
    ("Boss's Orders", "ja", None),                      # not a Pokémon
    ("Charizard", "en", None),
    ("Charizard", "de", None),                          # no German name in the sample
])
def test_translate(text, lang, expected):
    t = SAMPLE_NAMES.translate(text, lang)
    assert (t.name, t.english) == expected if expected else t is None


class FakeJapaneseAPI(FakeSearchAPI):
    lang = "ja"

    def list_sets(self):
        return [{"id": "SV3", "name": "黒炎の支配者", "cardCount": {"official": 108}}]

    def search_cards(self, local_id=None, name=None):
        cards = [{"id": "SV3-066", "localId": "066", "name": "リザードンex", "image": None},
                 {"id": "SV3-001", "localId": "001", "name": "ナゾノクサ", "image": None}]
        if local_id:
            cards = [c for c in cards if c["localId"] in local_id]
        if name:
            cards = [c for c in cards if name in c["name"]]
        return cards


def test_english_name_searches_other_language(client, monkeypatch):
    monkeypatch.setattr(web, "names", SAMPLE_NAMES)
    monkeypatch.setitem(web._other_apis, "ja", FakeJapaneseAPI())
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    page = client.get("/search", params={"q": "Charizard ex 66", "lang": "ja"}).text
    assert "<strong>リザードン</strong> (Charizard)" in page
    assert "リザードンex" in page and "#066" in page and "ナゾノクサ" not in page
    # English searches are untouched.
    monkeypatch.setattr(web, "api", FakeSearchAPI())
    assert "Searching" not in client.get("/search", params={"q": "Bulbasaur", "lang": "en"}).text


def test_name_list_download_failure_falls_back(client, monkeypatch, tmp_path):
    monkeypatch.setattr(web, "names", Names(tmp_path / "missing.csv", url="http://127.0.0.1:9/nope"))
    monkeypatch.setitem(web._other_apis, "ja", FakeJapaneseAPI())
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    page = client.get("/search", params={"q": "Charizard", "lang": "ja"}).text
    assert "load the Pokémon name list" in page and "No cards found" in page


# Shaped like TCGdex's pricing object (see the module docstring in app/prices.py).
PRICED_CARD = {
    "id": "t1-1", "rarity": "Common", "variants": {"normal": True, "reverse": True},
    "pricing": {
        "tcgplayer": {
            "unit": "USD", "updated": "2026-10-06T00:00:00Z",
            "normal": {"productId": 1, "lowPrice": 0.05, "midPrice": 0.25, "highPrice": 5, "marketPrice": 0.18},
            "reverse-holofoil": {"productId": 1, "lowPrice": 0.2, "midPrice": 0.6, "highPrice": 4, "marketPrice": None},
        },
        "cardmarket": {"unit": "EUR", "updated": "2026-10-06T00:00:00Z", "avg": 0.1, "trend": 0.12, "trend-holo": 0.4},
    },
    "variants_detailed": [
        {"type": "normal"},
        {"type": "reverse"},
        {"type": "reverse", "foil": "pokeball",
         "pricing": {"tcgplayer": {"unit": "USD", "reverse-holofoil": {"marketPrice": 2.5}}}},
    ],
}


def test_extract_prices():
    rows = {(v, s): (p, c) for v, s, p, c in prices.extract(PRICED_CARD)}
    assert rows == {
        ("normal", "tcgplayer"): (0.18, "USD"),
        ("reverse", "tcgplayer"): (0.6, "USD"),      # no market price, falls back to mid
        ("pokeball", "tcgplayer"): (2.5, "USD"),
    }
    holo_only = {"variants": {"holo": True}, "pricing": {"cardmarket": {"trend": 3, "trend-holo": 3}}}
    assert prices.extract(holo_only) == []  # Cardmarket (EUR) is ignored
    assert prices.extract({"pricing": None}) == []


class FakePricedAPI(FakeAPI):
    def __init__(self, normal_price=0.18):
        self.normal_price = normal_price

    def get_card(self, card_id):
        if card_id == "t1-1":
            card = json.loads(json.dumps(PRICED_CARD))
            card["pricing"]["tcgplayer"]["normal"]["marketPrice"] = self.normal_price
            return card
        if card_id == "t1-2":
            return {"id": card_id, "variants": {"normal": True},
                    "pricing": {"tcgplayer": {"unit": "USD", "normal": {"marketPrice": 1.5}}}}
        return super().get_card(card_id)


def test_values_and_price_display(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakePricedAPI())
    user_id = _setup_with_set(client)
    _entry(client, "t1", "1 x2")
    _entry(client, "t1", "1", "masterball")  # ≈ Poké Ball's

    v = prices.collection_value(main.conn, user_id)
    assert (round(v["value"], 2), v["unpriced"], v["cards"], v["estimated"]) == (2.86, 0, 3, 2.5)
    assert round(prices.set_values(main.conn, user_id)["t1"]["owned_value"], 2) == 2.86

    home = client.get("/").text
    assert "$2.86" in home and "incl. $2.50" in home and "to complete" not in home
    assert "0 cards with no price" in client.get("/review").text  # ≈ another printing counts as a price (round 7)
    # Only cards you have keep prices; round 20b: Ivysaur (not yours) isn't looked up live.
    assert {r[0] for r in main.conn.execute("SELECT DISTINCT card_id FROM prices")} == {"t1-1"}
    set_page = client.get("/sets/t1").text
    assert "$0.18" in set_page and "$1.50" not in set_page
    card_page = client.get("/cards/t1-1").text
    assert "Poké Ball Reverse" in card_page and "$2.50" in card_page
    sources = card_page.split("cv-sources")[1].split("</table>")[0] if "cv-sources" in card_page else ""
    assert "Cardmarket" not in sources  # no euro rate yet, so Cardmarket isn't counted
    assert "Search Cardmarket" in card_page  # its search link shows anyway, like TCGplayer's and PriceCharting's
    assert "$1.50" not in client.get("/cards/t1-2").text  # round 20b: no live price for a card you don't have
    assert main.conn.execute("SELECT 1 FROM prices WHERE card_id = 't1-2'").fetchone() is None


def test_damaged_not_counted_and_printing_mismatch(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakePricedAPI())
    user_id = _setup_with_set(client)
    _entry(client, "t1", "1", "normal", "LP")
    _entry(client, "t1", "1", "normal", "DMG")
    _entry(client, "t1", "1", "masterball")  # ≈ Poké Ball's
    client.post("/cards/t1-1/notes", data={"text": "Bent corner"})

    # Lightly Played counts at the near mint price; Damaged isn't counted.
    v = prices.collection_value(main.conn, user_id)
    assert (round(v["value"], 2), v["unpriced"], v["damaged"]) == (2.68, 0, 1)
    page = client.get("/collection").text
    assert 'badge-l">LP<' in page and ">DMG<" in page and "Damaged, not counted" in page
    # The Master Ball copy has no price, but TCGplayer prices other printings of the card.
    assert "TCGplayer has no Master Ball Reverse price for this card" in page and "Change to Normal" in page
    assert "Cardmarket" not in page
    approx = client.get("/collection?price=approx").text
    assert 'badge-l">LP<' not in approx and "Master Ball Reverse" in approx

    r = client.post("/cards/t1-1/move", data={"variant": "masterball", "condition": "NM", "to_variant": "normal",
                                              "back": "collection"})
    assert r.status_code == 200 and "Changed 1 copy." in r.text
    held = {(h["variant"], h["condition"]): h["quantity"] for h in inventory.holdings(main.conn, user_id, "t1-1")}
    assert held == {("normal", "NM"): 1, ("normal", "LP"): 1, ("normal", "DMG"): 1}
    assert main.conn.execute("SELECT variant FROM notes WHERE text = 'Bent corner'").fetchone()[0] is None  # whole card
    assert round(prices.collection_value(main.conn, user_id)["value"], 2) == 0.36

    # Changing the condition merges into the line that's already there.
    client.post("/cards/t1-1/move", data={"variant": "normal", "condition": "LP", "to_variant": "normal",
                                          "to_condition": "DMG"})
    held = {(h["variant"], h["condition"]): h["quantity"] for h in inventory.holdings(main.conn, user_id, "t1-1")}
    assert held == {("normal", "NM"): 1, ("normal", "DMG"): 2}


def test_old_cardmarket_prices_are_dropped(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakePricedAPI())
    _setup_with_set(client)
    _entry(client, "t1", "1")
    main.conn.execute("INSERT INTO price_snapshots (card_id, variant, source, price) VALUES ('t1-1', 'holo', 'old', 1.0)")
    prices.prune(main.conn)
    assert main.conn.execute("SELECT COUNT(*) FROM price_snapshots WHERE source = 'old'").fetchone()[0] == 0


def test_refresh_updates_prices_and_keeps_history(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakePricedAPI())
    user_id = _setup_with_set(client)
    _entry(client, "t1", "1")
    count, errors = sync.refresh_all(main.conn, lambda lang: FakePricedAPI(normal_price=0.5))
    assert (count, errors) == (1, [])  # only the card you have
    assert prices.collection_value(main.conn, user_id)["value"] == 0.5
    history = main.conn.execute(
        "SELECT price FROM price_snapshots WHERE card_id = 't1-1' AND variant = 'normal' AND source = 'tcgplayer' "
        "ORDER BY rowid").fetchall()
    assert [r[0] for r in history] == [0.18, 0.5]
    assert json.loads(main.conn.execute("SELECT pricing_json FROM cards WHERE id = 't1-1'").fetchone()[0])["tcgplayer"]


def test_refresh_reports_failing_card(client, monkeypatch):
    _setup_with_set(client)
    _entry(client, "t1", "1")

    class Broken(FakeAPI):
        def get_card(self, card_id):
            raise RuntimeError("TCGdex is down")

    count, errors = sync.refresh_all(main.conn, lambda lang: Broken())
    assert count == 0 and errors == ["t1-1: TCGdex is down"]


def test_prices_forgotten_when_nobody_has_the_card(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakePricedAPI())
    _setup_with_set(client)
    _entry(client, "t1", "1")
    assert main.conn.execute("SELECT 1 FROM price_snapshots WHERE card_id = 't1-1'").fetchone()
    _entry(client, "t1", "-1")
    sync.refresh_all(main.conn, lambda lang: web.api)
    for table in ("prices", "price_snapshots"):
        assert main.conn.execute(f"SELECT 1 FROM {table} WHERE card_id = 't1-1'").fetchone() is None
    assert main.conn.execute("SELECT pricing_json FROM cards WHERE id = 't1-1'").fetchone()[0] is None


def test_migration_adds_pricing_column(tmp_path):
    from app import db
    conn = db.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE cards (id TEXT PRIMARY KEY, set_id TEXT NOT NULL, number TEXT NOT NULL, "
                 "sort_number INTEGER NOT NULL, is_main_set INTEGER NOT NULL, name TEXT NOT NULL)")
    db.init(conn)
    assert "pricing_json" in {r["name"] for r in conn.execute("PRAGMA table_info(cards)")}


def test_update_button_runs_in_background(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakePricedAPI(normal_price=0.75))
    user_id = _setup_with_set(client)
    inventory.adjust(main.conn, user_id, "t1-1", "normal", "NM", 1)
    assert client.post("/prices/refresh", follow_redirects=False).status_code == 303
    for _ in range(100):
        if not web.updater.running:
            break
        __import__("time").sleep(0.05)
    assert web.updater.last_result.startswith("Updated prices for 1 card in")
    # Written on the updater's own connection, visible to the app's.
    assert main.conn.execute("SELECT price FROM prices WHERE card_id = 't1-1' AND variant = 'normal' "
                             "AND origin = 'tcgplayer'").fetchone()[0] == 0.75
    assert "Prices updated" in client.get("/").text


def test_cards_without_pictures_still_show(client):
    _setup_with_set(client)
    main.conn.execute("UPDATE cards SET image_url = 'https://example.test/t1/1/low.webp' WHERE id = 't1-1'")
    main.conn.commit()
    page = client.get("/sets/t1").text
    # Every card gets a tile; the one with a picture keeps its stand-in hidden in case the picture fails.
    assert page.count('class="card-ph"') == 3
    assert page.count('class="card-ph" hidden') == 1
    assert "Ivysaur" in page and "Venusaur" in page


# --- TCGplayer links, previews and search filters ----------------------------

def test_tcgplayer_link():
    pricing = {"tcgplayer": {"unit": "USD", "reverse-holofoil": {"productId": 509876, "marketPrice": 1}}}
    assert prices.tcgplayer_link(pricing, "Charizard ex", "125") == ("https://www.tcgplayer.com/product/509876", True)
    assert prices.tcgplayer_link(json.dumps(pricing), "x", "1")[1]
    url, exact = prices.tcgplayer_link(None, "Charizard ex", "125")
    assert not exact and url.endswith("q=Charizard+ex+125")


class FakeDetailAPI(FakeSearchAPI):
    """Card details for cards in sets that aren't loaded, as TCGdex returns them."""

    DETAILS = {
        "old-1": {"id": "old-1", "localId": "1", "name": "Alakazam", "rarity": "Rare Holo", "category": "Pokemon",
                  "hp": 80, "image": None, "set": {"id": "old", "name": "Old Set", "logo": "https://x.test/old/logo"},
                  "variants": {"holo": True},
                  "pricing": {"tcgplayer": {"unit": "USD", "holofoil": {"productId": 42, "marketPrice": 30.5}}}},
        "sv03.5-001": {"id": "sv03.5-001", "localId": "001", "name": "Bulbasaur", "rarity": "Common",
                       "category": "Pokemon", "hp": 70, "image": None, "set": {"id": "sv03.5", "name": "Dotted Set"},
                       "variants": {"normal": True, "reverse": True},
                       "pricing": {"tcgplayer": {"unit": "USD", "normal": {"marketPrice": 0.4},
                                                 "reverse-holofoil": {"marketPrice": 0.9}}}},
        "t1-TG01": {"id": "t1-TG01", "localId": "TG01", "name": "Venusaur", "rarity": "Ultra Rare",
                    "category": "Trainer", "trainerType": "Supporter", "image": None, "set": {"id": "t1"}},
    }

    def get_card(self, card_id):
        return self.DETAILS.get(card_id) or super().get_card(card_id)


@pytest.mark.parametrize("detail,kind", [
    ({"category": "Pokemon", "hp": 60}, "pokemon"),
    ({"category": "Dresseur", "trainerType": "Supporter"}, "trainer"),
    ({"category": "Énergie", "energyType": "Normal"}, "energy"),
    ({"category": "エネルギー"}, "energy"),
    ({"category": "Allenatore"}, "trainer"),
    ({}, None),
])
def test_card_kind(detail, kind):
    assert search.card_kind(detail) == kind


def test_search_filters_by_rarity_and_type():
    api = FakeDetailAPI()
    assert [r["id"] for r in search.find_cards(api, "1", rarity="rare holo")] == ["old-1"]
    assert [r["id"] for r in search.find_cards(api, "", set_id="t1", kind="trainer")] == ["t1-TG01"]
    [r] = search.find_cards(api, "bulbasaur", kind="pokemon", rarity="Common")
    assert r["rarity"] == "Common"


def test_search_shows_prices_and_owned_filter(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakeDetailAPI())
    user_id = _setup_with_set(client)
    main.conn.execute("INSERT INTO prices (card_id, variant, origin, price, updated_at) "
                      "VALUES ('t1-1', 'normal', 'tcgplayer', 2.25, '2026-10-06T00:00:00')")
    inventory.adjust(main.conn, user_id, "t1-1", "normal", "NM", 1)
    page = client.get("/search", params={"q": "1"}).text
    assert "$2.25" in page    # stored price for a loaded set
    assert "$30.50" not in page   # round 20b: nothing is looked up for the others
    not_owned = client.get("/search", params={"q": "1", "owned": "no"}).text
    assert "/preview/en/t1-1" not in not_owned and "/preview/en/old-1" in not_owned
    owned = client.get("/search", params={"q": "1", "owned": "yes"}).text
    assert "/preview/en/t1-1" in owned and "/preview/en/old-1" not in owned


def test_preview_adds_nothing_until_asked(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakeDetailAPI())
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    page = client.get("/preview/en/old-1").text
    assert "Alakazam" in page and "Preview only" not in page and "$30.50" in page  # round 28: TCGdex's TCGplayer price for a card nobody keeps
    assert "https://www.tcgplayer.com/product/42" in page and "pricecharting.com/search-products" in page
    assert "https://x.test/old/logo.webp" in page  # set logo stands in for the missing picture
    assert main.conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0] == 0

    # Only the printing that exists and has a price is offered first.
    # Holo is picked (Normal doesn't exist for this card); other printings wait behind "More...".
    add_form = page.split('id="cv-add"', 1)[1].split("</form>", 1)[0]
    assert '<input type="radio" name="variant" value="holo" checked' in add_form
    assert '<label class="chip" hidden data-more><input type="radio" name="variant" value="normal"' in add_form
    assert "More&hellip;" in add_form

    r = client.post("/search/add", data={"card_id": "old-1", "set_id": "old", "variant": "holo", "condition": "NM",
                                         "note": "PSA 9"})
    assert "Added to your stash." in r.text and "PSA 9" in r.text and "misty" in r.text
    # Once its set is loaded, the preview opens the card's own page.
    assert client.get("/preview/en/old-1", follow_redirects=False).headers["location"] == "/cards/old-1?from=find"


def test_offered_variants():
    card = {"has_normal": 0, "has_reverse": 1, "has_holo": 0, "has_first_ed": 0}
    assert inventory.offered_variants(card, {"normal"}) == ["normal", "reverse"]
    assert inventory.offered_variants(None, {"pokeball"}) == ["pokeball"]
    assert inventory.offered_variants(None, set()) == list(inventory.VARIANTS)


def test_card_note_and_paid(client):
    user_id = _setup_with_set(client)
    client.post("/cards/t1-1/adjust", data={"variant": "normal", "condition": "NM", "delta": 2, "note": "first pull"})
    client.post("/cards/t1-1/details", data={"variant": "normal", "condition": "NM", "paid": "$1.50"})
    [h] = inventory.holdings(main.conn, user_id, "t1-1")
    assert (h["quantity"], h["acquired_price"]) == (2, 1.5)
    assert [m["label"] for m in h["methods"]] == ["Added by hand by misty"]
    assert h["origin"] == "single"  # a price paid marks it as bought as a single
    [n] = notes.for_card(main.conn, user_id, "t1-1")
    assert (n["text"], n["author"], n["variant"], n["condition"]) == ("first pull", "misty", None, None)
    assert spending.totals(main.conn, user_id)["singles"] == 3.0
    assert client.post("/cards/t1-1/details", data={"variant": "normal", "condition": "NM", "paid": "abc"}).status_code == 400


# --- my cards, wants, spending, sets ------------------------------------------

def test_collection_filters_and_sorts(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakePricedAPI())
    user_id = _setup_with_set(client)
    main.conn.execute("UPDATE cards SET rarity = 'Ultra Rare' WHERE id = 't1-TG01'")
    for card in ("t1-1", "t1-2", "t1-TG01"):
        inventory.adjust(main.conn, user_id, card, "normal", "NM", 1)
    assert sync.fill_missing(main.conn, lambda lang: web.api) == (3, [])  # what an import does in the background
    by_price = collection.cards(main.conn, user_id)
    assert [r["card_id"] for r in by_price["rows"]] == ["t1-2", "t1-1", "t1-TG01"]  # $1.50, $0.18, no price
    assert [r["card_id"] for r in collection.cards(main.conn, user_id, sort="price_asc")["rows"]] == ["t1-1", "t1-2", "t1-TG01"]
    assert [r["card_id"] for r in collection.cards(main.conn, user_id, q="ivy")["rows"]] == ["t1-2"]
    assert [r["card_id"] for r in collection.cards(main.conn, user_id, rarity="ultra rare")["rows"]] == ["t1-TG01"]
    page = client.get("/collection", params={"sort": "set"}).text
    assert page.index("Bulbasaur") < page.index("Ivysaur") < page.index("Venusaur") and "worth $1.68" in page


def test_wants_from_preview(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakeDetailAPI())
    user_id = _setup_with_set(client)
    client.post("/wants/add", data={"card_id": "old-1", "set_id": "old", "variant": "any", "max_price": "35"})
    client.post("/wants/add", data={"card_id": "t1-2", "set_id": "t1", "variant": "reverse", "max_price": ""})
    client.post("/wants/add", data={"card_id": "t1-2", "set_id": "t1", "variant": "holo"})  # one chase per card
    [old, ivy] = sorted(wants.list_wants(main.conn, user_id, web._live_card), key=lambda w: w["card_id"])
    assert old["price"] == 30.5 and [tuple(r) for r in main.conn.execute("SELECT DISTINCT variant FROM wants")] == [("any",)]
    assert main.conn.execute("SELECT COUNT(*) FROM wants").fetchone()[0] == 2
    page = client.get("/chases").text
    assert "/cards/old-1" in page and "most I'd pay" not in page.lower()
    assert client.get("/wants", follow_redirects=False).headers["location"] == "/chases"
    assert 'class="star-ck"' in client.get("/sets/t1").text  # wanted card marked in the set grid
    client.post(f"/wants/{old['id']}/remove")
    assert [w["card_id"] for w in wants.list_wants(main.conn, user_id)] == ["t1-2"]


def test_pack_costs_are_shared(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakePricedAPI())
    user_id = _setup_with_set(client)
    r = client.post("/purchases", data={"label": "2 Test packs", "cost": "10", "packs": 2, "set_id": "t1"})
    purchase_id = int(r.url.path.rsplit("/", 1)[1])
    client.post(f"/purchases/{purchase_id}/pulls", data={"entry": "1 x3", "variant": "normal", "condition": "NM"})
    page = client.post(f"/purchases/{purchase_id}/pulls", data={"entry": "TG01", "variant": "normal", "condition": "NM"}).text
    assert "Pulled #TG01 Venusaur" in page
    assert inventory.holdings(main.conn, user_id, "t1-1")[0]["quantity"] == 3  # pulled cards join the collection

    purchase = spending.get_purchase(main.conn, user_id, purchase_id)
    assert {r["card_id"]: r["cost_each"] for r in spending.pulls(main.conn, purchase)} == {"t1-1": 2.5, "t1-TG01": 2.5}
    tg = next(r for r in spending.pulls(main.conn, purchase) if r["card_id"] == "t1-TG01")
    client.post(f"/purchases/{purchase_id}/pulls/{tg['id']}/cost", data={"cost_each": "7"})
    assert {r["card_id"]: r["cost_each"] for r in spending.pulls(main.conn, purchase)} == {"t1-1": 1.0, "t1-TG01": 7.0}

    client.post(f"/purchases/{purchase_id}/pulls", data={"entry": "-1", "variant": "normal", "condition": "NM"})
    assert inventory.holdings(main.conn, user_id, "t1-1")[0]["quantity"] == 2
    paid = {r["card_id"]: r["paid"] for r in collection.cards(main.conn, user_id)["rows"]}
    assert paid == {"t1-1": 3.0, "t1-TG01": 7.0}    # (10 - 7) shared over the two #1s left
    totals = spending.totals(main.conn, user_id)
    assert (totals["packs"], totals["singles"], totals["total"]) == (10.0, 0, 10.0)
    assert "2 Test packs" in client.get("/spending").text and "$10.00" in client.get("/spending").text  # Spent left Home (round 24)

    client.post(f"/purchases/{purchase_id}/delete")
    assert spending.get_purchase(main.conn, user_id, purchase_id) is None
    assert inventory.holdings(main.conn, user_id, "t1-1")[0]["quantity"] == 2  # cards stay


def test_home_lists_only_sets_with_cards_and_remove_set(client):
    user_id = _setup_with_set(client)
    assert "Test Set" not in client.get("/").text  # loaded, but nothing owned yet
    inventory.adjust(main.conn, user_id, "t1-1", "normal", "NM", 1)
    wants.add(main.conn, user_id, "t1-2")
    assert "Test Set" in client.get("/").text
    assert "Remove set" in client.get("/sets/t1").text

    client.post("/sets/t1/remove")
    assert "Test Set" not in client.get("/").text
    for table in ("inventory", "wants", "cards", "sets"):
        assert main.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


# --- sharing, notes, accounts -------------------------------------------------

def _login(username, password="pikachu123"):
    c = TestClient(main.app)
    r = c.post("/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


def test_partner_shares_collection_with_attribution(client):
    owner = _setup_with_set(client)
    r = client.post("/account/users", data={"username": "sam", "password": "squirtle99"})
    assert "Added sam" in r.text
    sam = _login("sam", "squirtle99")
    sam.post("/cards/t1-2/adjust", data={"variant": "normal", "condition": "NM", "delta": 1, "note": "trade with Jo"})
    sam.post("/wants/add", data={"card_id": "t1-1", "set_id": "t1", "variant": "any"})
    sam_id = main.conn.execute("SELECT id FROM users WHERE username = 'sam'").fetchone()[0]

    # Same collection: the owner sees Sam's card and want, credited to Sam.
    [h] = inventory.holdings(main.conn, owner, "t1-2")
    assert [m["label"] for m in h["methods"]] == ["Added by hand by sam"]
    page = client.get("/cards/t1-2").text
    assert "Added by hand by sam" in page and "trade with Jo" in page
    assert "Added by hand by sam" not in client.get("/collection").text  # the grid shows it on the card page only
    assert "added by sam" in client.get("/wants").text
    assert tuple(main.conn.execute("SELECT user_id, source FROM inventory_log WHERE card_id = 't1-2'").fetchone()) == (sam_id, "manual")

    # Notes: only the author or an admin can delete one.
    client.post("/cards/t1-2/notes", data={"text": "binder 2", "printing": "normal|NM"})
    mine = next(n for n in notes.for_card(main.conn, owner, "t1-2") if n["text"] == "binder 2")
    theirs = next(n for n in notes.for_card(main.conn, owner, "t1-2") if n["text"] == "trade with Jo")
    assert sam.post(f"/notes/{mine['id']}/delete", data={"card_id": "t1-2"}).status_code == 403
    assert client.post(f"/notes/{theirs['id']}/delete", data={"card_id": "t1-2"}).status_code == 200
    assert [n["text"] for n in notes.for_card(main.conn, owner, "t1-2")] == ["binder 2"]

    # Members can't manage people.
    assert sam.post("/account/users", data={"username": "x", "password": "longenough"}).status_code == 403
    assert "Clear collection" not in sam.get("/account").text


def test_password_change_signs_out_other_devices(client):
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    phone = _login("misty")
    assert "current password isn&#39;t right" in client.post(
        "/account/password", data={"current": "nope", "new": "charmander1", "again": "charmander1"}).text
    assert "match" in client.post(
        "/account/password", data={"current": "pikachu123", "new": "charmander1", "again": "charmander2"}).text
    r = client.post("/account/password", data={"current": "pikachu123", "new": "charmander1", "again": "charmander1"})
    assert "Password changed" in r.text
    assert client.get("/", follow_redirects=False).status_code == 200      # this device stays in
    assert phone.get("/", follow_redirects=False).headers["location"] == "/login"
    _login("misty", "charmander1")


def test_admin_resets_and_removes_users(client):
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    client.post("/account/users", data={"username": "sam", "password": "squirtle99"})
    sam_id = main.conn.execute("SELECT id FROM users WHERE username = 'sam'").fetchone()[0]
    me = main.conn.execute("SELECT id FROM users WHERE username = 'misty'").fetchone()[0]
    sam = _login("sam", "squirtle99")
    client.post(f"/account/users/{sam_id}/password", data={"password": "newpass123"})
    assert sam.get("/", follow_redirects=False).headers["location"] == "/login"
    _login("sam", "newpass123")
    assert "already an account" in client.post("/account/users", data={"username": "SAM", "password": "longenough"}).text
    assert "remove your own account" in client.post(f"/account/users/{me}/remove").text
    assert "take away your own admin" in client.post(f"/account/users/{me}/admin", data={"is_admin": ""}).text
    client.post(f"/account/users/{sam_id}/remove")
    assert main.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_login_lockout(client):
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    other = TestClient(main.app)
    for _ in range(5):
        assert other.post("/login", data={"username": "misty", "password": "wrong-one"}).status_code == 401
    r = other.post("/login", data={"username": "Misty", "password": "pikachu123"})
    assert r.status_code == 429 and "Too many wrong passwords" in r.text


def test_cli_password_reset(client, monkeypatch, capsys):
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    answers = iter(["fresh-start1", "fresh-start1"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert auth._cli(["reset-password", "misty"]) == 0
    assert auth.authenticate(main.conn, "misty", "fresh-start1") is not None


def test_clear_collection_needs_both_confirmations(client):
    user_id = _setup_with_set(client)
    client.post("/cards/t1-1/adjust", data={"variant": "normal", "condition": "NM", "delta": 2, "note": "hi"})
    client.post("/wants/add", data={"card_id": "t1-2", "set_id": "t1"})
    client.post("/purchases", data={"label": "Pack", "cost": "5"})
    r = client.post("/account/clear", data={"confirm": "clear"})
    assert r.status_code == 400 and main.conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0] == 1
    assert "The collection is empty" in client.post("/account/clear", data={"confirm": "CLEAR"}).text
    for table in ("inventory", "wants", "purchases", "notes", "inventory_log"):
        assert main.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert main.conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 3  # card data stays
    assert client.get("/", follow_redirects=False).status_code == 200      # still signed in
    assert spending.totals(main.conn, user_id)["total"] == 0


def test_migration_shares_and_moves_notes(tmp_path):
    from pathlib import Path
    from app import db
    old_schema = Path(__file__).parent.parent.parent / "base-v3" / "app" / "schema.sql"
    if not old_schema.exists():
        pytest.skip("no old schema to migrate from")
    conn = db.connect(tmp_path / "old.db")
    conn.executescript(old_schema.read_text())
    conn.execute("INSERT INTO users (username, password_hash, is_admin) VALUES ('misty', 'x', 1)")
    conn.execute("INSERT INTO sets (set_id, name, printed_total, total) VALUES ('s', 'S', 1, 1)")
    conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name) VALUES ('s-1', 's', '1', 1, 1, 'A')")
    conn.execute("INSERT INTO inventory (user_id, card_id, quantity, note) VALUES (1, 's-1', 2, 'PSA 9')")
    conn.commit()
    db.init(conn)
    db.init(conn)  # twice is harmless
    assert conn.execute("SELECT collection_id FROM users").fetchone()[0] == 1
    assert [tuple(r) for r in conn.execute("SELECT text, author_id, variant FROM notes")] == [("PSA 9", 1, None)]
    assert conn.execute("SELECT note FROM inventory").fetchone()[0] is None
    assert [tuple(r) for r in conn.execute("SELECT delta, user_id FROM inventory_log")] == [(2, 1)]


# --- Collectr import ----------------------------------------------------------

HEADER = ("Portfolio Name,Category,Set,Product Name,Card Number,Rarity,Variance,Grade,Card Condition,"
          "Average Cost Paid,Quantity,Market Price (As of 2026-10-03),Price Override,Watchlist,Date Added,Notes\n")


def _csv(*rows):
    return (HEADER + "".join(",".join(r) + "\n" for r in rows)).encode()


def _row(set_name, name, number, variance="Normal", qty="1", cost="0.0000", notes="", category="Pokemon",
         condition="Near Mint", grade="Ungraded", rarity="Common"):
    return ["Main", category, set_name, name, number, rarity, variance, grade, condition, cost, qty, "0.10", "0",
            "false", "2026-10-03", notes]


def test_collectr_parse():
    rows = collectr.parse(_csv(
        _row("Shining Fates", "Eevee (Poke Ball Pattern) CN", "1601/07", "Holofoil", notes="; "),
        _row("Base Set (Unlimited)", "Machamp", "8/102", "Unlimited", rarity="Holo Rare", condition="Lightly Played"),
        _row("Base Set (1st Edition & Shadowless)", "Bill", "91", "Shadowless", grade="PSA 9", notes="; mint; "),
        _row("Revue Starlight", "Karen", "RSL/S56", category="Weiss Schwarz"),
        _row("SV: 151", "Mew ex", "151/165", "Holofoil", qty="0", cost="2.5"),
    ))
    eevee, machamp, bill, weiss, mew = rows
    assert (eevee.variant, eevee.language, eevee.clean_name, eevee.number, eevee.note) == \
        ("pokeball", "zh", "Eevee", "1601", None)
    assert (machamp.variant, machamp.condition, machamp.printed_total, machamp.line) == ("holo", "LP", 102, 3)
    assert (bill.variant, bill.note) == ("other", "mint; PSA 9; Shadowless")
    assert weiss.skip and mew.skip == "Quantity is 0" and mew.cost == 2.5
    assert eevee.key == collectr.key_for("Shining Fates", "1601/07", "Eevee (Poke Ball Pattern) CN", "Holofoil")
    with pytest.raises(ValueError):
        collectr.parse(b"name,qty\nPikachu,1\n")


def test_collectr_sets_and_cards():
    sets = [{"id": "base1", "name": "Base Set", "cardCount": {"official": 102}},
            {"id": "swsh9", "name": "Brilliant Stars", "cardCount": {"official": 172}},
            {"id": "swsh9tg", "name": "Brilliant Stars Trainer Gallery", "cardCount": {"official": 30}},
            {"id": "tk-sm-l", "name": "SM trainer Kit (Lycanroc)", "cardCount": {"official": 30}},
            {"id": "tk-sm-r", "name": "SM trainer Kit (Alolan Raichu)", "cardCount": {"official": 30}},
            {"id": "swsh10.5", "name": "Pokémon GO", "cardCount": {"official": 78}}]
    assert collectr.resolve_set("Base Set (Unlimited)", 102, sets) == ["base1"]
    assert collectr.resolve_set("Brilliant Stars", 172, sets) == ["swsh9", "swsh9tg"]
    assert collectr.resolve_set("Pokemon Go", 78, sets) == ["swsh10.5"]
    assert collectr.resolve_set("Ninja Spinner", 83, sets) == []
    [row] = collectr.parse(_csv(_row("SM Trainer Kit: Lycanroc & Alolan Raichu", "Alolan Raichu", "1/30")))
    kit = [{"number": "1", "name": "Lycanroc", "set_id": "tk-sm-l"}, {"number": "1", "name": "Alolan Raichu", "set_id": "tk-sm-r"}]
    assert collectr.pick_card(row, kit)["set_id"] == "tk-sm-r"
    [reprint] = collectr.parse(_csv(_row("30th Celebration: Classic Collection", "Rayquaza EX", "85")))
    assert collectr.pick_card(reprint, [{"number": "015", "name": "Rayquaza EX"}, {"number": "016", "name": "N"}])
    [wrong] = collectr.parse(_csv(_row("Base Set", "Pikachu", "58/102")))
    assert collectr.pick_card(wrong, [{"number": "58", "name": "Raichu"}]) is None


class FakeImportAPI(FakeAPI):
    def list_sets(self):
        return [{"id": "t1", "name": "Test Set", "cardCount": {"official": 2, "total": 3}}]


def test_collectr_import_end_to_end(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakeImportAPI())
    monkeypatch.setattr(web.matcher, "start", lambda b, wait=False: importer.Matcher.start(web.matcher, b, wait=True))
    owner = _setup_with_set(client)
    me = main.conn.execute("SELECT id FROM users").fetchone()[0]
    client.post("/cards/t1-1/adjust", data={"variant": "normal", "condition": "NM", "delta": 3})
    client.post("/cards/t1-TG01/adjust", data={"variant": "normal", "condition": "NM", "delta": 1})
    data = _csv(
        _row("Test Set", "Bulbasaur", "001/002", qty="2"),
        _row("Test Set", "Ivysaur", "002/002", "Reverse Holofoil", qty="2", cost="0.5000", notes="from Jo; "),
        _row("Ninja Spinner", "Bulbasaur", "001/083", "Holofoil"),
        _row("Test Set", "Mystery", "099/002"),
        _row("Revue Starlight", "Karen", "RSL/S56-TE22 TD", category="Weiss Schwarz"),
    )
    r = client.post("/imports", files={"file": ("export.csv", data, "text/csv")})
    assert r.status_code == 200
    batch_id = main.conn.execute("SELECT MAX(id) FROM import_batches").fetchone()[0]
    page = client.get(f"/imports/{batch_id}").text
    assert "2 rows matched" in page and "2 not matched" in page and "1 ignored" in page
    assert "Set not found" in page and "No #099 Mystery in that set" in page

    review = importer.review(main.conn, owner, batch_id)
    assert [c["card_id"] for c in review["changes"]["new"]] == ["t1-2"]
    assert [(c["current"], c["target"]) for c in review["changes"]["fewer"]] == [(3, 2)]
    assert [c["card_id"] for c in review["changes"]["missing"]] == ["t1-TG01"]
    assert (review["spend_cards"], review["spend_total"]) == (2, 1.0)

    # Whole set by hand: Ninja Spinner -> Test Set, matched by number.
    client.post(f"/imports/{batch_id}/set", data={"set_name": "Ninja Spinner", "lang": "en", "set_id": "t1"})
    # One row by hand, from the card page: Mystery is really Ivysaur.
    mystery = main.conn.execute("SELECT id FROM import_rows WHERE batch_id = ? AND line = 5", (batch_id,)).fetchone()[0]
    assert "Use for this import row" in client.get(f"/cards/t1-2?fix={mystery}").text
    client.post(f"/imports/rows/{mystery}/match", data={"card_id": "t1-2", "set_id": "t1", "lang": "en"})
    assert importer.review(main.conn, owner, batch_id)["counts"] == {"matched": 4, "unmatched": 0, "ignored": 1}

    # Apply, keeping the higher quantity (lower unticked) and the card not in the file.
    client.post(f"/imports/{batch_id}/apply", data={"lower": ""})
    qty = dict(((r[0], r[1]), r[2]) for r in main.conn.execute("SELECT card_id, variant, quantity FROM inventory"))
    assert qty == {("t1-1", "normal"): 3, ("t1-2", "reverse"): 2, ("t1-1", "holo"): 1, ("t1-2", "normal"): 1,
                   ("t1-TG01", "normal"): 1}
    log = main.conn.execute("SELECT COUNT(*), MIN(user_id) FROM inventory_log WHERE source = 'import'").fetchone()
    assert tuple(log) == (3, me)
    purchase = main.conn.execute("SELECT * FROM purchases").fetchone()
    assert purchase["cost"] == 1.0 and purchase["created_by"] == me and purchase["label"].startswith("Import ")
    assert [tuple(r) for r in main.conn.execute("SELECT card_id, quantity, cost_each FROM pulls")] == [("t1-2", 2, 0.5)]
    assert [n["text"] for n in notes.for_card(main.conn, owner, "t1-2")] == ["from Jo"]
    assert "this spending entry" in client.get(f"/imports/{batch_id}").text

    # Importing the same file again lines up from the remembered matches and changes nothing.
    client.post(f"/imports/{batch_id}/again")
    again = main.conn.execute("SELECT MAX(id) FROM import_batches").fetchone()[0]
    assert again != batch_id
    hows = {r[0] for r in main.conn.execute("SELECT matched_by FROM import_rows WHERE batch_id = ? AND status = 'matched'",
                                            (again,))}
    assert hows == {"saved"}
    review = importer.review(main.conn, owner, again)
    assert not review["changes"]["new"] and not review["changes"]["more"] and review["spend_cards"] == 0
    client.post(f"/imports/{again}/apply", data={"lower": "1", "remove_missing": "1"})
    qty = dict(((r[0], r[1]), r[2]) for r in main.conn.execute("SELECT card_id, variant, quantity FROM inventory"))
    assert qty[("t1-1", "normal")] == 2 and ("t1-TG01", "normal") not in qty
    assert main.conn.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 1
    assert len(notes.for_card(main.conn, owner, "t1-2")) == 1  # notes aren't repeated


def test_import_ignore_is_remembered(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakeImportAPI())
    monkeypatch.setattr(web.matcher, "start", lambda b, wait=False: importer.Matcher.start(web.matcher, b, wait=True))
    owner = _setup_with_set(client)
    data = _csv(_row("Trick or Trade BOOster Bundle 2024", "Pikachu", "001/030"))
    client.post("/imports", files={"file": ("export.csv", data, "text/csv")})
    batch_id = main.conn.execute("SELECT MAX(id) FROM import_batches").fetchone()[0]
    row_id = main.conn.execute("SELECT id FROM import_rows WHERE batch_id = ?", (batch_id,)).fetchone()[0]
    client.post(f"/imports/rows/{row_id}/ignore")
    client.post(f"/imports/{batch_id}/again")
    again = main.conn.execute("SELECT MAX(id) FROM import_batches").fetchone()[0]
    assert tuple(main.conn.execute("SELECT status, matched_by FROM import_rows WHERE batch_id = ?", (again,)).fetchone()) == \
        ("ignored", "saved")
    assert client.post("/imports", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")}).status_code == 400
    assert importer.batches(main.conn, owner)[0]["status"] == "ready"


# --- how and where cards came from --------------------------------------------

def test_method_and_origin(client):
    owner = _setup_with_set(client)
    client.post("/account/users", data={"username": "sam", "password": "squirtle99"})
    sam = _login("sam", "squirtle99")
    sam_id = main.conn.execute("SELECT id FROM users WHERE username = 'sam'").fetchone()[0]
    client.post("/cards/t1-1/adjust", data={"variant": "normal", "condition": "NM", "delta": 1, "origin": "trade"})
    sam.post("/cards/t1-2/adjust", data={"variant": "normal", "condition": "NM", "delta": 1})
    inventory.adjust(main.conn, owner, "t1-2", "holo", "NM", 1, by=sam_id, source="scan", origin="pack")

    [h] = inventory.holdings(main.conn, owner, "t1-1")
    assert (h["origin"], h["origin_label"]) == ("trade", "Trade")
    assert inventory.history(main.conn, owner, "t1-1")[0]["origin_label"] == "Trade"
    holo = [h for h in inventory.holdings(main.conn, owner, "t1-2") if h["variant"] == "holo"][0]
    assert [m["label"] for m in holo["methods"]] == ["Scanned by sam"]

    # Changing where it came from on the card page; blank clears it.
    client.post("/cards/t1-1/details", data={"variant": "normal", "condition": "NM", "paid": "", "origin": "gift"})
    assert inventory.holdings(main.conn, owner, "t1-1")[0]["origin"] == "gift"
    client.post("/cards/t1-1/details", data={"variant": "normal", "condition": "NM", "paid": "", "origin": "clear"})
    assert inventory.holdings(main.conn, owner, "t1-1")[0]["origin"] is None

    def shown(**params):
        return [(r["card_id"], r["variant"]) for r in collection.cards(main.conn, owner, **params)["rows"]]
    assert shown(method="scan") == [("t1-2", "holo")]
    assert sorted(shown(by=sam_id)) == [("t1-2", "holo"), ("t1-2", "normal")]
    assert shown(method="manual", by=owner) == [("t1-1", "normal")]
    assert shown(origin="pack") == [("t1-2", "holo")]
    page = client.get("/collection?method=scan").text
    assert "Ivysaur" in page and "Bulbasaur" not in page


def test_pulls_are_from_a_pack(client):
    owner = _setup_with_set(client)
    pid = spending.create_purchase(main.conn, owner, "1 pack", 5, 1, "t1", None, by=owner)
    spending.add_pull(main.conn, owner, pid, "t1-1", "normal", "NM", 1, by=owner)
    [h] = inventory.holdings(main.conn, owner, "t1-1")
    assert h["origin"] == "pack" and [m["label"] for m in h["methods"]] == ["Added by hand by misty"]


def test_card_add_remembers_origin(client):
    owner = _setup_with_set(client)
    _entry(client, "t1", "1", "normal", "NM", "gift")
    assert inventory.holdings(main.conn, owner, "t1-1")[0]["origin"] == "gift"
    assert 'value="gift" selected' in client.get("/cards/t1-2").text  # the Add pop-up starts with it


# --- scanning -----------------------------------------------------------------

def _boxes(rows):
    return [scanner.Box(x=x, y=y, h=h, text=text) for x, y, h, text in rows]


# Text RapidOCR found on two real photos (positions in pixels).
NOSEPASS = _boxes([[305, 247, 24, "BASIC"], [407, 250, 53, "Nosepass"], [801, 264, 43, "90"], [782, 286, 18, "HP"],
                   [438, 639, 20, "NO. 0299 Compass Pokémon HT: 3'3° WT: 213.8 lbs."], [488, 796, 56, "Rolling Rocks"],
                   [262, 1065, 24, "weakness"], [261, 1126, 30, "Illus. Jerky"], [263, 1157, 34, "0"],
                   [290, 1156, 37, "POREN"], [351, 1165, 28, "037/8"],
                   [730, 1152, 35, "nearby iron objects to its body."],
                   [468, 1193, 31, "©2026 Pokémon /Nintendo / Creatures/ GAME FREAK"]])
SOBBLE = _boxes([[355, 455, 17, "BASIC"], [434, 450, 40, "Sobble"], [718, 448, 48, "OL"], [475, 862, 42, "Water Gun"],
                 [291, 1117, 23, "Illus.Saboteri"], [308, 1136, 44, "MEP EN 054"],
                 [572, 1126, 27, "pack the chemical punchof100 onions, and"], [1096, 1466, 34, "BASIC"]])
TAUROS = _boxes([[344, 510, 14, "BASIC"], [426, 509, 31, "Tauros"], [710, 502, 37, "130"], [692, 523, 15, "HP"],
                 [307, 1188, 27, "Illus. Tsuyoshi Nagano"], [329, 1212, 38, "CREN 096/086"]])


def test_read_text():
    r = scanner.read_text(NOSEPASS)
    assert (r.name, r.number, r.code, r.lang) == ("Nosepass", "037", "POR", "EN")
    r = scanner.read_text(SOBBLE)
    assert (r.name, r.number, r.total, r.code) == ("Sobble", "054", None, "MEP")
    r = scanner.read_text(TAUROS)
    assert (r.name, r.number, r.total, r.code) == ("Tauros", "096", "086", "CR")
    assert scanner.read_text([]).name is None


def test_match_by_code_total_and_name():
    sets = [{"id": "me03", "name": "Perfect Order", "official": 88}, {"id": "me04", "name": "Chaos Rising", "official": 86},
            {"id": "sv10.5w", "name": "White Flare", "official": 86}, {"id": "mep", "name": "MEP Black Star Promos", "official": 0}]
    cards = {
        "me03": [{"id": "me03-037", "number": "037", "name": "Nosepass", "image": None},
                 {"id": "me03-038", "number": "038", "name": "Probopass", "image": None}],
        "me04": [{"id": "me04-096", "number": "096", "name": "Tauros", "image": None},
                 {"id": "me04-069", "number": "069", "name": "Tauros", "image": None}],
        "sv10.5w": [{"id": "sv10.5w-096", "number": "096", "name": "Tepig", "image": None}],
        "mep": [{"id": "mep-054", "number": "054", "name": "Sobble", "image": None}],
    }
    def guess(boxes):
        return scanner.match(scanner.read_text(boxes), sets, lambda s: cards.get(s, []))
    g = guess(SOBBLE)
    assert g[0]["id"] == "mep-054" and g[0]["sure"]
    assert guess(NOSEPASS)[0]["id"] == "me03-037"  # '037/8': the code still finds the set
    g = guess(TAUROS)  # 'CR' isn't a code; the 86-card sets are tried and the name decides
    assert g[0]["id"] == "me04-096" and not g[0]["sure"]
    assert "sv10.5w-096" not in [x["id"] for x in g]  # same number, different name
    # No number read: the name search across sets is the fallback.
    no_number = scanner.read_text(_boxes([[400, 250, 50, "Probopass"]]))
    found = scanner.match(no_number, sets, lambda s: [],
                          lambda name: [dict(cards["me03"][1], set_id="me03")])
    assert found[0]["id"] == "me03-038"


class FakeReader:
    def __init__(self, boxes):
        self.boxes, self.closed = boxes, 0

    def read(self, path):
        assert path.exists()
        return self.boxes

    def close(self):
        self.closed += 1


IVYSAUR = _boxes([[300, 250, 24, "STAGE1"], [400, 250, 50, "Ivysaur"], [300, 1150, 30, "TSX EN 002/002"]])
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


def _scan_setup(client, monkeypatch, boxes=IVYSAUR):
    owner = _setup_with_set(client)
    reader = FakeReader(boxes)
    worker = scans.Worker(lambda lang: web.api, reader=reader)
    monkeypatch.setattr(web, "scan_worker", worker)
    r = client.post("/scans", follow_redirects=False)
    batch_id = int(r.headers["location"].split("?")[0].rsplit("/", 1)[1])
    scans.set_batch_origin(main.conn, batch_id, "pack")  # like a batch started before round 5, which asked
    return owner, batch_id, worker, reader


def test_scan_batch_add(client, monkeypatch):
    owner, batch_id, worker, reader = _scan_setup(client, monkeypatch)
    r = client.post(f"/scans/{batch_id}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg")),
                                                         ("photos", ("b.jpg", JPEG, "image/jpeg"))],
                    headers={"X-Scan-Upload": "1"})
    assert r.status_code == 200 and r.text.count('id="scan-') == 2
    worker.start(wait=True)
    assert reader.closed >= 1  # the reader lets go once the queue is empty
    first, second = sorted(r[0] for r in main.conn.execute("SELECT id FROM scans"))
    scan = scans.get_scan(main.conn, owner, first)
    assert scan["status"] == "ready" and scan["guesses"][0]["id"] == "t1-2"
    page = client.get(f"/scans/{batch_id}").text
    assert "2 photos &middot; 0 checked" in page and "Ivysaur" in page  # round 8: one batch screen
    assert f"/scans/item/{first}/details?open=1" in page and f"/scans/item/{first}/add" not in page  # no Add on the tiles
    assert "Add 2 to My Stash" in page and "Review Batch" not in page

    # Leaving and coming back: the batch list still shows them.
    assert "2 to confirm" in client.get("/scans").text

    photo = scans.photo_path(scan)
    r = client.post(f"/scans/item/{first}/add", data={"pick": 0, "variant": "reverse", "condition": "NM",
                                                    "quantity": 2, "origin": "pack"}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "Added" in r.text
    [h] = inventory.holdings(main.conn, owner, "t1-2")
    assert (h["variant"], h["quantity"], h["origin"]) == ("reverse", 2, "pack")  # an explicit choice still wins
    assert [m["label"] for m in h["methods"]] == ["Scanned by misty"]
    done = main.conn.execute("SELECT * FROM scans WHERE id = ?", (first,)).fetchone()
    assert (done["status"], done["photo"], done["read_json"], done["guesses_json"]) == ("added", None, None, None)
    assert not photo.exists()
    assert client.post(f"/scans/item/{first}/add", data={"pick": 0}).status_code == 400  # only once

    # Delete Scan keeps the photo until the batch is done (round 7).
    photo = scans.photo_path(scans.get_scan(main.conn, owner, second))
    client.post(f"/scans/item/{second}/skip")
    assert photo.exists()
    assert scans.get_scan(main.conn, owner, second)["status"] == "skipped"


def test_scan_no_match_then_find_it(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch, boxes=_boxes([[400, 250, 50, "Mystery"]]))
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg"))])
    worker.start(wait=True)
    [scan_id] = [r[0] for r in main.conn.execute("SELECT id FROM scans")]
    assert scans.get_scan(main.conn, owner, scan_id)["status"] == "nomatch"
    page = client.get(f"/scans/{batch_id}").text
    assert "No match yet" in page and f"/scans/item/{scan_id}/details" in page
    assert "1 needs a match" in page and "Add 0 to My Stash" in page and "to your collection…\" disabled>Add 0" in page
    assert client.get(f"/scans/{batch_id}/review", follow_redirects=False).headers["location"] == f"/scans/{batch_id}"
    details = client.get(f"/scans/item/{scan_id}/details", headers={"HX-Request": "true"}).text
    assert f"/scans/item/{scan_id}/found" in details and "Not there?" in details and "Enhanced" in details
    assert 'value="Mystery"' in details  # the Name box, filled from what was read
    # Picked by hand from the card page.
    assert "Use for this photo" in client.get(f"/cards/t1-1?scan={scan_id}").text
    client.post(f"/scans/item/{scan_id}/use", data={"card_id": "t1-1", "set_id": "t1", "lang": "en"})
    scan = scans.get_scan(main.conn, owner, scan_id)
    assert scan["status"] == "ready" and scan["guesses"][0]["id"] == "t1-1"
    client.post(f"/scans/item/{scan_id}/add", data={"pick": 0, "variant": "normal", "condition": "LP", "quantity": 1})
    [h] = inventory.holdings(main.conn, owner, "t1-1")
    assert (h["condition"], h["origin"]) == ("LP", "pack")  # the batch's "Got it from"


class PricedAPI(FakeAPI):
    """TCGplayer lists only the reverse holo of every card."""

    def get_card(self, card_id):
        return dict(super().get_card(card_id),
                    pricing={"tcgplayer": {"unit": "USD", "reverse-holofoil": {"marketPrice": 0.9}}})


def test_scan_add_uses_first_tcgplayer_printing(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    monkeypatch.setattr(web, "api", PricedAPI())
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg"))])
    worker.start(wait=True)
    [scan_id] = [r[0] for r in main.conn.execute("SELECT id FROM scans")]
    page = client.get(f"/scans/{batch_id}").text
    assert "Ivysaur" in page and 'name="variant"' not in page  # no pickers on the row
    r = client.post(f"/scans/item/{scan_id}/add", headers={"HX-Request": "true"})
    assert r.status_code == 200 and "Added" in r.text
    [h] = inventory.holdings(main.conn, owner, "t1-2")
    assert (h["variant"], h["condition"], h["quantity"], h["origin"]) == ("reverse", "NM", 1, "pack")


def test_text_search_tabs():
    sets = [{"id": "old", "name": "Old Set", "cardCount": {"official": 2}},
            {"id": "me03", "name": "Perfect Order", "cardCount": {"official": 88}}]
    cards = {
        "me03": [{"id": "me03-037", "number": "037", "name": "Nosepass", "image": None},
                 {"id": "me03-038", "number": "038", "name": "Probopass", "image": None},
                 {"id": "me03-001", "number": "001", "name": "Bulbasaur", "image": None},
                 {"id": "me03-099", "number": "099", "name": "Nosepass", "image": None}],
        "old": [{"id": "old-37", "number": "37", "name": "Nosepass", "image": None},
                {"id": "old-38", "number": "38", "name": "Nosepass", "image": None},
                {"id": "old-1", "number": "37", "name": "Pikachu", "image": None}],
    }

    class Lookup:
        def sets(self, lang="en"): return sets
        def codes(self, lang="en"): return {"POR": ["me03"]}
        def cards_of(self, set_id, lang="en"): return cards.get(set_id, [])
        def search_name(self, name, lang="en"):
            return [dict(c, set_id=s) for s, cs in cards.items() for c in cs if name.lower() in c["name"].lower()]
        def search_number(self, number, lang="en"):
            return [dict(c, set_id=s) for s, cs in cards.items() for c in cs if scanner.same_number(c["number"], number)]

    tabs = scans.text_tabs(Lookup(), "en", "Nosepass", "037/088", "por")
    shown = {t["key"]: [c["id"] for c in t["cards"]] for t in tabs}
    assert [t["label"] for t in tabs] == ["Name + Number", "Name + Set", "Set + Number"]
    assert shown["name_number"] == ["me03-037", "old-37"]  # all three first
    assert shown["name_set"] == ["me03-037", "me03-099"]
    assert shown["number_set"] == ["me03-037"]
    assert tabs[0]["cards"][0]["set_name"] == "Perfect Order"
    # Only a name read: the first tab falls back to name-only matches.
    tabs = scans.text_tabs(Lookup(), "en", "Pikachu")
    assert [c["id"] for c in tabs[0]["cards"]] == ["old-1"] and tabs[0]["fallback"]
    assert [len(t["cards"]) for t in scans.text_tabs(Lookup(), "en")] == [0, 0, 0]


def test_scan_details_tabs_pick_and_next(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch, boxes=_boxes([[400, 250, 50, "Mystery"]]))
    monkeypatch.setattr(scans_pages, "_lookup", {})
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg")),
                                                    ("photos", ("b.jpg", JPEG, "image/jpeg"))])
    worker.start(wait=True)
    first, second = sorted(r[0] for r in main.conn.execute("SELECT id FROM scans"))
    r = client.get(f"/scans/item/{first}/details", headers={"HX-Request": "true"})
    assert r.status_code == 200 and "No match yet" in r.text and 'value="Mystery"' in r.text and "<html" not in r.text
    assert "Name + Number <span class=\"n\">0</span>" in r.text
    r = client.get(f"/scans/item/{first}/found?name=&number=2&code=t1")
    assert "Set + Number <span class=\"n\">1</span>" in r.text and "Ivysaur" in r.text
    assert "<html" in client.get(f"/scans/item/{first}/details").text  # without JavaScript: its own page
    # Tapping a card makes it the match straight away.
    r = client.post(f"/scans/item/{first}/found", data={"card_id": "t1-2", "set_id": "t1", "lang": "en"},
                    headers={"HX-Request": "true"})
    assert r.status_code == 200 and "Picked by you" in r.text and 'name="variant"' in r.text  # the choices show
    assert scans.get_scan(main.conn, owner, first)["guesses"][0]["id"] == "t1-2"
    # Save & next opens the next photo (round 8).
    r = client.post(f"/scans/item/{first}/save", data={"variant": "normal", "condition": "NM", "quantity": 1, "next": "1"},
                    follow_redirects=False)
    assert r.headers["location"] == f"/scans/item/{second}/details?open=1"
    client.post(f"/scans/item/{first}/add")
    [h] = inventory.holdings(main.conn, owner, "t1-2")
    assert (h["variant"], h["condition"], h["quantity"]) == ("normal", "NM", 1)


def test_scan_changes_returns_only_changed_rows(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg")),
                                                    ("photos", ("b.jpg", JPEG, "image/jpeg"))])
    worker.start(wait=True)
    first, second = sorted(r[0] for r in main.conn.execute("SELECT id FROM scans"))
    data = client.get(f"/scans/{batch_id}/changes?since=0").json()
    assert {r["id"] for r in data["rows"]} == {first, second} and data["to_confirm"] == 2
    scans_pages.tile_prices.wait()  # the tiles' prices come in on a background thread, then the tiles are drawn again
    rev = client.get(f"/scans/{batch_id}/changes?since=0").json()["rev"]
    assert client.get(f"/scans/{batch_id}/changes?since={rev}").json()["rows"] == []
    client.post(f"/scans/item/{second}/skip")
    data = client.get(f"/scans/{batch_id}/changes?since={rev}").json()
    assert [r["id"] for r in data["rows"]] == [second] and "Deleted scan" in data["rows"][0]["html"]
    assert data["rev"] > rev and data["to_confirm"] == 1


def test_signed_out_background_request_redirects_page(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    client.post("/logout")
    r = client.get(f"/scans/{batch_id}/changes", headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.headers["HX-Redirect"] == "/login" and "<html" not in r.text
    assert client.get(f"/scans/{batch_id}", follow_redirects=False).headers["location"] == "/login"


def test_scan_rejects_other_files_and_other_collections(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    r = client.post(f"/scans/{batch_id}/photos", files=[("photos", ("a.txt", b"hello", "text/plain"))],
                    headers={"X-Scan-Upload": "1"})
    assert r.status_code == 400 and "isn" in r.text
    # Someone else's collection can't see the batch or photos.
    other = auth.create_user(main.conn, "stranger", "stranger123")
    s = _login("stranger", "stranger123")
    assert s.get(f"/scans/{batch_id}").status_code == 404


def test_scans_never_expire_and_resume(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    batch = scans.get_batch(main.conn, owner, batch_id)
    old = scans.add_photo(main.conn, batch, owner, JPEG)
    stuck = scans.add_photo(main.conn, batch, owner, JPEG)
    main.conn.execute("UPDATE scans SET created_at = datetime('now', '-400 days') WHERE id = ?", (old,))
    main.conn.execute("UPDATE scans SET status = 'reading' WHERE id = ?", (stuck,))
    main.conn.commit()
    path = scans.photo_path(scans.get_scan(main.conn, owner, old))
    scans.resume(main.conn, worker)
    worker.start(wait=True)
    assert scans.get_scan(main.conn, owner, old)["status"] == "ready" and path.exists()  # photos stay until you act
    assert scans.get_scan(main.conn, owner, stuck)["status"] == "ready"


def test_scan_list_sections_and_clearing(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    batch = scans.get_batch(main.conn, owner, batch_id)
    scans.add_photo(main.conn, batch, owner, JPEG)
    page = client.get("/scans").text
    assert "Being read" in page and "updates by itself" in page and "Refresh" in page
    worker.start(wait=True)
    page = client.get("/scans").text
    assert "Being read" not in page and "To confirm" in page and "1 to confirm" in page
    page = client.get(f"/scans/{batch_id}").text  # tapping a tile opens the card view; the camera opens from Scan more
    assert "/details?open=1" in page and 'id="cam"' in page and "Scan more" in page
    assert 'id="zoom"' not in page and "data-match-menu" not in page
    done = int(client.post("/scans", data={"lang": "ja"}, follow_redirects=False).headers["location"].split("?")[0].rsplit("/", 1)[1])
    page = client.get("/scans").text
    assert "Finished" in page and "Clear finished batches" in page and 'name="lang"' not in page  # round 12: no language chips
    assert 'class="st st-done"' in page and "Where they came from" not in page
    assert f'action="/scans/{done}/delete"' in page  # an ✕ on each batch
    client.post("/scans-finished/clear")
    assert [r[0] for r in main.conn.execute("SELECT id FROM scan_batches")] == [batch_id]
    assert "Cards are in" not in client.get(f"/scans/{batch_id}").text  # the language is set when it starts


def test_scan_shows_every_guess_and_adds_any_of_them(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    scan_id = scans.add_photo(main.conn, scans.get_batch(main.conn, owner, batch_id), owner, JPEG)
    worker.start(wait=True)
    scan = scans.get_scan(main.conn, owner, scan_id)
    second = dict(scan["guesses"][0], id="t1-1", number="1", name="Bulbasaur", sure=False)
    main.conn.execute("UPDATE scans SET guesses_json = ? WHERE id = ?", (json.dumps([scan["guesses"][0], second]), scan_id))
    main.conn.commit()
    page = client.get(f"/scans/{batch_id}").text
    assert "edge-maybe" in page and "% alike" not in page  # a Possible match gets a purple edge (round 8)
    details = client.get(f"/scans/item/{scan_id}/details", headers={"HX-Request": "true"}).text
    assert "Ivysaur" in details and "Bulbasaur" in details  # round 10: no View card
    assert "TCGplayer" in details and "PriceCharting" in details and "In your stash: 0" in details
    assert "Change match" in details and "Use this match" not in details and "Other guesses" in details
    details = client.get(f"/scans/item/{scan_id}/details?src=guess&pick=1", headers={"HX-Request": "true"}).text
    assert "Use this match" in details and 'name="pick" value="1"' in details
    # View best guess: the card page, with "Use for this photo".
    assert "Use for this photo" in client.get(f"/preview/en/t1-2?scan={scan_id}").text
    # Add from the compare view on the second guess.
    client.post(f"/scans/item/{scan_id}/add", data={"pick": 1})
    assert main.conn.execute("SELECT card_id FROM inventory WHERE user_id = ?", (owner,)).fetchone()[0] == "t1-1"


def test_scan_batch_add_all_from_the_grid(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    monkeypatch.setattr(web, "api", PricedAPI())
    batch = scans.get_batch(main.conn, owner, batch_id)
    first, second = (scans.add_photo(main.conn, batch, owner, JPEG) for _ in range(2))
    worker.start(wait=True)
    # Picking a second guess in the card view moves it to the front.
    scan = scans.get_scan(main.conn, owner, second)
    other = dict(scan["guesses"][0], id="t1-1", number="1", name="Bulbasaur", sure=False)
    main.conn.execute("UPDATE scans SET guesses_json = ? WHERE id = ?", (json.dumps([scan["guesses"][0], other]), second))
    main.conn.commit()
    r = client.post(f"/scans/item/{second}/match", data={"src": "guess", "pick": 1}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "Bulbasaur" in r.text and "Change match" in r.text
    assert scans.get_scan(main.conn, owner, second)["guesses"][0]["id"] == "t1-1"

    # Review your batch is gone: old links open the batch.
    assert client.get(f"/scans/{batch_id}/review", follow_redirects=False).headers["location"] == f"/scans/{batch_id}"
    page = client.get(f"/scans/{batch_id}").text
    assert "Add 2 to My Stash" in page and "From: <b>Not a purchase</b>" in page and "Binder: <b>none</b>" in page
    assert "$0.90" not in page.split('id="grid"')[0]  # no $ total for the batch
    # Save in the card view keeps the choices and marks the card checked.
    r = client.post(f"/scans/item/{first}/save", data={"variant": "reverse", "condition": "LP", "quantity": 3, "paid": "1.50"},
                    headers={"HX-Request": "true"})
    assert r.status_code == 204 and r.headers["HX-Trigger"] == "scan-picked"
    row = main.conn.execute("SELECT variant, condition, quantity, paid, checked FROM scans WHERE id = ?", (first,)).fetchone()
    assert tuple(row) == ("reverse", "LP", 3, 1.5, 1)
    assert scans.counts(main.conn, batch_id)["unchecked"] == [second]
    # From · Binder: a new purchase, kept on the batch. It needs what it was and the total.
    client.post(f"/scans/{batch_id}/from", data={"came": "new", "new_label": "", "new_cost": ""})
    r = client.post(f"/scans/{batch_id}/add", follow_redirects=False)
    assert "error=" in r.headers["location"]
    client.post(f"/scans/{batch_id}/from", data={"came": "new", "new_label": "Booster box", "new_cost": "20"})
    assert "New purchase &middot; Booster box" in client.get(f"/scans/{batch_id}").text

    r = client.post(f"/scans/{batch_id}/add", follow_redirects=False)
    assert r.headers["location"] == f"/scans/{batch_id}?added=1"
    [ivy] = inventory.holdings(main.conn, owner, "t1-2")
    assert (ivy["variant"], ivy["condition"], ivy["quantity"], ivy["acquired_price"], ivy["origin"]) == ("reverse", "LP", 3, None, "pack")
    [purchase] = spending.purchase_summaries(main.conn, owner)
    assert purchase["label"] == "Booster box" and purchase["cost"] == 20 and purchase["cards"] == 4
    costs = {r["card_id"]: r["cost_each"] for r in spending.pulls(main.conn, purchase)}
    assert costs == {"t1-2": 1.5, "t1-1": 15.5}  # its own Paid kept; the rest shares the total
    assert spending.totals(main.conn, owner)["total"] == 20  # pulls never count twice
    [bulba] = inventory.holdings(main.conn, owner, "t1-1")
    assert (bulba["variant"], bulba["condition"], bulba["quantity"]) == ("reverse", "NM", 1)  # first TCGplayer printing
    assert {r[0] for r in main.conn.execute("SELECT status FROM scans")} == {"added"}
    # The next Add from this batch goes to the same purchase.
    assert scans.get_batch(main.conn, owner, batch_id)["came"] == "add"
    page = client.get(f"/scans/{batch_id}?added=1").text
    assert "Added to your cards" in page and "Added &middot; <span data-n>2</span>" in page and "Delete Batch" in page
    assert "Add 0 to My Stash" in page


def test_card_view_cancel_puts_the_old_match_back_and_save_next_skips_reading(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    batch = scans.get_batch(main.conn, owner, batch_id)
    first, second, third = (scans.add_photo(main.conn, batch, owner, JPEG) for _ in range(3))
    worker.start(wait=True)
    main.conn.execute("UPDATE scans SET status = 'reading' WHERE id = ?", (second,))
    main.conn.commit()
    view = client.get(f"/scans/item/{first}/details?open=1", headers={"HX-Request": "true"}).text
    assert "1 of 3" in view and "Save &amp; next" in view and "Remove from batch" in view
    # A match picked by hand, then Cancel: the old match comes back and nothing is checked.
    client.post(f"/scans/item/{first}/found", data={"card_id": "t1-1", "set_id": "t1", "lang": "en"}, headers={"HX-Request": "true"})
    assert scans.get_scan(main.conn, owner, first)["guesses"][0]["id"] == "t1-1"
    r = client.post(f"/scans/item/{first}/cancel", headers={"HX-Request": "true"})
    assert r.status_code == 204 and r.headers["HX-Trigger"] == "scan-picked"
    scan = scans.get_scan(main.conn, owner, first)
    assert scan["guesses"][0]["id"] == "t1-2" and not scan["checked"] and not scan["undo_json"]
    # Save & next skips the photo still being read; on the last card it reads Save & finish.
    client.get(f"/scans/item/{first}/details?open=1", headers={"HX-Request": "true"})
    r = client.post(f"/scans/item/{first}/save", data={"variant": "normal", "condition": "NM", "quantity": 1, "next": "1"},
                    headers={"HX-Request": "true"})
    assert r.status_code == 200 and "3 of 3" in r.text and "Save &amp; finish" in r.text
    assert scans.get_scan(main.conn, owner, first)["checked"] == 1
    r = client.post(f"/scans/item/{third}/save", data={"variant": "normal", "condition": "NM", "quantity": 1, "next": "1"},
                    headers={"HX-Request": "true"})
    assert r.status_code == 204  # back to the grid
    # Save needs a match; Read again clears the ✓.
    assert client.post(f"/scans/item/{second}/save", data={"variant": "normal"}).status_code == 400
    scans.retry(main.conn, scans.get_scan(main.conn, owner, third))
    assert scans.get_scan(main.conn, owner, third)["checked"] == 0


def test_set_for_all_and_add_leaves_unmatched_photos(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    batch = scans.get_batch(main.conn, owner, batch_id)
    first, second, third = (scans.add_photo(main.conn, batch, owner, JPEG) for _ in range(3))
    worker.start(wait=True)
    main.conn.execute("UPDATE scans SET status = 'nomatch', guesses_json = '[]' WHERE id = ?", (second,))
    main.conn.commit()
    client.post(f"/scans/item/{third}/skip")  # a deleted scan keeps its photo while a photo still waits
    deleted_photo = scans.photo_path(scans.get_scan(main.conn, owner, third))
    assert client.post(f"/scans/{batch_id}/set-all", data={"condition": "MP", "paid": "2"}).status_code == 200
    rows = {r["id"]: (r["condition"], r["paid"], r["checked"]) for r in main.conn.execute("SELECT * FROM scans")}
    assert rows[first] == ("MP", 2.0, 0) and rows[second][0] is None and rows[third][0] is None  # matched, not added only
    counts = scans.counts(main.conn, batch_id)
    assert (counts["photos"], counts["needs_match"], counts["to_add"]) == (2, 1, 1)
    r = client.post(f"/scans/{batch_id}/add", follow_redirects=False)
    assert r.headers["location"] == f"/scans/{batch_id}?added=1"
    [h] = inventory.holdings(main.conn, owner, "t1-2")
    assert h["condition"] == "MP"
    assert scans.get_scan(main.conn, owner, second)["status"] == "nomatch"  # it stays in the batch
    assert deleted_photo.exists()


def test_from_binder_puts_cards_in_a_binder_unless_left_out(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    batch = scans.get_batch(main.conn, owner, batch_id)
    first, second = (scans.add_photo(main.conn, batch, owner, JPEG) for _ in range(2))
    worker.start(wait=True)
    client.post(f"/scans/{batch_id}/from", data={"came": "none", "to_binder": "new", "new_binder": "Trade binder"})
    view = client.get(f"/scans/item/{second}/details?open=1", headers={"HX-Request": "true"}).text
    assert "In the binder" in view
    client.post(f"/scans/item/{second}/save", data={"variant": "holo", "condition": "NM", "quantity": 1,
                                                     "in_binder_shown": "1"})  # switched off
    client.post(f"/scans/{batch_id}/add")
    [b] = binders.all_for(main.conn, owner)
    assert b["name"] == "Trade binder"
    placed = {r["variant"] for r in main.conn.execute(
        "SELECT i.variant FROM binder_copies bc JOIN inventory i ON i.id = bc.inventory_id")}
    assert placed == {"normal"}  # the holo was left out with In the binder
    assert scans.get_batch(main.conn, owner, batch_id)["binder"] == str(b["id"])  # the new binder is kept for the next Add


def test_likeness_words():
    assert scans.guess_word({"sure": True}) == "Very close"
    assert scans.guess_word({"sure": False}) == "Possible"
    assert scans.guess_word({"sure": False}, first=False) is None
    assert scans.guess_word({"by_hand": True}) is None
    words = scans.picture_words([{"score": 91}, {"score": 86}, {"score": 80}, {"score": 60}])
    assert words == ["Very close", "Possible", "Possible", "Long shot"]
    assert scans.picture_words([{"score": 88}, {"score": 86}]) == ["Possible", "Possible"]  # too close to call
    assert scans.picture_words([]) == []


def test_old_chinese_batches_are_deleted_once(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    chinese_batch = scans.create_batch(main.conn, owner, owner, None, "zh-cn")
    photo = scans.add_photo(main.conn, scans.get_batch(main.conn, owner, chinese_batch), owner, JPEG)
    path = scans.photo_path(scans.get_scan(main.conn, owner, photo))
    main.conn.execute("DELETE FROM app_meta WHERE key = 'chinese_batches_dropped'")
    main.conn.commit()
    assert scans.drop_chinese_batches(main.conn) == 1 and not path.exists()
    assert [r[0] for r in main.conn.execute("SELECT id FROM scan_batches")] == [batch_id]
    again = scans.create_batch(main.conn, owner, owner, None, "zh-cn")
    assert scans.drop_chinese_batches(main.conn) == 0 and scans.get_batch(main.conn, owner, again)


def test_clear_collection_clears_scans(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    batch = scans.get_batch(main.conn, owner, batch_id)
    scan_id = scans.add_photo(main.conn, batch, owner, JPEG)
    path = scans.photo_path(scans.get_scan(main.conn, owner, scan_id))
    client.post("/account/clear", data={"confirm": "CLEAR"})
    assert not path.exists() and main.conn.execute("SELECT COUNT(*) FROM scan_batches").fetchone()[0] == 0


def test_scans_page_without_reader(client, monkeypatch):
    _setup_with_set(client)
    monkeypatch.setattr(scanner.Reader, "available", staticmethod(lambda: False))
    monkeypatch.setattr(web, "scan_worker", scans.Worker(lambda lang: web.api))
    assert "pip install rapidocr onnxruntime" in client.get("/scans").text


def test_number_filter_stays_text():
    # TCGdex reads an all-digit value as a number and never matches '004'.
    from app.tcgdex import local_id_filter
    assert local_id_filter(["4", "04", "004"]) == "eq:4|04|004"
    assert local_id_filter("125") == "eq:125|125"
    assert local_id_filter("TG05") == "eq:TG05|TG05"


def test_backup_before_upgrade(tmp_path, monkeypatch):
    from app import db
    folder = tmp_path / "backups"
    fresh = db.connect(tmp_path / "new.db")
    assert backup.before_upgrade(fresh, folder) is None  # nothing to protect yet
    db.init(fresh)
    auth.create_user(fresh, "misty", "pikachu123", is_admin=True)
    made = backup.before_upgrade(fresh, folder)  # no version stored: older app
    assert made is not None and made.name.endswith("-upgrade.db")
    backup.mark_version(fresh)
    assert backup.before_upgrade(fresh, folder) is None  # same code, no copy
    monkeypatch.setattr(backup, "code_version", lambda: "something-new")
    assert backup.before_upgrade(fresh, folder) is not None
    copy = __import__("sqlite3").connect(made)
    assert copy.execute("SELECT username FROM users").fetchall() == [("misty",)]


def test_backup_restore_from_account_page(client, monkeypatch, tmp_path):
    monkeypatch.setattr(web, "BACKUPS", tmp_path / "backups")
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    page = client.post("/account/backups").text
    assert "Backed up to" in page and "Made by hand" in page
    [b] = backup.list_backups(web.BACKUPS)
    assert client.get(f"/account/backups/{b['name']}").content.startswith(b"SQLite format 3")
    assert client.get("/account/backups/..%2Ftest.db").status_code == 404

    client.post("/account/users", data={"username": "sam", "password": "squirtle99"})
    sam = _login("sam", "squirtle99")
    assert sam.post("/account/backups").status_code == 403
    assert "Backups" not in sam.get("/account").text

    assert "Nothing was restored" in client.post(f"/account/backups/{b['name']}/restore", data={"confirm": "yes"}).text
    r = client.post(f"/account/backups/{b['name']}/restore", data={"confirm": "RESTORE"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?restored=")
    assert [r[0] for r in main.conn.execute("SELECT username FROM users")] == ["misty"]  # sam came after the backup
    assert "Backup restored" in client.get(r.headers["location"]).text
    kinds = sorted(x["kind"] for x in backup.list_backups(web.BACKUPS))
    assert kinds == ["manual", "restore"]  # what was there before is kept

    # The copy made before the restore brings sam back.
    client.post("/login", data={"username": "misty", "password": "pikachu123"})
    safety = next(x for x in backup.list_backups(web.BACKUPS) if x["kind"] == "restore")
    r = client.post(f"/account/backups/{safety['name']}/restore", data={"confirm": "RESTORE"}, follow_redirects=False)
    assert r.status_code == 303, r.text[-600:]
    assert {r[0] for r in main.conn.execute("SELECT username FROM users")} == {"misty", "sam"}


def test_backups_keep_only_the_newest(tmp_path, monkeypatch):
    from app import db
    conn = db.connect(tmp_path / "x.db")
    db.init(conn)
    monkeypatch.setitem(backup.KEEP, "manual", 2)
    folder = tmp_path / "b"
    for i in range(4):
        (folder).mkdir(exist_ok=True)
        (folder / f"dexstash-2026010{i + 1}-000000-manual.db").write_bytes(b"")
    backup.make(conn, folder)
    names = [b["name"] for b in backup.list_backups(folder)]
    assert len(names) == 2 and names[1] == "dexstash-20260104-000000-manual.db"


def test_phone_menu_and_app_icon(client):
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    page = client.get("/chases").text
    assert 'class="tabs"' in page and '<a href="/collection" class="on"><svg' in page and ">Home</a>" in page
    assert "Import from CSV" in page and "Data sources" in page and "Sign out" in page and "More</summary>" not in page
    assert client.get("/manifest.webmanifest").json()["short_name"] == "dexSTASH"
    assert client.get("/static/icon-180.png").content.startswith(b"\x89PNG")


# --- Japanese and Chinese cards -------------------------------------------------

# Text RapidOCR found on two real photos: a Simplified Chinese card and a Japanese one.
FARIGIRAF_ZH = _boxes([[267, 293, 24, "1阶进化"], [359, 297, 47, "奇麒麟"], [470, 300, 44, "ex"], [703, 291, 56, "260"],
                       [365, 345, 20, "从颜解奇进化"], [372, 378, 17, "只要这只宝可梦，处于备战区，就不会受到招式的伤害。"],
                       [247, 1101, 30, "Illus,5ban Graphics"], [472, 1117, 26, "当宝可梦ex昏厥时，对手将本取2张奖赏卡。"],
                       [254, 1135, 18, "H"], [277, 1132, 23, "csv7C"], [323, 1135, 28, "141/204RR"]])
LOPUNNY_JA = _boxes([[157, 186, 35, "1進化"], [758, 185, 70, "110"], [269, 198, 58, "ミミロップ"], [730, 223, 25, "HP"],
                     [274, 259, 24, "ミミロルから進化"], [137, 1255, 26, "Illus. Oswaldo KATO"],
                     [558, 1248, 51, "暑い季節が終わると空気をたくさん"], [140, 1290, 25, "0"], [175, 1286, 32, "MIS"],
                     [230, 1295, 27, "054/063 C"], [531, 1282, 46, "答んだ体毛に生え変わり 寒さに備える。"],
                     [355, 1331, 23, "©2025 Pokémon/Nintendo/Creatures/GAME FREAK."]])
GEM_PACK_ZH = _boxes([[300, 190, 25, "基础"], [400, 192, 58, "煤炭龟"], [600, 190, 57, "130"],
                      [304, 1129, 27, "CBB84C"], [358, 1131, 31, "0602/07"], [599, 1128, 26, "在煤炭龟栖息着的山里"]])


def test_read_text_japanese_and_chinese():
    ja = scanner.set_codes_for([{"id": "M1S"}, {"id": "M1L"}, {"id": "M4"}, {"id": "SV2a"}])
    zh = scanner.set_codes_for([{"id": "CSV7C"}, {"id": "CBB4C"}])
    r = scanner.read_text(LOPUNNY_JA, ja)  # 'MIS' with the regulation mark read as '0'
    assert (r.name, r.number, r.total, r.code) == ("ミミロップ", "054", "063", "M1S")
    r = scanner.read_text(FARIGIRAF_ZH, zh)
    assert (r.name, r.number, r.total, r.code) == ("奇麒麟ex", "141", "204", "CSV7C")
    r = scanner.read_text(GEM_PACK_ZH, zh)  # 4-digit Gem Pack number, code one letter off
    assert (r.name, r.number, r.total, r.code) == ("煤炭龟", "0602", "07", "CBB4C")
    # English cards still need the language after the code.
    assert scanner.read_text(FARIGIRAF_ZH).code is None


class OfflineAPI:
    """TCGdex for a language it has no cards in (or can't be reached)."""

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise RuntimeError("not found")
        return fail


def _offline_languages(monkeypatch):
    real = web.api_for
    monkeypatch.setattr(web, "api_for", lambda lang: web.api if lang == "en" else (real(lang) and OfflineAPI()))


def test_add_card_by_hand(client, monkeypatch):
    owner = _setup_with_set(client)
    _offline_languages(monkeypatch)
    form = client.get("/cards/new?lang=zh-cn&number=141/204").text
    assert "Add a card by hand" in form and 'value="141/204"' in form
    r = client.post("/cards/new", data={"lang": "zh-cn", "code": "csv7c", "set_name": "利刃猛醒", "number": "141/204",
                                        "name": "奇麒麟ex", "rarity": "RR"}, follow_redirects=False)
    assert r.headers["location"] == "/cards/zh-cn:CSV7C-141"
    card = main.conn.execute("SELECT * FROM cards WHERE id = 'zh-cn:CSV7C-141'").fetchone()
    assert (card["name"], card["rarity"], card["is_main_set"], card["hand_added_by"]) == ("奇麒麟ex", "RR", 1, owner)
    s = main.conn.execute("SELECT * FROM sets WHERE set_id = 'zh-cn:CSV7C'").fetchone()
    assert (s["name"], s["language"], s["printed_total"]) == ("利刃猛醒", "zh-cn", 204)
    assert "Added by hand by misty" in client.get("/cards/zh-cn:CSV7C-141").text
    # Same set, a Gem Pack-style number; the set is found whatever the case typed.
    r = client.post("/cards/new", data={"lang": "zh-cn", "code": "CSV7C", "number": "0602/07", "name": "煤炭龟"},
                    follow_redirects=False)
    assert r.headers["location"] == "/cards/zh-cn:CSV7C-0602"
    assert "already" in client.post("/cards/new", data={"lang": "zh-cn", "code": "CSV7C", "number": "141",
                                                        "name": "x"}).text

    # It works like any card: copies, Find a card by number, and set completion.
    client.post("/cards/zh-cn:CSV7C-141/adjust", data={"variant": "holo", "condition": "NM", "delta": 1})
    assert inventory.holdings(main.conn, owner, "zh-cn:CSV7C-141")[0]["quantity"] == 1
    page = client.get("/search?q=141/204&lang=zh-cn").text
    assert "奇麒麟ex" in page and "/cards/zh-cn:CSV7C-141" in page
    assert "利刃猛醒" in client.get("/").text
    assert "Add a card that" in client.get("/sets/zh-cn:CSV7C").text

    # Edit; delete only once nobody has it.
    client.post("/cards/zh-cn:CSV7C-141/hand", data={"name": "奇麒麟 ex", "rarity": "RR"})
    assert main.conn.execute("SELECT name FROM cards WHERE id = 'zh-cn:CSV7C-141'").fetchone()[0] == "奇麒麟 ex"
    r = client.post("/cards/zh-cn:CSV7C-141/hand/delete", follow_redirects=False)
    assert "hand_error" in r.headers["location"]
    client.post("/cards/zh-cn:CSV7C-141/adjust", data={"variant": "holo", "condition": "NM", "delta": -1})
    client.post("/cards/zh-cn:CSV7C-141/hand/delete")
    client.post("/cards/zh-cn:CSV7C-0602/hand/delete")
    assert main.conn.execute("SELECT 1 FROM sets WHERE set_id = 'zh-cn:CSV7C'").fetchone() is None
    # TCGdex cards can't be edited or deleted this way.
    assert "hand_error" in client.post("/cards/t1-1/hand/delete", follow_redirects=False).headers["location"]


def test_price_update_skips_sets_made_by_hand(client, monkeypatch):
    owner = _setup_with_set(client)
    _offline_languages(monkeypatch)
    client.post("/cards/new", data={"lang": "zh-cn", "code": "CBB4C", "number": "0601/07", "name": "煤炭龟"})
    updated, errors = sync.refresh_all(main.conn, web.api_for)
    assert errors == []
    assert main.conn.execute("SELECT hand_added_by FROM cards WHERE id = 'zh-cn:CBB4C-0601'").fetchone()[0] == owner


def test_scan_chinese_batch(client, monkeypatch):
    owner = _setup_with_set(client)
    _offline_languages(monkeypatch)
    reader = FakeReader(FARIGIRAF_ZH)
    worker = scans.Worker(web.api_for, reader=reader)
    monkeypatch.setattr(web, "scan_worker", worker)
    batch_id = int(client.post("/scans", data={"lang": "zh-cn"}, follow_redirects=False)
                   .headers["location"].split("?")[0].rsplit("/", 1)[1])
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg"))])
    worker.start(wait=True)
    [scan_id] = [r[0] for r in main.conn.execute("SELECT id FROM scans")]
    assert scans.get_scan(main.conn, owner, scan_id)["status"] == "nomatch"  # TCGdex has no CSV7C cards
    page = client.get(f"/scans/item/{scan_id}/details", headers={"HX-Request": "true"}).text
    assert "By hand" in page and "code=CSV7C" in page and "lang=zh-cn" in page

    # Added by hand from the photo: it becomes the photo's pick.
    r = client.post("/cards/new", data={"lang": "zh-cn", "code": "CSV7C", "number": "141/204", "name": "奇麒麟ex",
                                        "scan": str(scan_id)}, follow_redirects=False)
    assert r.headers["location"] == f"/scans/{batch_id}#scan-{scan_id}"
    scan = scans.get_scan(main.conn, owner, scan_id)
    assert scan["status"] == "ready" and scan["guesses"][0]["id"] == "CSV7C-141"
    client.post(f"/scans/item/{scan_id}/add", data={"pick": 0, "variant": "holo", "condition": "NM", "quantity": 1})
    assert inventory.holdings(main.conn, owner, "zh-cn:CSV7C-141")[0]["quantity"] == 1

    # The next photo of that card is matched straight away.
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("b.jpg", JPEG, "image/jpeg"))])
    worker.start(wait=True)
    second = max(r[0] for r in main.conn.execute("SELECT id FROM scans"))
    g = scans.get_scan(main.conn, owner, second)["guesses"][0]
    assert (g["id"], g["lang"], g["sure"]) == ("CSV7C-141", "zh-cn", True)


def test_default_variant_never_picks_a_missing_normal():
    assert inventory.default_variant(["normal", "holo", "reverse"], ["holo", "reverse"]) == "holo"
    assert inventory.default_variant(["normal", "reverse"], ["normal", "reverse"], remembered="reverse") == "reverse"
    assert inventory.default_variant(["normal", "reverse"], ["reverse"], remembered="normal") == "reverse"
    assert inventory.default_variant(["holo"], []) == "holo"


def test_card_view_edits_a_line_and_adds_several(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakePricedAPI())
    user_id = _setup_with_set(client)
    client.post("/cards/t1-1/adjust", data={"variant": "normal", "condition": "NM", "delta": "3", "paid": "1.50"})
    page = client.get("/cards/t1-1").text
    assert "<b>Normal &times; 3</b>" in page and "paid $1.50" in page and "Add another" in page
    assert 'id="cv-add-dialog"' in page and "Modify price" in page and "api.tcgdex.net/v2/en/cards/t1-1" in page
    assert "pricecharting.com" in page and 'formaction="/wants/add"' in page

    r = client.post("/cards/t1-1/edit", data={"variant": "normal", "condition": "NM", "to_variant": "reverse",
                                              "to_condition": "LP", "origin": "trade", "paid": "2", "note": "Binder"})
    assert r.status_code == 200
    line = main.conn.execute("SELECT * FROM inventory WHERE user_id = ? AND card_id = 't1-1'", (user_id,)).fetchall()
    assert [(l["variant"], l["condition"], l["quantity"], l["origin"], l["acquired_price"]) for l in line] == \
        [("reverse", "LP", 3, "trade", 2.0)]
    assert main.conn.execute("SELECT variant, condition FROM notes WHERE text = 'Binder'").fetchone()[:] == (None, None)
    # Chase from the pinned bar chases the whole card, whatever printing is picked.
    client.post("/wants/add", data={"card_id": "t1-1", "set_id": "t1", "lang": "en", "variant": "reverse", "delta": "1"})
    assert main.conn.execute("SELECT variant FROM wants").fetchone()[0] == "any"
    assert 'class="cv-act chase on"' in client.get("/cards/t1-1").text


# --- mainland Chinese sets (PTCG-CHS-Datasets) ------------------------------------

def _chs_entry(cid, n, name, number, rarity, shiny=0):
    return {"id": cid * 1000 + n, "name": name, "image": f"img/{cid}/{n}.png",
            "details": {"cardName": name, "collectionNumber": number, "rarityText": rarity, "special_shiny_type": shiny}}


# Four real test cards, shaped like ptcg_chs_infos.json.
CHS_CATALOG = {"dict": {}, "collections": [
    {"id": 324, "commodityCode": "CSV7C", "name": "补充包 利刃猛醒", "salesDate": "2026-01-16", "cards": [
        _chs_entry(324, 0, "蔓藤怪", "001/204", "C"), _chs_entry(324, 1, "蔓藤怪", "001/204", "C★", 1),
        _chs_entry(324, 2, "蔓藤怪", "001/204", "C★★", 2), _chs_entry(324, 384, "奇麒麟ex", "141/204", "RR")]},
    {"id": 339, "commodityCode": "CBB4C", "name": "宝石包 VOL.4", "salesDate": "2026-02-06", "cards": [
        _chs_entry(339, 35, "煤炭龟", "06 01/07", "●"), _chs_entry(339, 36, "煤炭龟", "06 02/07", "●"),
        _chs_entry(339, 99, "龙头地鼠", "14 04/07", "★")]},
]}
CHS_TWINS = {"CSV7C": [{"cardIndex": "141", "cardName": "奇麒麟ex", "nameEn": "Farigiraf ex", "setCodeEn": "TEF",
                        "cardIndexEn": "108"}]}


def _png():
    from PIL import Image
    import io as _io
    b = _io.BytesIO()
    Image.new("RGB", (630, 880), (200, 30, 30)).save(b, "PNG")
    return b.getvalue()


@pytest.fixture
def chs(monkeypatch, tmp_path):
    """PTCG-CHS-Datasets and PTCG-card-data served from memory."""
    calls = []

    def fetch(url, timeout=120):
        calls.append(url)
        if url == chinese.CATALOG_URL:
            return json.dumps(CHS_CATALOG).encode(), '"v1"'
        if url.startswith(chinese.TWINS_BASE):
            code = url.rsplit("/", 1)[1].removesuffix(".json")
            if code not in CHS_TWINS:
                raise RuntimeError("404")
            return json.dumps(CHS_TWINS[code]).encode(), "md5-1"
        if url.startswith(chinese.IMAGE_BASE):
            return _png(), ""
        raise AssertionError(url)

    monkeypatch.setattr(chinese, "_get", fetch)
    monkeypatch.setattr(chinese, "FOLDER", tmp_path / "datasets")
    monkeypatch.setattr(chinese, "PICTURES", tmp_path / "chs")
    return calls


def _load(client):
    r = client.post("/account/data/chs/all", follow_redirects=False)
    assert r.status_code == 303
    for job in web.background.current():
        while job.running:
            time.sleep(0.01)
    return web.background.current()[0]


def test_load_chinese_set_with_star_printings_and_english_version(client, chs):
    owner = _setup_with_set(client)
    page = client.get("/account/data").text
    assert "Load all Chinese sets" in page and "Nothing is running" in page
    job = _load(client)
    assert job.error is None and job.result == "Loaded 2 Chinese sets."
    assert not (chinese.FOLDER / "ptcg_chs_infos.json").exists()  # only the set list is kept
    assert [s["code"] for s in chinese.known_sets()] == ["CBB4C", "CSV7C"]
    card = main.conn.execute("SELECT * FROM cards WHERE id = 'zh-cn:CSV7C-001'").fetchone()
    assert (card["name"], card["rarity"], card["has_normal"], card["has_star"], card["has_star2"], card["source"]) == \
        ("蔓藤怪", "C", 1, 1, 1, "chs")
    page = client.get("/cards/zh-cn:CSV7C-001").text
    chips = {m.group(2): "hidden" in m.group(1) for m in re.finditer(r'<label class="chip"([^>]*)><input type="radio" name="variant" value="(\w+)"', page)}
    assert chips["normal"] is False and chips["star"] is False and chips["star2"] is False and chips["reverse"] is True
    page = client.get("/cards/zh-cn:CSV7C-141").text
    assert "English version: Farigiraf ex" not in page and "PriceCharting" in page  # round 11 (P): no English version
    assert "No price" in page and "q=Farigiraf+ex+141+Chinese" in page  # its own number, not the twin's
    page = client.get("/account/data").text
    assert "Load all Chinese sets" not in page and "Chinese (mainland)</td><td class=\"num\">2 sets" in page
    # Gem Pack cards are numbered like the printed ones.
    gem = main.conn.execute("SELECT * FROM cards WHERE id = 'zh-cn:CBB4C-0601'").fetchone()
    assert gem["is_main_set"] == 1 and gem["rarity"] == "●"
    assert main.conn.execute("SELECT printed_total FROM sets WHERE set_id = 'zh-cn:CBB4C'").fetchone()[0] == 3
    # Pictures come from GitHub once: a small copy for cards nobody has (round 7), full size for cards someone has.
    assert gem["image_url"] == "/chs-img/339/35"
    r = client.get("/chs-img/339/35")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    small = len(chs)
    assert client.get("/chs-img/339/35").status_code == 200 and len(chs) == small
    assert chinese.pictures_size()[0] == 1
    client.post("/cards/zh-cn:CBB4C-0601/adjust", data={"variant": "normal", "condition": "NM", "delta": 1})
    client.get("/chs-img/339/35")
    fetched = len(chs)
    client.get("/chs-img/339/35")
    assert len(chs) == fetched and chinese.pictures_size()[0] == 1
    assert client.get("/chs-img/339/x").status_code == 404
    # A kept picture of a card nobody has any more goes at the next start.
    client.post("/cards/zh-cn:CBB4C-0601/adjust", data={"variant": "normal", "condition": "NM", "delta": -1})
    assert chinese.prune_pictures(main.conn) == 1 and chinese.pictures_size()[0] == 0


def test_chinese_load_merges_hand_cards(client, chs, monkeypatch):
    owner = _setup_with_set(client)
    _offline_languages(monkeypatch)
    client.post("/cards/new", data={"lang": "zh-cn", "code": "CSV7C", "number": "141/204", "name": "奇麒麟", "rarity": ""})
    client.post("/cards/zh-cn:CSV7C-141/adjust", data={"variant": "normal", "condition": "NM", "delta": 2})
    client.post("/cards/zh-cn:CSV7C-141/notes", data={"text": "from Shanghai"})
    _load(client)
    card = main.conn.execute("SELECT * FROM cards WHERE id = 'zh-cn:CSV7C-141'").fetchone()
    assert (card["name"], card["hand_added_by"], card["source"]) == ("奇麒麟ex", None, "chs")
    assert inventory.holdings(main.conn, owner, "zh-cn:CSV7C-141")[0]["quantity"] == 2
    assert notes.for_card(main.conn, owner, "zh-cn:CSV7C-141")[0]["text"] == "from Shanghai"


def test_chinese_scan_matches_a_loaded_set(client, chs, monkeypatch):
    owner = _setup_with_set(client)
    _offline_languages(monkeypatch)
    reader = FakeReader(FARIGIRAF_ZH)
    worker = scans.Worker(web.api_for, reader=reader)
    monkeypatch.setattr(web, "scan_worker", worker)
    monkeypatch.setattr(scans_pages, "_lookup", {})
    batch_id = int(client.post("/scans", data={"lang": "zh-cn"}, follow_redirects=False).headers["location"].split("?")[0].rsplit("/", 1)[1])
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg"))])
    worker.start(wait=True)
    [first] = [r[0] for r in main.conn.execute("SELECT id FROM scans")]
    assert scans.get_scan(main.conn, owner, first)["status"] == "nomatch"
    panel = client.get(f"/scans/item/{first}/details", headers={"HX-Request": "true"}).text
    assert "CSV7C isn't here yet" in panel and "Load all Chinese sets" in panel
    r = client.post("/account/data/chs/all", data={"back": f"/scans/{batch_id}"}, follow_redirects=False)
    assert r.headers["location"] == f"/scans/{batch_id}"
    _load(client)  # waits for it
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("b.jpg", JPEG, "image/jpeg"))])
    worker.start(wait=True)
    second = max(r[0] for r in main.conn.execute("SELECT id FROM scans"))
    g = scans.get_scan(main.conn, owner, second)["guesses"][0]
    assert (g["id"], g["lang"], g["sure"]) == ("CSV7C-141", "zh-cn", True)
    # A quick Add of a card with no price picks its first printing.
    client.post(f"/scans/item/{second}/add")
    [h] = inventory.holdings(main.conn, owner, "zh-cn:CSV7C-141")
    assert (h["variant"], h["condition"], h["quantity"]) == ("normal", "NM", 1)
    # Chinese cards aren't sent to TCGdex for prices.
    assert prices.missing(main.conn) == []


def test_data_sources_only_for_the_owner(client, chs):
    _setup_with_set(client)
    client.post("/account/users", data={"username": "sam", "password": "squirtle99"})
    sam = _login("sam", "squirtle99")
    assert sam.get("/account/data").status_code == 403
    assert sam.post("/account/data/chs/all").status_code == 403
    assert sam.post("/account/data/check").status_code == 403
    assert "Data sources" in client.get("/account").text and "Data sources" not in sam.get("/account").text
    r = client.get("/account/data/running")
    assert r.status_code == 200 and "Running now" in r.text


def test_check_for_updates_reloads_when_the_catalogue_changed(client, chs, monkeypatch):
    _setup_with_set(client)
    _load(client)
    monkeypatch.setattr(chinese, "_version", lambda url: '"v1"')
    client.post("/account/data/check")
    _load_wait()
    assert web.background.current()[0].result.endswith("Chinese sets are up to date.")
    monkeypatch.setattr(chinese, "_version", lambda url: '"v2"')
    CHS_CATALOG["collections"][0]["cards"].append(_chs_entry(324, 400, "新卡", "150/204", "R"))
    try:
        client.post("/account/data/check")
        _load_wait()
        assert web.background.current()[0].result.endswith("Loaded 2 Chinese sets.")
        assert main.conn.execute("SELECT name FROM cards WHERE id = 'zh-cn:CSV7C-150'").fetchone()[0] == "新卡"
    finally:
        CHS_CATALOG["collections"][0]["cards"].pop()


def _load_wait():
    for job in web.background.current():
        while job.running:
            time.sleep(0.01)


# --- picture search ---------------------------------------------------------------

from app import pictures  # noqa: E402

COLOURS = {"1": (200, 30, 30), "2": (30, 180, 40), "TG01": (30, 40, 200)}


def _card_png(colour, size=(126, 176), background=None):
    import io as _io
    from PIL import Image
    if background is None:
        image = Image.new("RGB", size, colour)
    else:  # a card in the middle of a plain photo
        image = Image.new("RGB", (size[0] * 3, size[1] * 2), background)
        image.paste(Image.new("RGB", size, colour), (size[0], size[1] // 2))
    out = _io.BytesIO()
    image.save(out, "PNG" if background is None else "JPEG")
    return out.getvalue()


class FakeEncoder:
    """Fingerprints from a picture's average colour, so a green photo matches the green card."""

    loaded = False

    def __init__(self):
        self.calls = 0

    def embed(self, images):
        import numpy as np
        self.calls += 1
        out = np.zeros((len(images), pictures.DIM), np.float32)
        for i, image in enumerate(images):
            r, g, b = np.asarray(image.convert("RGB"), np.float32).reshape(-1, 3).mean(0) / 255
            out[i, :3] = (r - 0.3, g - 0.3, b - 0.3)
            out[i, 3] = 0.05
        return out / np.linalg.norm(out, axis=1, keepdims=True)

    def unload_if_idle(self, now=None):
        return False


class NoSetsAPI(FakeAPI):
    def list_sets(self):
        return []


class FakePictureAPI(FakeAPI):
    def get_set(self, set_id):
        data = super().get_set(set_id)
        for c in data["cards"]:
            c["image"] = f"https://assets.example/en/{set_id}/{c['localId']}"
        return data


@pytest.fixture
def pics(client, monkeypatch, tmp_path):
    """Picture search with a fake model, its files in a temporary folder."""
    monkeypatch.setattr(web, "api", FakePictureAPI())
    monkeypatch.setitem(web._other_apis, "ja", NoSetsAPI())
    for name, sub in (("IMAGES", "card-images"), ("INDEX", "index"), ("MODELS", "models")):
        monkeypatch.setattr(pictures, name, tmp_path / sub)
    pictures.forget_loaded()
    encoder = FakeEncoder()
    monkeypatch.setattr(web, "picture_encoder", encoder)
    monkeypatch.setattr(web.picture_worker, "encoder", encoder)
    monkeypatch.setattr(pictures, "has_model", lambda: True)
    monkeypatch.setattr(pictures, "download_model", lambda job=None, url=None: None)
    fetched = []

    def fetch(url):
        fetched.append(url)
        return _card_png(COLOURS[url.split("/")[-2]])
    monkeypatch.setattr(pictures, "_get", fetch)
    yield fetched
    main.conn.execute("DELETE FROM app_meta WHERE key = 'pictures_on'")
    main.conn.commit()
    pictures.forget_loaded()


def test_picture_index_keeps_one_row_per_picture_and_survives_a_restart(tmp_path):
    import numpy as np
    index = pictures.Index("en", tmp_path)
    v = np.eye(pictures.DIM, dtype=np.float32)
    index.put([{"card": "a", "set": "s", "key": "k1"}, {"card": "b", "set": "s", "key": "k2"}], v[:2])
    index.put([{"card": "a", "set": "s", "key": "k1"}], v[5:6])  # the same picture again replaces it
    index.save()
    again = pictures.Index("en", tmp_path)
    assert len(again.rows) == 2 and again.keys() == {"k1", "k2"}
    (best, score), = again.search(v[5], k=1)[:1]
    assert best["card"] == "a" and score > 0.99
    assert again.keep_only(lambda r: r["card"] == "b") == 1 and again.sets() == {"s"}


def test_card_is_cropped_from_the_background_and_from_its_text():
    from PIL import Image
    import io as _io
    photo = Image.open(_io.BytesIO(_card_png((30, 180, 40), background=(20, 20, 24))))
    crop = pictures.background_crop(photo)
    assert crop is not None and abs(crop.width - 126) <= 4 and abs(crop.height - 176) <= 4
    boxes = [scanner.Box(x=110, y=40, h=20, w=120, text="Ivysaur"), scanner.Box(x=100, y=200, h=12, w=150, text="Razor Leaf"),
             scanner.Box(x=100, y=300, h=12, w=150, text="Weakness"), scanner.Box(x=100, y=360, h=10, w=60, text="TSX EN 002/002")]
    box = scanner.card_box(boxes, 400, 400)
    assert box and box[1] < 0.1 and box[3] > 0.9 and 0.3 < (box[2] - box[0]) < 0.8
    assert len(pictures.crops(photo, box)) == 2


def _all_jobs_done():
    while any(job.running for job in web.background.current()):
        time.sleep(0.01)


def test_turning_on_enhanced_search_fingerprints_every_set(client, pics):
    pytest.importorskip("onnxruntime")  # round 13: Enhanced Search needs the photo reader's libraries
    _setup_with_set(client)
    (pictures.IMAGES / "en" / "old").mkdir(parents=True)
    (pictures.IMAGES / "en" / "old" / "1.webp").write_bytes(b"kept by an earlier version")
    assert pictures.drop_kept_images() == 1 and not pictures.IMAGES.exists()
    r = client.get("/account/data")
    assert "Turn on Enhanced Search" in r.text and "every set in English, Japanese and Chinese" in r.text
    client.post("/account/data/pictures/on")
    _all_jobs_done()
    assert web.background.current()[0].result == "Fingerprinted 3 pictures."
    assert len(pics) == 3 and not pictures.IMAGES.exists()  # only fingerprints are kept
    rows = pictures.index_for("en").rows
    assert {r["card"] for r in rows} == {"t1-1", "t1-2", "t1-TG01"} and {r["name"] for r in rows} == {"Bulbasaur", "Ivysaur", "Venusaur"}
    r = client.get("/account/data")
    assert "3 pictures" in r.text and "Rebuild" not in r.text.replace("Rebuild downloaded data", "")  # (round 15c: the catalog's button)
    # Check for updates adds new sets; a set it already has all of isn't fetched again.
    client.post("/account/data/check")
    _all_jobs_done()
    time.sleep(0.05)
    _all_jobs_done()
    results = {job.label: job.result for job in web.background.current()}
    assert results["Adding new sets to Enhanced Search"] == "Enhanced Search is up to date." and len(pics) == 3
    client.post("/account/data/pictures/off")
    assert "Turn on Enhanced Search" in client.get("/account/data").text


def test_picture_search_queue_finds_the_card_and_badges_other_languages(client, monkeypatch, pics):
    pytest.importorskip("onnxruntime")  # round 13: Enhanced Search needs the photo reader's libraries
    owner, batch_id, worker, reader = _scan_setup(client, monkeypatch, boxes=[])
    _entry(client, "t1", "1")
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("g.jpg", _card_png((30, 180, 40), background=(20, 20, 24)), "image/jpeg"))])
    worker.start(wait=True)
    scan = main.conn.execute("SELECT * FROM scans WHERE batch_id = ?", (batch_id,)).fetchone()
    assert scan["status"] == "nomatch"
    panel = client.get(f"/scans/item/{scan['id']}/details", headers={"HX-Request": "true"}).text
    assert "Enhanced is off" in panel and f'/scans/item/{scan["id"]}/picture' not in panel

    client.post("/account/data/pictures/on")
    _load_wait()
    panel = client.get(f"/scans/item/{scan['id']}/details", headers={"HX-Request": "true"}).text
    assert f'action="/scans/item/{scan["id"]}/picture"' in panel
    r = client.post(f"/scans/item/{scan['id']}/picture", headers={"HX-Request": "true"})
    assert r.headers.get("HX-Trigger") == "scan-picked" and "Waiting for Enhanced Search" in r.text
    web.picture_worker.start(wait=True)
    row = main.conn.execute("SELECT status, picture_json FROM scans WHERE id = ?", (scan["id"],)).fetchone()
    assert row["status"] == "picture_ready"
    matches = json.loads(row["picture_json"])
    assert matches[0]["id"] == "t1-2" and matches[0]["lang"] == "en" and matches[0]["score"] > 90
    # Round 7: a batch only gets its own language's pictures; a Japanese batch has no English matches.
    import io
    from PIL import Image
    photo = Image.open(io.BytesIO(_card_png((30, 180, 40), background=(20, 20, 24)))).convert("RGB")
    assert pictures.search(main.conn, FakeEncoder(), photo, batch_lang="ja") == []
    assert pictures.search(main.conn, FakeEncoder(), photo, batch_lang="en")[0]["id"] == "t1-2"

    page = client.get(f"/scans/{batch_id}").text
    assert "No match yet" in page and "1 needs a match" in page
    details = client.get(f"/scans/item/{scan['id']}/details", headers={"HX-Request": "true"}).text
    assert "Very close" in details and "Enhanced Search</button>" in details and '"src": "picture"' in details
    assert "% alike" not in details
    r = client.post(f"/scans/item/{scan['id']}/match", data={"src": "picture", "pick": 0}, follow_redirects=False)
    assert r.status_code == 303
    assert scans.get_scan(main.conn, owner, scan["id"])["guesses"][0]["id"] == "t1-2"
    client.post(f"/scans/item/{scan['id']}/add")
    assert main.conn.execute("SELECT quantity FROM inventory WHERE card_id = 't1-2'").fetchone()[0] == 1


def test_queued_photo_can_be_removed_and_waits_after_a_restart(client, monkeypatch, pics):
    owner, batch_id, worker, reader = _scan_setup(client, monkeypatch, boxes=[])
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("g.jpg", JPEG, "image/jpeg"))])
    worker.start(wait=True)
    scan_id = main.conn.execute("SELECT id FROM scans WHERE batch_id = ?", (batch_id,)).fetchone()[0]
    main.conn.execute("UPDATE scans SET status = 'picture_searching' WHERE id = ?", (scan_id,))
    main.conn.commit()
    scans.resume(main.conn, worker, picture_worker=scans.PictureWorker(FakeEncoder(), ready=lambda: False))
    assert main.conn.execute("SELECT status FROM scans WHERE id = ?", (scan_id,)).fetchone()[0] == "picture_waiting"
    assert "Waiting for Enhanced Search" in client.get(f"/scans/{batch_id}").text
    client.post(f"/scans/item/{scan_id}/skip")
    status, photo = main.conn.execute("SELECT status, photo FROM scans WHERE id = ?", (scan_id,)).fetchone()
    assert status == "skipped" and photo  # kept for Put back (round 7)
    client.post(f"/scans/item/{scan_id}/put-back")
    assert main.conn.execute("SELECT status FROM scans WHERE id = ?", (scan_id,)).fetchone()[0] == "picture_waiting"


def test_card_added_by_hand_with_a_scan_photo_goes_in_the_index(client, monkeypatch, pics):
    pytest.importorskip("onnxruntime")  # round 13: Enhanced Search needs the photo reader's libraries
    owner, batch_id, worker, reader = _scan_setup(client, monkeypatch, boxes=[])
    client.post("/account/data/pictures/on")
    _load_wait()
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("g.jpg", _card_png((30, 180, 40), background=(20, 20, 24)), "image/jpeg"))])
    worker.start(wait=True)
    scan_id = main.conn.execute("SELECT id FROM scans WHERE batch_id = ?", (batch_id,)).fetchone()[0]
    r = client.post("/cards/new", data={"lang": "zh-cn", "code": "CSV9C", "number": "012/100", "name": "妙蛙草",
                                        "scan": str(scan_id), "use_scan_photo": "1"}, follow_redirects=False)
    assert r.status_code == 303
    _load_wait()
    rows = pictures.index_for("zh-cn").rows
    assert len(rows) == 1 and rows[0]["key"].startswith("hand:") and rows[0]["card"] == "zh-cn:CSV9C-012"


def test_picture_model_unloads_when_idle():
    encoder = pictures.Encoder(idle=300)
    encoder._session, encoder._used = object(), 1000.0
    assert not encoder.unload_if_idle(now=1100.0) and encoder.loaded
    assert encoder.unload_if_idle(now=1301.0) and not encoder.loaded


# --- 8 Oct redesign: Home, My cards, Chases, editing several cards ------------------

def test_home_stats_change_recent_and_review(client, monkeypatch):
    from datetime import date
    from app import home
    monkeypatch.setattr(web, "api", FakePricedAPI())
    user_id = _setup_with_set(client)
    _entry(client, "t1", "1 x2")
    _entry(client, "t1", "2")  # $1.50, no product id
    # An update 31 days ago at a lower price, and one 10 days ago: the one closest to 30 days back is used.
    main.conn.executemany("INSERT INTO price_snapshots (card_id, variant, source, price, captured_at) VALUES (?, ?, ?, ?, ?)",
                          [("t1-1", "normal", "tcgplayer", 0.10, "2026-09-07 10:00:00"),
                           ("t1-1", "normal", "tcgplayer", 0.15, "2026-09-28 10:00:00")])
    main.conn.commit()
    c = home.change(main.conn, user_id, today=date(2026, 10, 8))
    assert c == {"amount": 0.16, "percent": 80.0, "since": "2026-09-07"}  # 2 x (0.18 - 0.10)
    assert home.change(main.conn, user_id, today=date(2026, 9, 1)) is None  # nothing earlier to compare with

    s = home.stats(main.conn, user_id)
    assert (s["cards"], s["top"]["name"], s["top"]["price"]) == (3, "Ivysaur", 1.5)
    assert [r["name"] for r in home.recently_added(main.conn, user_id)] == ["Ivysaur", "Bulbasaur"]
    assert home.to_review(main.conn, user_id) == []  # both priced; a missing TCGplayer link no longer counts (round 7)
    page = client.get("/").text
    assert "Most valuable card" in page and "Recently added" in page and "Your sets" not in page  # round 11 (I2)
    assert "by misty" in page and 'name="set_id"' not in page  # the old Add set box is gone
    assert "0 cards with no price" in client.get("/review").text and "Show all" not in page


def test_home_hides_empty_sections(client):
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    page = client.get("/").text
    assert "Recently added" not in page and "Cards to review" not in page and "Your top sets" not in page
    assert "No cards yet." in page


def test_my_cards_language_and_price_filters(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakePricedAPI())
    user_id = _setup_with_set(client)
    _entry(client, "t1", "1")   # $0.18
    _entry(client, "t1", "2", "masterball")  # ≈ Normal's $1.50
    main.conn.execute("DELETE FROM prices WHERE card_id = 't1-2'")  # as if TCGdex had none
    rows = lambda **kw: [r["name"] for r in collection.cards(main.conn, user_id, **kw)["rows"]]
    assert rows(price="lt5") == ["Bulbasaur"] and rows(price="none") == ["Ivysaur"] and rows(price="50") == []
    assert rows(language="en", sort="newest") == ["Ivysaur", "Bulbasaur"] and rows(language="ja") == []
    page = client.get("/collection?price=lt5&language=en").text
    assert "Under $5 <svg class=\"ic\"" in page and "English <svg class=\"ic\"" in page and '<span class="badge">2</span>' in page
    assert ">Cards</a>" in page and ">Chases</a>" in page and ">Spending</a>" in page and "Select all 1</a>" in page  # round 13: every card the filters match


def test_bulk_paid_note_and_spending(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakePricedAPI())
    user_id = _setup_with_set(client)
    _entry(client, "t1", "1 x2")
    _entry(client, "t1", "2", "normal", "LP")
    lines = ["t1-1|normal|NM", "t1-2|normal|LP"]
    paid = lambda: {r["card_id"]: r["acquired_price"] for r in main.conn.execute("SELECT * FROM inventory")}

    r = client.post("/collection/bulk", data={"action": "paid", "lines": lines, "paid": "2", "mode": "each", "back": "sort=name"})
    assert "Set the price paid on 3 copies." in r.text and paid() == {"t1-1": 2.0, "t1-2": 2.0}
    assert str(r.url).split("?", 1)[1].startswith("sort=name&done=")
    client.post("/collection/bulk", data={"action": "paid", "lines": lines, "paid": "$10", "mode": "split"})
    assert paid() == {"t1-1": 3.33, "t1-2": 3.33}  # $10 over three copies

    client.post("/collection/bulk", data={"action": "note", "lines": lines + ["t1-1|holo|NM"], "note": "binder 2"})
    assert sorted(r[0] for r in main.conn.execute("SELECT card_id FROM notes WHERE text = 'binder 2'")) == ["t1-1", "t1-2"]

    r = client.post("/collection/bulk", data={"action": "spending", "lines": lines[:1], "purchase_id": "new",
                                              "new_label": "Card show", "new_cost": "9"})
    assert "Added 2 copies to “Card show”." in r.text
    [p] = spending.purchase_summaries(main.conn, user_id)
    assert (p["label"], p["cost"], p["cards"]) == ("Card show", 9.0, 2)
    assert paid()["t1-1"] is None and inventory.holdings(main.conn, user_id, "t1-1")[0]["quantity"] == 2  # nothing added
    assert {r["card_id"]: r["paid"] for r in collection.cards(main.conn, user_id)["rows"]}["t1-1"] == 9.0
    # Into the existing entry: the copy leaves no other entry behind, and the cost is shared.
    client.post("/collection/bulk", data={"action": "spending", "lines": lines, "purchase_id": str(p["id"])})
    assert spending.purchase_summaries(main.conn, user_id)[0]["cards"] == 3
    totals = spending.totals(main.conn, user_id)
    assert (totals["packs"], totals["singles"], totals["total"]) == (9.0, 0, 9.0)
    assert client.post("/collection/bulk", data={"action": "paid", "lines": []}).status_code == 400


def test_migration_merges_wants_into_chases(tmp_path):
    from app import db
    conn = db.connect(tmp_path / "w.db")
    db.init(conn)
    conn.execute("INSERT INTO users (username, password_hash, is_admin) VALUES ('misty', 'x', 1)")
    conn.execute("INSERT INTO sets (set_id, name, printed_total, total) VALUES ('s', 'S', 1, 1)")
    conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name) VALUES ('s-1', 's', '1', 1, 1, 'A')")
    conn.execute("ALTER TABLE wants ADD COLUMN max_price REAL")  # a database from before round 10
    conn.executemany("INSERT INTO wants (user_id, card_id, variant, max_price) VALUES (1, 's-1', ?, ?)",
                     [("holo", 20), ("reverse", None), ("any", 5)])
    conn.execute("INSERT INTO notes (collection_id, card_id, variant, condition, text) VALUES (1, 's-1', 'holo', 'NM', 'x')")
    conn.commit()
    conn.execute("PRAGMA user_version = 0")  # round 12: a database from before the numbered steps
    db.init(conn)
    assert [tuple(r) for r in conn.execute("SELECT card_id, variant FROM wants")] == [("s-1", "any")]
    assert "max_price" not in [c[1] for c in conn.execute("PRAGMA table_info(wants)")]  # round 10: dropped
    assert [tuple(r) for r in conn.execute("SELECT variant, condition FROM notes")] == [(None, None)]


# --- 8 Oct redesign: card page, links, prices and pictures you add -------------------

def test_clean_link():
    from app import extras
    assert extras.clean_link("tcgplayer.com/product/123/pokemon-x?Language=English") == \
        ("tcgplayer", "https://www.tcgplayer.com/product/123")
    assert extras.clean_link("https://www.pricecharting.com/game/pokemon-chinese-stellar-miracle/farigiraf-ex-141?q=1") == \
        ("pricecharting", "https://www.pricecharting.com/game/pokemon-chinese-stellar-miracle/farigiraf-ex-141")
    assert extras.clean_link("") is None
    for bad in ("https://www.tcgplayer.com/search?q=x", "file:///etc/passwd", "just some words"):
        with pytest.raises(ValueError):
            extras.clean_link(bad)
    for other in ("https://evil.example/product/1", "https://pricecharting.com.evil.example/game/x"):  # round 14: kept, never read
        assert extras.clean_link(other) == ("other", other)


def test_card_extras_price_and_pictures(client, monkeypatch):
    from app import extras
    monkeypatch.setattr(web, "api", FakePricedAPI())
    user_id = _setup_with_set(client)
    _entry(client, "t1", "1", "masterball")  # no TCGdex price
    _entry(client, "t1", "1")      # $0.18

    # Round 15b: a PriceCharting link is a note; its price is the one you type with it.
    r = client.post("/cards/t1-1/extras", data={"link": "pricecharting.com/game/pokemon/bulbasaur-1"})
    assert "Saved the PriceCharting link." in r.text
    client.post("/cards/t1-1/extras", data={"pc_form": "1", "pc_price": "1234.50", "variant": "masterball"})
    assert _typed("t1-1", "pricecharting") == {"masterball": 1234.5}  # round 20: for the printing picked
    # The Master Ball copy counts at that price, the Normal one at TCGdex's: a typed price isn't spread.
    v = prices.collection_value(main.conn, user_id)
    assert (round(v["value"], 2), v["unpriced"]) == (1234.68, 0)
    # An Update prices keeps it (TCGdex prices are stored again first).
    sync.refresh_all(main.conn, lambda lang: web.api)
    assert round(prices.collection_value(main.conn, user_id)["value"], 2) == 1234.68
    assert "PriceCharting · you" in client.get("/cards/t1-1").text

    # Round 18: your own price counts too (round 20: for the printing you own first, Normal);
    # removing the link takes its price away.
    client.post("/cards/t1-1/extras", data={"manual_price": "4"})
    assert _typed("t1-1") == {"normal": 4.0}
    assert round(prices.collection_value(main.conn, user_id)["value"], 2) == 1236.59
    client.post("/cards/t1-1/extras", data={"remove": "pricecharting"})
    assert _typed("t1-1", "pricecharting") == {}
    assert "Your price · today" not in client.get("/cards/t1-1").text  # two sources: the chip is the average
    with pytest.raises(ValueError):
        extras.save(main.conn, "t1-1", user_id, tcgplayer_url="https://www.pricecharting.com/game/x")

    # Each Modify pop-up sends only its own fields; the rest is kept.
    client.post("/cards/t1-1/extras", data={"tcgplayer_url": "https://www.tcgplayer.com/product/77/x", "pricecharting_url": ""})
    e = extras.get(main.conn, "t1-1")
    assert (e["tcgplayer_url"], _typed("t1-1")) == ("https://www.tcgplayer.com/product/77", {"normal": 4.0})
    assert not any(r["id"] == "t1-1" and "TCGplayer link" in r["reason"] for r in home_page.to_review(main.conn, user_id))
    client.post("/cards/t1-1/extras", data={"manual_price": "5"})
    assert extras.get(main.conn, "t1-1")["tcgplayer_url"] == "https://www.tcgplayer.com/product/77"
    page = client.get("/cards/t1-1").text
    assert "Modify links" in page and "Undo customization" in page and ">Chases</h2>" not in page
    # Undo customization: back to TCGdex.
    r = client.post("/cards/t1-1/extras/reset")
    assert "Back to what TCGdex has." in r.text and extras.get(main.conn, "t1-1") is None
    assert round(prices.collection_value(main.conn, user_id)["value"], 2) == 2.68  # Master Ball ≈ Poké Ball's
    assert "Undo customization" not in r.text

    card = {"image_url": None, "pricing_json": json.dumps(PRICED_CARD["pricing"])}
    pics = extras.pictures(card, {"photo": "/card-photos/p.jpg", "tcgplayer_url": None}, ("E", "English version"))
    assert pics == [(extras.tcgplayer_picture(1), "TCGplayer picture"),
                    ("E", "English version"), ("/card-photos/p.jpg", "Your photo")]


def test_card_page_from_find_and_set_colors(client):
    user_id = _setup_with_set(client)
    page = client.get("/cards/t1-1?from=find").text
    assert '<a href="/search?lang=en" class="on">' in page and "Not in your stash" in page
    assert '<a href="/collection" class="on"><svg' in client.get("/cards/t1-1").text  # a line icon since round 25
    grid = client.get("/sets/t1").text
    assert "grayscale" not in grid and "api.tcgdex.net/v2/en/sets/t1" in grid  # a set you don't own: all in color
    _entry(client, "t1", "1")
    grid = client.get("/sets/t1").text
    assert "grayscale" not in grid and grid.count('class="ctile owned"') == 1  # the ring, never grey


def test_find_shows_matching_sets_and_previews_them(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakeSearchAPI())
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    page = client.get("/search", params={"q": "dotted"}).text
    assert "/set-preview/en/sv03.5" in page and "Dotted Set" in page
    assert "/set-preview/" not in client.get("/search", params={"q": "1"}).text  # numbers don't match sets
    # A set that isn't stored: every card in color, each opening its preview.
    page = client.get("/set-preview/en/t1").text
    assert "/preview/en/t1-1" in page and "grayscale" not in page and "You don't have any yet" in page
    assert '<a href="/search?lang=en" class="on"' in page  # Find stays lit, and tapping it starts over
    assert '<a href="/search" class="on"' not in page
    # Once stored, the set's own page opens instead.
    client.post("/search/add", data={"card_id": "t1-1", "set_id": "t1", "variant": "normal", "condition": "NM"})
    r = client.get("/set-preview/en/t1", follow_redirects=False)
    assert r.headers["location"] == "/sets/t1?from=find"
    assert "/sets/t1?from=find" in client.get("/search", params={"q": "test set"}).text
    # The filters are in the Filter panel, with a chip for each one that's on.
    page = client.get("/search", params={"q": "1", "lang": "ja", "owned": "no"}).text
    assert 'data-filter-toggle' in page and '<span class="badge">1</span>' in page and "Not owned <svg class=\"ic\"" in page


def test_check_for_updates_fills_in_sets_loaded_one_at_a_time(client, chs, monkeypatch):
    _setup_with_set(client)
    _load(client)
    main.conn.execute("DELETE FROM datasets WHERE id = 'chs:CBB4C'")  # as if only CSV7C had been loaded before
    main.conn.commit()
    monkeypatch.setattr(chinese, "_version", lambda url: '"v1"')
    client.post("/account/data/check")
    _load_wait()
    assert web.background.current()[0].result.endswith("Loaded 2 Chinese sets.")
    assert chinese.loaded_codes(main.conn) == ["CBB4C", "CSV7C"]


def test_import_set_picker_lists_mainland_sets(client, chs):
    _setup_with_set(client)
    _load(client)
    page = client.get("/imports/set-options", params={"lang": "zh"}).text
    assert 'value="zh-cn:CSV7C"' in page and "mainland" in page


def test_shared_connection_survives_many_threads(tmp_path):
    """Many request threads on the app's one connection: no 'API misuse'
    errors and no empty lookups (an empty user lookup signed people out)."""
    import threading
    conn = db.SharedConnection(db.connect(tmp_path / "race.db"))
    conn.execute("CREATE TABLE u (id INTEGER PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO u (name) VALUES (?)", [(f"n{i}",) for i in range(100)])
    conn.commit()
    errors = []

    def run(k):
        for i in range(500):
            want = (i * 7 + k) % 100 + 1
            try:
                row = conn.execute("SELECT id FROM u WHERE id = ?", (want,)).fetchone()
                if row is None or row[0] != want:
                    errors.append(want)
            except Exception as e:
                errors.append(e)
    threads = [threading.Thread(target=run, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert [r[0] for r in conn.execute("SELECT id FROM u WHERE id < 3")] == [1, 2]


# --- Pokémon themes -------------------------------------------------------------

def _sprite_png(colors):
    """A small fake sprite: blocks of colour on a transparent background, with a dark outline."""
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([10, 10, 86, 86], fill=(16, 16, 16, 255))
    for i, c in enumerate(colors):
        d.rectangle([12, 12 + i * 18, 84, 28 + i * 18], fill=(*c, 255))
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


SPRITES = {1: [(57, 148, 148), (57, 148, 148), (115, 172, 49)], 25: [(248, 208, 48), (248, 208, 48), (200, 40, 40)],
           94: [(112, 88, 152), (112, 88, 152), (230, 80, 80)]}


class FakeResponse:
    def __init__(self, content, status_code=200):
        self.content, self.status_code = content, status_code


def _fake_get(url):
    species = int(url.rsplit("/", 1)[1].split(".")[0])
    if species not in SPRITES:
        return FakeResponse(b"", 404)
    colors = SPRITES[species] if "/shiny/" not in url else [tuple(255 - v for v in c) for c in SPRITES[species]]
    return FakeResponse(_sprite_png(colors))


@pytest.fixture
def sprites(tmp_path, monkeypatch):
    from app import themes
    monkeypatch.setattr(themes, "FOLDER", tmp_path / "sprites")
    return themes


def test_palette_contrast_rules():
    from app import themes
    from PIL import Image
    for colors in list(SPRITES.values()) + [[(250, 250, 120)], [(20, 40, 160)], [(240, 200, 220), (30, 30, 30)]]:
        p = themes.palette(Image.open(io.BytesIO(_sprite_png(colors))))
        assert themes.contrast(p["light"]["accent"], p["light"]["on"]) >= 4.5
        assert themes.contrast(p["light"]["ink"], "#ffffff") >= 4.5
        assert themes.contrast(p["dark"]["accent"], "#1c1c1f") >= 4.5
        assert themes.contrast(p["dark"]["accent"], p["dark"]["on"]) >= 4.5
    b = themes.BULBASAUR
    assert (b["light"]["accent"], b["light"]["ink"], b["light"]["on"]) == ("#399494", "#328282", "#1d1d1f")


def test_theme_defaults_to_bulbasaur_without_sprites(client, sprites):
    _setup_with_set(client)
    page = with_assets(client.get("/").text)
    assert "--accent:#399494" in page and 'class="poke-tile"' not in page  # Bulbasaur colours, no tile until there's a sprite
    assert "--danger-text:#d62d20" in page and ".error { color:var(--danger-text); }" in page  # errors stay red (round 26: a lighter red in Dark)
    page = with_assets(client.get("/account").text)
    assert "Always the same Pokémon" in page and "A new Pokémon every day" in page and "#001" in page
    assert "Only Pokémon from My Stash" in page and "Shiny colors now and then" in page


def test_sprites_download_and_theme_applies(client, sprites):
    owner = _setup_with_set(client)
    result = sprites.download(main.conn, get=_fake_get, ids=[1, 25, 94, 95])
    assert "6 sprites downloaded" in result and "2 couldn't be fetched" in result
    assert sprites.counts(main.conn) == {"normal": 3, "shiny": 3, "at": sprites.counts(main.conn)["at"]}
    assert sprites.download(main.conn, get=lambda url: 1 / 0, ids=[1, 25, 94]).startswith("0 sprites")  # once only
    page = client.get("/").text
    assert 'class="poke-tile"' in page and 'src="/sprites/1.png"' in page and "<button" not in page.split('class="poke-tile"')[1].split("</div>")[0]
    assert client.get("/sprites/1.png").headers["content-type"] == "image/png"
    assert client.get("/sprites/999.png").status_code == 404

    r = client.post("/account/theme", data={"mode": "fixed", "pokemon": "#025 Pikachu"}, follow_redirects=False)
    assert r.headers["location"] == "/account?theme=saved#theme"
    pikachu = sprites.get_palette(main.conn, 25)
    page = client.get("/").text
    assert f"--accent:{pikachu['light']['accent']}" in page and 'src="/sprites/25.png"' in page
    assert client.post("/account/theme", data={"mode": "fixed", "pokemon": "Notamon"}).status_code == 400
    # A Pokémon without a palette yet falls back to Bulbasaur.
    sprites.save_user_setting(main.conn, owner, "fixed", 150)
    assert sprites.for_user(main.conn, owner)["species_id"] == 1


def test_daily_pick_is_shared_and_never_repeats(client, sprites):
    import datetime as dt
    import random
    owner = _setup_with_set(client)
    client.post("/account/users", data={"username": "sam", "password": "pikachu123"})
    sam = main.conn.execute("SELECT id FROM users WHERE username = 'sam'").fetchone()[0]
    sprites.download(main.conn, get=_fake_get, ids=[1, 25, 94])
    for user_id in (owner, sam):
        sprites.save_user_setting(main.conn, user_id, "daily", 1)
    day = dt.date(2026, 10, 9)
    assert sprites.for_user(main.conn, owner, today=day)["species_id"] == sprites.for_user(main.conn, sam, today=day)["species_id"]
    rng = random.Random(4)
    picks = [sprites.daily_pick(main.conn, day + dt.timedelta(days=i), rng=rng)["species_id"] for i in range(60)]
    assert all(a != b for a, b in zip(picks, picks[1:])) and len(set(picks)) == 3
    assert sprites.daily_pick(main.conn, day)["species_id"] == picks[0]  # stored: a restart keeps the day's pick

    # Only Pokémon from My Stash, with one owned species: the pick repeats without errors.
    sprites.save_shared_settings(main.conn, sam, True, False)
    assert sprites.shared_settings(main.conn)["updated_by_name"] == "sam"
    later = day + dt.timedelta(days=100)
    picks = [sprites.daily_pick(main.conn, later + dt.timedelta(days=i), owned=lambda: {25})["species_id"] for i in range(5)]
    assert picks == [25] * 5


def test_owned_species_from_card_names(client, tmp_path):
    from app import themes
    csv = tmp_path / "species.csv"
    csv.write_text("pokemon_species_id,local_language_id,name,genus\n2,9,Ivysaur,Seed\n2,1,フシギソウ,x\n1,9,Bulbasaur,Seed\n")
    _setup_with_set(client)
    _entry(client, "t1", "2")  # Ivysaur
    assert themes.owned_species(main.conn, Names(csv, url="http://unused")) == {2}
    assert Names(csv, url="http://unused").species_id("フシギソウex") == 2


ROUND3_NAMES = ("pokemon_species_id,local_language_id,name,genus\n"
                "1,9,Bulbasaur,Seed\n1,1,フシギダネ,x\n6,9,Charizard,Flame\n6,1,リザードン,x\n6,12,喷火龙,x\n"
                "428,9,Lopunny,Rabbit\n428,1,ミミロップ,x\n154,9,Meganium,Herb\n154,1,メガニウム,x\n")


def test_card_names_in_english(tmp_path):
    csv = tmp_path / "species.csv"
    csv.write_text(ROUND3_NAMES)
    n = Names(csv, url="http://unused")
    assert n.to_english("リザードンex") == "Charizard ex"
    assert n.to_english("メガリザードンex") == "Mega Charizard ex"
    assert n.to_english("メガニウム") == "Meganium"          # a Pokémon whose name starts with Mega
    assert n.to_english("喷火龙V") == "Charizard V"
    assert n.to_english("ボスの指令") is None                 # Trainers stay as printed


def _foreign_card(user_id, card_id, set_id, lang, name, twin=None, image=None):
    main.conn.execute("INSERT OR IGNORE INTO sets (set_id, language, name, printed_total, total) VALUES (?, ?, 'Foreign set', 99, 99)",
                      (set_id, lang))
    main.conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name, has_normal, twin_name, image_url) "
                      "VALUES (?, ?, '054', 54, 1, ?, 1, ?, ?)", (card_id, set_id, name, twin, image))
    main.conn.execute("INSERT INTO inventory (user_id, card_id) VALUES (?, ?)", (user_id, card_id))


def test_my_cards_grid_names_and_sprites(client, sprites, tmp_path, monkeypatch):
    csv = tmp_path / "species.csv"
    csv.write_text(ROUND3_NAMES)
    monkeypatch.setattr(web, "names", Names(csv, url="http://unused"))
    owner = _setup_with_set(client)
    sprites.download(main.conn, get=_fake_get, ids=[1, 25, 94])
    _foreign_card(owner, "ja:M1S-054", "ja:M1S", "ja", "ミミロップ", image="http://x/m.webp")
    _foreign_card(owner, "zh-cn:CSV7C-141", "zh-cn:CSV7C", "zh-cn", "老大的指令", twin="Boss's Orders")
    _foreign_card(owner, "ja:M1S-055", "ja:M1S", "ja", "ボスの指令")
    _foreign_card(owner, "ja:M1S-056", "ja:M1S", "ja", "フシギダネ")  # no picture: Bulbasaur's sprite stands in
    page = client.get("/collection").text
    assert 'class="cgrid"' in page
    assert 'Lopunny <span class="lmark" title="ミミロップ">(j)</span>' in page
    assert 'Boss&#39;s Orders <span class="lmark" title="老大的指令">(c)</span>' in page
    assert 'ボスの指令 <span class="lmark" title="ボスの指令">(j)</span>' in page
    assert 'class="ph-spr" src="/sprites/1.png"' in page
    card = client.get("/cards/ja:M1S-054").text
    assert "ミミロップ<div class=\"muted cv-en\"" in card and "Lopunny</div>" in card  # round 11 (A4): its own line

    # Account > Theme: a search list of every Pokémon, and their colours for the live preview.
    account = client.get("/account").text
    assert "data-ps-grid" in account and '[1, "Bulbasaur"]' in account and "<datalist" not in account
    colours = client.get("/themes/palette/25").json()
    assert colours["light"]["accent"] == sprites.get_palette(main.conn, 25)["light"]["accent"]
    assert client.get("/themes/palette/999").status_code == 404
    assert client.post("/account/theme", data={"mode": "fixed", "pokemon": "94"}).status_code == 200
    assert sprites.user_setting(main.conn, owner)["species_id"] == 94


def test_import_groups_open_in_the_cards_language():
    rows = [{"status": "unmatched", "set_name": "Gem Pack (CN)", "reason": "Chinese card"},
            {"status": "unmatched", "set_name": "Gem Pack (CN)", "reason": "Chinese card: pick its set below"},  # older imports
            {"status": "unmatched", "set_name": "Mega Brave", "reason": "Korean card"},
            {"status": "unmatched", "set_name": "Odd Set", "reason": "Set not found: pick it below"}]
    groups = {g["set_name"]: g for g in importer.unmatched_sets(rows)}
    assert (groups["Gem Pack (CN)"]["lang"], groups["Gem Pack (CN)"]["language_name"]) == ("zh", "Chinese")
    assert groups["Mega Brave"]["lang"] == "ko" and groups["Odd Set"]["lang"] == "en"


def test_reset_starts_over_and_keeps_downloads(client, tmp_path):
    from app import reset
    _setup_with_set(client)
    data = tmp_path / "data"
    db_path = data / "dexstash.db"
    data.mkdir()
    dest = sqlite3.connect(db_path)
    main.conn.commit()
    main.conn.backup(dest)
    dest.close()
    for rel in ("scans/1/a.jpg", "card-photos/x.jpg", "sprites/1.png", "models/m.onnx", "index/en.npy", "index/zh-cn.npy",
                "index/zh-cn.json", "pokemon_species_names.csv"):
        (data / rel).parent.mkdir(parents=True, exist_ok=True)
        (data / rel).write_bytes(b"x")
    assert any("Erase scans" in line for line in reset.plan(data)) and any("Keep sprites" in line for line in reset.plan(data))
    saved = reset.run(data, db_path)
    assert not db_path.exists() and not (data / "scans").exists() and not (data / "card-photos").exists()
    assert not (data / "index/zh-cn.npy").exists() and (data / "index/en.npy").exists()
    assert all((data / rel).exists() for rel in ("sprites/1.png", "models/m.onnx", "pokemon_species_names.csv"))
    assert saved.parent == data / "backups" and [b["kind"] for b in backup.list_backups(data / "backups")] == ["reset"]
    with sqlite3.connect(saved) as old:
        assert old.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_find_sort_puts_missing_prices_last():
    rows = [{"name": "B", "number": "10", "price": None, "set_id": "a"}, {"name": "a", "number": "2", "price": 5.0, "set_id": "b"},
            {"name": "C", "number": "TG3", "price": 9.0, "set_id": "a"}]
    rank = {"a": 0.1, "b": 0.9}
    pick = lambda sort: [r["name"] for r in search.sort_results(rows, sort, lambda r: rank[r["set_id"]])]
    assert pick("price_desc") == ["C", "a", "B"] and pick("price_asc") == ["a", "C", "B"]
    assert pick("number") == ["a", "C", "B"] and pick("name") == ["a", "B", "C"]
    assert pick("newest")[0] == "a" and pick("oldest")[-1] == "a" and pick("") == ["B", "a", "C"]


def test_my_cards_filters_stay_until_cleared(client):
    _setup_with_set(client)
    _entry(client, "t1", "1")
    r = client.get("/collection?q=Bulb&sort=name")
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    back = client.get("/collection", follow_redirects=False)  # tapping the My cards tab keeps them
    assert back.status_code == 303 and back.headers["location"] == "/collection?q=Bulb&sort=name"
    assert client.get("/collection?clear=1", follow_redirects=False).headers["location"] == "/collection"
    assert client.get("/collection", follow_redirects=False).status_code == 200
    client.get("/chases?price=none")
    assert client.get("/chases", follow_redirects=False).headers["location"] == "/chases?price=none"


def test_card_opened_from_find_goes_back_to_find(client):
    _setup_with_set(client)
    _entry(client, "t1", "1")
    client.get("/search?q=Bulbasaur&sort=name")
    page = client.get("/cards/t1-1?from=find").text
    assert 'href="/search?q=Bulbasaur&amp;sort=name"' in page and "Find another card" not in page


def test_price_links_search_in_english(monkeypatch):
    monkeypatch.setattr(web.names, "to_english", lambda name: {"ピッピ": "Clefairy"}.get(name, name))
    pc = prices.pricecharting_link("Clefairy", "035", "ポケモンカード151", "ja")
    assert "q=Clefairy+35+Japanese" in pc  # round 27: PriceCharting numbers without leading zeros  # a Japanese set name finds nothing, so it's left out
    assert "q=Charizard+Base+Set+4" in prices.pricecharting_link("Charizard", "4/102", "Base Set")  # round 27: name, set, number
    url, exact = prices.tcgplayer_link(None, "Clefairy", "035", "", "ja")
    assert not exact and "/search/pokemon-japan/" in url and "q=Clefairy+035" in url
    assert "Chinese" in prices.pricecharting_link("Bulbasaur", "001", "利刃猛醒", "zh-cn")
    # A card page searches with the English name, not the printed one.
    links = web._links({"id": "ja:M1S-035", "name": "ピッピ", "number": "035", "set_name": "メガシンフォニア"}, "ja", None)
    assert "Clefairy" in links["pc_url"] and "%E3%83" not in links["pc_url"] and "pokemon-japan" in links["tcg_url"]
    twin = web._links({"id": "zh-cn:CSV7C-001", "name": "妙蛙种子", "twin_name": "Bulbasaur", "number": "001",
                        "set_name": "利刃猛醒"}, "zh-cn", None)
    assert "q=Bulbasaur+1+Chinese" in twin["pc_url"]


class FakeEnglishTwinAPI(FakePricedAPI):
    def get_card(self, card_id):
        if card_id == "swsh10-1":
            return {"id": card_id, "pricing": {"tcgplayer": {"unit": "USD", "holofoil": {"marketPrice": 3.0},
                                                             "reverse-holofoil": {"marketPrice": 4.0}}}}
        return super().get_card(card_id)

    get_card_cached = get_card


def test_no_approx_english_prices(client, monkeypatch):
    api = FakeEnglishTwinAPI()
    monkeypatch.setattr(web, "api", api)
    user_id = _setup_with_set(client)
    main.conn.execute("INSERT INTO sets (set_id, name, language, printed_total, total) VALUES ('zh-cn:CSA', '测试', 'zh-cn', 1, 1)")
    main.conn.execute("INSERT INTO cards (id, set_id, number, name, twin_name, twin_code, twin_number, source, sort_number, "
                      "is_main_set, has_holo) VALUES ('zh-cn:CSA-001', 'zh-cn:CSA', '001', '妙蛙种子', 'Bulbasaur', 'ASR', '1', "
                      "'chs', 1, 1, 1)")
    main.conn.commit()
    main.conn.execute("INSERT INTO inventory (user_id, card_id, variant, condition, quantity) "
                      "VALUES (?, 'zh-cn:CSA-001', 'star', 'NM', 1)", (user_id,))
    main.conn.commit()
    sync.refresh_all(main.conn, lambda lang: api)
    # Round 11 (P): no ≈ English price, so the mainland card (and its ★ copy) has no price.
    assert prices.card_prices(main.conn, "zh-cn:CSA-001") == []
    v = prices.collection_value(main.conn, user_id)
    assert (v["value"], v["estimated"], v["unpriced"]) == (0, 0, 1)
    page = client.get("/cards/zh-cn:CSA-001").text
    assert "≈ English" not in page and "/preview/en/swsh10-1" not in page

    # Nothing is guessed for an English card that has a price for every printing you have.
    _entry(client, "t1", "1")
    assert {r["origin"] for r in prices.card_prices(main.conn, "t1-1") if r["variant"] == "normal"} == {"tcgplayer"}


def test_extras_on_a_card_you_dont_have_stay(client, monkeypatch):
    from app import extras
    monkeypatch.setattr(web, "api", FakePricedAPI())
    user_id = _setup_with_set(client)
    # The ⋯ menu is on every card now, and a price can be set on one you don't have.
    page = client.get("/cards/t1-2").text
    assert "Modify price" in page and 'id="mod-price"' in page
    r = client.post("/cards/t1-2/extras", data={"manual_price": "7"})
    assert "Saved." in r.text and _typed("t1-2") == {"normal": 7.0}
    assert main.conn.execute("SELECT 1 FROM prices WHERE card_id = 't1-2'").fetchone()  # kept with yours
    # Having and removing it, Update prices, and removing the set all keep your price.
    _entry(client, "t1", "2")
    _entry(client, "t1", "-2")
    sync.refresh_all(main.conn, lambda lang: web.api)
    client.post("/sets/t1/remove")
    assert _typed("t1-2") == {"normal": 7.0}
    assert main.conn.execute("SELECT 1 FROM cards WHERE id = 't1-2'").fetchone()
    assert main.conn.execute("SELECT 1 FROM prices WHERE card_id = 't1-2'").fetchone()
    # Only "Back to what TCGdex has" takes it away.
    client.post("/cards/t1-2/extras/reset")
    assert extras.get(main.conn, "t1-2") is None
    assert main.conn.execute("SELECT 1 FROM prices WHERE card_id = 't1-2'").fetchone() is None


def test_nightly_price_update(client, monkeypatch):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from app import display
    monkeypatch.setattr(display, "_zone", ZoneInfo("America/New_York"))  # round 13: the zone is a setting
    _setup_with_set(client)
    s = sync.nightly_settings(main.conn)
    assert (s["on"], s["time"], s["last"]) == (True, "04:00", None)  # on by default
    assert "Nightly price update" in client.get("/account/data").text
    client.post("/account/data/nightly", data={"on": "1", "at": "5:30"})
    assert sync.nightly_settings(main.conn)["time"] == "05:30"
    assert "Use a time like" in client.post("/account/data/nightly", data={"on": "1", "at": "25:00"}).text
    client.post("/account/data/nightly", data={"at": "05:30"})
    assert sync.nightly_settings(main.conn)["on"] is False

    tz = timezone.utc
    assert sync.next_run(datetime(2026, 10, 9, 3, 0, tzinfo=tz), "04:00") == datetime(2026, 10, 9, 4, 0, tzinfo=tz)
    assert sync.next_run(datetime(2026, 10, 9, 4, 0, tzinfo=tz), "04:00") == datetime(2026, 10, 10, 4, 0, tzinfo=tz)

    class Updater:
        running, last_result, last_failed, starts = False, None, False, 0

        def run(self, conn, only_missing=False, job=None):
            self.starts += 1
            self.last_result, self.last_failed = "Price update failed: offline", True
            raise RuntimeError("offline")

    # Round 23: the scheduler adds the night's update and its chores as jobs, one after the other.
    up, chores = Updater(), []
    night = sync.Nightly(up, now=lambda: datetime(2026, 10, 9, 4, 0, tzinfo=tz), after=lambda: chores.append(1),
                         jobs=web.background)
    assert night.run_once(main.conn)
    web.background.last("nightly").wait(10)
    web.background.last("nightly-chores").wait(10)
    assert up.starts == 1 and chores == [1]
    s = sync.nightly_settings(main.conn)
    assert s["last"].startswith("Oct 9, 2026, 12:00 AM") and "offline" in s["error"]  # shown in Eastern time
    up.running = True  # already updating: skipped
    assert not night.run_once(main.conn) and up.starts == 1


# --- Pokémon chase (9c) ----------------------------------------------------------------

def _vec(*xs):
    import numpy as np
    v = np.zeros(512, np.float32)
    v[:len(xs)] = xs
    return v / np.linalg.norm(v)


def test_chase_groups_arts_and_keeps_fixes():
    from app import chase
    cards = [
        {"card_id": "sv03.5-035", "lang": "en", "illustrator": "Saya Tsuruta", "release_date": "2023-09-22"},
        {"card_id": "ja:SV2a-035", "lang": "ja", "illustrator": "Saya Tsuruta", "release_date": "2023-06-16"},
        {"card_id": "fr:sv03.5-035", "lang": "fr", "illustrator": "saya tsuruta", "release_date": "2023-09-22"},
        {"card_id": "sv03.5-173", "lang": "en", "illustrator": "Saya Tsuruta", "release_date": "2023-09-22"},  # other art
        {"card_id": "base1-5", "lang": "en", "illustrator": "Ken Sugimori", "release_date": "1999-01-09"},
        {"card_id": "xy-1", "lang": "en", "illustrator": "Other", "release_date": "2014-01-01"},  # no picture
    ]
    vectors = {"sv03.5-035": _vec(1, 0, 0), "ja:SV2a-035": _vec(1, 0.05, 0), "fr:sv03.5-035": _vec(1, 0, 0.05),
               "sv03.5-173": _vec(0, 1, 0), "base1-5": _vec(1, 0.02, 0)}  # same picture, other illustrator
    arts = chase.group(cards, vectors, [])
    assert arts[0]["cover"] == "sv03.5-035" and set(arts[0]["cards"]) == {"sv03.5-035", "ja:SV2a-035", "fr:sv03.5-035"}
    assert len(arts) == 4 and arts[0]["cards"]["sv03.5-035"] == 1.0 and arts[0]["cards"]["ja:SV2a-035"] > 0.99
    # Your fixes: one taken out stays on its own, one added joins.
    fixes = [{"card_id": "fr:sv03.5-035", "join_card_id": None}, {"card_id": "xy-1", "join_card_id": "sv03.5-035"}]
    arts = chase.group(cards, vectors, fixes)
    main_art = next(a for a in arts if "sv03.5-035" in a["cards"])
    assert set(main_art["cards"]) == {"sv03.5-035", "ja:SV2a-035", "xy-1"}
    assert main_art["cover"] == "xy-1"  # the earliest English one
    assert any(set(a["cards"]) == {"fr:sv03.5-035"} for a in arts)
    # Only Japanese: the earliest is the cover.
    assert chase.cover_of([cards[1], dict(cards[1], card_id="ja:SV1-1", release_date="2023-01-01")]) == "ja:SV1-1"


class FakeChaseAPI:
    """TCGdex in one language, with or without the dexId filter."""

    def __init__(self, lang, cards, honours_filter=True):
        self.lang, self.cards, self.honours = lang, cards, honours_filter
        test = self

        class Client:
            def get(self, path, params=None, timeout=None):
                class R:
                    def raise_for_status(self):
                        pass

                    def json(self):
                        if test.honours:
                            return [{"id": c["id"]} for c in test.cards if c["dexId"][0] == int(params["dexId"][3:])]
                        return [{"id": c["id"]} for c in test.cards] * 1000  # the filter was ignored
                return R()
        self.client = Client()

    def search_cards(self, name=None, local_id=None):
        return [{"id": c["id"]} for c in self.cards if name and name.lower() in c["name"].lower()]

    def get_card(self, card_id):
        c = next(c for c in self.cards if c["id"] == card_id)
        return {"id": c["id"], "localId": c["id"].split("-")[1], "name": c["name"], "dexId": c["dexId"],
                "illustrator": c.get("illustrator"), "image": None, "variants": {"normal": True, "reverse": True},
                "set": {"id": c["id"].split("-")[0], "name": "Set " + c["id"].split("-")[0]},
                "pricing": {"tcgplayer": {"normal": {"marketPrice": 1.25}}}}

    def get_set(self, set_id):
        return {"id": set_id, "releaseDate": "2023-09-22", "abbreviation": {"official": "MEW"}}


class FakeNames:
    def english_name(self, dex):
        return {35: "Clefairy"}.get(dex)

    def name_in(self, dex, lang):
        return {"ja": "ピッピ"}.get(lang, "Clefairy")

    def species_id(self, text):
        return 35 if text and ("Clefairy" in text or "皮皮" in text) else None


def test_chase_follow_job_lists_stores_and_cleans_up(client, tmp_path):
    from app import chase
    user_id = _setup_with_set(client)
    en = FakeChaseAPI("en", [{"id": "sv03.5-035", "name": "Clefairy", "dexId": [35], "illustrator": "A"},
                             {"id": "sv03.5-036", "name": "Clefable", "dexId": [36]},
                             {"id": "sv03.5-200", "name": "Clefairy & Friend", "dexId": [133, 35]}])  # not its main Pokémon
    ja = FakeChaseAPI("ja", [{"id": "SV2a-035", "name": "ピッピ", "dexId": [35], "illustrator": "A"}], honours_filter=False)
    apis = {"en": en, "ja": ja}

    def api_for(lang):
        if lang not in apis:
            raise RuntimeError("offline")
        return apis[lang]

    assert chase.follow(main.conn, user_id, 35, "Clefairy", user_id) and not chase.follow(main.conn, user_id, 35, "Clefairy", user_id)

    class NoEncoder:
        def embed(self, images):
            raise RuntimeError("no model")

    text = chase.run_follow(main.conn, 35, FakeNames(), api_for, NoEncoder())
    rows = {r["card_id"]: dict(r) for r in main.conn.execute("SELECT * FROM pokemon_cards WHERE dex_id = 35")}
    assert set(rows) == {"sv03.5-035", "ja:SV2a-035"}  # the name search found the Japanese one; dexId checked
    assert rows["sv03.5-035"]["set_code"] == "MEW" and json.loads(rows["sv03.5-035"]["prices_json"]) == {"normal": 1.25}
    assert json.loads(rows["ja:SV2a-035"]["variants"]) == ["normal", "reverse"]
    assert "2 cards in 2 arts" in text and "Couldn't reach TCGdex for Chinese (Taiwan)" in text
    # Prices are kept for followed cards and refreshed with Update prices.
    assert "sv03.5-035" in prices.kept_ids(main.conn)
    assert chase.refresh_prices(main.conn, api_for)[0] == 2

    # Your fixes and hand-added cards outlive a re-check and an unfollow.
    chase.take_out(main.conn, 35, "ja:SV2a-035", user_id)
    chase.put_with(main.conn, 35, "ja:SV2a-035", "sv03.5-035", user_id)
    chase.regroup(main.conn, 35, {})
    assert main.conn.execute("SELECT COUNT(*) FROM arts WHERE dex_id = 35").fetchone()[0] == 1
    assert main.conn.execute("SELECT how FROM art_cards WHERE card_id = 'ja:SV2a-035'").fetchone()[0] == "manual"
    chase.add_card(main.conn, 35, chase.row_from_cards(main.conn, "t1-1"), user_id)  # e.g. a Trainer showing Clefairy
    chase.remove_card(main.conn, 35, "sv03.5-035")
    chase.run_follow(main.conn, 35, FakeNames(), api_for, NoEncoder(), only_new=True)
    hows = dict(main.conn.execute("SELECT card_id, how FROM pokemon_cards WHERE dex_id = 35").fetchall())
    assert hows == {"sv03.5-035": "removed", "ja:SV2a-035": "auto", "t1-1": "manual"}

    chase.unfollow(main.conn, user_id, 35)
    hows = dict(main.conn.execute("SELECT card_id, how FROM pokemon_cards WHERE dex_id = 35").fetchall())
    assert hows == {"sv03.5-035": "removed", "t1-1": "manual"} and chase.fixes_of(main.conn, 35)
    assert not main.conn.execute("SELECT 1 FROM arts WHERE dex_id = 35").fetchone()
    assert "sv03.5-035" not in prices.kept_ids(main.conn)


def _chase_rows(dex=35):
    def row(cid, lang, set_id, set_name, number, date, variants=("normal", "reverse"), image="https://img/x"):
        return {"card_id": cid, "lang": lang, "set_id": set_id, "set_name": set_name, "set_code": set_name[:3].upper(),
                "release_date": date, "number": number, "name": "Clefairy" if lang != "ja" else "ピッピ",
                "illustrator": "A", "rarity": "Common", "image_url": image, "variants": json.dumps(list(variants)),
                "prices_json": json.dumps({"normal": 0.25})}
    return [row("sv03.5-035", "en", "sv03.5", "151", "035", "2023-09-22"),
            row("zh-tw:SV2a-035", "zh-tw", "zh-tw:SV2a", "151 TW", "035", "2023-09-22"),
            row("ja:SV2a-035", "ja", "ja:SV2a", "Pokémon Card 151", "035", "2023-06-16"),
            row("base1-5", "en", "base1", "Base Set", "5", "1999-01-09", ("holo",), None)]


def test_pokemon_chase_screens(client, monkeypatch):
    from app import chase
    user_id = _setup_with_set(client)
    chase.follow(main.conn, user_id, 35, "Clefairy", user_id)
    chase.save_cards(main.conn, 35, _chase_rows())
    vectors = {"sv03.5-035": _vec(1, 0, 0), "zh-tw:SV2a-035": _vec(1, 0, 0.01), "ja:SV2a-035": _vec(1, 0.01, 0)}
    monkeypatch.setattr(chase, "fingerprints", lambda conn, dex, encoder, *a, **k: vectors)
    chase.regroup(main.conn, 35, vectors)
    main.conn.execute("INSERT INTO sets (set_id, name, printed_total, total) VALUES ('sv03.5', '151', 165, 207)")
    main.conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name) VALUES "
                      "('sv03.5-035', 'sv03.5', '035', 35, 1, 'Clefairy')")
    main.conn.execute("INSERT INTO inventory (user_id, card_id, variant, condition, quantity) VALUES (?, 'sv03.5-035', 'normal', 'NM', 1)",
                      (user_id,))
    main.conn.commit()

    page = client.get("/pokemon").text  # followed Pokémon are on Chases now
    assert "Clefairy" in page and "1 of 2</span>" in page and 'aria-current="page">Chases</a>' in page and "Single cards" in page
    page = client.get("/pokemon/35").text
    assert "Pokémon set" not in page and "</svg> Following" in page and "1 of 2 arts" in page and "No picture" in page
    assert "♥ Chased" in page and "grayscale" not in page
    assert "1 of 4 cards" in page and "1 of 7 printings" in page  # every language counts (#24)
    assert "<b>Language</b>" not in page and "<b>Sort</b>" not in page and 'name="lang"' not in page
    art = client.get("/pokemon/35/art", params={"card": "sv03.5-035"}).text
    assert "151 art" in art and "Reverse Holo" in art and "3 cards" in art and 'href="/preview/ja/SV2a-035"' in art
    assert 'ltg ltg-en h">EN<' in art and 'ltg ltg-ja">JA<' in art and 'ltg ltg-zh">ZH<' in art
    assert re.search(r'>JA</span>\s*<a href="/preview/ja/SV2a-035">[^<]+ &middot; 035</a>', art)  # each card's set and number
    assert "Not this art" in art and 'value="base1-5"' in art and "Add a card to this art" in art

    # Editing: take Chinese out, it's listed under Taken out of this art, Undo puts it back; Add brings Base Set in.
    r = client.post("/pokemon/35/fix/out", data={"card": "zh-tw:SV2a-035", "back": "sv03.5-035"})
    assert r.status_code == 200
    assert main.conn.execute("SELECT COUNT(*) FROM arts WHERE dex_id = 35").fetchone()[0] == 3
    assert "Taken out of this art (1)" in client.get("/pokemon/35/art", params={"card": "sv03.5-035"}).text
    client.post("/pokemon/35/fix/undo", data={"card": "zh-tw:SV2a-035", "back": "sv03.5-035"})
    client.post("/pokemon/35/fix/add", data={"card": "base1-5", "to": "sv03.5-035"})
    assert main.conn.execute("SELECT COUNT(*) FROM arts WHERE dex_id = 35").fetchone()[0] == 1

    assert 'href="/pokemon/35"' not in client.get("/").text  # Your Pokémon left Home in round 24 (still on Chases)

    # Unfollow, then the set page is a preview with ☆ Follow.
    monkeypatch.setattr(web, "api", FakeChaseAPI("en", [{"id": "sv03.5-035", "name": "Clefairy", "dexId": [35]}]))
    client.post("/pokemon/35/unfollow")
    page = client.get("/pokemon/35").text
    assert "</svg> Follow" in page and "/preview/en/sv03.5-035" in page and "arts" not in page.split('class="nm"')[1][:300]


def test_card_page_pokemon_line_and_sets(client, monkeypatch):
    from app import chase

    class Names:
        def available(self):
            return True

        def species_id(self, text):
            return 1 if "Bulbasaur" in text else None

        def english_name(self, dex):
            return {1: "Bulbasaur", 35: "Clefairy"}.get(dex)

        def to_english(self, text):
            return None

    monkeypatch.setattr(web, "names", Names())
    user_id = _setup_with_set(client)
    page = client.get("/cards/t1-1").text
    assert "Bulbasaur's set &rsaquo;" in page and 'href="/pokemon/1"' in page and "Add to a Pokémon set" not in page
    chase.follow(main.conn, user_id, 35, "Clefairy", user_id)
    page = client.get("/cards/t1-1").text
    assert "Add to a Pokémon set" in page and 'id="mod-pokemon"' in page
    client.post("/cards/t1-1/pokemon-sets", data={"dex": "35"})
    assert main.conn.execute("SELECT how FROM pokemon_cards WHERE dex_id = 35 AND card_id = 't1-1'").fetchone()[0] == "manual"
    assert "Added by you" in client.get("/pokemon/35").text
    client.post("/cards/t1-1/pokemon-sets", data={})
    assert main.conn.execute("SELECT 1 FROM pokemon_cards WHERE dex_id = 35 AND card_id = 't1-1'").fetchone() is None
    # A Trainer named after a Pokémon gets no line.
    assert web._card_pokemon({"collection_id": user_id}, "Bulbasaur Doll", "en") is None
    assert web._card_pokemon({"collection_id": user_id}, "Team Rocket's Bulbasaur ex", "en")["dex"] == 1


# --- Round 5 -----------------------------------------------------------------------

def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (20, 28), (200, 40, 40)).save(buf, "PNG")
    return buf.getvalue()


def test_chase_box_links_photos_and_unchase(client, monkeypatch, tmp_path):
    from app import chase_box, handmade, wants
    monkeypatch.setattr(handmade, "FOLDER", tmp_path)
    user_id = _setup_with_set(client)
    # Only a chased card has the box.
    assert client.post("/cards/t1-1/chase/links", data={"url": "https://ebay.com/x"}).status_code == 404
    wants.add(main.conn, user_id, "t1-1", by=user_id)
    page = client.get("/cards/t1-1").text
    assert 'id="chase"' in page and "</svg> Chasing &middot; any printing" in page

    client.post("/cards/t1-1/chase/links", data={"url": "www.ebay.com/itm/123"})
    assert "Paste the whole address" in client.post("/cards/t1-1/chase/links", data={"url": "not a link"}).text
    [link] = chase_box.links(main.conn, user_id, "t1-1")
    assert link["url"] == "https://www.ebay.com/itm/123" and link["label"] == "ebay.com"
    client.post("/cards/t1-1/chase/photos", files={"photo": ("p.png", _png(), "image/png")})
    assert "That file isn" in client.post("/cards/t1-1/chase/photos", files={"photo": ("p.txt", b"hello", "text/plain")}).text
    [photo] = chase_box.photos(main.conn, user_id, "t1-1")
    assert (tmp_path / photo["photo"].rsplit("/", 1)[1]).exists()
    page = client.get("/cards/t1-1").text
    assert "ebay.com" in page and photo["photo"] in page

    client.post(f"/cards/t1-1/chase/links/{link['id']}/delete")
    assert chase_box.links(main.conn, user_id, "t1-1") == []
    # Stop chasing: the photos go too, file and all.
    want_id = main.conn.execute("SELECT id FROM wants WHERE card_id = 't1-1'").fetchone()[0]
    client.post(f"/wants/{want_id}/remove")
    assert not chase_box.has_any(main.conn, user_id, "t1-1")
    assert not (tmp_path / photo["photo"].rsplit("/", 1)[1]).exists()


def test_purge_languages_keeps_en_ja_zh():
    from app import chase
    conn = main.conn
    conn.execute("DELETE FROM pokemon_cards")
    conn.execute("DELETE FROM art_fixes")
    for cid, lang in [("sv1-1", "en"), ("ja:SV1-1", "ja"), ("zh-tw:SV1-1", "zh-tw"), ("zh-cn:CSV1-1", "zh-cn"),
                      ("fr:sv1-1", "fr"), ("ko:SV1-1", "ko")]:
        conn.execute("INSERT INTO pokemon_cards (dex_id, card_id, lang, set_id, how) VALUES (35, ?, ?, 's', 'auto')", (cid, lang))
    conn.execute("INSERT INTO art_fixes (dex_id, card_id, join_card_id, by, at) VALUES (35, 'fr:sv1-1', 'sv1-1', NULL, '2026-10-10')")
    conn.commit()
    assert chase.purge_languages(conn) == [35]
    left = {r[0] for r in conn.execute("SELECT lang FROM pokemon_cards")}
    assert left == {"en", "ja", "zh-tw", "zh-cn"}
    assert not conn.execute("SELECT 1 FROM art_fixes").fetchone()
    assert chase.purge_languages(conn) == []  # nothing left to do: the startup job doesn't run again
    conn.execute("DELETE FROM pokemon_cards")
    conn.commit()


SPECIES_CSV = """id,identifier,evolves_from_species_id,evolution_chain_id,order,color_id,shape_id
25,pikachu,172,10,35,10,8
26,raichu,25,10,36,10,6
35,clefairy,173,14,59,6,6
133,eevee,,67,201,3,8
134,vaporeon,133,67,202,2,8
135,jolteon,133,67,203,10,8
172,pichu,,10,34,10,8
173,cleffa,,14,58,6,6
197,umbreon,133,67,206,1,8
280,ralts,,140,340,9,12
281,kirlia,280,140,341,9,12
282,gardevoir,281,140,342,9,12
475,gallade,281,140,343,9,12
7,squirtle,,3,10,2,6
1026,somethingform,,999,1100,1,1
"""


def _fake_pokeapi(extra=None):
    files = {"pokemon_species": SPECIES_CSV,
             "pokemon_colors": "id,identifier\n1,black\n2,blue\n3,brown\n6,pink\n9,white\n10,yellow\n",
             "pokemon_shapes": "id,identifier\n6,upright\n8,quadruped\n12,humanoid\n",
             "types": "id,identifier\n11,water\n13,electric\n18,fairy\n1,normal\n17,dark\n14,psychic\n",
             "pokemon_types": "pokemon_id,type_id,slot\n25,13,1\n7,11,1\n35,18,1\n133,1,1\n197,17,1\n280,14,1\n280,18,2\n10100,13,1\n",
             "pokemon_species_flavor_text": "species_id,version_id,language_id,flavor_text\n"
                                            "7,1,9,\"After birth, its back swells and hardens into a shell. It sprays foam from its mouth.\"\n"
                                            "7,2,9,\"After birth, its back swells and hardens into a shell. It sprays foam from its mouth.\"\n"
                                            "25,1,9,\"It stores electricity in its cheeks.\"\n"
                                            "25,1,5,\"Il stocke l'électricité.\"\n"
                                            "35,1,9,\"Its magical and cute appeal has many admirers. It is rare.\"\n",
             "pokemon_species_names": "pokemon_species_id,local_language_id,name,genus\n"
                                      "7,9,Squirtle,Tiny Turtle Pokémon\n25,9,Pikachu,Mouse Pokémon\n35,9,Clefairy,Fairy Pokémon\n"
                                      "133,9,Eevee,Evolution Pokémon\n197,9,Umbreon,Moonlight Pokémon\n280,9,Ralts,Feeling Pokémon\n"
                                      "25,1,ピカチュウ,\n"}
    files.update(extra or {})
    asked = []

    class Resp:
        def __init__(self, body):
            self.content = body.encode()

        def raise_for_status(self):
            pass

    def get(url):
        name = url.rsplit("/", 1)[1].removesuffix(".csv")
        asked.append(name)
        return Resp(files[name])
    return get, asked


def test_evolution_lines(tmp_path):
    from app import evolution
    get, asked = _fake_pokeapi()
    lines = evolution.Lines(tmp_path, get)
    assert not lines.available()
    assert lines.line(25) == {"trunk": [172, 25, 26], "branches": [], "here": 25}
    assert lines.line(133) == {"trunk": [133], "branches": [[134], [135], [197]], "here": 133}
    assert lines.line(281) == {"trunk": [280, 281], "branches": [[282], [475]], "here": 281}
    assert lines.line(197) == {"trunk": [133], "branches": [[134], [135], [197]], "here": 197}  # the whole line (round 7)
    assert lines.line(7) is None and lines.chain_of(7) == 3 and lines.members(1026) == [1026]
    assert lines.available() and asked == ["pokemon_species"]  # downloaded once
    assert evolution.Lines(tmp_path, lambda url: 1 / 0).line(26)["trunk"] == [172, 25, 26]  # the saved file is used


def test_describe_search(tmp_path):
    from app import describe
    get, asked = _fake_pokeapi()
    d = describe.Describe(tmp_path / "describe.db", tmp_path / "names.csv", tmp_path, get)
    assert not d.ready()
    assert d.build() == 14  # species up to #1025 only
    assert d.ready() and (tmp_path / "names.csv").exists() and not (tmp_path / "describe.part").exists()
    first = lambda **k: [r["name"] for r in d.search(**k)]
    assert first(words="a turtle with a shell that sprays foam")[0] == "Squirtle"
    assert first(words="mouse")[0] == "Pikachu"  # the genus counts
    assert first(words="cheek electricity", colour="blue") == ["Squirtle", "Vaporeon"]  # no blue one matches: the blue ones
    assert first(colour="pink") == ["Clefairy", "Cleffa"]  # chips alone: Pokédex order
    assert first(kind="fairy") == ["Clefairy", "Ralts"] and first(kind="dark") == ["Umbreon"]
    assert first(words="xyzzy", kind="electric") == ["Pikachu"]  # words found nothing: the chips still answer
    assert d.search() == [] and first(colour="plaid") == []
    assert d.build() == 14 and asked.count("pokemon_species") == 1  # built already: nothing downloaded again


def test_describe_route_starts_download_then_shows_results(client, monkeypatch, tmp_path):
    from app import describe
    get, _ = _fake_pokeapi()
    monkeypatch.setattr(web, "describer", describe.Describe(tmp_path / "describe.db", tmp_path / "names.csv", tmp_path, get))
    started = []
    monkeypatch.setattr(web.background, "start", lambda key, *a, **k: started.append(key))
    _setup_with_set(client)
    assert "loading" in client.get("/describe", params={"words": "turtle"}).text.lower() or started == ["describe"]
    assert started == ["describe"]
    web.describer.build()
    page = client.get("/describe", params={"words": "turtle shell"}).text
    assert "Squirtle" in page


def test_spending_totals_no_double_count_and_before_tracking(client):
    from datetime import date
    user_id = _setup_with_set(client)
    _entry(client, "t1", "1 x2")
    _entry(client, "t1", "2")
    main.conn.execute("UPDATE inventory SET acquired_price = 3, acquired_date = '2026-10-05' WHERE card_id = 't1-1'")
    main.conn.execute("UPDATE inventory SET acquired_price = 5, acquired_date = '2025-03-01' WHERE card_id = 't1-2'")
    main.conn.commit()
    pid = spending.create_purchase(main.conn, user_id, "Booster", 10, purchased_on="2026-10-02")
    spending.record_pull(main.conn, pid, "t1-1", "normal", "NM", 1)  # one of the two copies came from the pack
    spending.set_before_tracking(main.conn, user_id, 100, "2024-01-01", user_id)
    spending.set_before_tracking(main.conn, user_id, 120, "", user_id)  # one line, updated; undated it still isn't this month
    t = spending.totals(main.conn, user_id, today=date(2026, 10, 10))
    assert t == {"packs": 130.0, "singles": 8.0, "total": 138.0, "month": 13.0, "year": 13.0}
    months = spending.by_month(main.conn, user_id)
    assert [m["key"] for m in months] == ["2026-10", "2025-03"] and months[0]["total"] == 13.0
    assert {i["type"] for i in months[0]["items"]} == {"purchase", "card"}
    assert all(i.get("kind") != spending.BEFORE for m in months for i in m["items"])
    page = client.get("/spending").text
    assert "Spent before we started tracking" in page and "$138.00" in page
    spending.set_before_tracking(main.conn, user_id, None, None, user_id)
    assert spending.before_tracking(main.conn, user_id) is None


def test_sets_tab_filters(client):
    user_id = _setup_with_set(client)
    client.post("/sets", data={"set_id": "t2"})
    _entry(client, "t1", "1")
    _entry(client, "t1", "2")
    _entry(client, "t2", "1")
    page = client.get("/collection/sets").text
    assert 'aria-current="page">Sets</a>' in page and "/sets/t1" in page and "/sets/t2" in page
    page = client.get("/collection/sets", params={"show": "complete"}).text
    assert "/sets/t1" in page and "/sets/t2" not in page and "Complete" in page
    page = client.get("/collection/sets", params={"show": "incomplete", "sort": "name"}).text
    assert "/sets/t1\"" not in page and "/sets/t2" in page
    # Remembered for this login, until cleared.
    assert "/sets/t2" in client.get("/collection/sets").text and "/sets/t1\"" not in client.get("/collection/sets").text
    assert "/sets/t1" in client.get("/collection/sets", params={"clear": 1}).text


def test_chases_groups_followed_pokemon_by_line(client, monkeypatch, tmp_path):
    from app import chase, evolution, wants
    get, _ = _fake_pokeapi()
    monkeypatch.setattr(web, "lines", evolution.Lines(tmp_path, get))
    web.lines.members(25)  # downloaded
    user_id = _setup_with_set(client)
    chase.follow(main.conn, user_id, 25, "Pikachu", user_id)
    chase.follow(main.conn, user_id, 172, "Pichu", user_id)
    chase.follow(main.conn, user_id, 35, "Clefairy", user_id)
    rows = _chase_rows()
    for r in rows:
        r["card_id"], r["lang"] = "t1-1", "en"
    chase.save_cards(main.conn, 25, rows[:1])
    wants.add(main.conn, user_id, "t1-1", by=user_id)  # in Pikachu's set: still a single card (round 7)
    wants.add(main.conn, user_id, "t1-2", by=user_id)
    groups = collection_pages._followed_groups(user_id)
    assert [[f["dex_id"] for f in g] for g in groups] == [[172, 25], [35]]
    page = client.get("/chases").text
    assert "&#9733; 1" not in page and page.index("Single cards") < page.index("<h2>Pokémon</h2>")
    singles = page.split("Single cards")[1].split("<h2>Pokémon</h2>")[0]
    assert "/cards/t1-2" in singles and "/cards/t1-1" in singles and "Stop chasing" not in singles  # no ✕ (round 7)
    assert page.count('class="mtile"') == 2 and 'data-n="2"' in page


def test_own_colour_differs_from_accent():
    from app import themes
    white = themes.WHITE
    two = themes.with_own({"accent": "#3060c0", "second": "#e0b000"}, white)
    assert two["own"] != two["accent"] and themes.contrast(themes._rgb(two["own"]), white) >= 3.0
    one = themes.with_own({"accent": "#3060c0", "second": "#3262c2"}, white)  # too alike: a deeper accent
    assert one["own"] != one["accent"] and themes.contrast(themes._rgb(one["own"]), white) >= 7.0
    assert one["own_on"] in ("#ffffff", themes._hex(themes.INK))


# --- Efficiency bundle (API review A1, A2, A3, B1, B2, C2, C3) -------------------

class CountingAPI(FakePricedAPI):
    def __init__(self, lang="en", calls=None, normal_price=0.18, missing=(), set_total=None):
        super().__init__(normal_price)
        self.lang, self.calls, self.missing, self.set_total = lang, calls if calls is not None else [], missing, set_total

    def get_card(self, card_id):
        self.calls.append(("card", self.lang, card_id))
        if card_id in self.missing:
            raise RuntimeError("404 Not Found")
        return super().get_card(card_id)

    def get_set(self, set_id):
        self.calls.append(("set", self.lang, set_id))
        data = super().get_set(set_id)
        if self.set_total is not None:
            data["cardCount"]["total"] = self.set_total
        return data


def test_update_fetches_a_followed_card_you_own_once(client, monkeypatch):
    from app import chase
    user_id = _setup_with_set(client)
    _entry(client, "t1", "1")
    chase.follow(main.conn, user_id, 1, "Bulbasaur", user_id)
    row = dict(_chase_rows()[0], card_id="t1-1", lang="en", set_id="t1")
    chase.save_cards(main.conn, 1, [row])
    calls = []
    sync.refresh_all(main.conn, lambda lang: CountingAPI(lang, calls))
    assert [c for c in calls if c[0] == "card"].count(("card", "en", "t1-1")) == 1
    # Stored in cards: priced in prices only, no second copy.
    assert main.conn.execute("SELECT prices_json FROM pokemon_cards WHERE card_id = 't1-1'").fetchone()[0] is None
    assert main.conn.execute("SELECT 1 FROM prices WHERE card_id = 't1-1'").fetchone()


def test_hand_card_set_reloads_only_when_it_grew(client):
    user_id = _setup_with_set(client)  # t1: 3 cards stored
    main.conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name, hand_added_by) "
                      "VALUES ('t1-99', 't1', '99', 99, 0, 'Mew', ?)", (user_id,))
    main.conn.commit()
    calls = []
    assert sync.reload_hand_sets(main.conn, lambda lang: CountingAPI(lang, calls)) == 0
    assert calls == [("set", "en", "t1")]  # one call, no card fetches
    calls.clear()
    assert sync.reload_hand_sets(main.conn, lambda lang: CountingAPI(lang, calls, set_total=4)) == 1
    assert calls.count(("set", "en", "t1")) == 1 and sum(c[0] == "card" for c in calls) == 3  # fetched once, then its cards


def test_history_only_when_price_changes_and_tcgplayer_time(client, monkeypatch):
    from datetime import date, timedelta
    monkeypatch.setattr(web, "api", FakePricedAPI())
    user_id = _setup_with_set(client)
    _entry(client, "t1", "1")
    for _ in range(3):
        sync.refresh_all(main.conn, lambda lang: FakePricedAPI(normal_price=0.18))
    history = main.conn.execute("SELECT price FROM price_snapshots WHERE card_id = 't1-1' AND variant = 'normal' "
                                "AND source = 'tcgplayer'").fetchall()
    assert [r[0] for r in history] == [0.18]  # three updates, one row: the price never moved
    assert main.conn.execute("SELECT COUNT(*) FROM price_runs").fetchone()[0] >= 1

    # Home's change: the price stayed 0.18 for a month, then rose. The run log supplies the date 30 days ago.
    main.conn.execute("DELETE FROM price_snapshots")
    main.conn.execute("DELETE FROM price_runs")
    today = date(2026, 10, 12)
    start = (today - timedelta(days=40)).isoformat()
    main.conn.execute("INSERT INTO price_snapshots (card_id, variant, source, price, captured_at) VALUES ('t1-1', 'normal', 'tcgplayer', 0.18, ?)",
                      (start + "T04:00:00+00:00",))
    for d in range(40, 0, -1):
        main.conn.execute("INSERT INTO price_runs (ran_at) VALUES (?)", ((today - timedelta(days=d)).isoformat() + "T04:00:00Z",))
    main.conn.execute("UPDATE prices SET price = 0.5 WHERE card_id = 't1-1' AND variant = 'normal'")
    main.conn.commit()
    from app import home
    ch = home.change(main.conn, user_id, today)
    assert ch["since"] == (today - timedelta(days=30)).isoformat() and ch["amount"] == 0.32


def test_tcgdex_retries_a_blip_once_and_refreshes_the_set_list(monkeypatch):
    import httpx
    from app import tcgdex
    hits = {"n": 0, "sets": 0}

    def handler(request):
        if request.url.path.endswith("/sets"):
            hits["sets"] += 1
            return httpx.Response(200, json=[{"id": f"s{hits['sets']}"}])
        if request.url.path.endswith("/cards/missing"):
            hits["n"] += 1
            return httpx.Response(404)
        hits["n"] += 1
        assert request.headers["user-agent"] == sites.USER_AGENT
        return httpx.Response(503) if hits["n"] == 1 else httpx.Response(200, json={"id": "x"})

    client = httpx.Client(base_url="https://tcgdex.test/v2/en", transport=httpx.MockTransport(handler),
                          headers=sites.HEADERS)
    api = tcgdex.TCGdex("en", client)
    assert api.get_card("x") == {"id": "x"} and hits["n"] == 2  # 503, then fine
    hits["n"] = 0
    with pytest.raises(httpx.HTTPStatusError):
        api.get_card("missing")
    assert hits["n"] == 1  # "not found" isn't retried
    assert api.list_sets() == [{"id": "s1"}] and api.list_sets() == [{"id": "s1"}]
    api._sets_at -= tcgdex.SETS_MAX_AGE + 1  # half a day later: fetched again, so new sets show up
    assert api.list_sets() == [{"id": "s1"}]  # reliability round: the search keeps the old list meanwhile
    for _ in range(100):
        if not api._sets_fetching:
            break
        time.sleep(0.02)
    assert api.list_sets() == [{"id": "s2"}]
    assert "dexSTASH" in tcgdex.TCGdex("en").client.headers["user-agent"]
