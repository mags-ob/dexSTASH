"""Round 9: English names for links, the confirm pop-up, Enhanced Search score and learning, pictures
wherever we have one, the scanner container, the dexSTASH rename and the UI overhaul."""
from conftest import template_with_assets, with_assets  # noqa: E402

import json
from urllib.parse import unquote_plus

import pytest

from app import clock, main, prices
from app import web
from app.names import Names
from test_app import _setup_with_set, client  # noqa: F401  (the client fixture)

NAMES = ("pokemon_species_id,local_language_id,name,genus\n"
         "37,9,Vulpix,Fox\n37,1,ロコン,x\n37,12,六尾,x\n52,9,Meowth,Cat\n52,1,ニャース,x\n52,12,喵喵,x\n"
         "571,9,Zoroark,Fox\n571,1,ゾロアーク,x\n35,9,Clefairy,Fairy\n35,1,ピッピ,x\n"
         "6,9,Charizard,Flame\n6,1,リザードン,x\n6,12,喷火龙,x\n")


@pytest.fixture
def names(tmp_path, monkeypatch):
    csv = tmp_path / "species.csv"
    csv.write_text(NAMES)
    n = Names(csv, url="http://unused")
    monkeypatch.setattr(web, "names", n)
    return n


# --- step 3: English names for links -------------------------------------------------------

def test_regional_forms_and_owners_have_english_names(names):
    assert names.to_english("アローラ ロコン") == "Alolan Vulpix"
    assert names.to_english("アローラロコン") == "Alolan Vulpix"
    assert names.to_english("阿罗拉六尾") == "Alolan Vulpix"
    assert names.to_english("ガラル ニャースV") == "Galarian Meowth V"
    assert names.to_english("ロケット団のニャース") == "Team Rocket's Meowth"
    assert names.to_english("火箭队的喵喵") == "Team Rocket's Meowth"
    assert names.to_english("Nのゾロアークex") == "N's Zoroark ex"
    assert names.to_english("リーリエのピッピex") == "Lillie's Clefairy ex"
    assert names.to_english("メガリザードンex") == "Mega Charizard ex"  # still works
    assert names.to_english("ボスの指令") is None                      # a Trainer: not a Pokémon
    # Every owner and region in the tables reads as English.
    assert all(prices.latin(v) for v in {**names.OWNERS, **names.REGIONS}.values())


def _url_text(url: str) -> str:
    return unquote_plus(url)


def test_no_link_ever_searches_with_japanese_or_chinese_text(client, names):
    """The bug found in use: tapping TCGplayer or PriceCharting on a Japanese card searched with the printed name."""
    _setup_with_set(client)
    cards = [("ja:SV1-012", "ja", "アローラ ロコン"), ("ja:SV1-013", "ja", "ボスの指令"),
             ("zh-cn:CSV1C-001", "zh-cn", "喷火龙ex"), ("zh-tw:SV1-014", "zh-tw", "博士的研究"),
             ("ja:SV1-015", "ja", "ロケット団のニャース")]
    for card_id, lang, name in cards:
        links = web._links({"id": card_id, "name": name, "number": "012", "set_name": "スカーレットex"}, lang, None)
        for url in (links["tcg_url"], links["pc_url"]):
            assert prices.latin(_url_text(url)), url
    links = web._links({"id": "ja:SV1-012", "name": "アローラ ロコン", "number": "012", "set_name": ""}, "ja", None)
    assert "Alolan Vulpix 012" in _url_text(links["tcg_url"]) and "pokemon-japan" in links["tcg_url"]
    assert "Alolan Vulpix 12 Japanese" in _url_text(links["pc_url"])  # round 27: no leading zeros
    # A Trainer with no English name searches by number only.
    links = web._links({"id": "ja:SV1-013", "name": "ボスの指令", "number": "013", "set_name": ""}, "ja", None)
    assert _url_text(links["pc_url"]).endswith("q=13 Japanese")  # round 27: no leading zeros


def _japanese_card(card_id: str, name: str, twin: str | None = None, lang: str = "ja"):
    set_id = card_id.rsplit("-", 1)[0]
    main.conn.execute("INSERT OR IGNORE INTO sets (set_id, language, name, printed_total, total) VALUES (?, ?, ?, 100, 100)",
                      (set_id, lang, "スカーレットex"))
    main.conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name, twin_name) "
                      "VALUES (?, ?, ?, 1, 1, ?, ?)", (card_id, set_id, card_id.rsplit("-", 1)[1], name, twin))
    user = main.conn.execute("SELECT id FROM users").fetchone()[0]
    main.conn.execute("INSERT INTO inventory (user_id, card_id) VALUES (?, ?)", (user, card_id))
    main.conn.commit()


def test_english_name_order_for_search_links(client, names):
    _setup_with_set(client)
    _japanese_card("ja:SV1-013", "ボスの指令")
    _japanese_card("ja:SV1-014", "ナンジャモ")
    _japanese_card("zh-cn:CSV1C-002", "老大的指令", twin="Boss's Orders", lang="zh-cn")
    # A Trainer whose English name was found before uses that name (kept on the card by database step 7).
    main.conn.execute("UPDATE cards SET english_name = 'Iono' WHERE id = 'ja:SV1-014'")
    main.conn.commit()
    assert web._link_name("ナンジャモ", "ja:SV1-014") == "Iono"
    assert web._link_name("老大的指令", "zh-cn:CSV1C-002") == "Boss's Orders"
    assert web._link_name("ボスの指令", "ja:SV1-013") is None  # round 15b: no list to type one in any more


# --- step 4: "Was this the right card?" ------------------------------------------------------

# --- step 5: Enhanced Search score and learning ---------------------------------------------

import io as _io  # noqa: E402

from app import pictures, scans  # noqa: E402
from test_app import FakeEncoder, _card_png, _load_wait, _scan_setup, pics  # noqa: E402,F401

YELLOW = (220, 200, 30)


def _picture_scan(client, batch_id, worker, colour):
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("p.jpg", _card_png(colour, background=(20, 20, 24)), "image/jpeg"))])
    worker.start(wait=True)
    scan_id = main.conn.execute("SELECT MAX(id) FROM scans WHERE batch_id = ?", (batch_id,)).fetchone()[0]
    client.post(f"/scans/item/{scan_id}/picture", headers={"HX-Request": "true"})
    web.picture_worker.start(wait=True)
    return scan_id


def test_enhanced_search_scores_and_learns_from_confirmed_scans(client, monkeypatch, pics):
    pytest.importorskip("onnxruntime")  # round 13: Enhanced Search needs the photo reader's libraries
    owner, batch_id, worker, reader = _scan_setup(client, monkeypatch, boxes=[])
    client.post("/account/data/pictures/on")
    _load_wait()
    # A green photo: the green card is first; adding it scores rank 1.
    green = _picture_scan(client, batch_id, worker, (30, 180, 40))
    assert main.conn.execute("SELECT picture_vector FROM scans WHERE id = ?", (green,)).fetchone()[0]
    client.post(f"/scans/item/{green}/match", data={"src": "picture", "pick": 0})
    client.post(f"/scans/item/{green}/add")
    assert main.conn.execute("SELECT rank FROM picture_scores WHERE scan_id = ?", (green,)).fetchone()[0] == 1
    assert main.conn.execute("SELECT picture_vector FROM scans WHERE id = ?", (green,)).fetchone()[0] is None  # gone with the photo
    # A yellow photo the blue card isn't near: you pick the blue card by hand and add it.
    yellow = _picture_scan(client, batch_id, worker, YELLOW)
    matches = json.loads(main.conn.execute("SELECT picture_json FROM scans WHERE id = ?", (yellow,)).fetchone()[0])
    assert matches[0]["id"] != "t1-TG01"
    blue_rank = next(i + 1 for i, m in enumerate(matches) if m["id"] == "t1-TG01")
    scan = scans.get_scan(main.conn, owner, yellow)
    scans.use_card(main.conn, scan, {"id": "t1-TG01", "set_id": "t1", "lang": "en", "number": "TG01", "name": "Venusaur"})
    client.post(f"/scans/item/{yellow}/add")
    assert main.conn.execute("SELECT rank FROM picture_scores WHERE scan_id = ?", (yellow,)).fetchone()[0] == blue_rank
    assert main.conn.execute("SELECT card_id FROM picture_learned").fetchall()[-1][0] == "t1-TG01"
    page = client.get("/account/data").text
    assert "Your card was first in 1 of 2 scans, in the top 5 in 2" in page and "learned from 2 of your confirmed scans" in page
    # Next time the same yellow photo finds the blue card first.
    again = _picture_scan(client, batch_id, worker, YELLOW)
    matches = json.loads(main.conn.execute("SELECT picture_json FROM scans WHERE id = ?", (again,)).fetchone()[0])
    assert matches[0]["id"] == "t1-TG01"
    # Forget learned photos: the index drops them on its next run.
    client.post("/account/data/pictures/learned/forget")
    assert pictures.sync_learned(main.conn) == 2
    assert not any(r["key"].startswith("scan:") for r in pictures.index_for("en").rows)
    client.post("/account/data/pictures/score/reset")
    assert "No scores yet" in client.get("/account/data").text


def test_learning_keeps_five_per_card_and_a_changed_match_forgets():
    import numpy as np
    main.conn.execute("INSERT OR IGNORE INTO sets (set_id, language, name, printed_total, total) VALUES ('t9', 'en', 'T', 9, 9)")
    main.conn.execute("INSERT OR IGNORE INTO cards (id, set_id, number, sort_number, is_main_set, name) VALUES ('t9-1', 't9', '1', 1, 1, 'X')")
    vector = np.ones(pictures.DIM, np.float32).tobytes()
    for i in range(1, 8):
        pictures.learn(main.conn, {"id": 9000 + i, "picture_json": '[{"id": "t9-1"}]', "picture_vector": vector}, "t9-1")
    keys = [r[0] for r in main.conn.execute("SELECT key FROM picture_learned WHERE card_id = 't9-1' ORDER BY key")]
    assert keys == [f"scan:{9000 + i}" for i in range(3, 8)]
    pictures.unlearn(main.conn, 9007)
    assert main.conn.execute("SELECT COUNT(*) FROM picture_learned WHERE card_id = 't9-1'").fetchone()[0] == 4
    # Scans never sent to Enhanced Search aren't counted.
    pictures.learn(main.conn, {"id": 9100, "picture_json": None, "picture_vector": None}, "t9-1")
    assert main.conn.execute("SELECT 1 FROM picture_scores WHERE scan_id = 9100").fetchone() is None
    main.conn.execute("DELETE FROM picture_learned")
    main.conn.execute("DELETE FROM picture_scores")
    main.conn.commit()


# --- step 2: pictures wherever we have one ------------------------------------------------

from app import card_pictures  # noqa: E402


class PairAPI:
    """TCGdex in one language: a card with or without a picture."""

    def __init__(self, cards):
        self.cards = cards

    def get_card_cached(self, card_id):
        if card_id not in self.cards:
            raise KeyError(card_id)
        return self.cards[card_id]


def test_unstored_cards_show_every_picture_we_know_and_look_one_up_once(client, monkeypatch):
    _setup_with_set(client)
    # Nothing here for a Japanese card from search: no picture, so its tile shows the sprite.
    assert card_pictures.known(card_pictures.for_id(main.conn, "ja:SV9-001")) == []
    # The Taiwan card with the same set and number is stored with a picture: the Japanese tile uses it.
    main.conn.execute("INSERT OR IGNORE INTO sets (set_id, language, name, printed_total, total) VALUES ('zh-tw:SV9', 'zh-tw', 'x', 9, 9)")
    main.conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name, image_url) "
                      "VALUES ('zh-tw:SV9-001', 'zh-tw:SV9', '001', 1, 1, 'x', 'https://assets.tcgdex.net/tw/SV9/001/low.webp')")
    main.conn.commit()
    assert card_pictures.known(card_pictures.for_id(main.conn, "ja:SV9-001")) == [("https://assets.tcgdex.net/tw/SV9/001/low.webp", "Taiwan version")]
    # A card nobody has a picture of is asked once; the lookup tries TCGdex's other language, then TCGplayer.
    assert card_pictures.ask(main.conn, ["ja:SV9-002", "ja:SV9-001"]) == 1  # the other one has a picture
    assert card_pictures.ask(main.conn, ["ja:SV9-002"]) == 0                # already waiting
    apis = {"ja": PairAPI({"SV9-002": {"name": "アローラ ロコン", "localId": "002", "set": {"name": "x"}}}),
            "zh-tw": PairAPI({})}
    # Round 15b: TCGplayer and PriceCharting aren't searched for a picture; with none on TCGdex or TCGCSV it's a
    # miss, asked again after 14 days.
    sent = []
    monkeypatch.setattr(card_pictures, "_tcgcsv_picture", lambda *a: sent.append(a) and None)
    assert card_pictures.lookup(main.conn, "ja:SV9-002", apis.get, lambda name, lang: "Alolan Vulpix", pause=0) is None
    assert len(sent) == 1
    card_pictures.ask(main.conn, ["ja:SV9-003"])
    apis["ja"].cards["SV9-003"] = {"name": "x", "localId": "003", "set": {"name": "x"}}
    assert card_pictures.lookup(main.conn, "ja:SV9-003", apis.get, lambda name, lang: "X", pause=0) is None
    assert card_pictures.ask(main.conn, ["ja:SV9-003"]) == 0
    assert card_pictures.known(card_pictures.for_id(main.conn, "ja:SV9-003")) == []


# --- step 1: the scanner container -------------------------------------------------------

import time  # noqa: E402

from app import jobs, scan_worker  # noqa: E402
from test_app import IVYSAUR, JPEG, FakeReader  # noqa: E402


def test_the_scanner_reads_photos_and_runs_the_apps_jobs(client, monkeypatch):
    owner, batch_id, worker, reader = _scan_setup(client, monkeypatch)
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg"))])
    # The web app only queued it: the scanner picks it up on its next round, and says it's alive.
    scanner = scan_worker.Scanner(main.conn, api_for=lambda lang: web.api, names=web.names, reader=FakeReader(IVYSAUR))
    scanner.start()
    scanner.tick(now=time.time())
    scanner.worker.start(wait=True)
    assert main.conn.execute("SELECT status FROM scans WHERE batch_id = ?", (batch_id,)).fetchone()[0] == "ready"
    assert clock.to_unix(main.conn.execute("SELECT value FROM app_meta WHERE key = 'scanner_seen'").fetchone()[0]) > time.time() - 5
    # A job the app asked for (grouping a followed Pokémon's cards by artwork) shows in Running now; the scanner's
    # runner runs it by its kind (round 23: one jobs table).
    ran = []
    monkeypatch.setattr(scan_worker.chase, "run_recheck", lambda conn, dex, encoder, job: ran.append(dex) or "Compared again: 3 arts.")
    box = web.background
    assert box.queue("recheck:6", "Grouping Charizard's cards by artwork", "recheck", "6")
    assert not box.queue("recheck:6", "again", "recheck", "6")  # one at a time
    assert box.running("recheck:6") and box.current()[0].label == "Grouping Charizard's cards by artwork"
    try:
        scanner.jobs.start_runner()
        for _ in range(200):
            if not box.running("recheck:6"):
                break
            time.sleep(0.05)
        assert ran == [6]
        assert box.last("recheck:6").result == "Compared again: 3 arts." and not box.running("recheck:6")
    finally:
        scanner.jobs.stop()
        main.conn.execute("DELETE FROM jobs")
        main.conn.commit()


def test_the_app_says_when_the_scanner_is_off(client, monkeypatch):
    owner, batch_id, worker, reader = _scan_setup(client, monkeypatch)
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg"))])
    monkeypatch.setattr(web, "SCANNER_EXTERNAL", True)
    monkeypatch.setattr(web, "SCANNER_MODE", "separate")
    web._scanner_seen.clear()
    main.conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('scanner_seen', ?)", (str(time.time() - 600),))
    main.conn.commit()
    page = client.get(f"/scans/{batch_id}").text
    assert "Scanner is off" in page and "sudo docker compose --profile scanner up -d" in page
    assert "Scanner is off" in client.get("/account/data").text
    main.conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('scanner_seen', ?)", (str(time.time()),))
    main.conn.commit()
    web._scanner_seen.clear()
    assert "Scanner is off" not in client.get(f"/scans/{batch_id}").text
    assert "Scanner running" in client.get("/account/data").text
    web._scanner_seen.clear()


def test_scanner_waits_for_the_app_to_set_up_the_database(tmp_path):
    from app import backup, db
    path = tmp_path / "dexstash.db"
    naps = []

    def sleep(s):
        naps.append(s)
        conn = db.connect(path)
        db.init(conn)
        backup.mark_version(conn)
        conn.close()
    scan_worker._wait_for_the_app(path, backup.code_version(), sleep=sleep)
    assert naps == [5]


# --- step 6: the rename -----------------------------------------------------------------------

def test_app_name_comes_from_env(monkeypatch):
    from app import settings
    monkeypatch.delenv("APP_NAME", raising=False)
    assert settings.app_name() == "dexSTASH"
    monkeypatch.setenv("APP_NAME", "Our Cards")
    assert settings.app_name() == "Our Cards"


# --- lump 3: the UI overhaul ----------------------------------------------------------------

from app import icons, themes  # noqa: E402
from test_app import _scan_setup  # noqa: E402


def test_a_photo_sent_twice_is_added_once(client, monkeypatch):
    owner, batch_id, worker, reader = _scan_setup(client, monkeypatch)
    send = lambda cid: client.post(f"/scans/{batch_id}/photos", data={"client_id": cid},  # noqa: E731
                                   files=[("photos", ("a.jpg", JPEG, "image/jpeg"))], headers={"X-Scan-Upload": "1"})
    first = send("phone-1")
    assert first.status_code == 200 and "X-Scan-Repeat" not in first.headers
    again = send("phone-1")  # the phone lost the answer and tried again
    assert again.headers.get("X-Scan-Repeat") == "1" and again.text == ""
    send("x" * 500)  # an id is kept to 64 characters
    rows = main.conn.execute("SELECT client_id FROM scans WHERE batch_id = ? ORDER BY id", (batch_id,)).fetchall()
    assert [r[0] for r in rows] == ["phone-1", "x" * 64]
    assert send("x" * 64).headers.get("X-Scan-Repeat") == "1"


def test_use_this_on_find_picks_the_card_for_the_photo(client, monkeypatch):
    owner, batch_id, worker, reader = _scan_setup(client, monkeypatch)
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg"))])
    scan_id = main.conn.execute("SELECT MAX(id) FROM scans WHERE batch_id = ?", (batch_id,)).fetchone()[0]
    from test_app import FakeSearchAPI
    monkeypatch.setattr(web, "api", FakeSearchAPI())
    page = client.get(f"/search?q=bulbasaur&scan={scan_id}").text
    assert 'action="/find/use"' in page and f'name="scan" value="{scan_id}"' in page
    r = client.post("/find/use", data={"ref": "/cards/t1-1", "scan": str(scan_id)}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/scans/{batch_id}#scan-{scan_id}"
    picked = json.loads(main.conn.execute("SELECT guesses_json FROM scans WHERE id = ?", (scan_id,)).fetchone()[0])[0]
    assert picked["id"] == "t1-1" and picked["by_hand"]
    # Anything but a card link, or no photo or row to use it for, is refused.
    for data in ({"ref": "/cards/nope", "scan": str(scan_id)},
                 {"ref": "/sets/t1", "scan": str(scan_id)}, {"ref": "/cards/t1-1"},
                 {"ref": "/preview/xx/t1-1", "scan": str(scan_id)}):
        assert client.post("/find/use", data=data, follow_redirects=False).status_code in (400, 404), data
    client.post("/logout")
    assert client.post("/find/use", data={"ref": "/cards/t1-1", "scan": str(scan_id)},
                       follow_redirects=False).status_code in (303, 401)
    assert json.loads(main.conn.execute("SELECT guesses_json FROM scans WHERE id = ?", (scan_id,)).fetchone()[0])[0]["id"] == "t1-1"


@pytest.mark.parametrize("accent,second,swatches", [
    ("#7b62a3", "#ff5a5a", ["#7b62a3", "#544591", "#ff5a5a", "#d8d0e8", "#2e2440"]),  # Gengar
    ("#e3350d", "#2271e7", ["#e3350d", "#f8a030", "#2271e7", "#fde7a0", "#6a1a08"]),  # Charizard
    ("#9a9a9a", None, ["#9a9a9a", "#bbbbbb", "#555555"]),  # a grey Pokémon: no colour to borrow
])
def test_every_theme_role_has_readable_text(accent, second, swatches):
    for dark in (False, True):
        r = themes.roles({"accent": accent, "second": second}, swatches, dark)
        for bg, fg, need in (("dark", "on_dark", 7.0), ("second_bg", "on_second", 4.5), ("pop", "on_pop", 4.5)):
            assert themes.contrast(themes._rgb(r[bg]), themes._rgb(r[fg])) >= need - 0.01, (accent, dark, bg)


def test_icons_are_inline_and_back_buttons_say_where_they_go(client):
    star = icons.icon("star-on", 18, label="Chasing")
    assert 'fill="currentColor"' in star and 'aria-label="Chasing"' in star and 'width="18"' in star
    assert 'aria-hidden="true"' in icons.icon("camera") and 'fill="none"' in icons.icon("camera")
    assert "&lt;b&gt;" in icons.icon("x", label="<b>")
    _setup_with_set(client)
    card = client.get("/cards/t1-1").text
    assert 'class="back-fab"' in card and 'aria-label="Back"' in card
    home = client.get("/").text
    assert "back-fab" not in home.split("<main")[1].split("</main>")[0]  # top-level pages have none


# --- round 9b: Want/Add on Find's tiles, actions in the PC top bar, Nunito ----------------------

def test_page_actions_move_to_the_top_bar_on_big_screens(client):
    _setup_with_set(client)
    home = client.get("/").text
    head, body = home.split("</header>", 1)
    assert 'action="/prices/refresh" class="pc-only"' in head and 'class="phone-only"' in body


def test_nunito_ships_with_the_app():
    import pathlib
    static = pathlib.Path(main.__file__).with_name("static") / "fonts"
    assert (static / "nunito-latin.woff2").stat().st_size > 10_000 and "Open Font License" in (static / "OFL.txt").read_text()
    for name in ("base.html", "public_base.html"):
        text = template_with_assets(name)
        assert '"Nunito", system-ui' in text and "fonts.googleapis" not in text


# --- round 9c: "database is locked" with the scanner container ----------------------------------

import sqlite3  # noqa: E402

from app import db  # noqa: E402


def test_background_connections_never_hold_the_write_lock(tmp_path):
    path = tmp_path / "two.db"
    a, b = db.connect(path), db.connect(path)
    a.execute("CREATE TABLE t (x)")
    a.execute("INSERT INTO t VALUES (1)")  # no commit, like a job that stopped between a write and its commit
    assert not a.in_transaction
    b.execute("PRAGMA busy_timeout = 100")
    b.execute("INSERT INTO t VALUES (2)")  # the other process can still write at once
    # Statements that must land together still do, or not at all.
    with pytest.raises(ValueError):
        with db.transaction(a):
            a.execute("INSERT INTO t VALUES (3)")
            raise ValueError("stop")
    with db.transaction(a):
        a.execute("INSERT INTO t VALUES (4)")
        a.execute("INSERT INTO t VALUES (5)")
    assert [r[0] for r in b.execute("SELECT x FROM t ORDER BY x")] == [1, 2, 4, 5]


def test_a_request_leaves_nothing_uncommitted_and_a_busy_upload_is_kept_for_later(client, monkeypatch):
    owner, batch_id, worker, reader = _scan_setup(client, monkeypatch)
    main.conn.execute("UPDATE scan_batches SET language = language WHERE id = ?", (batch_id,))  # a write left open...
    client.get("/scans")
    assert not main.conn.in_transaction  # ...is committed when the request ends
    other = db.connect(main.db.DB_PATH)
    other.execute("BEGIN IMMEDIATE")  # the scanner container is in the middle of a write
    main.conn.busy_ms = 100  # every thread's connection (reliability round)
    try:
        r = client.post(f"/scans/{batch_id}/photos", data={"client_id": "p1"},
                        files=[("photos", ("a.jpg", JPEG, "image/jpeg"))], headers={"X-Scan-Upload": "1"})
        assert r.status_code == 503 and "kept on your phone" in r.text
    finally:
        other.rollback()
        main.conn.busy_ms = db.BUSY_MS
    r = client.post(f"/scans/{batch_id}/photos", data={"client_id": "p1"},
                    files=[("photos", ("a.jpg", JPEG, "image/jpeg"))], headers={"X-Scan-Upload": "1"})
    assert r.status_code == 200 and main.conn.execute("SELECT COUNT(*) FROM scans WHERE client_id = 'p1'").fetchone()[0] == 1


def test_photos_that_wont_send_can_wait_or_be_deleted(client):
    _setup_with_set(client)
    page = with_assets(client.get("/scans").text)
    assert 'id="photos-later"' in page and "data-send" in page and "data-drop" in page
    assert 'over.second("Later"' in page
    assert "data-over-act2" in page and "second(label, fn)" in page


def test_find_shows_a_card_added_by_hand_by_its_name(client, monkeypatch):
    # Round 9d: Suicune added by hand and Find by name gave Internal Server Error (the tile had no price).
    from test_app import FakeSearchAPI, _offline_languages
    _setup_with_set(client)
    _offline_languages(monkeypatch)
    monkeypatch.setattr(web, "api", FakeSearchAPI())
    r = client.post("/cards/new", data={"lang": "en", "code": "PROMO", "number": "12", "name": "Suicune"}, follow_redirects=False)
    assert r.headers["location"] == "/cards/PROMO-12"
    page = client.get("/search?q=Suicune&lang=en")
    assert page.status_code == 200 and "Added by hand" in page.text and "/cards/PROMO-12" in page.text
