"""Round 28 (0.28.0): prices for cards you don't keep (product ids first, set matches checked and fixed), sets
nobody matches, more Japanese and Chinese card names in English, and API keys on Data sources (CardTrader moved
from .env, PriceCharting's official API)."""
import json
from pathlib import Path

import pytest

from app import backup, cardtrader, keys, main, pricecharting, prices, sites, tcgcsv, tile_prices, upgrades, web
from app.names import Names
from test_app import _entry, _setup_with_set, client  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


def _group(group_id, name, abbreviation, category=3):
    main.conn.execute("INSERT OR REPLACE INTO tcgcsv_groups (category, group_id, name, abbreviation) VALUES (?, ?, ?, ?)",
                      (category, group_id, name, abbreviation))
    main.conn.commit()


def _product(pid, number, name):
    return {"productId": pid, "name": name, "extendedData": [{"name": "Number", "value": number}]}


class FakeTCGCSV:
    def __init__(self, products: dict[int, list], prices_: dict[int, list] | None = None):
        self.products, self.prices, self.asked = products, prices_ or {}, []

    def __call__(self, url):
        self.asked.append(url)
        group = int(url.rstrip("/").split("/")[-2])
        if url.endswith("/products"):
            return {"success": True, "results": self.products.get(group, [])}
        return {"success": True, "results": self.prices.get(group, [])}


# --- 1. set matches -----------------------------------------------------------------------------------------

def test_a_set_code_with_zeros_and_the_name_over_a_printed_code():
    groups = [{"group_id": 1853, "name": "Something Else", "abbreviation": "BST"},
              {"group_id": 2765, "name": "SWSH05: Battle Styles", "abbreviation": "SWSH05"}]
    assert tcgcsv._choose(groups, "swsh5", {"BST"}, "Battle Styles")[0]["group_id"] == 2765  # SWSH05 is swsh5
    groups[1]["abbreviation"] = "B5"
    assert tcgcsv._choose(groups, "swsh5", {"BST"}, "Battle Styles") == (groups[1], "name")  # the name beats BST
    assert tcgcsv._choose(groups[:1], "swsh5", {"BST"}, "Battle Styles") == (groups[0], "code")  # only the code: as before
    assert tcgcsv._choose(groups + [dict(groups[1], group_id=9)], "x", set(), "Battle Styles") == (None, None)  # two: none


def test_a_new_set_is_matched_when_its_stored(client):
    _group(1422, "Test Set", "ZZZ")
    _setup_with_set(client)  # stores set t1
    assert main.conn.execute("SELECT group_id, matched_by FROM tcgcsv_sets WHERE set_id = 't1'").fetchone()[:] == (1422, "name")


def test_products_by_tcgdex_id_first_and_a_wrong_set_is_fixed(client):
    _setup_with_set(client)
    _group(1853, "Other Set", "BST")
    _group(2765, "Test Set Extra", "TSX")
    main.conn.execute("INSERT OR REPLACE INTO tcgcsv_sets (set_id, category, group_id, matched_by) VALUES ('t1', 3, 1853, 'code')")
    main.conn.execute("UPDATE cards SET tcgplayer_id = 501 WHERE id = 't1-1'")
    main.conn.execute("UPDATE cards SET tcgplayer_id = 502 WHERE id = 't1-2'")
    main.conn.execute("UPDATE cards SET tcgplayer_id = 999 WHERE id = 't1-TG01'")  # put there by the old match (G3)
    main.conn.execute("INSERT INTO tcgcsv_cards (card_id, variant, product_id, group_id) VALUES ('t1-TG01', '', 999, 1853)")
    main.conn.commit()
    get = FakeTCGCSV({1853: [_product(999, "TG01/TG30", "Not Venusaur"), _product(7, "1/2", "Someone Else")],
                      2765: [_product(501, "001/002", "Bulbasaur (Holo)"), _product(500, "001/002", "Bulbasaur (Non-Holo)"),
                             _product(502, "002/002", "Ivysaur")]})
    assert tcgcsv.load_products(main.conn, "t1", get) == 2
    row = main.conn.execute("SELECT group_id, matched_by FROM tcgcsv_sets WHERE set_id = 't1'").fetchone()
    assert tuple(row) == (2765, "product")
    got = dict(main.conn.execute("SELECT card_id, product_id FROM tcgcsv_cards WHERE variant = ''").fetchall())
    assert got == {"t1-1": 501, "t1-2": 502}  # two products numbered 1: TCGdex's decides
    assert main.conn.execute("SELECT tcgplayer_id FROM cards WHERE id = 't1-TG01'").fetchone()[0] is None
    assert tcgcsv.match_sets(main.conn)["changed"] == []  # kept: a match fixed by products isn't redone by name


def test_a_set_picked_by_hand_is_kept(client):
    _setup_with_set(client)
    _group(77, "Whatever", "W")
    assert tcgcsv.pick(main.conn, "t1", 77) and not tcgcsv.pick(main.conn, "t1", 78)
    tcgcsv.match_sets(main.conn)
    assert main.conn.execute("SELECT group_id, matched_by FROM tcgcsv_sets WHERE set_id = 't1'").fetchone()[:] == (77, "you")
    r = client.post("/account/data/tcgcsv/pick", data={"set_id": "t1", "group": "nope"}, follow_redirects=False)
    assert "Pick%20one" in r.headers["location"]


def test_an_unmatched_kept_set_is_found_by_its_products(client):
    _setup_with_set(client)
    _entry(client, "t1", "1", "normal")
    main.conn.execute("DELETE FROM tcgcsv_sets")
    main.conn.execute("UPDATE cards SET tcgplayer_id = 501 WHERE id = 't1-1'")
    _group(10, "Test Set: Trainer Gallery", "TG")
    _group(11, "Test Set Promos", "TP")
    main.conn.commit()
    get = FakeTCGCSV({10: [_product(9, "1", "x")], 11: [_product(501, "1", "Bulbasaur")]})
    assert tcgcsv.match_by_products(main.conn, get, sleep=lambda s: None) == 1
    assert main.conn.execute("SELECT group_id FROM tcgcsv_sets WHERE set_id = 't1'").fetchone()[0] == 11


def test_upgrade_rematches_and_reprices(client):
    _setup_with_set(client)
    _entry(client, "t1", "2", "normal")
    _group(1, "Elsewhere", "T1X")
    _group(2, "Test Set", "T1")  # t1 itself now: the TCGdex id is its abbreviation
    main.conn.execute("INSERT OR REPLACE INTO tcgcsv_sets (set_id, category, group_id, matched_by) VALUES ('t1', 3, 1, 'name')")
    main.conn.execute("INSERT OR REPLACE INTO price_quotes (card_id, variant, source, price, currency, basis, updated_at) "
                      "VALUES ('t1-2', 'normal', 'tcgplayer', 99.0, 'USD', 'tcgcsv', '2026-11-01')")
    main.conn.commit()
    prices.resolve(main.conn, "t1-2")
    upgrades.tcgplayer_matches(main.conn)
    assert main.conn.execute("SELECT group_id FROM tcgcsv_sets WHERE set_id = 't1'").fetchone()[0] == 2
    assert not main.conn.execute("SELECT 1 FROM prices WHERE card_id = 't1-2' AND price = 99.0").fetchone()


# --- 1. prices for cards nobody keeps ------------------------------------------------------------------------

def test_a_tile_uses_the_product_tcgdex_names_in_any_set(client):
    _setup_with_set(client)
    main.conn.execute("UPDATE cards SET tcgplayer_id = 97807 WHERE id = 't1-1'")
    main.conn.execute("INSERT INTO tcgcsv_product_prices (group_id, product_id, subtype, market) VALUES "
                      "(1422, 97807, 'Normal', 117.55), (1422, 90124, 'Holofoil', 195.18)")
    main.conn.commit()
    found, waiting = tile_prices.for_cards(main.conn, [{"key": "k", "id": "t1-1", "lang": "en", "set_id": "t1",
                                                        "set_name": "Test Set", "number": "1"}])
    assert found == {"k": 117.55} and not waiting


def test_a_known_product_missing_from_the_set_gives_no_other_cards_price(client):
    _setup_with_set(client)
    main.conn.execute("UPDATE cards SET tcgplayer_id = 234059 WHERE id = 't1-2'")
    main.conn.execute("INSERT INTO tcgcsv_sets (set_id, category, group_id, matched_by) VALUES ('t1', 3, 1853, 'code') "
                      "ON CONFLICT (set_id) DO UPDATE SET group_id = 1853")
    main.conn.execute("INSERT INTO tcgcsv_group_reads (group_id, category, products_at, prices_at) VALUES (1853, 3, '2099', '2099')")
    main.conn.execute("INSERT INTO tcgcsv_products (category, group_id, product_id, number, name, market) VALUES (3, 1853, 5, '2', 'Ivysaur', 50)")
    main.conn.execute("INSERT INTO tcgcsv_product_prices (group_id, product_id, subtype, market) VALUES (1853, 5, 'Normal', 50)")
    main.conn.commit()
    found, _ = tile_prices.for_cards(main.conn, [{"key": "k", "id": "t1-2", "lang": "en", "set_id": "t1",
                                                  "set_name": "Test Set", "number": "2", "english": "Ivysaur"}])
    assert found == {}  # #2 in another TCGplayer set isn't this card


def test_a_lowest_listing_only_price_is_used_and_labelled(client):
    _setup_with_set(client)
    main.conn.execute("INSERT OR REPLACE INTO tcgcsv_sets (set_id, category, group_id, matched_by) VALUES ('t1', 3, 40, 'code')")
    main.conn.execute("INSERT INTO tcgcsv_cards (card_id, variant, product_id, group_id) VALUES ('t1-2', '', 402, 40)")
    main.conn.commit()
    get = FakeTCGCSV({}, {40: [{"productId": 402, "subTypeName": "Normal", "marketPrice": None, "midPrice": None, "lowPrice": 3.5}]})
    assert tcgcsv.load_prices(main.conn, "t1", get) == 1
    row = tcgcsv.for_card(main.conn, "t1-2")["normal"]
    assert row["market"] == 3.5
    assert prices.source_of({"origin": "tcgplayer", "price": 3.5, "low": 3.5, "mid": None})["label"] == "TCGplayer · lowest listing"
    assert prices.source_of({"origin": "tcgplayer", "price": 4.0, "low": 3.5, "mid": 4.5})["label"] == "TCGplayer"


# --- 2. sets with no price source ---------------------------------------------------------------------------

def test_data_sources_lists_your_sets_with_no_price_source(client, monkeypatch):
    monkeypatch.setenv("DEXSTASH_TCGCSV", "1")
    _setup_with_set(client)
    _entry(client, "t1", "1", "normal")
    main.conn.execute("DELETE FROM tcgcsv_sets")
    main.conn.commit()
    page = client.get("/account/data").text
    assert "1 of your set with no price source" in page and "cardtrader.com/en/search?q=Test+Set" in page
    _group(5, "Not It", "NI")
    main.conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('tcgcsv_groups_at', '2026-11-04T00:00:00Z')")
    main.conn.commit()
    page = client.get("/account/data").text
    assert "not matched on TCGplayer" in page and 'action="/account/data/tcgcsv/pick"' in page


# --- 3. card names ------------------------------------------------------------------------------------------

@pytest.fixture
def names(tmp_path):
    rows = ["pokemon_species_id,local_language_id,name,genus"]
    for sid, en, ja, zh in [(25, "Pikachu", "ピカチュウ", "皮卡丘"), (644, "Zekrom", "ゼクロム", "捷克罗姆"),
                            (658, "Greninja", "ゲッコウガ", "甲贺忍蛙"), (6, "Charizard", "リザードン", "喷火龙"),
                            (1017, "Ogerpon", "オーガポン", "厄诡椪"), (892, "Urshifu", "ウーラオス", "武道熊师"),
                            (197, "Umbreon", "ブラッキー", "月亮伊布")]:
        rows += [f"{sid},9,{en},", f"{sid},1,{ja},", f"{sid},12,{zh},"]
    path = tmp_path / "names.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return Names(path)


@pytest.mark.parametrize("printed, english", [
    ("ピカチュウ&ゼクロムGX", "Pikachu & Zekrom GX"), ("かがやくゲッコウガ", "Radiant Greninja"),
    ("光辉甲贺忍蛙", "Radiant Greninja"), ("MリザードンEX", "M Charizard EX"), ("メガリザードンXex", "Mega Charizard X ex"),
    ("オーガポン みどりのめんex", "Teal Mask Ogerpon ex"), ("ウーラオス いちげきのかたVMAX", "Single Strike Urshifu VMAX"),
    ("ブラッキー☆", "Umbreon ☆"), ("ゲッコウガBREAK", "Greninja BREAK"), ("ピカチュウ-GX", "Pikachu GX"),
    ("皮卡丘ex", "Pikachu ex"), ("ポケモンのふえ", None), ("基本雷エネルギー", None),
])
def test_more_card_names_in_english(names, printed, english):
    assert names.to_english(printed) == english


# --- 4. API keys --------------------------------------------------------------------------------------------

@pytest.fixture
def key_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(keys, "folder", tmp_path)
    monkeypatch.delenv(cardtrader.TOKEN_ENV, raising=False)
    return tmp_path


def test_a_key_is_sealed_never_shown_and_not_in_backups(client, key_folder, monkeypatch):
    _setup_with_set(client)
    keys.save("pricecharting", "test-token-not-a-real-one")
    raw = (key_folder / keys.FILE_NAME).read_text()
    assert "test-token-not-a-real-one" not in raw and keys.get("pricecharting") == "test-token-not-a-real-one"
    info = keys.info("pricecharting")
    assert info["last4"] == "-one" and "test-token" not in json.dumps(info)
    monkeypatch.setattr(web, "SECRET_KEY", "x" * 64)  # another install's secret key: no key, not garbage
    assert keys.get("pricecharting") is None and keys.info("pricecharting")["unreadable"]
    monkeypatch.undo()
    monkeypatch.setattr(keys, "folder", key_folder)
    made = backup.make(main.conn, key_folder / "backups")
    assert b"test-token" not in made.read_bytes() and b"sealed" not in made.read_bytes()
    with pytest.raises(ValueError):
        keys.save("pricecharting", "has a space")
    with pytest.raises(ValueError):
        keys.save("ebay", "anything")  # coming soon: no key yet
    keys.remove("pricecharting")
    assert keys.get("pricecharting") is None


def test_the_cardtrader_token_moves_from_env_and_still_works_before(key_folder, monkeypatch):
    monkeypatch.setenv(cardtrader.TOKEN_ENV, "test-token-not-a-real-one")
    assert cardtrader.token() == "test-token-not-a-real-one" and keys.info("cardtrader")["from_env"]
    assert keys.move_env_tokens() == ["cardtrader"]
    monkeypatch.delenv(cardtrader.TOKEN_ENV)
    assert cardtrader.token() == "test-token-not-a-real-one" and keys.info("cardtrader")["last4"] == "-one"
    assert keys.move_env_tokens() == []


def test_data_sources_keys_save_test_and_remove(client, key_folder, monkeypatch):
    _setup_with_set(client)
    monkeypatch.setattr(cardtrader, "test", lambda: (True, "Works (Misty's shop)."))
    page = client.get("/account/data").text
    assert '<h2 id="keys">API keys</h2>' in page and "Coming soon" in page and "eBay sold prices" in page
    r = client.post("/account/data/keys/cardtrader", data={"key": "test-token-not-a-real-one"}, follow_redirects=False)
    assert "message=Saved.%20Works" in r.headers["location"]
    page = client.get("/account/data").text
    assert "test-token-not-a-real-one" not in page and "&bull;&bull;&bull;&bull;-one" in page and "Works (Misty" in page
    assert client.post("/account/data/keys/nowhere", data={"key": "x"}).status_code == 404
    client.post("/account/data/keys/cardtrader/remove")
    assert keys.get("cardtrader") is None


def test_only_pricechartings_api_is_let_through():
    assert sites.blocked("https://www.pricecharting.com/game/pokemon-base-set/charizard-4")
    assert not sites.blocked("https://www.pricecharting.com/api/product?id=1")
    assert sites.blocked("https://www.pricecharting.com/apix")


def test_pricecharting_matches_by_number_set_and_language():
    card = {"number": "077", "set_name": "Twilight Masquerade", "lang": "en"}
    products = [{"id": 1, "product-name": "Pikachu ex #77", "console-name": "Pokemon Twilight Masquerade"},
                {"id": 2, "product-name": "Pikachu ex [Reverse Holo] #77", "console-name": "Pokemon Twilight Masquerade"},
                {"id": 3, "product-name": "Pikachu ex #177", "console-name": "Pokemon Twilight Masquerade"},
                {"id": 4, "product-name": "Pikachu ex #77", "console-name": "Pokemon Japanese Twilight Masquerade"},
                {"id": 5, "product-name": "Pikachu ex [Cosmos Holo] #77", "console-name": "Pokemon Twilight Masquerade"}]
    got = pricecharting.matches(card, products)
    assert {v: p["id"] for v, p in got.items()} == {"": 1, "reverse": 2}
    assert {v: p["id"] for v, p in pricecharting.matches(dict(card, lang="ja"), products).items()} == {"": 4}
    assert pricecharting.matches(card, products + [dict(products[0], id=9)]).keys() == {"reverse"}  # two: none


def test_pricecharting_prices_kept_cards_and_a_typed_price_wins(client, key_folder, monkeypatch):
    _setup_with_set(client)
    _entry(client, "t1", "2", "normal")
    keys.save("pricecharting", "test-token-not-a-real-one")
    asked = []

    def fake(path, params):
        asked.append(path)
        if path == "products":
            return {"status": "success", "products": [{"id": 42, "product-name": "Ivysaur #2", "console-name": "Pokemon Test Set"}]}
        return {"status": "success", "id": 42, "loose-price": 1234}
    monkeypatch.setattr(pricecharting, "fetch", fake)
    assert pricecharting.run(main.conn, sleep=lambda s: None) == (1, [])
    q = main.conn.execute("SELECT variant, price, basis FROM price_quotes WHERE card_id = 't1-2' AND source = 'pricecharting'").fetchone()
    assert tuple(q) == ("", 12.34, "api")
    prices.resolve(main.conn, "t1-2")
    row = next(r for r in prices.card_prices(main.conn, "t1-2") if r["variant"] == "normal")
    assert any(f["origin"] == "pricecharting" and f["price"] == 12.34 for f in row["sources"])
    assert prices.source_of(dict(row, origin="pricecharting", sources=[{"origin": "pricecharting", "basis": "api"}]))["label"] \
        == "PriceCharting · ungraded"
    assert pricecharting.run(main.conn, sleep=lambda s: None) == (0, []) and asked == ["products", "product"]  # not again today
    main.conn.execute("INSERT INTO typed_prices (card_id, variant, kind, price, currency, typed_at) "
                      "VALUES ('t1-2', 'normal', 'pricecharting', 20.0, 'USD', '2026-11-04')")
    main.conn.commit()
    prices.resolve(main.conn, "t1-2")
    row = next(r for r in prices.card_prices(main.conn, "t1-2") if r["variant"] == "normal")
    assert [f["price"] for f in row["sources"] if f["origin"] == "pricecharting"] == [20.0]


def test_no_pricecharting_token_no_request(key_folder, monkeypatch):
    monkeypatch.setattr(sites, "client", lambda **kw: (_ for _ in ()).throw(AssertionError("asked PriceCharting")))
    assert pricecharting.run(main.conn) == (0, [])


def test_env_example_and_docs_send_keys_to_the_app():
    env = (ROOT / ".env.example").read_text()
    assert "CARDTRADER_TOKEN" not in env and "API keys" in env
    assert "Data sources › Prices › API keys" in (ROOT / "README.md").read_text()
