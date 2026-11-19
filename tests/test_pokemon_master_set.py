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


def test_one_grid_and_a_language_filter(client, monkeypatch):
    _clefairy(client, monkeypatch)
    page = client.get("/pokemon/35").text
    assert 'class="pgrid" id="arts"' in page and "era-rows" not in page and "View all" not in page
    assert "<b>Language</b>" in page and "<b>Sort</b>" not in page  # #96: a Language filter, no language switch
    for show in ("Owned", "Missing", "♥ Chased", "Full art", "Promo"):  # Filters keeps Show
        assert f">{show}</label>" in page
    assert client.get("/pokemon/35", params={"era": "sv", "lang": "xx"}).status_code == 200  # old links still open
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
    assert "Add a card" not in sheet and 'name="q"' not in sheet and "<h2>This art</h2>" in sheet  # #96: slim
    r = client.post("/pokemon/35/fix/out", data={"card": "ja:SV2a-035", "back": "sv03.5-035", "stay": "sv03.5-035",
                                                  "sort": "name", "lang": "ja"}, follow_redirects=False)
    assert r.headers["location"] == "/pokemon/35?edit=1&sort=name&lang=ja&open=sv03.5-035"  # the same art's sheet, same view
    assert 'data-open-art="' in client.get(r.headers["location"]).text
    sheet = client.get("/pokemon/35/art", params={"card": "sv03.5-035"}).text
    assert "2 cards" in sheet and 'value="ja:SV2a-035"' not in sheet


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


def test_the_art_sheet_has_no_search_and_add_a_card_is_gone(client, monkeypatch):
    """#96: the pencil's sheet lists the art's cards only; adding a card the Pokémon isn't named on is done
    from that card's page (Add to a Pokémon set…)."""
    _clefairy(client, monkeypatch)
    sheet = client.get("/pokemon/35/art", params={"card": "sv03.5-035"}).text
    assert "Closest pictures" not in sheet and "All cards" not in sheet and "data-add" not in sheet
    assert client.get("/pokemon/35/art/search", params={"card": "sv03.5-035", "q": "x"}).status_code == 404
    assert client.post("/pokemon/35/fix/add", data={"card": "base1-5", "to": "sv03.5-035"}).status_code in (404, 405)
    assert client.post("/pokemon/35/fix/undo", data={"card": "base1-5", "back": "sv03.5-035"}).status_code in (404, 405)
    assert not hasattr(chase_pages, "candidates") and not hasattr(chase_pages, "search_cards")
    assert "Add to a Pokémon set" in client.get("/cards/sv03.5-035").text


def _art_of(card: str) -> set[str]:
    return set(next(a for a in chase_pages.arts(main.conn, 35) if card in a["card_ids"])["card_ids"])


def _hand_add(cards, to: str) -> None:
    """A card put in an art by hand (as Merge arts does): it joins the art `to` is in."""
    for card in [cards] if isinstance(cards, str) else cards:
        chase.put_with(main.conn, 35, card, to, 1)
    chase.regroup(main.conn, 35, VECTORS)


def test_a_card_added_by_hand_stays_when_the_cover_is_taken_out(client, monkeypatch):
    """#71: Not this art moves only that card, even when it's the cover a hand-added card was joined to."""
    _clefairy(client, monkeypatch)
    _hand_add("base1-5", "sv03.5-035")  # joins the cover
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
    _hand_add(["base1-5", "sv03.5-035"], "ja:SV2a-035")
    assert _art_of("ja:SV2a-035") == {"ja:SV2a-035", "base1-5", "sv03.5-035"}
    client.post("/pokemon/35/fix/out", data={"card": "ja:SV2a-035", "back": "ja:SV2a-035"})
    assert _art_of("ja:SV2a-035") == {"ja:SV2a-035"} and _art_of("base1-5") == {"base1-5", "sv03.5-035"}


def test_hand_choices_outlast_recheck_and_updates(client, monkeypatch):
    """#71: cards added or taken out by hand keep their place after ↻ Re-check and Check for updates,
    even when the pictures say otherwise."""
    _clefairy(client, monkeypatch)
    client.post("/pokemon/35/fix/out", data={"card": "zh-tw:SV2a-035", "back": "sv03.5-035"})
    _hand_add("base1-5", "sv03.5-035")
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
    assert 'for (const k of ["open", "message", "undo", "merged"]) here.searchParams.delete(k);' in js
    assert "history.replaceState(" in js and "history.pushState(" not in js
    assert "form.submit()" in js  # if sending fails, the form is posted as before
    assert r"(fix\/|recheck$)" in js  # Not this art, Merge arts, Undo and Re-check


def test_the_chasing_section_fits_a_phone():
    """#68: the Chasing box's one column is never wider than the box, so nothing is cut off at 375px."""
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "app/static/css/card.css").read_text()
    rule = re.search(r"\.copy-body \{([^}]*)\}", css).group(1)
    assert "grid-template-columns:minmax(0, 1fr)" in rule


# #96: Edit arts on the grid: tick arts and merge them, Undo, and a Language filter.

def _two_arts(client, monkeypatch, **kw):
    """Clefairy with the 151 art (EN, JA, ZH) and Base Set (EN), and a third art: Japanese only, by another artist."""
    rows = _chase_rows()
    rows.append(dict(rows[2], card_id="ja:SV9-051", set_id="ja:SV9", set_name="Battle Partners", number="051",
                     illustrator="B", release_date="2025-01-24"))
    vectors = dict(VECTORS, **{"ja:SV9-051": _vec(0, 1, 0)})
    uid = _clefairy(client, monkeypatch, rows=rows, **kw)
    monkeypatch.setattr(chase, "fingerprints", lambda conn, dex, encoder, *a, **k: vectors)
    chase.regroup(main.conn, 35, vectors)
    return uid, vectors


def test_merge_arts_joins_every_card_of_the_ticked_arts(client, monkeypatch):
    _, vectors = _two_arts(client, monkeypatch)
    r = client.post("/pokemon/35/fix/merge", data={"card": ["base1-5", "ja:SV9-051"], "sort": "name", "lang": "ja"},
                    follow_redirects=False)
    location = r.headers["location"]
    assert location.startswith("/pokemon/35?edit=1&sort=name&lang=ja&undo=") and location.endswith("&merged=2")
    assert _art_of("base1-5") == {"base1-5", "ja:SV9-051"}  # across illustrators, no warning
    art = next(a for a in chase_pages.arts(main.conn, 35) if "base1-5" in a["card_ids"])
    assert art["cover"] == "base1-5"  # the usual rule: the English card
    assert _art_of("sv03.5-035") == {"sv03.5-035", "zh-tw:SV2a-035", "ja:SV2a-035"}  # untouched
    # Kept through ↻ Re-check and Check for updates, like any hand fix.
    chase.run_recheck(main.conn, 35, None)
    chase.save_cards(main.conn, 35, [dict(_chase_rows()[3])])
    chase.regroup(main.conn, 35, vectors)
    assert _art_of("base1-5") == {"base1-5", "ja:SV9-051"}
    # Three arts at once, one ticked by a card other than its cover: all one art, the English 151 card on top.
    client.post("/pokemon/35/fix/merge", data={"card": ["ja:SV2a-035", "base1-5"]})
    assert len(_art_of("base1-5")) == 5
    assert next(a for a in chase_pages.arts(main.conn, 35) if "base1-5" in a["card_ids"])["cover"] == "base1-5"


def test_merge_needs_two_arts(client, monkeypatch):
    _two_arts(client, monkeypatch)
    before = chase_pages.arts(main.conn, 35)
    for cards in ([], ["base1-5"], ["sv03.5-035", "ja:SV2a-035"], ["nope"]):
        r = client.post("/pokemon/35/fix/merge", data={"card": cards}, follow_redirects=False)
        assert r.headers["location"] == "/pokemon/35?edit=1" and chase_pages.arts(main.conn, 35) == before


def test_undo_puts_every_card_back(client, monkeypatch):
    _two_arts(client, monkeypatch)
    client.post("/pokemon/35/fix/out", data={"card": "zh-tw:SV2a-035", "back": "sv03.5-035"})  # a card taken out
    r = client.post("/pokemon/35/fix/merge", data={"card": ["sv03.5-035", "zh-tw:SV2a-035", "ja:SV9-051"]},
                    follow_redirects=False)
    assert len(_art_of("sv03.5-035")) == 4
    token = re.search(r"undo=([^&]+)", r.headers["location"]).group(1)
    r = client.post("/pokemon/35/fix/merge/undo", data={"token": token, "show": "missing"}, follow_redirects=False)
    assert r.headers["location"] == "/pokemon/35?edit=1&show=missing"
    assert _art_of("sv03.5-035") == {"sv03.5-035", "ja:SV2a-035"} and _art_of("zh-tw:SV2a-035") == {"zh-tw:SV2a-035"}
    assert _art_of("ja:SV9-051") == {"ja:SV9-051"}
    fix = main.conn.execute("SELECT join_card_id, left_card_id FROM art_fixes WHERE card_id = 'zh-tw:SV2a-035'").fetchone()
    assert fix["join_card_id"] is None and fix["left_card_id"]  # its own fix is back, as it was
    r = client.post("/pokemon/35/fix/merge/undo", data={"token": token}, follow_redirects=False)  # only once
    assert "can%27t+be+undone" in r.headers["location"] or "can't" in r.headers["location"]
    assert not chase.undo_merge(main.conn, 34, "made-up")


def test_not_this_art_on_a_merged_art_moves_only_that_card(client, monkeypatch):
    _two_arts(client, monkeypatch)
    client.post("/pokemon/35/fix/merge", data={"card": ["base1-5", "ja:SV9-051"]})
    client.post("/pokemon/35/fix/out", data={"card": "base1-5", "back": "base1-5", "stay": "ja:SV9-051"})
    assert _art_of("base1-5") == {"base1-5"} and _art_of("ja:SV9-051") == {"ja:SV9-051"}
    client.post("/pokemon/35/fix/merge", data={"card": ["base1-5", "ja:SV9-051", "sv03.5-035"]})
    client.post("/pokemon/35/fix/out", data={"card": "base1-5", "back": "base1-5"})  # the cover the others joined
    assert _art_of("base1-5") == {"base1-5"} and len(_art_of("ja:SV9-051")) == 4


def test_language_filter_only_narrows_which_arts_show(client, monkeypatch):
    uid, _ = _two_arts(client, monkeypatch, own=("sv03.5-035", "normal"))  # the 151 art, owned in English only
    every = {t["cover"]["card_id"]: t for t in chase_pages.art_tiles(main.conn, uid, 35)}
    ja = {t["cover"]["card_id"]: t for t in chase_pages.art_tiles(main.conn, uid, 35, lang="ja")}
    assert set(ja) == {"sv03.5-035", "ja:SV9-051"}  # printed in Japanese; Base Set isn't
    assert ja["sv03.5-035"] == every["sv03.5-035"]  # owned, printings, price and tags count every language
    assert [t["cover"]["card_id"] for t in chase_pages.art_tiles(main.conn, uid, 35, show="missing", lang="ja")] == ["ja:SV9-051"]
    assert [t["cover"]["card_id"] for t in chase_pages.art_tiles(main.conn, uid, 35, lang="zh")] == ["sv03.5-035"]
    page = client.get("/pokemon/35", params={"lang": "ja", "show": "missing"}).text
    assert _tile_order(page) == ["Battle Partners"] and "1 of 3 arts" in page  # the summary counts every art
    assert 'aria-label="Filter, 2 on"' in page
    assert '>Japanese <svg' in page and 'href="/pokemon/35?show=missing&amp;sort=value"' in page  # its chip clears it
    assert 'href="/pokemon/35?sort=value&amp;lang=ja"' in page  # the Missing chip keeps the language
    assert re.search(r'href="/pokemon/35\?show=missing&amp;sort=number&amp;lang=ja"', page)  # sorts keep it
    assert 'value="ja" checked' in page
    assert _tile_order(client.get("/pokemon/35").text) == ["151", "Base Set", "Battle Partners"] or \
        len(_tile_order(client.get("/pokemon/35").text)) == 3  # not kept: the next visit shows every language


def test_edit_mode_on_the_grid():
    """Ticks on the grid and a Merge bar; sorts and filters keep edit mode (edit=1, ticks in session storage) and
    replace the page; a reload starts over. Script checks, as the other page-script tests do."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    js = (root / "app/static/js/pokemon-art-tray.js").read_text()
    assert "sessionStorage" in js and 'searchParams.set("edit", "1")' in js
    assert '"navigation")[0]?.type === "reload"' in js and "write(TICKS, null)" in js
    assert "location.replace(a.href)" in js and "location.replace(filterForm.action" in js  # no step for Back
    assert 'e.target.closest("[data-pen]")' in js and "tick(a.parentNode.dataset.card)" in js
    assert "ticks.size < 2" in js and "hidden by filters" in js and "setTimeout(() => { toast.hidden = true; }, 6000)" in js
    html = (root / "app/templates/pokemon_set.html").read_text()
    assert 'class="merge-bar"' in html and 'action="/pokemon/{{ dex }}/fix/merge"' in html and "Undo merge" in html
    assert 'data-pen role="button"' in html and 'class="sel"' in html


def test_merge_and_language_on_an_artist_page(client, monkeypatch):
    uid = _setup_with_set(client)
    key = chase.artist_key(main.conn, "Saya Tsuruta")
    chase.follow(main.conn, uid, key, "Saya Tsuruta", uid)
    chase.save_cards(main.conn, key, _chase_rows())
    monkeypatch.setattr(chase, "fingerprints", lambda conn, dex, encoder, *a, **k: VECTORS)
    chase.regroup(main.conn, key, VECTORS)
    r = client.post(f"/pokemon/{key}/fix/merge", data={"card": ["sv03.5-035", "base1-5"], "lang": "en"},
                    follow_redirects=False)
    assert r.headers["location"].startswith("/artists/saya%20tsuruta?edit=1&lang=en&undo=")
    assert len(chase_pages.arts(main.conn, key)) == 1
    page = client.get("/artists/saya%20tsuruta", params={"lang": "ja"}).text
    assert "<b>Language</b>" in page and len(_tile_order(page)) == 1
