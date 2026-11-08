"""Round 10: TCG Pocket hidden, unused data cleaned up, Light/Dark per person, plain back, card view
buttons, sort first, one link check, scan details, batch names, sets grid, artists."""
from conftest import template_with_assets, with_assets  # noqa: E402

import os

import pytest
import time

from app import card_pictures, catalog, db, main, tcgdex
from app import web
from app.routes import pokemon as pokemon_pages
from test_app import _setup_with_set, client  # noqa: F401  (the client fixture)


# --- step 9: cleanup ------------------------------------------------------------------------

def test_pocket_sets_are_known():
    assert tcgdex.is_pocket("A1") and tcgdex.is_pocket("ja:A1a") and tcgdex.is_pocket("P-A")
    assert not tcgdex.is_pocket("sv03") and not tcgdex.is_pocket(None)
    assert tcgdex.card_set_id("P-A-007") == "P-A" and tcgdex.card_set_id("sv03-223") == "sv03"
    cards = [{"id": "A1-001"}, {"id": "sv03-223"}, {"id": "P-A-007"}]
    assert tcgdex.without_pocket(cards) == [{"id": "sv03-223"}]


def test_unowned_pocket_sets_are_deleted_owned_kept(tmp_path):
    conn = db.connect(tmp_path / "p.db")
    db.init(conn)
    conn.execute("INSERT INTO users (username, password_hash, is_admin) VALUES ('m', 'x', 1)")
    for s in ("A1", "A2", "sv03"):
        conn.execute("INSERT INTO sets (set_id, name, printed_total, total) VALUES (?, ?, 1, 1)", (s, s))
        conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name) VALUES (?, ?, '1', 1, 1, 'A')",
                     (f"{s}-001", s))
    conn.execute("INSERT INTO inventory (user_id, card_id, quantity) VALUES (1, 'A2-001', 1)")
    conn.commit()
    assert catalog.drop_pocket(conn) == 1
    left = {r[0] for r in conn.execute("SELECT set_id FROM sets")}
    assert left == {"A2", "sv03"}
    assert conn.execute("SELECT COUNT(*) FROM cards WHERE set_id = 'A1'").fetchone()[0] == 0


def test_unused_columns_are_gone(client):
    cols = lambda t: {r[1] for r in main.conn.execute(f"PRAGMA table_info({t})")}  # noqa: E731
    assert "source_updated" not in cols("prices")
    assert "max_price" not in cols("wants")


def test_loose_picture_files_are_deleted(client, tmp_path):
    keep, loose, fresh = tmp_path / "keep.webp", tmp_path / "loose.webp", tmp_path / "fresh.webp"
    for p in (keep, loose, fresh):
        p.write_bytes(b"x")
    old = time.time() - 7200
    os.utime(keep, (old, old))
    os.utime(loose, (old, old))
    _setup_with_set(client)
    card = main.conn.execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
    main.conn.execute("UPDATE cards SET saved_picture = 'keep.webp' WHERE id = ?", (card,))
    main.conn.commit()
    assert card_pictures.drop_orphans(main.conn, tmp_path) == 1
    assert keep.exists() and fresh.exists() and not loose.exists()


# --- step 7: Light / Dark -------------------------------------------------------------------

def test_dark_by_default_and_light_is_per_person(client):
    _setup_with_set(client)
    page = client.get("/account").text
    assert 'data-mode="dark"' in page and "prefers-color-scheme" not in page
    assert page.count('name="theme-color"') == 1 and "#1c1c1f" in page
    r = client.post("/account/mode", data={"mode": "light"}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/").text
    assert 'data-mode="light"' in page and 'name="theme-color" content="#ffffff"' in page
    assert client.post("/account/mode", data={"mode": "pink"}).status_code == 400


# --- step 4: back = previous page ----------------------------------------------------------

def test_back_is_one_page_back_or_home(client):
    _setup_with_set(client)
    for url in ("/cards/t1-1", "/cards/t1-1?from=find", "/scans", "/account/data"):
        page = with_assets(client.get(url).text)
        if 'class="back-fab"' not in page:
            continue
        assert 'aria-label="Back"' in page or 'aria-label="Close"' in page
        assert "history.back()" in page and 'location.href = "/"' in page
        assert "cameFromFind" not in page and "data-smart" not in page
    # a pop-up save replaces the page and ?open= is dropped, so one back is enough
    card = with_assets(client.get("/cards/t1-1?open=add").text)
    assert "location.replace(resp.url)" in card and "url.searchParams.delete('open')" in card


# --- step 3: card view ----------------------------------------------------------------------

def test_card_view_has_menu_and_floating_add_and_chase(client, monkeypatch):
    from test_app import FakeSearchAPI
    _setup_with_set(client)
    card = client.get("/cards/t1-1").text
    head = card.split("</header>", 1)[0]
    assert "cv-pcacts" not in head and "Edit</button>" not in head  # round 25b: Add and Chase under the price everywhere
    assert '<summary aria-label="Modify this card"' in card and 'class="cv-bar"' not in card
    assert 'class="cv-act add pop"' in card and 'class="cv-act chase"' in card and 'formaction="/wants/add"' in card
    # Find tiles: a tap opens the card; no ♡ or + on the tile
    monkeypatch.setattr(web, "api", FakeSearchAPI())
    page = client.get("/search?q=bulbasaur").text
    assert "tile-act" not in page and "/find/want" not in page and "open=add" not in page
    assert client.post("/find/want", data={"ref": "/cards/t1-1"}).status_code in (404, 405)


# --- step 10: Sort first in every filter panel ----------------------------------------------

def test_sort_is_the_first_filter_group(client):
    _setup_with_set(client)
    for url in ("/collection", "/collection/sets", "/search?q=bulba"):
        page = client.get(url).text
        panel = page[page.index("<h2>Filters"):]
        panel = panel[:panel.index("</form>")]
        first = panel.index("<b>")
        assert panel[first:].startswith(("<b>Sort</b>", "<b>Sort by</b>")), url


# --- step 5: one link check -----------------------------------------------------------------

# --- step 6: scan details -------------------------------------------------------------------

def test_scan_details_close_left_change_match_and_full_screen(client, monkeypatch):
    from app import scans
    from test_app import JPEG, _scan_setup
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    scan_id = scans.add_photo(main.conn, scans.get_batch(main.conn, owner, batch_id), owner, JPEG)
    worker.start(wait=True)
    url = f"/scans/item/{scan_id}/details"
    d = with_assets(client.get(url, headers={"HX-Request": "true"}).text)
    head = d[d.index('<div class="ts-head'):d.index("</div>", d.index('<div class="ts-head'))]
    assert head.index("x-close") < head.index("<h2")  # ✕ on the left
    assert "View card" not in d and 'target="_blank"><img' not in d and "data-lightbox=" in d
    links = d.index('class="sd-links"')
    assert d.index("data-cv-change", links) > d.index("PriceCharting", links)  # Change match after the links
    assert 'aria-expanded="false"' in d and "<div data-cv-search hidden>" in d and '<form id="cv-save" class="cv-choices" method="post" action="/scans/item/%d/save">' % scan_id in d
    # back from Enhanced search: the panel is open and the fields are hidden until a card is picked
    d = with_assets(client.get(url + "?change=1", headers={"HX-Request": "true"}).text)
    assert 'aria-expanded="true"' in d and "<div data-cv-search>" in d and "/save\" hidden>" in d
    assert "dsLightbox" in with_assets(client.get(f"/scans/{batch_id}").text)


# --- step 8: batch names --------------------------------------------------------------------

def test_batches_can_be_named(client, monkeypatch):
    from test_app import _scan_setup
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    page = client.get(f"/scans/{batch_id}").text
    assert f'hx-post="/scans/{batch_id}/name"' in page and 'maxlength="60"' in page
    r = client.post(f"/scans/{batch_id}/name", data={"name": "  Trade night   at Gary's " + "x" * 80},
                    headers={"HX-Request": "true"})
    assert r.status_code == 204
    name = main.conn.execute("SELECT name FROM scan_batches WHERE id = ?", (batch_id,)).fetchone()[0]
    assert name.startswith("Trade night at Gary's x") and len(name) == 60
    assert name in client.get("/scans").text.replace("&#39;", "'")
    client.post(f"/scans/{batch_id}/name", data={"name": " "})
    assert main.conn.execute("SELECT name FROM scan_batches WHERE id = ?", (batch_id,)).fetchone()[0] is None
    assert client.post("/scans/999999/name", data={"name": "x"}).status_code == 404


# --- step 2: sets grid and the arts page ----------------------------------------------------

def test_sets_are_a_grid_of_tiles(client):
    _setup_with_set(client)
    client.post("/cards/t1-1/adjust", data={"variant": "normal", "condition": "NM", "delta": "1"})
    page = with_assets(client.get("/collection/sets").text)
    assert 'class="set-grid"' in page and 'class="set-tile" href="/sets/t1"' in page and 'class="set-line' not in page
    assert "repeat(3, minmax(0, 1fr))" in page


def test_an_art_tap_opens_the_card_and_modify_set_edits(client, monkeypatch):
    from app import chase
    from test_app import _chase_rows, _vec
    user_id = _setup_with_set(client)
    chase.follow(main.conn, user_id, 35, "Clefairy", user_id)
    chase.save_cards(main.conn, 35, _chase_rows())
    vectors = {"sv03.5-035": _vec(1, 0, 0), "zh-tw:SV2a-035": _vec(1, 0, 0.01), "ja:SV2a-035": _vec(1, 0.01, 0)}
    monkeypatch.setattr(chase, "fingerprints", lambda conn, dex, encoder, *a, **k: vectors)
    chase.regroup(main.conn, 35, vectors)
    page = client.get("/pokemon/35").text
    assert '<a href="/preview/en/sv03.5-035" aria-label="Open the' in page or '<a href="/cards/sv03.5-035" aria-label="Open the' in page
    assert "data-modify" in page and "Modify set" in page and '.ptile > button' not in page


# --- step 1: artist search and follow -------------------------------------------------------

from test_app import FakeAPI  # noqa: E402

ARITA = "Mitsuhiro Arita"


class _Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class ArtistAPI(FakeAPI):
    """English TCGdex where Mitsuhiro Arita drew t1-1 and t1-2."""
    lang = "en"

    def __init__(self):
        outer = self
        self.asked = []

        class Client:
            def get(self, path, params=None, timeout=None):
                outer.asked.append(params)
                return _Resp([{"id": "t1-1", "localId": "1", "name": "Bulbasaur"}, {"id": "t1-2", "localId": "2", "name": "Ivysaur"}])
        self.client = Client()

    def search_cards(self, local_id=None, name=None, illustrator=None):
        if illustrator and "arita" in illustrator.lower():
            return [{"id": "t1-1", "localId": "1", "name": "Bulbasaur"}, {"id": "t1-2", "localId": "2", "name": "Ivysaur"}]
        return []

    def get_card(self, card_id):
        return {"id": card_id, "localId": card_id.split("-")[1], "name": "Bulbasaur", "illustrator": ARITA, "rarity": "Common",
                "dexId": [1], "variants": {"normal": True}, "set": {"id": "t1", "name": "Test Set"}}


@pytest.fixture
def artists(monkeypatch):
    fake = ArtistAPI()
    monkeypatch.setattr(web, "api", fake)
    monkeypatch.setattr(tcgdex, "artist_names", lambda api=None, max_age=0: [ARITA, "Kagemaru Himeno"])
    return fake


def test_find_searches_by_artist(client, artists):
    _setup_with_set(client)
    page = client.get("/search", params={"q": "mitsuhiro arita"}).text  # an artist's name: their cards
    assert "Cards drawn by <strong>Mitsuhiro Arita</strong>" in page and "/preview/en/t1-1" in page and "/preview/en/t1-2" in page
    assert 'href="/artists/mitsuhiro%20arita"' in page and 'type="hidden" name="by"' not in page and 'value="artist" checked' not in page  # found, not picked: doesn't stick
    page = client.get("/search", params={"q": "arita"}).text  # part of a name: offered
    assert "Artist: Mitsuhiro Arita" in page and "Cards drawn by" not in page
    page = client.get("/search", params={"q": "arita", "by": "artist"}).text  # the Artist chip
    assert "Cards drawn by" in page and "/preview/en/t1-1" in page and 'value="artist" checked' in page


def test_follow_an_artist_groups_their_cards(client, artists, monkeypatch):
    from app import chase, chase_pages
    started = []
    monkeypatch.setattr(pokemon_pages, "_start_follow", lambda dex, only_new=False: started.append(dex))
    uid = _setup_with_set(client)
    page = client.get("/chases").text
    assert 'name="kind" value="artist"' in page and "<h2>Artists</h2>" in page
    r = client.post("/pokemon/follow", data={"kind": "artist", "artist": "mitsuhiro ARITA", "dex": ""}, follow_redirects=False)
    assert r.headers["location"] == "/artists/mitsuhiro%20arita"
    key = chase.artist(main.conn, ARITA)["key"]
    assert key < 0 and started == [key] and chase.artist(main.conn, key)["name"] == ARITA  # TCGdex's spelling
    assert chase.followed(main.conn, uid) == [] and chase.followed(main.conn, uid, artists=True)[0]["norm"] == "mitsuhiro arita"
    # the follow job, with the fake: their cards, then arts (every card its own art without pictures)
    text = chase.run_follow(main.conn, key, web.names, lambda lang: artists, None)
    assert ARITA in text and main.conn.execute("SELECT COUNT(*) FROM pokemon_cards WHERE dex_id = ?", (key,)).fetchone()[0] == 6  # 2 cards × English, Japanese, Taiwan (the fake answers all three)
    assert artists.asked[0] == {"illustrator": f"eq:{ARITA}"}
    page = client.get("/artists/mitsuhiro%20arita").text
    assert ARITA in page and "Artist</small>" in page and "Following" in page and "Modify set" in page
    assert f"/pokemon/{key}/unfollow" in page and 'class="era-rows" id="arts"' in page
    assert chase_pages.followed_artist_list(main.conn, uid)[0]["arts"] >= 2
    assert "/artists/mitsuhiro%20arita" in client.get("/chases").text
    assert key in {r[0] for r in main.conn.execute("SELECT dex_id FROM followed_pokemon")}  # covered by updates and prices
    assert client.get("/artists/nobody%20at%20all").status_code == 404
    client.post(f"/pokemon/{key}/unfollow")
    assert chase.followed(main.conn, uid, artists=True) == []


# --- 10b: the camera sometimes didn't open ---------------------------------------------------

def test_camera_lets_go_and_can_be_turned_on_by_a_tap(client, monkeypatch):
    from test_app import _scan_setup
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    page = with_assets(client.get(f"/scans/{batch_id}?camera=1").text)
    assert "data-cam-retry" in page and "Turn on the camera" in page
    assert 'addEventListener("pagehide", release)' in page and '"NotReadableError"' in page


# --- 10b: Pokémon page tiles use every picture we know --------------------------------------

def test_pokemon_page_tiles_use_the_shared_picture_list(client, monkeypatch):
    from app import chase, chase_pages
    from test_app import _chase_rows
    uid = _setup_with_set(client)
    chase.follow(main.conn, uid, 35, "Clefairy", uid)
    rows = _chase_rows()
    for r in rows:
        r["image_url"] = None  # TCGdex has no picture for any of them
    rows[1]["image_url"] = "https://img.example/twin.webp"  # except another print of the same art
    chase.save_cards(main.conn, 35, rows)
    chase.regroup(main.conn, 35, {})
    main.conn.execute("INSERT INTO sets (set_id, name, printed_total, total) VALUES ('base1', 'Base Set', 102, 102)")
    main.conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name, saved_picture) VALUES "
                      "('base1-5', 'base1', '5', 5, 1, 'Clefairy', 'saved.webp')")
    main.conn.execute("INSERT INTO inventory (user_id, card_id, variant, condition, quantity) VALUES (?, 'base1-5', 'holo', 'NM', 1)", (uid,))
    main.conn.commit()
    tiles = {t["cover"]["card_id"]: t for t in chase_pages.art_tiles(main.conn, uid, 35, "")}
    assert tiles["base1-5"]["pics"][0][0].endswith("saved.webp")  # the saved picture of the card you have
    from app import card_pictures
    assert all(not card_pictures.known(t["pics"]) for cid, t in tiles.items()
               if cid not in ("base1-5", "zh-tw:SV2a-035"))  # nothing known: No picture (Japanese ones get Limitless, 10d)
    assert any(not t["pics"] for t in tiles.values())
    page = client.get("/pokemon/35").text
    assert "saved.webp" in page and 'data-fail="art"' in page and "No picture" in page  # round 19: no inline handlers


def test_an_art_shows_another_prints_picture():
    from app import chase_pages
    cover = {"card_id": "x-1", "image_url": None}
    other = {"card_id": "ja:x-1", "image_url": "https://img.example/ja.webp"}
    assert chase_pages.art_pics(main.conn, cover, cover, [cover, other]) == [("https://img.example/ja.webp", "")]


# --- 10d: Japanese and Taiwan pictures from Limitless TCG ---------------------------------------

def test_limitless_address():
    from app import card_pictures as cp
    base = "https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/tpc/"
    assert cp.limitless("ja:SV8-001") == base + "SV8/SV8_1_R_JP_SM.png"
    assert cp.limitless("ja:M4-120", "LG") == base + "M4/M4_120_R_JP_LG.png"
    assert cp.limitless("zh-tw:SV2a-035") == base + "SV2a/SV2a_35_R_JP_SM.png"
    assert cp.limitless("ja:M-P-007") == base + "M-P/M-P_7_R_JP_SM.png"
    assert cp.limitless("ja:SV8-TG01") is None  # numbers with letters
    assert cp.limitless("base1-5") is None and cp.limitless("fr:sv03-001") is None and cp.limitless("zh-cn:CBB4C-001") is None
    assert cp.limitless(None) is None


def test_limitless_in_more_candidates_and_card_page(client):
    from app import card_pictures as cp
    _setup_with_set(client)
    jp = cp.more(main.conn, "ja:SV8-001")
    assert jp[-1] == ("https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/tpc/SV8/SV8_1_R_JP_SM.png", "")
    assert cp.more(main.conn, "zh-tw:SV8-001")[-1][1] == "Japanese version"
    assert cp.more(main.conn, "base1-5") == []
    card = {"id": "ja:SV8-001", "image_url": None}
    assert cp.candidates(card, None)[-1][0].endswith("SV8_1_R_JP_SM.png")  # so the picture check saves a copy
    card["image_url"] = "https://assets.tcgdex.net/ja/sv/SV8/001/low.webp"
    assert cp.candidates(card, None)[0][0] == card["image_url"]  # TCGdex's own picture stays first
    assert cp.known(jp) == []  # a Japanese card with only Limitless still gets its one-off lookup
    assert cp.large(jp)[-1][0].endswith("SV8_1_R_JP_LG.png")
    assert cp.large([("https://img.example/a.png", "")]) == [("https://img.example/a.png", "")]
