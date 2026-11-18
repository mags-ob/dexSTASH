"""Find's first page without prices (#66), and the artist's tag searching Find for them (#67)."""
from app import main, tcgdex
from app import web
from test_app import FakeSearchAPI, _setup_with_set, client  # noqa: F401  (the client fixture)
from test_round10 import ARITA, artists  # noqa: F401  (the artists fixture)


def _recent_block(page: str) -> str:
    return page.split('class="cgrid recent-views"')[1]  # nothing else follows it on an empty Find


# --- #66: no price line on Find's first page ----------------------------------------------------------

def test_recently_viewed_has_no_price_line(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakeSearchAPI())
    _setup_with_set(client)
    client.get("/cards/t1-1")
    client.get("/cards/t1-2")
    block = _recent_block(client.get("/search").text)
    assert 'href="/cards/t1-1"' in block and 'href="/cards/t1-2"' in block  # picture and name
    assert 'class="p"' not in block and "No price" not in block


def test_search_results_and_set_pages_keep_their_prices(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakeSearchAPI())
    _setup_with_set(client)
    client.get("/cards/t1-1")
    page = client.get("/search", params={"q": "bulbasaur"}).text
    assert "recent-views" not in page and '<span class="p">' in page
    assert '<span class="p">' in client.get("/sets/t1").text


# --- #67: the artist's tag searches Find for them -------------------------------------------------------

def _tag(card_id: str, tag: str, kind: str = "auto"):
    main.conn.execute("DELETE FROM card_tags WHERE card_id = ? AND tag = ?", (card_id, tag))
    main.conn.execute("INSERT INTO card_tags (card_id, tag, kind) VALUES (?, ?, ?)", (card_id, tag, kind))
    main.conn.commit()


def test_the_artist_tag_searches_find_for_the_artist(client, artists):
    _setup_with_set(client)
    _tag("t1-1", ARITA)
    _tag("t1-1", "Basic")
    page = client.get("/cards/t1-1").text
    assert 'href="/search?q=Mitsuhiro+Arita" title="Every card drawn by Mitsuhiro Arita"' in page
    assert "/search?tag=Mitsuhiro+Arita" not in page
    assert 'href="/search?tag=Basic"' in page  # other tags still search by tag
    found = client.get("/search", params={"q": ARITA}).text  # where the link leads: the same as typing it
    assert "Cards drawn by <strong>Mitsuhiro Arita</strong>" in found and 'href="/artists/mitsuhiro%20arita"' in found
    assert "/preview/en/t1-1" in found and "/preview/en/t1-2" in found


def test_only_the_automatic_artist_tag_is_the_artist(client, artists):
    _setup_with_set(client)
    _tag("t1-2", ARITA, "mine")  # your own tag with an artist's name stays a tag
    page = client.get("/cards/t1-2").text
    assert 'href="/search?tag=Mitsuhiro+Arita"' in page and "Every card drawn by" not in page


def test_the_card_page_never_asks_tcgdex_for_the_artist_list(client, monkeypatch):
    asked = []
    monkeypatch.setattr(web, "api", FakeSearchAPI())
    monkeypatch.setattr(tcgdex, "artist_names", lambda api=None, max_age=86400: asked.append(api) or [ARITA])
    _setup_with_set(client)
    _tag("t1-1", ARITA)
    assert "Every card drawn by" in client.get("/cards/t1-1").text
    assert asked and all(a is None for a in asked)


def test_artist_pages_are_unchanged(client, artists):
    _setup_with_set(client)
    _tag("t1-1", ARITA)
    client.post("/pokemon/follow", data={"kind": "artist", "artist": ARITA, "dex": ""})
    assert client.get("/artists/mitsuhiro%20arita").status_code == 200
