"""Round 7: layout, card page, tags, evolution line, Pokémon page, Chases, scan languages, security and pictures."""

import re
import time

import pytest

from app import main
from app import web
from test_app import FakeSearchAPI, _entry, _setup_with_set, client  # noqa: F401  (the client fixture)


def _wait_for_jobs():
    for job in web.background.current():
        while job.running:
            time.sleep(0.01)


def test_grid_pages_use_the_whole_window_and_reading_pages_dont(client):
    _setup_with_set(client)
    for path in ("/collection", "/chases", "/sets/t1"):
        assert '<main class="wide">' in client.get(path).text, path
    for path in ("/account", "/cards/t1-1", "/collection/binders"):
        assert '<main class="wide">' not in client.get(path).text, path


def test_card_page_puts_the_picture_on_top_and_tags_in_their_own_row(client):
    _setup_with_set(client)
    page = client.get("/cards/t1-1").text
    top = page[page.index('class="panel cv-top"'):page.index("</section>", page.index('class="panel cv-top"'))]
    assert top.index('class="cv-menu"') < top.index('class="cv-pic"') < top.index('class="cv-info"')
    assert "cv-tags" not in top  # tags moved out of the top panel
    assert page.index("PriceCharting &#8599;") < page.index('class="cv-tagrow"')  # under the links line


WURMPLE = ("265,wurmple,,135,330,8,14\n266,silcoon,265,135,331,9,2\n267,beautifly,266,135,332,10,13\n"
           "268,cascoon,265,135,333,7,2\n269,dustox,268,135,334,4,13\n")


def test_evolution_line_is_whole_on_every_page_with_two_stage_branches(tmp_path):
    from app import evolution
    from test_app import SPECIES_CSV, _fake_pokeapi
    get, _ = _fake_pokeapi({"pokemon_species": SPECIES_CSV + WURMPLE})
    lines = evolution.Lines(tmp_path, get)
    assert lines.line(265) == {"trunk": [265], "branches": [[266, 267], [268, 269]], "here": 265}
    assert lines.line(269)["branches"] == [[266, 267], [268, 269]]  # Dustox sees Wurmple's whole line
    assert lines.line(475) == {"trunk": [280, 281], "branches": [[282], [475]], "here": 475}  # Gallade


def test_umbreon_page_shows_eevees_whole_line(client, monkeypatch, tmp_path):
    from app import evolution
    from test_app import _fake_pokeapi
    get, _ = _fake_pokeapi()
    monkeypatch.setattr(web, "lines", evolution.Lines(tmp_path, get))
    web.lines.line(1)  # load the list
    _setup_with_set(client)
    page = client.get("/pokemon/197").text
    evo = page[page.index('class="evo"'):page.index("</section>", page.index('class="evo"'))]
    assert all(f"/pokemon/{d}" in evo for d in (133, 134, 135, 197))
    assert 'class="here" aria-current="page"' in evo and "evolves into" not in evo
    assert 'class="evo-grid" style="--rows:2"' in evo and "evo-brace" in evo


def test_set_where_names_the_language_set_and_number():
    from app import display
    assert display.set_where("sv08", "Surging Sparks") == "Surging Sparks"
    assert display.set_where("ja:SV8a", "テラスタルフェスex", "054") == "JA · テラスタルフェスex (SV8A) · 054"
    assert display.set_where("zh-tw:SV8a", "太晶慶典ex", "054").startswith("TW · 太晶慶典ex (SV8A)")


def test_removing_a_word_changes_results_and_reset_brings_it_back(client, monkeypatch, tmp_path):
    from app import pokemon_words, tags
    from test_round6 import _describe_data
    d = _describe_data(tmp_path, own=None)
    monkeypatch.setattr(web, "describer", d)
    d.own = lambda: pokemon_words.own_and_hidden(main.conn)
    main.conn.executescript("DELETE FROM pokemon_tags; DELETE FROM pokemon_tag_hidden; DELETE FROM card_tag_hidden;")
    d.build()
    _setup_with_set(client)
    assert {r["name"] for r in d.search("mouse")} == {"Tandemaus", "Maushold"}
    # ✕ from Find swaps the chip row in place and tells the page to reload Describe's results.
    r = client.post("/pokemon/925/words/remove", data={"word": "mouse", "back": "/search?q=maushold"}, headers={"HX-Request": "true"})
    assert r.headers["HX-Trigger"] == "words-changed" and 'id="ptags"' in r.text and "data-tag-type" in r.text
    assert "mouse" not in [t for t, _ in pokemon_words.for_pokemon(main.conn, d, 925)["tags"]]
    assert [r["name"] for r in d.search("mouse")] == ["Tandemaus"]
    # A removed colour stops a colour chip finding it too.
    client.post("/pokemon/249/words/remove", data={"word": "white"})
    assert "Lugia" not in [r["name"] for r in d.search(colour="white")]
    # Data sources › Reset tags brings both back; your own words stay.
    client.post("/pokemon/151/words/add", data={"word": "kitten"})
    card_id = main.conn.execute("SELECT id FROM cards WHERE set_id = 't1' ORDER BY sort_number").fetchone()[0]
    tags.add(main.conn, card_id, "Pull", 1)
    tags.remove(main.conn, card_id, "Reverse holo")
    client.post("/account/data/reset-tags")
    _wait_for_jobs()
    assert {r["name"] for r in d.search("mouse")} == {"Tandemaus", "Maushold"}
    assert "Lugia" in [r["name"] for r in d.search(colour="white")]
    assert [r["name"] for r in d.search("kitten")] == ["Mew"]
    entry = tags.for_card(main.conn, card_id)
    assert entry["hidden"] == [] and ("Pull", "mine") in entry["tags"]
    assert "Reset tags" in client.get("/account/data").text


def test_type_in_tag_chip_on_the_card_page(client):
    _setup_with_set(client)
    page = client.get("/cards/t1-1").text
    assert 'class="tag-type"' in page and 'placeholder="+ Tag"' in page and "Hidden tags" not in page and "tag-add" not in page
    r = client.post("/cards/t1-1/tags/add", data={"tag": "Favourite"}, headers={"HX-Request": "true"})
    assert r.headers["HX-Trigger"] == "tags-changed" and r.text.lstrip().startswith("{#") is False and 'id="tags"' in r.text
    assert "Favourite" in r.text


def test_chinese_batch_searches_mainland_then_taiwan_pictures(monkeypatch):
    from app import pictures

    class Index:
        def __init__(self, rows):
            self.rows = rows

        def search(self, vector, k):
            return self.rows

    rows = {"en": [({"card": "sv08-025", "set": "sv08", "number": "25", "name": "Pikachu"}, 0.99)],
            "ja": [({"card": "ja:SV8a-054", "set": "ja:SV8a", "number": "054", "name": "ピカチュウ"}, 0.97)],
            "zh-cn": [({"card": "zh-cn:CSV7C-141", "set": "zh-cn:CSV7C", "number": "141", "name": "皮卡丘"}, 0.80)]}
    monkeypatch.setattr(pictures, "languages", lambda: ["en", "ja", "zh-cn"])
    monkeypatch.setattr(pictures, "index_for", lambda lang: Index(rows[lang]))
    monkeypatch.setattr(pictures, "photo_vector", lambda encoder, image, box=None: None)
    found = pictures.search(main.conn, None, None, batch_lang="zh-cn")
    assert [(m["id"], m["lang"]) for m in found] == [("CSV7C-141", "zh-cn"), ("SV8a-054", "zh-tw")]  # no English
    assert [m["lang"] for m in pictures.search(main.conn, None, None, batch_lang="en")] == ["en"]
    assert [m["lang"] for m in pictures.search(main.conn, None, None, batch_lang="ja")] == ["ja"]


def test_chinese_batch_tabs_list_mainland_before_taiwan_and_translate_names():
    from app import scans

    class Lookup(scans.CardLookup):
        def __init__(self):
            super().__init__(main.conn, None, lambda text, lang: {"zh-cn": "皮卡丘", "zh-tw": "皮卡丘"}.get(lang) if text == "Pikachu" else None)

        def sets(self, lang="en"):
            return [{"id": "CSV7C" if lang == "zh-cn" else "SV8a", "name": "x"}]

        def codes(self, lang="en"):
            return {}

        def search_name(self, name, lang="en"):
            return [{"id": f"{lang}-1", "number": "141", "name": "皮卡丘", "set_id": "CSV7C" if lang == "zh-cn" else "SV8a", "image": None}]

        def search_number(self, number, lang="en"):
            return self.search_name("", lang)

    tabs = scans.batch_tabs(Lookup(), "zh-cn", name="Pikachu", number="141")
    first = tabs[0]["cards"]
    assert [(c["id"], c["lang"]) for c in first] == [("zh-cn-1", "zh-cn"), ("zh-tw-1", "zh-tw")]


# --- step 9: security ---------------------------------------------------------

def test_wrong_passwords_from_another_address_dont_lock_you_out(client):
    from fastapi.testclient import TestClient
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    stranger = TestClient(main.app, client=("203.0.113.9", 50000))
    for _ in range(5):
        assert stranger.post("/login", data={"username": "misty", "password": "wrong-one"}).status_code == 401
    assert stranger.post("/login", data={"username": "misty", "password": "pikachu123"}).status_code == 429
    me = TestClient(main.app, client=("198.51.100.4", 50001))
    r = me.post("/login", data={"username": "misty", "password": "pikachu123"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_unknown_usernames_take_as_long_as_real_ones(monkeypatch):
    from app import auth
    checked = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password", lambda p, h: checked.append(h) or real(p, h))
    assert auth.authenticate(main.conn, "nobody-here", "whatever1") is None
    assert checked == [auth._DUMMY_HASH]


def test_sign_out_everywhere_signs_out_other_devices(client):
    from fastapi.testclient import TestClient
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    phone = TestClient(main.app)
    phone.post("/login", data={"username": "misty", "password": "pikachu123"})
    assert phone.get("/account", follow_redirects=False).status_code == 200
    assert "Sign out everywhere" in client.get("/account").text
    r = client.post("/logout/everywhere", follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert phone.get("/account", follow_redirects=False).status_code == 303
    assert client.get("/account", follow_redirects=False).status_code == 303
    assert main.SESSION_DAYS == 14


def test_no_api_pages_and_safe_headers(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path
    r = client.get("/login")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "same-origin"


def test_the_app_wont_start_with_a_weak_key(monkeypatch):
    # Round 13: an empty key is fine now (one is made in data/secret_key); see test_round13.
    for bad in ("replace-me-with-a-long-random-string", "short-key"):
        monkeypatch.setenv("DEXSTASH_SECRET_KEY", bad)
        with pytest.raises(RuntimeError, match="32"):
            web._secret_key()
    monkeypatch.setenv("DEXSTASH_SECRET_KEY", "k" * 32)
    assert web._secret_key() == "k" * 32


def test_tcgdex_addresses_escape_ids():
    import httpx
    from app.tcgdex import TCGdex
    seen = []

    def handler(request):
        seen.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"releaseDate": "2024-01-01"})

    api = TCGdex("en", client=httpx.Client(base_url="https://api.example/v2/en", transport=httpx.MockTransport(handler)))
    api.get_set("sv1?x=1/../cards")
    api.get_card("a/b#c")
    api.release_date("p 1")
    assert seen == ["/v2/en/sets/sv1%3Fx%3D1%2F..%2Fcards", "/v2/en/cards/a%2Fb%23c", "/v2/en/sets/p%201"]


def test_huge_pictures_are_capped():
    from PIL import Image
    assert Image.MAX_IMAGE_PIXELS == 40_000_000


# --- step 10: scan additions ---------------------------------------------------

def test_deleted_scan_can_be_put_back_until_add_all(client, monkeypatch):
    from app import scans
    from test_app import JPEG, _scan_setup
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    batch = scans.get_batch(main.conn, owner, batch_id)
    first, second, third = (scans.add_photo(main.conn, batch, owner, JPEG) for _ in range(3))
    worker.start(wait=True)
    details = client.get(f"/scans/item/{first}/details").text
    assert "Remove from batch" in details and "128465" not in details
    for scan_id in (first, second):
        client.post(f"/scans/item/{scan_id}/skip")
    page = client.get(f"/scans/{batch_id}").text
    assert "Deleted scans" in page and page.count("Put back") == 2 and "Delete Batch" in page
    # Put back: the photo returns to To confirm with what was read.
    client.post(f"/scans/item/{first}/put-back", headers={"HX-Request": "true"})
    back = scans.get_scan(main.conn, owner, first)
    assert back["status"] == "ready" and back["guesses"] and scans.photo_path(back)
    # Add all finishes the batch: the deleted scan's photo goes, the others are added.
    photo = scans.photo_path(scans.get_scan(main.conn, owner, second))
    assert photo.exists()
    r = client.post(f"/scans/{batch_id}/add", data={"came": "none"}, follow_redirects=False)
    assert r.headers["location"] == f"/scans/{batch_id}?added=1"
    assert not photo.exists()
    assert {r[0] for r in main.conn.execute("SELECT status FROM scans WHERE id IN (?, ?)", (first, third))} == {"added"}


def test_a_scan_deleted_while_being_read_stays_deleted():
    from app import scans
    main.conn.execute("INSERT INTO scan_batches (collection_id, created_by, language) VALUES (1, 1, 'en')")
    batch_id = main.conn.execute("SELECT MAX(id) FROM scan_batches").fetchone()[0]
    main.conn.execute("INSERT INTO scans (collection_id, batch_id, status, photo) VALUES (1, ?, 'reading', 'x.jpg')", (batch_id,))
    scan_id = main.conn.execute("SELECT MAX(id) FROM scans").fetchone()[0]
    main.conn.commit()
    scans.skip(main.conn, {"id": scan_id, "status": "reading"})
    main.conn.execute(f"UPDATE scans SET {scans._KEEP_DELETED} WHERE id = ?", ("ready", "ready", scan_id))
    assert main.conn.execute("SELECT status, prev_status FROM scans WHERE id = ?", (scan_id,)).fetchone()[:] == ("skipped", "ready")


def test_enhanced_search_has_its_own_row(client, monkeypatch):
    import json
    from app import scans
    from test_app import JPEG, _scan_setup
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    batch = scans.get_batch(main.conn, owner, batch_id)
    scan_id = scans.add_photo(main.conn, batch, owner, JPEG)
    worker.start(wait=True)
    page = client.get(f"/scans/item/{scan_id}/details").text
    assert "Enhanced Search</div>" not in page
    match = {"id": "t1-1", "name": "Bulbasaur", "number": "1", "set_id": "t1", "set_name": "Test", "image": "", "score": 80, "lang": "en"}
    main.conn.execute("UPDATE scans SET status = 'picture_ready', picture_json = ? WHERE id = ?", (json.dumps([match]), scan_id))
    main.conn.commit()
    page = client.get(f"/scans/item/{scan_id}/details").text
    assert page.index('sd-label">Other guesses') < page.index('sd-label">Enhanced Search')


def test_cards_to_review_lists_only_cards_with_no_price(client):
    from app import extras, home
    user_id = _setup_with_set(client)
    _entry(client, "t1", "1")
    _entry(client, "t1", "2")
    assert {r["id"]: r["reason"] for r in home.to_review(main.conn, user_id)} == {"t1-1": "No price", "t1-2": "No price"}
    # A PriceCharting price (like most mainland Chinese cards) is a price.
    main.conn.execute("INSERT INTO card_extras (card_id, pricecharting_url) VALUES ('t1-1', 'https://www.pricecharting.com/game/x/y')")
    main.conn.execute("INSERT INTO typed_prices (card_id, variant, kind, price, currency, typed_at) "
                      "VALUES ('t1-1', 'normal', 'pricecharting', 3.5, 'USD', '2026-10-26T00:00:00Z')")
    extras.apply_price(main.conn, "t1-1")
    main.conn.commit()
    assert [r["id"] for r in home.to_review(main.conn, user_id)] == ["t1-2"]
    page = client.get("/review").text
    assert "1 card with no price" in page and "TCGplayer link" not in page.split("<main")[1].split("Add a TCGplayer")[0]


def test_english_batch_name_box_changes_the_name_tab(client, monkeypatch):
    from app import scans
    from test_app import JPEG, _scan_setup
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    batch = scans.get_batch(main.conn, owner, batch_id)
    scan_id = scans.add_photo(main.conn, batch, owner, JPEG)
    worker.start(wait=True)
    for name, number in (("Bulbasaur", "1"), ("Ivysaur", "2")):
        page = client.get(f"/scans/item/{scan_id}/found", params={"name": name, "number": "", "code": "", "edited": "name"}).text
        assert re.findall(r'aria-selected="true"[^>]*>([^<]*)', page)[0].strip() == "Name + Number"
        assert re.findall(r'aria-label="Use ([^"]+)"', page)[0] == f"{name} #{number}"


# --- step 8 and 10.1: card pictures ----------------------------------------------

def _png(color=(200, 40, 40), size=(630, 880)):
    import io
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


def _add_card(card_id, set_id, lang, number="1", image=None, **cols):
    main.conn.execute("INSERT OR IGNORE INTO sets (set_id, language, name, printed_total, total) VALUES (?, ?, ?, 10, 10)",
                      (set_id, lang, set_id))
    main.conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name, image_url) VALUES (?, ?, ?, ?, 1, 'X', ?)",
                      (card_id, set_id, number, int(number) if number.isdigit() else 0, image))
    for k, v in cols.items():
        main.conn.execute(f"UPDATE cards SET {k} = ? WHERE id = ?", (v, card_id))
    main.conn.commit()


def test_one_picture_list_saved_copy_first_then_tcgdex_tcgplayer_link_twin_photo(client):
    from app import card_pictures
    _setup_with_set(client)
    main.conn.execute("UPDATE cards SET image_url = NULL, tcgplayer_id = 77, twin_picture = 'T', twin_label = 'Japanese version' WHERE id = 't1-1'")
    main.conn.execute("INSERT INTO card_extras (card_id, photo) VALUES ('t1-1', '/card-photos/me.jpg')")
    main.conn.commit()
    pics = card_pictures.for_id(main.conn, "t1-1")
    assert [label for _, label in pics] == ["TCGplayer picture", "Japanese version", "Your photo"]  # (15c: no link pictures)
    main.conn.execute("UPDATE cards SET saved_picture = 'abc.jpg', saved_from = 'TCGplayer picture' WHERE id = 't1-1'")
    main.conn.commit()
    assert card_pictures.for_id(main.conn, "t1-1")[0] == ("/card-pictures/abc.jpg", "TCGplayer picture")
    assert card_pictures.for_id(main.conn, "nope", "https://x/y.webp") == [("https://x/y.webp", "")]
    # Find, Chases and Home use it too.
    _entry(client, "t1", "1")
    assert "/card-pictures/abc.jpg" in client.get("/").text
    assert "/card-pictures/abc.jpg" in client.get("/collection").text


def test_tcgplayer_product_is_saved_from_details_already_fetched(client, monkeypatch):
    from test_app import FakePricedAPI
    calls = []

    class Counting(FakePricedAPI):
        def get_card(self, card_id):
            calls.append(card_id)
            return super().get_card(card_id)

    from app import catalog
    catalog.cache_set(main.conn, Counting(), "t1")
    assert len(calls) == 3  # one per card, as before
    assert main.conn.execute("SELECT tcgplayer_id FROM cards WHERE id = 't1-1'").fetchone()[0] == 1  # kept for a card nobody has


def test_picture_check_keeps_the_first_picture_that_works_and_a_new_one_replaces_a_borrowed_one(client, tmp_path):
    from app import card_pictures
    _setup_with_set(client)
    _entry(client, "t1", "1")
    main.conn.execute("UPDATE cards SET image_url = 'https://assets.tcgdex.net/en/t1/1/low.webp', tcgplayer_id = 9 WHERE id = 't1-1'")
    main.conn.commit()
    asked = []

    def fetch(url):
        asked.append(url)
        if "tcgdex" in url:
            raise OSError("404")
        return _png()

    assert card_pictures.due(main.conn) == ["t1-1"]
    assert card_pictures.check(main.conn, "t1-1", fetch, tmp_path) == "TCGplayer picture"
    assert asked[0].endswith("/high.webp")  # the bigger TCGdex picture, made small here
    card = main.conn.execute("SELECT * FROM cards WHERE id = 't1-1'").fetchone()
    from PIL import Image
    with Image.open(tmp_path / card["saved_picture"]) as im:
        assert max(im.size) == 400
    assert card_pictures.due(main.conn) == []  # checked
    # A week later the borrowed picture is looked at again, and TCGdex's now works.
    old = card["saved_picture"]
    main.conn.execute("UPDATE cards SET picture_checked_at = '2000-01-01T00:00:00+00:00' WHERE id = 't1-1'")
    main.conn.commit()
    assert card_pictures.due(main.conn, recheck=True) == ["t1-1"]
    assert card_pictures.check(main.conn, "t1-1", lambda url: _png((0, 0, 200)), tmp_path) == ""
    assert not (tmp_path / old).exists()
    # The saved copy is served from the NAS.
    main.conn.execute("UPDATE cards SET saved_picture = NULL WHERE id = 't1-1'")
    assert client.get(f"/card-pictures/{old}").status_code == 404
    assert client.get("/card-pictures/..%2Fdb.jpg").status_code == 404


def test_same_art_twins_link_japanese_taiwan_and_chinese_but_never_guess_english(client):
    from app import card_pictures
    _setup_with_set(client)
    main.conn.execute("UPDATE cards SET image_url = 'EN' WHERE id = 't1-1'")
    _add_card("ja:SV8a-054", "ja:SV8a", "ja", "054")                    # no picture
    _add_card("zh-tw:SV8a-054", "zh-tw:SV8a", "zh-tw", "054", "TW")     # same TCGdex set and number
    _add_card("ja:t1-1", "ja:t1", "ja", "1")                            # same id as an English card: not a proven link
    n = card_pictures.refresh_twins(main.conn)
    twin = lambda cid: tuple(main.conn.execute("SELECT twin_picture, twin_label FROM cards WHERE id = ?", (cid,)).fetchone())
    assert twin("ja:SV8a-054") == ("TW", "Taiwan version")
    assert twin("ja:t1-1") == (None, None)
    assert twin("zh-tw:SV8a-054") == (None, None)  # has its own
    assert n == 1


def test_mainland_chinese_card_no_longer_twins_its_english_version(client, monkeypatch):
    from app import card_pictures, scanner
    _setup_with_set(client)
    main.conn.execute("UPDATE cards SET image_url = 'EN-2' WHERE id = 't1-2'")
    monkeypatch.setitem(scanner.SET_CODES, "TST", ["t1"])
    _add_card("zh-cn:CSV1C-010", "zh-cn:CSV1C", "zh-cn", "010", source="chs", twin_code="TST", twin_number="2")
    card_pictures.refresh_twins(main.conn)
    # Round 11 (P): the English version's picture isn't borrowed any more.
    assert main.conn.execute("SELECT twin_picture, twin_label FROM cards WHERE id = 'zh-cn:CSV1C-010'").fetchone()[:] == (None, None)


def test_scan_crop_is_kept_only_when_nothing_else_exists(client, tmp_path):
    from PIL import Image
    from app import card_pictures
    _setup_with_set(client)
    crop = Image.new("RGB", (630, 880), (10, 120, 10))
    assert card_pictures.needs_scan(main.conn, "t1-1")
    assert card_pictures.save_scan(main.conn, "t1-1", crop, tmp_path)
    assert main.conn.execute("SELECT saved_from FROM cards WHERE id = 't1-1'").fetchone()[0] == "Your scan"
    main.conn.execute("UPDATE cards SET image_url = 'https://x/1.png' WHERE id = 't1-2'")
    assert not card_pictures.save_scan(main.conn, "t1-2", crop, tmp_path)
    # Nothing works: the scan stays. A real picture later replaces it.
    assert card_pictures.check(main.conn, "t1-1", lambda url: b"", tmp_path) is None
    assert main.conn.execute("SELECT saved_from FROM cards WHERE id = 't1-1'").fetchone()[0] == "Your scan"
    main.conn.execute("UPDATE cards SET image_url = 'https://x/real.png' WHERE id = 't1-1'")
    assert card_pictures.check(main.conn, "t1-1", lambda url: _png(), tmp_path) == ""
    assert len(list(tmp_path.glob("*.jpg"))) == 1


def test_cards_with_no_picture_are_listed(client):
    from app import card_pictures
    user_id = _setup_with_set(client)
    _entry(client, "t1", "1")
    _entry(client, "t1", "2")
    main.conn.execute("UPDATE cards SET image_url = 'https://x/2.png' WHERE id = 't1-2'")
    main.conn.commit()
    assert [r["id"] for r in card_pictures.missing(main.conn, True, user_id)] == ["t1-1"]
    assert len(card_pictures.missing(main.conn, False)) == 2  # t1-1 and Venusaur
    page = client.get("/account/data").text
    assert "<strong>1</strong> of your cards has no picture" in page
    assert "1 of your cards has no picture" in client.get("/account/data/no-pictures").text
    assert "2 stored cards have no picture" in client.get("/account/data/no-pictures?all=1").text


def test_checker_runs_queued_cards_on_its_own_thread(client, tmp_path):
    from app import card_pictures
    _setup_with_set(client)
    _entry(client, "t1", "1")
    main.conn.execute("UPDATE cards SET image_url = 'https://x/1.png' WHERE id = 't1-1'")
    main.conn.commit()
    checker = card_pictures.Checker(fetch=lambda url: _png(), folder=tmp_path)
    checker.poke(wait=True)
    assert main.conn.execute("SELECT saved_from FROM cards WHERE id = 't1-1'").fetchone()[0] == ""
    assert not checker.running
