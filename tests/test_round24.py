"""Round 24 (0.24.0): Home's price area (option B). Spent leaves Home, the card count sits under the value,
today's biggest movers, the Pokémon tile's fun fact and PokéDoku link, Cards to review above Recently added, and
Your Pokémon off Home."""
from datetime import date, timedelta

from app import display, facts, home, main, pokeapi, value, web
from test_app import FakePricedAPI, _entry, _setup_with_set, client  # noqa: F401


def _history(rows):
    """(card, average price, days ago) rows of price history."""
    main.conn.executemany("INSERT INTO price_snapshots (card_id, variant, source, price, captured_at) VALUES (?, 'normal', 'average', ?, ?)",
                          [(c, p, (display.today() - timedelta(days=n)).isoformat() + " 03:00:00") for c, p, n in rows])
    main.conn.commit()


def _moved(client, monkeypatch):
    """Two cards: t1-1 $4 yesterday, $5 now; t1-2 $2 yesterday, $1.50 now."""
    monkeypatch.setattr(web, "api", FakePricedAPI())
    uid = _setup_with_set(client)
    _entry(client, "t1", "1")
    _entry(client, "t1", "2")
    main.conn.execute("UPDATE prices SET price = 5.0 WHERE card_id = 't1-1'")
    main.conn.execute("DELETE FROM price_snapshots")
    _history([("t1-1", 4.0, 1), ("t1-1", 5.0, 0), ("t1-2", 2.0, 1), ("t1-2", 1.5, 0)])
    return uid


def test_movers_since_the_day_before(client, monkeypatch):
    uid = _moved(client, monkeypatch)
    m = home.movers(main.conn, uid)
    assert [(r["card_id"], r["then"], r["now"]) for r in m["up"]] == [("t1-1", 4.0, 5.0)]
    assert [(r["card_id"], r["change"]) for r in m["down"]] == [("t1-2", -0.5)]
    assert m["start"] == (display.today() - timedelta(days=1)).isoformat()
    # A move from two days back isn't today's.
    main.conn.execute("DELETE FROM price_snapshots WHERE card_id = 't1-1'")
    _history([("t1-1", 4.0, 3), ("t1-1", 5.0, 1)])
    assert home.movers(main.conn, uid)["up"] == []
    assert value.movers(main.conn, uid, "1w")["up"][0]["card_id"] == "t1-1"  # still a mover over the week


def test_home_price_area(client, monkeypatch):
    _moved(client, monkeypatch)
    main.conn.execute("INSERT INTO price_runs (ran_at, status) VALUES (?, 'ok')",
                      ((display.today() - timedelta(days=30)).isoformat() + " 03:00:00",))
    main.conn.commit()
    page = client.get("/").text
    assert "spent" not in page and "/spending" not in page  # H1
    assert '<div class="card-count">2 cards</div>' in page and "since " in page  # H2
    assert "Most valuable card" in page  # H3
    assert "Today's movers" in page and 'href="/value?moves=1d#movers"' in page  # H4
    up, down = page.index('class="mlist mv-up"'), page.index('class="mlist mv-down"')
    assert up < page.index('href="/cards/t1-1"', up) < down < page.index('href="/cards/t1-2"', down)
    assert "Your Pokémon" not in page and "poke-grid" not in page  # H7


def test_movers_fill_in_and_review_comes_first(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakePricedAPI())
    _setup_with_set(client)
    _entry(client, "t1", "1")
    main.conn.execute("DELETE FROM prices")  # a card with no price is one to review
    main.conn.commit()
    page = client.get("/").text
    assert "Moves fill in day by day" in page
    assert page.index("Cards to review") < page.index("Recently added")


def test_value_page_shows_todays_movers(client, monkeypatch):
    _moved(client, monkeypatch)
    page = client.get("/value?moves=1d").text
    assert "Top movers today" in page and "Since the day before" in page and "&moves=1d#movers" in page
    assert "Top movers today" not in client.get("/value").text


FLAVOUR = """species_id,version_id,language_id,flavor_text
197,1,9,"The light of the moon changed EEVEE's genetic
structure."
197,2,9,"The light of the moon changed Eevee's genetic structure."
197,3,1,"月の 光で"
197,4,9,"When agitated, this POKéMON pro\xad tects itself."
197,5,9,"When agitated, this Pokémon protects itself."
25,1,9,"It stores electricity in its cheeks."
"""


def test_facts_one_a_day_in_normal_case(tmp_path):
    (tmp_path / "pokemon_species_flavor_text.csv").write_text(FLAVOUR, encoding="utf-8")
    found = facts.read(tmp_path)
    assert found[197] == ["The light of the moon changed Eevee's genetic structure.", "When agitated, this Pokémon protects itself."]
    facts.forget()
    try:
        day = date(2026, 10, 30)
        assert facts.for_day(197, day, tmp_path) is None  # read in the background first, never waited for
        for t in [t for t in __import__("threading").enumerate() if t.name == "facts"]:
            t.join(5)
        shown = {facts.for_day(197, day + timedelta(days=n), tmp_path) for n in range(2)}
        assert shown == set(found[197])  # they take turns, one a day
        assert facts.for_day(25, day, tmp_path) == "It stores electricity in its cheeks."
        assert facts.for_day(9999, day, tmp_path) is None
    finally:
        facts.forget()


def test_no_fact_without_the_file(tmp_path):
    facts.forget()
    assert not pokeapi.have("pokemon_species_flavor_text", tmp_path)
    assert facts.for_day(197, date(2026, 10, 30), tmp_path) is None


def test_pokemon_tile_has_the_fact_and_pokedoku(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakePricedAPI())
    _setup_with_set(client)
    from app.routes import home as home_routes
    monkeypatch.setattr(home_routes, "_theme_for", lambda user: {"species_id": 197, "shiny": False, "name": "Umbreon",
                                                                  "swatches": [], "sprite": "/sprites/197.png"})
    monkeypatch.setattr(facts, "for_day", lambda species, day: "Its rings glow." if species == 197 else None)
    page = client.get("/").text
    assert 'aria-label="Umbreon\'s set"' in page and "Its rings glow." in page
    assert f'href="{home.POKEDOKU_URL}" target="_blank" rel="noopener noreferrer"' in page
