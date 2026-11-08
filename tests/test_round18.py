"""Round 18 (0.18.0): a printing's price is the average of every source, shown as a range on the card page."""
import json

from app import main
from test_app import _entry, _setup_with_set, client  # noqa: F401


def test_every_source_counts_toward_a_range_and_average(client):
    from app import value
    from test_round15d import _quote, _shown
    _setup_with_set(client)
    _entry(client, "t1", "2", "holo")
    main.conn.execute("DELETE FROM price_quotes")
    main.conn.execute("DELETE FROM price_snapshots")
    _quote("t1-2", "holo", "tcgplayer", 6.0)
    _quote("t1-2", "", "cardtrader", 12648.0, basis="nm")  # a placeholder far above the rest is left out
    main.conn.execute("INSERT INTO typed_prices (card_id, variant, kind, price, currency, typed_at) "
                      "VALUES ('t1-2', 'holo', 'manual', 12.0, 'USD', '2026-10-28T00:00:00Z')")
    assert _shown("t1-2") == {"holo": (9.0, "tcgplayer", None)}  # (6 + 12) / 2; round 20: no Cardmarket
    row = main.conn.execute("SELECT range_low, range_high, sources FROM prices WHERE card_id = 't1-2'").fetchone()
    assert (row["range_low"], row["range_high"]) == (6.0, 12.0) and "cardtrader" not in [f["origin"] for f in json.loads(row["sources"]) if not f.get("left_out")]
    page = client.get("/cards/t1-2").text
    assert "$6.00 – $12.00" in page and "Average $9.00 · 2 sources" in page
    assert "https://www.cardmarket.com/en/Pokemon/Products/Search?searchString=Ivysaur" in page
    # The average is the price history.
    hist = main.conn.execute("SELECT price FROM price_snapshots WHERE card_id = 't1-2' AND source = 'average'").fetchall()
    assert [r[0] for r in hist] == [9.0]
    assert value.card_series(main.conn, "t1-2", "holo")["points"][-1][1:3] == (9.0, "Average")


def test_a_set_tcgdex_wont_send_doesnt_send_every_search_to_tcgdex(client, monkeypatch):
    """0.18.2: one set that fails no longer keeps the catalog unused, and a search TCGdex doesn't answer is
    answered from what the catalog has so far."""
    import httpx
    from app import card_index
    from test_round15d import INDEX_CARDS, IndexTCGdex, _index_on
    _setup_with_set(client)
    _index_on(monkeypatch)
    main.conn.execute("DELETE FROM app_meta WHERE key LIKE 'card_index_failed:%'")
    fake = IndexTCGdex()
    monkeypatch.setattr(card_index, "start", lambda api: None)
    sv1 = INDEX_CARDS.pop("sv1")
    try:
        monkeypatch.setattr(card_index, "_fetch_set", lambda api, set_id: INDEX_CARDS.get(set_id))
        assert card_index.build(fake.api, main.conn, sleep=lambda s: None) == (1, ["en sv1"])
    finally:
        INDEX_CARDS["sv1"] = sv1
    card_index.forget()
    assert [c["id"] for c in fake.api.search_cards(name="char")] == ["base1-4"]  # from the catalog
    # Forgotten after two days: TCGdex is asked again, and when it doesn't answer the catalog does.
    main.conn.execute("UPDATE app_meta SET value = '{\"sv1\": \"2000-01-01T00:00:00+00:00\"}' WHERE key = 'card_index_failed:en'")
    card_index.forget()

    def down(*a, **k):
        raise httpx.ConnectTimeout("no answer")
    monkeypatch.setattr(fake.api, "_get", down)
    assert [c["id"] for c in fake.api.search_cards(name="char")] == ["base1-4"]
