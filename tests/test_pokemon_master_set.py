"""#24: a Pokémon's (or artist's) master set page: one grid, every language counted, language tags on each
tile, Value / Number / Name sorts, the Edit art sheet, and Languages on the card page."""
import json
import re

from app import chase, chase_pages, main
from test_app import _chase_rows, _setup_with_set, _vec, client  # noqa: F401

VECTORS = {"sv03.5-035": _vec(1, 0, 0), "zh-tw:SV2a-035": _vec(1, 0, 0.01), "ja:SV2a-035": _vec(1, 0.01, 0)}


def _clefairy(client, monkeypatch, own=("zh-tw:SV2a-035", "normal"), rows=None):
    """Clefairy followed: one art in English, Japanese and Taiwan Chinese (151), and Base Set on its own.
    The household has `own` (a card id and printing)."""
    uid = _setup_with_set(client)
    chase.follow(main.conn, uid, 35, "Clefairy", uid)
    chase.save_cards(main.conn, 35, rows or _chase_rows())
    monkeypatch.setattr(chase, "fingerprints", lambda conn, dex, encoder, *a, **k: VECTORS)
    chase.regroup(main.conn, 35, VECTORS)
    main.conn.execute("INSERT INTO sets (set_id, name, printed_total, total) VALUES ('sv03.5', '151', 165, 207), "
                      "('zh-tw:SV2a', '151 TW', 165, 207)")
    main.conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name) VALUES "
                      "('sv03.5-035', 'sv03.5', '035', 35, 1, 'Clefairy'), ('zh-tw:SV2a-035', 'zh-tw:SV2a', '035', 35, 1, 'Clefairy')")
    if own:
        main.conn.execute("INSERT INTO inventory (user_id, card_id, variant, condition, quantity) VALUES (?, ?, ?, 'NM', 1)",
                          (uid, *own))
    main.conn.commit()
    return uid


def _tile_order(page: str) -> list[str]:
    return re.findall(r'aria-label="Open the ([^"]+) art"', page)


def test_any_language_makes_the_art_yours(client, monkeypatch):
    uid = _clefairy(client, monkeypatch)  # only the Taiwan Chinese copy
    page = client.get("/pokemon/35").text
    assert "1 of 2 arts &middot; 50%" in page and "1 of 4 cards" in page and "1 of 7 printings" in page
    assert chase_pages.summary(main.conn, uid, 35)["arts_owned"] == 1
    assert chase_pages.followed_list(main.conn, uid)[0]["arts_owned"] == 1  # Chases says the same
    # The language tags: only the languages the art is printed in; filled when you have one.
    tiles = {t["cover"]["card_id"]: t for t in chase_pages.art_tiles(main.conn, uid, 35)}
    assert [(lang["tag"], lang["owned"]) for lang in tiles["sv03.5-035"]["langs"]] == [("EN", False), ("JA", False), ("ZH", True)]
    assert [lang["tag"] for lang in tiles["base1-5"]["langs"]] == ["EN"]
    assert 'class="ltg ltg-en">EN</span><span class="ltg ltg-ja">JA</span><span class="ltg ltg-zh h">ZH</span>' in page
    assert 'aria-label="English missing, Japanese missing, Chinese owned"' in page


def test_one_grid_and_no_language_choice(client, monkeypatch):
    _clefairy(client, monkeypatch)
    page = client.get("/pokemon/35").text
    assert 'class="pgrid" id="arts"' in page and "era-rows" not in page and "View all" not in page
    assert "<b>Language</b>" not in page and 'name="lang"' not in page and "<b>Sort</b>" not in page
    for show in ("Owned", "Missing", "♥ Chased", "Full art", "Promo"):  # Filters keeps Show
        assert f">{show}</label>" in page
    assert client.get("/pokemon/35", params={"era": "sv", "lang": "ja"}).status_code == 200  # old links still open
    assert "No arts match." in client.get("/pokemon/35", params={"show": "promo"}).text
    missing = client.get("/pokemon/35", params={"show": "missing"}).text
    assert _tile_order(missing) == ["Base Set"]  # the 151 art is yours through its Chinese copy


def test_sorts_value_number_and_name_kept_per_login(client, monkeypatch):
    rows = _chase_rows()
    rows[3]["prices_json"] = json.dumps({"holo": 40.0})  # Base Set is the dearest
    rows.append(dict(rows[0], card_id="sv03.5-200", number="200", name="Clefairy ex", set_name="151", set_code="151",
                     prices_json=json.dumps({"holo": 3.0})))
    _clefairy(client, monkeypatch, rows=rows)
    chase.regroup(main.conn, 35, dict(VECTORS, **{"sv03.5-200": _vec(0, 0, 1)}))
    page = client.get("/pokemon/35").text
    assert re.search(r'<a class="chip on"\s+href="[^"]*sort=value"', page)  # Value to start
    assert _tile_order(page) == ["Base Set", "151", "151"]  # dearest printing first
    tiles = chase_pages.art_tiles(main.conn, 1, 35, sort="number")
    assert [t["cover"]["card_id"] for t in tiles] == ["sv03.5-035", "sv03.5-200", "base1-5"]  # newest set, then number
    tiles = chase_pages.art_tiles(main.conn, 1, 35, sort="name")
    assert [t["cover"]["name"] for t in tiles] == ["Clefairy", "Clefairy", "Clefairy ex"]
    client.get("/pokemon/35", params={"sort": "number"})
    page = client.get("/pokemon/35").text  # remembered
    assert re.search(r'<a class="chip on"\s+href="[^"]*sort=number"', page)
    assert main.conn.execute("SELECT value FROM user_prefs WHERE key = 'pokemon_sort'").fetchone()[0] == "number"


def test_edit_arts_opens_a_sheet_of_the_arts_cards(client, monkeypatch):
    _clefairy(client, monkeypatch, own=("sv03.5-035", "reverse"))
    page = client.get("/pokemon/35").text
    assert "Edit arts" in page and "Modify set" not in page and 'id="art-sheet"' in page
    assert 'action="/pokemon/35/recheck"' in page.split('class="pset"')[1].split("</section>")[0]  # beside Following
    sheet = client.get("/pokemon/35/art", params={"card": "ja:SV2a-035"}).text
    assert "151 art &middot; 3 cards &middot; 6 printings" in sheet
    assert sheet.count('class="as-card"') == 3 and sheet.count(">Not this art</button>") == 3
    assert 'class="pr-chip have"><svg' in sheet and "Reverse Holo" in sheet  # your printing, ticked
    assert "Add a card to this art" in sheet and 'value="base1-5"' in sheet
    r = client.post("/pokemon/35/fix/out", data={"card": "ja:SV2a-035", "back": "sv03.5-035", "stay": "sv03.5-035"},
                    follow_redirects=False)
    assert r.headers["location"] == "/pokemon/35?open=sv03.5-035"  # the same art's sheet opens again
    assert 'data-open-art="' in client.get(r.headers["location"]).text
    sheet = client.get("/pokemon/35/art", params={"card": "sv03.5-035"}).text
    assert "2 cards" in sheet and "Taken out" not in sheet and 'value="ja:SV2a-035"' in sheet  # found again under Add


def test_the_card_page_lists_the_arts_languages(client, monkeypatch):
    _clefairy(client, monkeypatch)
    page = client.get("/cards/sv03.5-035").text
    langs = page.split('aria-label="Languages"')[1].split("</section>")[0]
    assert '<span class="pr-chip here" aria-current="page">English</span>' in langs
    assert '<a class="pr-chip" href="/preview/ja/SV2a-035">Japanese</a>' in langs
    assert re.search(r'<a class="pr-chip have" href="/cards/zh-tw:SV2a-035"><svg[^>]*>.*?</svg>Chinese</a>', langs, re.S)
    assert page.index('aria-label="Printings"') < page.index('aria-label="Languages"')
    assert 'aria-label="Languages"' not in client.get("/cards/t1-1").text  # in no followed art
    # An art in one language only: nothing to list.
    main.conn.execute("INSERT INTO sets (set_id, name, printed_total, total) VALUES ('base1', 'Base Set', 102, 102)")
    main.conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name) VALUES ('base1-5', 'base1', '5', 5, 1, 'Clefairy')")
    main.conn.commit()
    assert 'aria-label="Languages"' not in client.get("/cards/base1-5").text


def test_the_pokemon_words_start_collapsed_as_on_find(client, monkeypatch):
    from app import pokemon_words, web
    _clefairy(client, monkeypatch)
    monkeypatch.setattr(web.describer, "ready", lambda: True)
    monkeypatch.setattr(pokemon_words, "for_pokemon", lambda conn, d, dex: {"tags": [("pink", "auto"), ("fairy", "auto")]})
    page = client.get("/pokemon/35").text
    assert '<details class="fp-tags pset-tags"><summary>Tags &middot; 2 &rsaquo;</summary>' in page and 'id="ptags"' in page
    assert 'class="ltags"' in page  # the language tags on the tiles stay as they are


def test_tiles_show_printings_owned_of_total(client, monkeypatch):
    _clefairy(client, monkeypatch)  # the Taiwan Chinese normal of the 151 art
    page = client.get("/pokemon/35").text
    assert '<span aria-label="1 of 6 printings">1/6</span>' in page and '<span aria-label="0 of 1 printings">0/1</span>' in page


def test_add_a_card_offers_every_card_and_all_cards(client, monkeypatch):
    rows = _chase_rows()
    rows += [dict(rows[3], card_id=f"far-{i}", number=str(i)) for i in range(20)]  # 20 more, nothing like the art
    _clefairy(client, monkeypatch, rows=rows)
    vectors = dict(VECTORS, **{f"far-{i}": _vec(0, 1, i / 100) for i in range(20)}, **{"base1-5": _vec(0, 0, 1)})
    monkeypatch.setattr(chase, "fingerprints", lambda conn, dex, encoder, *a, **k: vectors)
    found = chase_pages.candidates(main.conn, 35, "sv03.5-035", vectors)
    assert len(found["every"]) == 21 and len(found["close"]) == chase_pages.CLOSEST  # however unalike
    assert "word" not in found["every"][0]
    sheet = client.get("/pokemon/35/art", params={"card": "sv03.5-035"}).text
    assert "Closest pictures" in sheet and "Every Clefairy card &middot; 21, newest first" in sheet
    assert ">Clefairy cards</button>" in sheet and ">All cards</button>" in sheet and "Long shot" not in sheet
    # All cards: any stored card by name, set or number, even one not of this Pokémon; adding it puts it on the page.
    main.conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name) VALUES "
                      "('sv03.5-150', 'sv03.5', '150', 150, 1, 'Pokémon Center Lady')")
    main.conn.commit()
    hits = client.get("/pokemon/35/art/search", params={"card": "sv03.5-035", "q": "pokemon center"}).text
    assert 'value="sv03.5-150"' in hits and "Pokémon Center Lady" in hits and 'value="sv03.5-035"' not in hits
    assert "No cards match" in client.get("/pokemon/35/art/search", params={"card": "sv03.5-035", "q": "zzz"}).text
    client.post("/pokemon/35/fix/add", data={"card": "sv03.5-150", "to": "sv03.5-035"})
    assert main.conn.execute("SELECT how FROM pokemon_cards WHERE dex_id = 35 AND card_id = 'sv03.5-150'").fetchone()[0] == "manual"
    art = next(a for a in chase_pages.arts(main.conn, 35) if "sv03.5-035" in a["card_ids"])
    assert "sv03.5-150" in art["card_ids"]


def _art_of(card: str) -> set[str]:
    return set(next(a for a in chase_pages.arts(main.conn, 35) if card in a["card_ids"])["card_ids"])


def test_a_card_added_by_hand_stays_when_the_cover_is_taken_out(client, monkeypatch):
    """#71: Not this art moves only that card, even when it's the cover a hand-added card was joined to."""
    _clefairy(client, monkeypatch)
    client.post("/pokemon/35/fix/add", data={"card": "base1-5", "to": "sv03.5-035"})  # joins the cover
    assert _art_of("sv03.5-035") == {"sv03.5-035", "zh-tw:SV2a-035", "ja:SV2a-035", "base1-5"}
    client.post("/pokemon/35/fix/out", data={"card": "sv03.5-035", "back": "sv03.5-035", "stay": "ja:SV2a-035"})
    assert _art_of("sv03.5-035") == {"sv03.5-035"}
    assert _art_of("base1-5") == {"zh-tw:SV2a-035", "ja:SV2a-035", "base1-5"}
    client.post("/pokemon/35/fix/out", data={"card": "ja:SV2a-035", "back": "ja:SV2a-035", "stay": "base1-5"})
    assert _art_of("ja:SV2a-035") == {"ja:SV2a-035"} and _art_of("base1-5") == {"zh-tw:SV2a-035", "base1-5"}


def test_hand_added_cards_left_alone_keep_together(client, monkeypatch):
    """#71: when only cards added by hand are left, they stay one art, apart from the card taken out."""
    _clefairy(client, monkeypatch)
    client.post("/pokemon/35/fix/out", data={"card": "ja:SV2a-035", "back": "sv03.5-035"})
    client.post("/pokemon/35/fix/add", data={"card": ["base1-5", "sv03.5-035"], "to": "ja:SV2a-035"})
    assert _art_of("ja:SV2a-035") == {"ja:SV2a-035", "base1-5", "sv03.5-035"}
    client.post("/pokemon/35/fix/out", data={"card": "ja:SV2a-035", "back": "ja:SV2a-035"})
    assert _art_of("ja:SV2a-035") == {"ja:SV2a-035"} and _art_of("base1-5") == {"base1-5", "sv03.5-035"}


def test_hand_choices_outlast_recheck_and_updates(client, monkeypatch):
    """#71: cards added or taken out by hand keep their place after ↻ Re-check and Check for updates,
    even when the pictures say otherwise."""
    _clefairy(client, monkeypatch)
    client.post("/pokemon/35/fix/out", data={"card": "zh-tw:SV2a-035", "back": "sv03.5-035"})
    client.post("/pokemon/35/fix/add", data={"card": "base1-5", "to": "sv03.5-035"})
    # The pictures now say the opposite: the taken-out card matches, the added one doesn't.
    other = {"sv03.5-035": _vec(1, 0, 0), "zh-tw:SV2a-035": _vec(1, 0, 0), "ja:SV2a-035": _vec(1, 0.01, 0),
             "base1-5": _vec(0, 1, 0)}
    monkeypatch.setattr(chase, "fingerprints", lambda conn, dex, encoder, *a, **k: other)
    chase.run_recheck(main.conn, 35, None)
    assert _art_of("sv03.5-035") == {"sv03.5-035", "ja:SV2a-035", "base1-5"} and _art_of("zh-tw:SV2a-035") == {"zh-tw:SV2a-035"}
    chase.save_cards(main.conn, 35, _chase_rows())  # Check for updates stores the cards again
    chase.regroup(main.conn, 35, other)
    assert _art_of("sv03.5-035") == {"sv03.5-035", "ja:SV2a-035", "base1-5"} and _art_of("zh-tw:SV2a-035") == {"zh-tw:SV2a-035"}


def test_refresh_and_back_dont_reopen_the_edit_art_sheet():
    """#79: a fix is sent from the page and replaces it (no new step for Back), and open and message leave the
    address once read, so a refresh or Back shows the plain grid. The server's redirect is unchanged."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "app/static/js/pokemon-art-tray.js").read_text()
    assert "location.replace(r.url)" in js and "location.assign" not in js
    assert 'here.searchParams.delete("open")' in js and 'here.searchParams.delete("message")' in js
    assert "history.replaceState(" in js and "history.pushState(" not in js
    assert "form.submit()" in js  # if sending fails, the form is posted as before
    assert r"(fix\/|recheck$)" in js  # Not this art, Add a card, Undo and Re-check
