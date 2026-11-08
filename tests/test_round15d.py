"""Round 15d (0.15.3): one price table (A2) and the local card catalog (A1)."""
from app import db, main, prices
from test_app import _entry, _setup_with_set, client  # noqa: F401
from test_round15b import OLD_CT_PRICES, OLD_PRICES


# --- A2: one price table, one rule for the price a card shows ---------------------------------------------------

def _quote(card_id, variant, source, price, currency="USD", basis=None, ref=None, at="2026-10-27T08:00:00Z"):
    main.conn.execute("INSERT OR REPLACE INTO price_quotes (card_id, variant, source, price, currency, basis, ref, updated_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (card_id, variant, source, price, currency, basis, ref, at))


def _shown(card_id):
    with db.transaction(main.conn):
        prices.resolve(main.conn, card_id)
    return {r["variant"]: (r["price"], r["origin"], r["basis"]) for r in prices.card_prices(main.conn, card_id)}


def test_the_shown_price_comes_from_one_rule(client):
    _setup_with_set(client)
    for variant in ("normal", "reverse", "holo", "pokeball"):
        _entry(client, "t1", "1", variant)
    main.conn.execute("DELETE FROM price_quotes")
    _quote("t1-1", "normal", "tcgplayer", 1.0, basis="tcgcsv")
    _quote("t1-1", "normal", "cardtrader", 0.5, basis="nm")  # round 18: both count, TCGplayer's named first
    _quote("t1-1", "reverse", "cardtrader", 2.0, basis="any")
    assert _shown("t1-1") == {"normal": (0.75, "tcgplayer", "tcgcsv"), "reverse": (2.0, "cardtrader", "any"),
                              "holo": (2.0, "printing", "reverse"), "pokeball": (2.0, "printing", "reverse")}
    # Round 20: your price is for one printing, and counts in that printing's average.
    main.conn.execute("INSERT INTO typed_prices (card_id, variant, kind, price, currency, typed_at) "
                      "VALUES ('t1-1', 'holo', 'manual', 7.0, 'USD', '2026-10-27T00:00:00Z')")
    shown = _shown("t1-1")
    assert shown["holo"] == (7.0, "manual", None) and shown["reverse"][1] == "cardtrader" and shown["normal"][1] == "tcgplayer"
    # The PriceCharting price you typed says so.
    main.conn.execute("UPDATE typed_prices SET kind = 'pricecharting', price = 6.0")
    assert _shown("t1-1")["holo"] == (6.0, "pricecharting", None)
    # Each kind keeps its own history, under the names it had before.
    sources = {r[0] for r in main.conn.execute("SELECT DISTINCT source FROM price_snapshots WHERE card_id = 't1-1'")}
    assert {"cardtrader", "printing", "manual", "link"} <= sources


def test_a_downloaded_quote_disappearing_brings_the_next_price_back(client):
    _setup_with_set(client)
    _entry(client, "t1", "2", "normal")
    main.conn.execute("DELETE FROM price_quotes")
    _quote("t1-2", "normal", "tcgplayer", 3.0)
    _quote("t1-2", "", "cardtrader", 2.5, basis="nm", ref=77)
    assert _shown("t1-2")["normal"][1] == "tcgplayer"
    main.conn.execute("DELETE FROM price_quotes WHERE source = 'tcgplayer'")
    assert _shown("t1-2")["normal"] == (2.5, "cardtrader", "nm")
    page = client.get("/cards/t1-2").text
    assert "CardTrader · Near Mint median" in page and 'href="https://www.cardtrader.com/en/cards/77"' in page


def test_prune_forgets_quotes_of_cards_nobody_keeps(client):
    _setup_with_set(client)
    _quote("t1-3", "normal", "tcgplayer", 1.0)
    _quote("t1-3", "", "cardtrader", 1.0, basis="nm")
    prices.prune(main.conn)
    assert main.conn.execute("SELECT COUNT(*) FROM price_quotes WHERE card_id = 't1-3'").fetchone()[0] == 0


# --- Database step 9 ----------------------------------------------------------------------------------------

def _step8_db(path):
    """A database as 15c left it (step 8): the shown price marked in prices.currency, CardTrader's in the catalog."""
    conn = db.connect(path)
    db.init(conn)
    conn.executescript(OLD_PRICES + OLD_CT_PRICES.format(schema="catalog") + "PRAGMA user_version = 8;")
    conn.execute("INSERT INTO sets (set_id, name, printed_total, total) VALUES ('s', 'S', 9, 9)")
    for n in range(1, 5):
        conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name) VALUES (?, 's', ?, ?, 1, 'x')",
                     (f"s-{n}", str(n), n))
    conn.executemany("INSERT INTO prices (card_id, variant, source, price, currency, updated_at, basis, low, mid) "
                     "VALUES (?, ?, 'tcgplayer', ?, ?, '2026-10-26', ?, ?, ?)",
                     [("s-1", "normal", 1.5, "USD", "tcgcsv", 1.0, 2.0), ("s-1", "holo", 1.5, "USD-print", "normal", None, None),
                      ("s-2", "normal", 4.0, "USD-ct", "any", None, None), ("s-3", "normal", 6.0, "USD-extra", None, None, None),
                      ("s-4", "normal", 8.0, "USD-extra", None, None, None)])
    conn.execute("INSERT INTO card_extras (card_id, pc_typed, pc_typed_currency, updated_at) VALUES ('s-3', 6.0, 'USD', '2026')")
    conn.execute("INSERT INTO card_extras (card_id, manual_price, updated_at) VALUES ('s-4', 8.0, '2026')")
    conn.execute("INSERT INTO catalog.ct_prices (card_id, variant, price, currency, any_condition, blueprint_id, updated_at) "
                 "VALUES ('s-2', '', 3.6, 'EUR', 1, 4242, '2026-10-26')")
    conn.commit()
    return conn


def test_step_9_keeps_every_price_and_says_where_it_came_from(tmp_path):
    conn = _step8_db(tmp_path / "dexstash.db")
    db.init(conn)
    assert db.version(conn) == db.LATEST
    shown = {(r["card_id"], r["variant"]): (r["price"], r["origin"], r["basis"])
             for r in conn.execute("SELECT * FROM prices")}
    assert shown == {("s-1", "normal"): (1.5, "tcgplayer", "tcgcsv"), ("s-1", "holo"): (1.5, "printing", "normal"),
                     ("s-2", "normal"): (4.0, "cardtrader", "any"), ("s-3", "normal"): (6.0, "pricecharting", None),
                     ("s-4", "normal"): (8.0, "manual", None)}
    assert tuple(conn.execute("SELECT low, mid FROM prices WHERE card_id = 's-1' AND variant = 'normal'").fetchone()) == (1.0, 2.0)
    quotes = {(r["card_id"], r["variant"], r["source"]): (r["price"], r["currency"], r["basis"], r["ref"])
              for r in conn.execute("SELECT * FROM price_quotes")}
    assert quotes == {("s-1", "normal", "tcgplayer"): (1.5, "USD", "tcgcsv", None),
                      ("s-2", "", "cardtrader"): (3.6, "EUR", "any", 4242)}
    tables = {r[0] for r in conn.execute("SELECT name FROM catalog.sqlite_master WHERE type = 'table'")}
    assert "ct_prices" not in tables and "price_quotes" in tables
    assert "currency" not in db._columns(conn, "prices")
    db.init(conn)  # a restart changes nothing
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 5


def test_a_backup_from_before_15c_gets_steps_8_and_9(tmp_path):
    from test_round15c import _old_db
    conn = _old_db(tmp_path / "dexstash.db")  # ct_prices still in the database itself
    conn.execute("INSERT INTO main.ct_prices (card_id, variant, price, currency, any_condition, blueprint_id, updated_at) "
                 "VALUES ('a', '', 2.0, 'USD', 0, 9, '2026')")
    conn.commit()
    db.init(conn)
    assert db.version(conn) == db.LATEST
    assert tuple(conn.execute("SELECT price, basis, ref FROM price_quotes WHERE card_id = 'a'").fetchone()) == (2.0, "nm", 9)
    assert not {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE name = 'ct_prices'")}


# --- A1: the local card catalog ---------------------------------------------------------------------------------

import json  # noqa: E402

import httpx  # noqa: E402

from app import card_index, search, tcgdex  # noqa: E402

INDEX_SETS = [{"id": "base1", "name": "Base Set", "cardCount": {"total": 2, "official": 2}},
              {"id": "sv1", "name": "Scarlet & Violet", "cardCount": {"total": 3, "official": 3}},
              {"id": "A1", "name": "Genetic Apex", "cardCount": {"total": 1}}]  # TCG Pocket: never fetched
INDEX_CARDS = {
    "base1": [{"id": "base1-4", "localId": "4", "name": "Charizard", "image": "img/c", "rarity": "Rare Holo",
               "illustrator": "Mitsuhiro Arita", "dexId": [6], "types": ["Fire"], "hp": 120},
              {"id": "base1-96", "localId": "96", "name": "Double Colorless Energy", "rarity": "Uncommon"}],
    "sv1": [{"id": "sv1-004", "localId": "004", "name": "Pokémon Center Lady", "rarity": "Uncommon", "trainerType": "Supporter"},
            {"id": "sv1-005", "localId": "005", "name": "Charmander", "rarity": "Common", "dexId": [4], "types": ["Fire"]},
            {"id": "sv1-199", "localId": "199", "name": "Flabébé", "rarity": "Common", "dexId": [669]}],
}


class IndexTCGdex:
    """TCGdex over a fake network: the set list, and one GraphQL request per set."""

    def __init__(self, refuse_fields=False):
        self.asked, self.refuse = [], refuse_fields

        def handle(request: httpx.Request):
            self.asked.append((request.method, request.url.path))
            if request.url.path.endswith("/sets"):
                return httpx.Response(200, json=INDEX_SETS)
            if request.url.path.endswith("/graphql"):
                query = json.loads(request.content)["query"]
                if self.refuse and "energyType" in query:
                    return httpx.Response(200, json={"errors": [{"message": "Cannot query field energyType"}]})
                set_id = query.split('"')[1]
                return httpx.Response(200, json={"data": {"set": {"cards": INDEX_CARDS.get(set_id, [])}}})
            return httpx.Response(200, json=[{"id": "remote-1", "localId": "1", "name": "From TCGdex"}])

        self.api = tcgdex.TCGdex("en", client=httpx.Client(transport=httpx.MockTransport(handle),
                                                          base_url="https://api.tcgdex.net/v2/en"))


def _index_on(monkeypatch):
    monkeypatch.setenv("DEXSTASH_CARD_INDEX", "1")
    card_index.reset()
    for table in ("index_cards", "index_sets"):
        main.conn.execute(f"DELETE FROM {table}")
    if card_index.make_names(main.conn):
        main.conn.execute("DELETE FROM index_names")
    main.conn.commit()


def test_find_answers_from_the_local_catalog_once_a_language_is_complete(client, monkeypatch):
    _setup_with_set(client)
    _index_on(monkeypatch)
    fake = IndexTCGdex()
    started = []
    monkeypatch.setattr(card_index, "start", lambda api: started.append(api.lang))
    assert [c["id"] for c in fake.api.search_cards(name="char")] == ["remote-1"]  # not fetched yet: TCGdex answers
    assert started == ["en"]
    assert card_index.build(fake.api, main.conn, sleep=lambda s: None) == (2, [])
    assert ("POST", "/v2/graphql") in fake.asked and not any("A1" in p for _, p in fake.asked)
    card_index.forget()
    fake.asked.clear()
    assert sorted(c["id"] for c in fake.api.search_cards(name="char")) == ["base1-4", "sv1-005"]
    assert [c["id"] for c in fake.api.search_cards(name="flabebe")] == ["sv1-199"]  # accents don't matter
    assert sorted(c["id"] for c in fake.api.search_cards(local_id=["4", "04", "004"])) == ["base1-4", "sv1-004"]
    assert [c["id"] for c in fake.api.search_cards(illustrator="arita")] == ["base1-4"]
    assert sorted(c["id"] for c in fake.api.search_cards(name="ch")) == ["base1-4", "sv1-005"]  # short: matched without the index
    assert all(path.endswith("/sets") for _, path in fake.asked)  # no card was asked of TCGdex
    # Find's kind and rarity filters read what's kept instead of fetching each card.
    monkeypatch.setattr(fake.api, "get_card", lambda card_id: (_ for _ in ()).throw(AssertionError(card_id)))
    found = search.find_cards(fake.api, "c", kind="trainer")
    assert [r["id"] for r in found] == ["sv1-004"]
    assert [r["id"] for r in search.find_cards(fake.api, "char", rarity="Rare Holo")] == ["base1-4"]
    assert [r["id"] for r in search.find_cards(fake.api, "energy", kind="energy")] == ["base1-96"]


def test_the_catalog_fetches_grown_sets_again_and_takes_basic_fields(client, monkeypatch):
    _setup_with_set(client)
    _index_on(monkeypatch)
    fake = IndexTCGdex(refuse_fields=True)
    assert card_index.build(fake.api, main.conn, sleep=lambda s: None)[0] == 2
    assert main.conn.execute("SELECT kind FROM index_cards WHERE id = 'sv1-004'").fetchone()[0] == "trainer"
    assert card_index.build(fake.api, main.conn, sleep=lambda s: None)[0] == 0  # nothing due
    INDEX_SETS[1]["cardCount"]["total"] = 4  # TCGdex added a card
    try:
        assert [s["id"] for s in card_index.due(main.conn, "en", INDEX_SETS)] == ["sv1"]
    finally:
        INDEX_SETS[1]["cardCount"]["total"] = 3
    status = card_index.status(main.conn)
    assert (status[0]["lang"], status[0]["sets"], status[0]["cards"]) == ("en", 2, 5)
    assert "Card catalog for Find: English 2 sets, 5 cards" in client.get("/account/data").text
    from app import db as dbm
    dbm.rebuild_catalog(main.conn)  # Rebuild downloaded data empties it
    assert main.conn.execute("SELECT COUNT(*) FROM index_cards").fetchone()[0] == 0


def test_find_prices_cards_nobody_keeps_from_tcgcsv(client):
    _setup_with_set(client)
    main.conn.execute("INSERT INTO tcgcsv_groups (category, group_id, name, abbreviation) VALUES (3, 300, 'Far Set', 'FAR')")
    main.conn.execute("INSERT INTO tcgcsv_group_reads (group_id, category, products_at, prices_at) VALUES (300, 3, '2099', '2099')")
    main.conn.execute("INSERT INTO tcgcsv_products (category, group_id, product_id, number, name, market) VALUES (3, 300, 1, '7', 'A', 2.5)")
    rows = [{"id": "far-007", "set_id": "far", "set_name": "Far Set", "number": "007"},
            {"id": "far-008", "set_id": "far", "set_name": "Far Set", "number": "008"}]
    from app import tile_prices  # round 27: Find's tiles use the card page's main price
    found, waiting = tile_prices.for_cards(main.conn, [dict(r, key=r["id"], lang="en") for r in rows])
    assert found == {"far-007": 2.5} and not waiting


def test_version_is_0153():
    from app import __version__
    assert __version__.startswith("0.")
