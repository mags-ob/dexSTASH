"""Find with every language, one tile per art (#95), and the language chips on a card page."""

import re

from app import card_index, main, web

from test_app import FakeSearchAPI, client  # noqa: F401


class FakeJapaneseAPI(FakeSearchAPI):
    """Same ids as English, as if TCGdex's Japanese sets matched them."""
    lang = "ja"


class FakeTaiwanAPI(FakeSearchAPI):
    lang = "zh-tw"


class FakeKoreanAPI(FakeSearchAPI):
    lang = "ko"


def _setup(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakeSearchAPI())
    monkeypatch.setitem(web._other_apis, "ja", FakeJapaneseAPI())
    monkeypatch.setitem(web._other_apis, "zh-tw", FakeTaiwanAPI())
    monkeypatch.setitem(web._other_apis, "ko", FakeKoreanAPI())
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    main.conn.execute("DELETE FROM card_arts")  # the catalog file outlives a test's own database
    main.conn.execute("DELETE FROM index_cards")
    main.conn.commit()


def _art(*card_ids, dex=1):
    """One picture-matched art (as #116's rebuild stores them)."""
    main.conn.executemany("INSERT INTO card_arts (card_id, dex_id, art) VALUES (?, ?, ?)",
                          [(c, dex, card_ids[0]) for c in card_ids])
    main.conn.commit()


def _indexed(lang, card_id, illustrator, dex=1):
    """A card in the local catalog, with its artist and Pokémon (for the rough rule)."""
    main.conn.execute("INSERT INTO index_cards (lang, id, set_id, local_id, name, folded, illustrator, dex_ids) "
                      "VALUES (?, ?, ?, ?, 'Bulbasaur', 'bulbasaur', ?, ?)",
                      (lang, card_id, card_id.split("-")[0], card_id.split("-")[1], illustrator, f"[{dex}]"))
    main.conn.commit()


def _tiles(page):
    """Find's tiles: (the link, the language tags with 'h' when owned)."""
    grid = page.split('id="find-grid"')[1]
    out = []
    for tile in grid.split('class="ctile')[1:]:
        href = re.search(r'<a href="([^"]+)"', tile).group(1)
        tags = re.findall(r'class="ltg ltg-(\w+)( h)?"', tile)
        out.append((href.split("?")[0], [k + ("*" if h else "") for k, h in tags]))
    return out


def test_find_searches_every_language_by_default_with_no_language_buttons(client, monkeypatch):
    _setup(client, monkeypatch)
    _art("t1-1", "ja:t1-1", "zh-tw:t1-1")
    page = client.get("/search", params={"q": "1"}).text
    assert 'class="chip lc-' not in page  # AC1: no language buttons
    assert not re.search(r'name="lang" value="\w+" checked', page)  # nothing ticked under Filters
    assert '<span class="badge">' not in page  # no filter on
    # AC2, AC3: English, Japanese and Taiwan cards (3 of each) in one search; one art makes one tile.
    assert re.search(r'<span id="find-count">7</span> arts &middot; 9 cards', page)
    tiles = dict(_tiles(page))
    assert tiles["/preview/en/t1-1"] == ["en", "ja", "zh"]  # AC4: the English card is the cover, with its languages
    assert tiles["/preview/en/sv03.5-001"] == ["en"] and tiles["/preview/ja/sv03.5-001"] == ["ja"]
    assert "/preview/ja/t1-1" not in tiles and "/preview/zh-tw/t1-1" not in tiles


def test_a_tile_fills_the_languages_you_have(client, monkeypatch):
    _setup(client, monkeypatch)
    _art("t1-1", "ja:t1-1", "zh-tw:t1-1")
    client.post("/search/add", data={"card_id": "t1-1", "set_id": "t1", "lang": "ja", "variant": "normal", "condition": "NM"})
    tiles = dict(_tiles(client.get("/search", params={"q": "1"}).text))
    assert tiles["/preview/en/t1-1"] == ["en", "ja*", "zh"]


def test_without_picture_matches_the_same_pokemon_and_artist_group(client, monkeypatch):
    _setup(client, monkeypatch)
    _indexed("en", "t1-1", "Misty Art")
    _indexed("ja", "t1-1", "Misty  art")  # spelt a little differently: still the same artist
    _indexed("en", "sv03.5-001", "Someone Else")
    page = client.get("/search", params={"q": "1"}).text
    tiles = dict(_tiles(page))
    assert tiles["/preview/en/t1-1"] == ["en", "ja"]  # AC5
    assert tiles["/preview/en/sv03.5-001"] == ["en"] and "/preview/zh-tw/t1-1" in tiles  # not in the catalog: alone
    assert re.search(r'<span id="find-count">8</span> arts &middot; 9 cards', page)


def test_a_picked_language_is_a_chip_and_shows_that_languages_cards(client, monkeypatch):
    _setup(client, monkeypatch)
    _art("t1-1", "ja:t1-1", "zh-tw:t1-1")
    page = client.get("/search", params={"q": "1", "lang": "ja"}).text
    assert 'name="lang" value="ja" checked' in page and '<span class="badge">1</span>' in page  # AC6
    assert re.search(r'<a href="/search\?q=1&amp;lang=all" aria-label="Clear Japanese">Japanese', page)
    tiles = dict(_tiles(page))
    assert tiles == {"/preview/ja/t1-1": ["en", "ja", "zh"], "/preview/ja/sv03.5-001": ["ja"],
                     "/preview/ja/old-1": ["ja"]}  # Japanese covers
    page = client.get("/search", params={"q": "1"}).text  # it sticks
    assert 'name="lang" value="ja" checked' in page
    page = client.get("/search", params={"q": "1", "lang": "all"}).text  # ✕: every language again
    assert "/preview/en/t1-1" in page and not re.search(r'name="lang" value="\w+" checked', page)


def test_korean_searches_on_its_own(client, monkeypatch):
    _setup(client, monkeypatch)
    page = client.get("/search", params={"q": "1", "lang": "ko"}).text
    assert "/preview/ko/t1-1" in page and "/preview/en/" not in page
    assert 'class="ltags"' not in page and "match" in page.split('id="find-count"')[1][:80]


def test_languages_still_loading_are_left_out_and_named(client, monkeypatch):
    _setup(client, monkeypatch)
    monkeypatch.setattr(card_index, "enabled", lambda: True)
    monkeypatch.setattr(card_index, "_db", lambda: None)
    monkeypatch.setattr(card_index, "complete", lambda conn, api: api.lang != "ja")
    page = client.get("/search", params={"q": "1"}).text
    assert "Japanese cards are still loading" in page  # AC8
    assert "/preview/ja/" not in page and "/preview/en/t1-1" in page and "/preview/zh-tw/t1-1" in page


def test_the_card_page_shows_an_unfollowed_arts_languages_near_the_picture(client, monkeypatch):
    _setup(client, monkeypatch)
    client.post("/search/add", data={"card_id": "t1-1", "set_id": "t1", "lang": "en", "variant": "normal", "condition": "NM"})
    _art("t1-1", "ja:t1-1")
    page = client.get("/cards/t1-1").text
    langs = page.split('aria-label="Languages"')[1].split("</div>")[0]
    assert '<span class="pr-chip lchip lchip-en here have" aria-current="page" aria-label="English, you have it">English</span>' in langs
    assert '<a class="pr-chip lchip lchip-ja" href="/preview/ja/t1-1" aria-label="Japanese">Japanese</a>' in langs  # AC7
    assert "Chinese" not in langs
    # A preview shows them too, linking back to the stored English card.
    langs = client.get("/preview/ja/t1-1").text.split('aria-label="Languages"')[1].split("</div>")[0]
    assert 'href="/cards/t1-1"' in langs and 'lchip-ja here' in langs


def test_the_card_page_uses_the_rough_rule_without_picture_matches(client, monkeypatch):
    _setup(client, monkeypatch)
    client.post("/search/add", data={"card_id": "t1-1", "set_id": "t1", "lang": "en", "variant": "normal", "condition": "NM"})
    _indexed("en", "t1-1", "Misty Art")
    _indexed("ja", "t1-1", "Misty Art")
    langs = client.get("/cards/t1-1").text.split('aria-label="Languages"')[1].split("</div>")[0]
    assert 'href="/preview/ja/t1-1"' in langs


def test_a_korean_card_page_has_no_language_chips(client, monkeypatch):
    _setup(client, monkeypatch)
    client.post("/search/add", data={"card_id": "t1-1", "set_id": "t1", "lang": "ko", "variant": "normal", "condition": "NM"})
    assert 'aria-label="Languages"' not in client.get("/cards/ko:t1-1").text


def test_every_language_lists_newest_sets_first_across_languages(client, monkeypatch):
    _setup(client, monkeypatch)
    order = [href for href, _ in _tiles(client.get("/search", params={"q": "1"}).text)]
    # Test Set is the newest in every language, then Dotted Set, then Old Set: not English first, then the rest.
    assert [h.rsplit("/", 1)[1].split("-")[0] for h in order] == ["t1"] * 3 + ["sv03.5"] * 3 + ["old"] * 3
    assert order[:3] == ["/preview/en/t1-1", "/preview/ja/t1-1", "/preview/zh-tw/t1-1"]


def test_set_codes_read_the_same_in_every_language():
    from app import display
    assert display.printed_code("sv08") == "SSP" and display.printed_code("sv03.5") == "MEW"  # English: the printed code
    assert display.printed_code("t1") == "T1"  # not listed: its id
    assert display.printed_code("SV8a", "ja") == "SV8a" and display.printed_code("zh-cn:CSV7C") == "CSV7C"
    assert display.set_code_line("SV8a", "054", 106, "ja") == "SV8a · 054/106"
    assert display.set_code_line("sv08", "TG05", 191) == "SSP · TG05"  # the total only follows a plain number
    assert display.set_code_line("svp", "001") == "SVP · 001"


def test_find_tiles_and_card_views_show_set_codes_not_foreign_set_names(client, monkeypatch):
    _setup(client, monkeypatch)
    page = client.get("/search", params={"q": "1", "lang": "ja"}).text
    grid = page.split('id="find-grid"')[1]
    assert '<span class="t">t1 · 1/2</span>' in grid and "Test Set" not in grid.replace('title="Test Set', "")  # AC14
    page = client.get("/search", params={"q": "1", "lang": "all"}).text
    assert '<span class="t">T1 · 1/2</span>' in page.split('id="find-grid"')[1]  # English tiles too
    top = client.get("/preview/ja/t1-1").text.split('class="cno"')[0].rsplit("<a ", 1)[1]
    assert ">t1</a>" in top and "Test Set" not in top  # a Japanese card's view: the code
    client.post("/search/add", data={"card_id": "t1-1", "set_id": "t1", "lang": "en", "variant": "normal", "condition": "NM"})
    assert 'href="/sets/t1">Test Set</a>' in client.get("/cards/t1-1").text  # an English card keeps its set's name
