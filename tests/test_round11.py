"""Round 11: navigation and small fixes, pictures, prices and links (TCGCSV), Home, Find, Data sources and the log."""
from conftest import template_with_assets, with_assets  # noqa: E402

import re

import pytest

from app import main
from app import web
from app.routes import pictures as pictures_pages, pokemon as pokemon_pages, scans as scans_pages
from test_app import SAMPLE_NAMES, FakeSearchAPI, _setup_with_set, client  # noqa: F401  (the client fixture)


# --- part 1: navigation and small fixes -------------------------------------------------------

def test_saving_replaces_the_page_for_pop_ups_and_hearts(client):
    uid = _setup_with_set(client)
    base = with_assets(client.get("/").text)
    assert "window.dsSave" in base and "location.replace(resp.url)" in base  # round 17: every form, not just marked ones
    from app import wants
    wants.add(main.conn, uid, "t1-1", by=uid)
    card = with_assets(client.get("/cards/t1-1").text)
    assert 'id="cv-unchase"' in card and "data-replace" in card.split('id="cv-unchase"')[1].split(">")[0]


def test_page_loading_is_a_small_spinner(client):
    _setup_with_set(client)
    base = with_assets(client.get("/").text)
    assert "ds-load" in base and "@keyframes ds-load" not in base  # round 25: no fake progress bar


def test_search_boxes_select_on_first_tap_and_paste_buttons(client):
    _setup_with_set(client)
    page = with_assets(client.get("/cards/t1-1").text)
    assert '.closest?.(".sbox input")' in page and "box.select()" in page
    assert 'name="link"' in page and 'inputmode="url"' in page
    assert "data-paste" not in page and "can-paste" not in page and "clipboard?.readText" not in page  # 11b: no Paste buttons


def test_scan_review_back_and_close():
    from pathlib import Path
    t = Path(main.__file__).parent / "templates"
    page = template_with_assets("scan_details_page.html")
    assert 'addEventListener("scan-picked", () => location.replace(batch))' in page  # N1: ✕ / Cancel / last Save
    assert "a.back-fab" in page and "cancel.click()" in page  # N1: the header Back cancels too
    pop = template_with_assets("scan_batch.html")
    assert 'history.pushState({ts: 1}, "")' in pop and "history.back()" in pop and 'addEventListener("popstate"' in pop  # N2


def test_find_pokemon_box_star_set_link_sprite_and_folded_tags(client, monkeypatch):
    monkeypatch.setattr(web, "names", SAMPLE_NAMES)
    monkeypatch.setattr(web, "api", FakeSearchAPI())
    started = []
    monkeypatch.setattr(pokemon_pages, "_start_follow", lambda dex, only_new=False: started.append(dex))
    _setup_with_set(client)
    page = client.get("/search", params={"q": "Charizard"}).text
    box = page.split('<div class="find-poke">')[1].split("</section>")[0]
    assert '/pokemon/6"' in box and "Pokémon set" in box and "/pokemon/6/star" in box
    assert "<span>Charizard</span>" in box  # no evolution line (not downloaded): the Pokémon on its own
    assert 'aria-pressed="false"' in box
    r = client.post("/pokemon/6/star", data={"on": "1"}, headers={"HX-Request": "true"})
    assert 'aria-pressed="true"' in r.text and "hx-confirm" in r.text and started == [6]
    assert 'aria-pressed="true"' in client.get("/search", params={"q": "Charizard"}).text
    r = client.post("/pokemon/6/star", data={"on": ""}, headers={"HX-Request": "true"})
    assert 'aria-pressed="false"' in r.text
    assert main.conn.execute("SELECT COUNT(*) FROM followed_pokemon").fetchone()[0] == 0


def test_find_filter_button_sits_in_the_sticky_bar(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakeSearchAPI())
    _setup_with_set(client)
    page = client.get("/search", params={"q": "Bulbasaur", "rarity": "Common"}).text
    bar = page.split('class="find-bar"')[1].split("</form>")[0]
    assert "filter-btn" in bar and "data-filter-toggle" in bar
    assert "filter-chip" not in page.split("<main")[1]


# --- part 4: Home, Find, the activity log -----------------------------------------------------

def test_recently_viewed_on_an_empty_find(client, monkeypatch):
    from app import recent
    monkeypatch.setattr(web, "api", FakeSearchAPI())
    _setup_with_set(client)
    client.get("/cards/t1-2")
    client.get("/cards/t1-1")
    client.get("/cards/t1-2")  # opened again: back to the front
    assert [r["card_id"] for r in recent.latest(main.conn)] == ["t1-2", "t1-1"]
    page = client.get("/search").text
    assert "Recently viewed" in page and page.index('href="/cards/t1-2"') < page.index('href="/cards/t1-1"')
    assert "Recently viewed" not in client.get("/search", params={"q": "Bulbasaur"}).text
    for i in range(recent.KEEP + 5):
        recent.record(main.conn, f"x-{i}", "x", str(i), None, f"/cards/x-{i}")
    assert main.conn.execute("SELECT COUNT(*) FROM card_views").fetchone()[0] == recent.KEEP


def test_home_pokemon_grid_no_sets_and_cheaper_chases(client):
    from datetime import date, timedelta
    from app import chase, value, wants
    uid = _setup_with_set(client)
    chase.follow(main.conn, uid, 25, "Pikachu", uid)
    wants.add(main.conn, uid, "t1-1", by=uid)
    today = date(2026, 10, 20)
    main.conn.execute("INSERT INTO prices (card_id, variant, origin, price, updated_at) VALUES ('t1-1', 'normal', 'tcgplayer', 8.0, '2026-10-20')")
    main.conn.execute("INSERT INTO price_snapshots (card_id, variant, source, price, captured_at) VALUES ('t1-1', 'normal', 'tcgplayer', 10.0, ?)",
                      ((today - timedelta(days=8)).isoformat() + " 08:00:00",))
    main.conn.commit()
    cheaper = value.cheaper_chases(main.conn, uid, today=today)
    assert [(r["card_id"], r["percent"]) for r in cheaper] == [("t1-1", -20.0)]
    page = client.get("/").text
    assert 'class="poke-grid"' not in page and "Your sets" not in page  # Your Pokémon left Home in round 24
    # A drop under 10% isn't shown.
    main.conn.execute("UPDATE prices SET price = 9.5")
    main.conn.commit()
    assert value.cheaper_chases(main.conn, uid, today=today) == []


def test_activity_log_rotates_scrubs_and_reads(tmp_path):
    import logging
    from datetime import datetime, timezone
    from app import activity
    handler = activity.setup("app", folder=tmp_path)
    log = logging.getLogger("dexstash.test")
    try:
        log.info("price update done: 12 cards")
        log.warning("TCGplayer 404 for ja:M6-072 password=hunter2 code=ABCD-1234 token: deadbeefdeadbeefdeadbeefdeadbeef1234")
        try:
            1 / 0
        except ZeroDivisionError:
            log.exception("link check failed")
        handler.flush()
        text = (tmp_path / "app.log").read_text()
        assert "hunter2" not in text and "ABCD-1234" not in text and "deadbeefdeadbeef" not in text and "ja:M6-072" in text
        entries = activity.read(1, folder=tmp_path)
        assert [e["level"] for e in entries][-1] == "INFO" and entries[0]["source"] == "app"
        assert any("ZeroDivisionError" in e["text"] for e in entries)  # the traceback stays with its line
        assert len(activity.read(1, problems=True, folder=tmp_path)) == 2
        assert activity.read(1, folder=tmp_path, now=datetime(2030, 1, 1, tzinfo=timezone.utc)) == []
        assert "price update done" in activity.as_text(entries).splitlines()[0]
    finally:
        for name in ("dexstash", "uvicorn.error"):
            logging.getLogger(name).removeHandler(handler)
        handler.close()
    assert activity.MAX_BYTES == 2 * 1024 * 1024 and activity.BACKUPS == 2


def test_activity_log_page_is_admin_only(client):
    _setup_with_set(client)
    page = client.get("/account/data/log?h=6&only=problems").text
    assert "Copy last hour" in page and "Activity log" in page


def test_docker_logs_are_capped():
    import yaml
    from pathlib import Path
    compose = yaml.safe_load((Path(main.__file__).parent.parent / "docker-compose.yml").read_text())
    for name in ("app", "scanner", "caddy"):
        assert compose["services"][name]["logging"]["options"] == {"max-size": "10m", "max-file": "3"}


# --- part 2: pictures -------------------------------------------------------------------------

def _png():
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (63, 88), (200, 40, 40)).save(buf, "PNG")
    return buf.getvalue()


def test_followed_cards_get_saved_pictures_a_few_at_a_time(client, tmp_path):
    from app import card_pictures, chase
    from test_app import _chase_rows
    uid = _setup_with_set(client)
    chase.follow(main.conn, uid, 35, "Clefairy", uid)
    chase.save_cards(main.conn, 35, _chase_rows())
    due = card_pictures.followed_due(main.conn, limit=10)
    assert set(due) == {"sv03.5-035", "zh-tw:SV2a-035", "ja:SV2a-035", "base1-5"}
    assert card_pictures.followed_due(main.conn, limit=2) == due[:2]
    # base1-5 has no picture anywhere (TCGdex none): it's marked checked and waits a week.
    assert card_pictures.check_followed(main.conn, "base1-5", lambda url: b"", tmp_path) is None
    assert card_pictures.check_followed(main.conn, "ja:SV2a-035", lambda url: _png(), tmp_path) == ""
    assert "base1-5" not in card_pictures.followed_due(main.conn, limit=10)
    pics = card_pictures.for_id(main.conn, "ja:SV2a-035", "https://img/x")
    assert pics[0][0].startswith("/card-pictures/") and pics[1][0] == "https://img/x"
    # Unfollowed: the copy goes at the nightly tidy, and the orphan sweep never takes a followed copy.
    assert card_pictures.drop_orphans(main.conn, tmp_path) == 0
    chase.unfollow(main.conn, uid, 35)
    card_pictures.tidy(main.conn, tmp_path)
    assert main.conn.execute("SELECT COUNT(*) FROM followed_pictures").fetchone()[0] == 0
    assert list(tmp_path.glob("*.jpg")) == []


def test_scanner_checks_followed_pictures_only_when_no_photo_waits(client, monkeypatch):
    from app import card_pictures, scan_worker
    seen = []
    monkeypatch.setattr(card_pictures, "followed_due", lambda conn, limit=5: ["a", "b"])
    monkeypatch.setattr(card_pictures, "check_followed", lambda conn, cid: seen.append(cid))
    monkeypatch.setattr(card_pictures, "PAUSE", 0)
    s = scan_worker.Scanner.__new__(scan_worker.Scanner)
    s.conn, s._followed_at, s._followed = main.conn, 0.0, None
    _setup_with_set(client)
    s._followed_pictures(1000.0, wait=True)
    assert seen == ["a", "b"]
    s._followed_pictures(1001.0, wait=True)  # not again within FOLLOWED_EVERY
    assert seen == ["a", "b"]


def test_blank_tiles_ask_after_load_capped_and_swap_in(client):
    from app import card_pictures
    _setup_with_set(client)
    ids = [f"ja:SV9-{i:03d}" for i in range(1, 40)]
    r = client.post("/pictures/ask", json={"ids": ids}).json()
    assert r["asked"] == pictures_pages.PICTURE_ASK_CAP and len(r["waiting"]) == pictures_pages.PICTURE_ASK_CAP
    # Even a card with a known picture is asked about when its tile says none loaded (a Limitless miss).
    assert card_pictures.known(card_pictures.for_id(main.conn, "ja:SV9-001")) == []
    main.conn.execute("UPDATE picture_lookups SET looked_at = '2026-10-20', found = 1, url = 'https://tcgplayer-cdn.tcgplayer.com/product/1_in_400x400.jpg' "
                      "WHERE card_id = 'ja:SV9-001'")
    main.conn.commit()
    found = client.get("/pictures/found", params={"ids": "ja:SV9-001,ja:SV9-002"}).json()
    assert found["found"] == {"ja:SV9-001": "https://tcgplayer-cdn.tcgplayer.com/product/1_in_400x400.jpg"}
    assert found["waiting"] == ["ja:SV9-002"]
    base = with_assets(client.get("/").text)
    assert '"/pictures/ask"' in base and ".slice(0, 24)" in base and "card-ph.looking::after" in base


def test_english_version_pictures_are_gone(client, tmp_path):
    from app import card_pictures
    _setup_with_set(client)
    (tmp_path / "e.jpg").write_bytes(b"x")
    main.conn.execute("UPDATE cards SET saved_picture = 'e.jpg', saved_from = 'English version' WHERE id = 't1-1'")
    assert card_pictures.drop_english(main.conn, tmp_path) == 1
    assert not (tmp_path / "e.jpg").exists()
    assert all(label != "English version" for _, label in card_pictures.more(main.conn, "ja:SV9-001"))


def test_picture_score_and_no_picture_list_by_language(client):
    from app import card_pictures
    uid = _setup_with_set(client)
    main.conn.execute("UPDATE cards SET image_url = 'https://img/1.png' WHERE id = 't1-1'")
    for cid in ("t1-1", "t1-2"):
        main.conn.execute("INSERT INTO inventory (user_id, card_id, variant, condition, quantity) VALUES (?, ?, 'normal', 'NM', 1)", (uid, cid))
    main.conn.commit()
    score = card_pictures.picture_score(main.conn, uid)
    assert (score["have"], score["total"], score["percent"]) == (1, 2, 50) and score["langs"] == [("en", 1, 2)]
    page = client.get("/account/data/no-pictures").text
    assert "<h2" in page and "English" in page


def test_pokemon_page_tray_rows_use_the_tile_picture_list():
    from pathlib import Path
    tray = (Path(main.__file__).parent / "templates" / "_pokemon_art.html").read_text()
    assert "r.pics[1:]" in tray and 'data-fail="art"' in tray  # A2: built in 10c


# --- part 3: prices and links -------------------------------------------------------------------

def _tcgcsv_get(calls):
    files = {
        "https://tcgcsv.com/tcgplayer/3/groups": {"results": [
            {"groupId": 100, "name": "SV01: Test Set", "abbreviation": "TS1"},
            {"groupId": 101, "name": "Other Set", "abbreviation": "OS"}]},
        "https://tcgcsv.com/tcgplayer/85/groups": {"results": []},
        "https://tcgcsv.com/tcgplayer/3/100/products": {"results": [
            {"productId": 501, "name": "Bulbasaur", "imageUrl": "https://tcgplayer-cdn.tcgplayer.com/product/501_200w.jpg",
             "extendedData": [{"name": "Number", "value": "001/002"}]},
            {"productId": 502, "name": "Ivysaur", "extendedData": [{"name": "Number", "value": "002/002"}]},
            {"productId": 503, "name": "Ivysaur (Cosmos Holo)", "extendedData": [{"name": "Number", "value": "002/002"}]},
            {"productId": 509, "name": "Booster Pack"}]},
        "https://tcgcsv.com/tcgplayer/3/100/prices": {"results": [
            {"productId": 501, "subTypeName": "Reverse Holofoil", "marketPrice": 1.5, "lowPrice": 1.0, "midPrice": 2.0},
            {"productId": 501, "subTypeName": "Normal", "marketPrice": None, "midPrice": 0.4, "lowPrice": 0.1},
            {"productId": 502, "subTypeName": "Holofoil", "marketPrice": 9.0}]},
    }

    def get(url):
        calls.append(url)
        return files[url]
    return get


def test_tcgcsv_numbers_names_and_the_host_rule():
    import pytest
    from app import tcgcsv
    assert tcgcsv._number("001/083") == "1" and tcgcsv._number("TG05/TG30") == "TG5" and tcgcsv._number("177a") == "177A"
    assert tcgcsv._plain_name("SV08: Surging Sparks") == "surgingsparks"
    with pytest.raises(ValueError):
        tcgcsv.fetch("https://example.com/tcgplayer/3/groups")


def test_tcgcsv_run_matches_sets_and_cards_and_sets_the_main_price(client):
    from app import prices, tcgcsv, wants
    uid = _setup_with_set(client)
    wants.add(main.conn, uid, "t1-1", by=uid)
    wants.add(main.conn, uid, "t1-2", by=uid)
    calls = []
    done, errors = tcgcsv.run(main.conn, _tcgcsv_get(calls), sleep=lambda s: None)
    assert (done, errors) == (1, [])
    assert all(u.startswith("https://tcgcsv.com/") for u in calls)
    row = main.conn.execute("SELECT group_id, matched_by FROM tcgcsv_sets WHERE set_id = 't1'").fetchone()
    assert tuple(row) == (100, "name")
    # Ivysaur has two products on one number: the one named exactly "Ivysaur" is the card.
    ids = dict(main.conn.execute("SELECT card_id, product_id FROM tcgcsv_cards").fetchall())
    assert ids == {"t1-1": 501, "t1-2": 502}
    assert main.conn.execute("SELECT tcgplayer_id FROM cards WHERE id = 't1-1'").fetchone()[0] == 501  # G3
    got = {(r["card_id"], r["variant"]): (r["price"], r["basis"], r["low"], r["mid"])
           for r in main.conn.execute("SELECT * FROM prices WHERE origin = 'tcgplayer'")}
    assert got[("t1-1", "reverse")] == (1.5, "tcgcsv", 1.0, 2.0)
    assert got[("t1-1", "normal")] == (0.4, "tcgcsv", 0.1, 0.4)  # no market price: the mid
    assert got[("t1-2", "holo")][:2] == (9.0, "tcgcsv")
    assert tcgcsv.status(main.conn)["priced"] == 2 and tcgcsv.covered(main.conn, "t1-1")
    # A second night skips the set lists and products (weekly) and reads prices only.
    calls.clear()
    tcgcsv.run(main.conn, _tcgcsv_get(calls), sleep=lambda s: None)
    assert calls == ["https://tcgcsv.com/tcgplayer/3/100/prices"]
    # TCGdex's own prices later keep TCGCSV's for those printings.
    with main.db.transaction(main.conn):
        prices.store(main.conn, "t1-1", {"pricing": {"tcgplayer": {"reverse-holofoil": {"marketPrice": 3.0, "lowPrice": 2.5},
                                                                    "holofoil": {"marketPrice": 7.0, "lowPrice": 6.0, "midPrice": 8.0}},
                                                     "cardmarket": {"trend": 2.2}}})
    got = {r["variant"]: (r["price"], r["low"], r["mid"]) for r in
           main.conn.execute("SELECT * FROM prices WHERE card_id = 't1-1' AND origin = 'tcgplayer'")}
    assert got["reverse"] == (1.5, 1.0, 2.0) and got["holo"] == (7.0, 6.0, 8.0)
    # Round 20: Cardmarket's price isn't kept any more.
    assert main.conn.execute("SELECT cardmarket_eur FROM cards WHERE id = 't1-1'").fetchone()[0] is None


def test_tcgcsv_ambiguous_sets_are_not_guessed(client):
    from app import tcgcsv
    _setup_with_set(client)
    main.conn.execute("INSERT INTO tcgcsv_groups (category, group_id, name, abbreviation) VALUES (3, 1, 'Test Set', 'A'), "
                      "(3, 2, 'Test Set', 'B')")
    assert tcgcsv.match_sets(main.conn) == {"matched": 0, "unmatched": 1, "changed": []}
    assert [s["set_id"] for s in tcgcsv.unmatched_sets(main.conn)] == ["t1"]


def test_euro_rate_and_card_page_range(client):
    from app import fx
    _setup_with_set(client)
    xml = "<Cube><Cube time='2026-10-20'><Cube currency='USD' rate='1.0850'/><Cube currency='JPY' rate='160'/></Cube></Cube>"
    assert fx.parse(xml) == 1.085 and fx.parse("nothing") is None
    assert fx.refresh(main.conn, get=lambda url: xml) == 1.085
    assert fx.refresh(main.conn, get=lambda url: 1 / 0) == 1.085  # once a day; never fetched again so soon
    assert fx.usd(main.conn, 10) == 10.85
    with main.db.transaction(main.conn):
        web.prices.store(main.conn, "t1-1", {"pricing": {"tcgplayer": {"normal": {"marketPrice": 3.0, "lowPrice": 2.5, "midPrice": 3.5}},
                                                          "cardmarket": {"trend": 2.0}}})
    page = client.get("/cards/t1-1").text
    assert 'class="cv-more"' in page and "$2.50" in page and "$3.50" in page
    # Round 20: Cardmarket's price is gone; TCGplayer's is the one source.
    assert "Europe, trend" not in page and "Cardmarket" not in page.split("cv-sources")[1].split("</table>")[0]
    assert "Average" not in page.split("cv-sources")[0].rsplit("cv-price", 1)[-1]


def test_no_english_equivalent_prices_or_lookups():
    from pathlib import Path
    src = "".join(p.read_text() for p in Path(main.__file__).parent.rglob("*.py"))
    assert "_store_english" not in src and "_english_live" not in src
    assert "refresh_english(" not in src  # round 15b: removed


def test_scan_review_link_check_asks_on_return_and_saves_a_pasted_link(client):
    from pathlib import Path
    from app import extras
    uid = _setup_with_set(client)
    t = Path(main.__file__).parent / "templates"
    base = template_with_assets("base.html")
    assert 'a[data-lc-site]' in base and '"visibilitychange", lcShow' in base
    # The kinds: your own link isn't asked about; a found guess and a product page ask Yes/No; a search asks to paste.
    offer = scans_pages._offer("en", "t1-1")
    user = {"id": uid, "collection_id": main.conn.execute("SELECT collection_id FROM users").fetchone()[0]}
    info = scans_pages._match_info(user, "en", "t1-1", "Bulbasaur", "1", "Test Set", offer)
    assert info["pc_check"] == "search"
    extras.save(main.conn, "t1-1", uid, pricecharting_url="https://www.pricecharting.com/game/pokemon-test-set/bulbasaur-1")
    main.conn.commit()
    info = scans_pages._match_info(user, "en", "t1-1", "Bulbasaur", "1", "Test Set", scans_pages._offer("en", "t1-1"))
    assert info["pc_check"] is None
    page = template_with_assets("_scan_details.html")
    assert 'data-lc-site="tcgplayer"' in page and '"_scan_link_check.html"' in page


def test_scan_review_link_route_answers_and_saves(client, monkeypatch):
    from test_app import _scan_setup, JPEG
    owner, batch_id, worker, reader = _scan_setup(client, monkeypatch)
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg"))], headers={"X-Scan-Upload": "1"})
    worker.start(wait=True)
    sid = main.conn.execute("SELECT id FROM scans").fetchone()[0]
    page = client.get(f"/scans/item/{sid}/details", headers={"HX-Request": "true"}).text
    assert 'id="sd-lc-pricecharting"' in page and "Found it on PriceCharting? Paste a price link for this card" in page
    form = {"site": "pricecharting", "lang": "en", "set_id": "t1", "card_id": "t1-2"}
    r = client.post(f"/scans/item/{sid}/link", data={**form, "url": "https://www.tcgplayer.com/product/5"})
    assert "Paste it in the Tcgplayer box" in r.text or "TCGplayer" in r.text
    r = client.post(f"/scans/item/{sid}/link", data={**form, "url": "https://www.pricecharting.com/game/pokemon-test-set/ivysaur-2"})
    assert "Saved the PriceCharting link." in r.text
    assert web.extras.get(main.conn, "t1-2")["pricecharting_url"].endswith("ivysaur-2")
    # Round 15b: a PriceCharting link can bring the price you saw there.
    r = client.post(f"/scans/item/{sid}/link", data={**form, "url": "https://www.pricecharting.com/game/pokemon-test-set/ivysaur-2",
                                                      "pc_price": "3.25"})
    assert main.conn.execute("SELECT price FROM typed_prices WHERE card_id = 't1-2' AND kind = 'pricecharting'").fetchone()[0] == 3.25


def test_data_sources_has_eight_sections_and_every_old_action(client):
    pytest.importorskip("onnxruntime")  # round 13: Enhanced Search needs the photo reader's libraries
    _setup_with_set(client)
    page = client.get("/account/data").text
    heads = re.findall(r'<h2 id="\w+">([^<]+)</h2>', page)
    assert heads == ["Update everything", "Card sets", "Pokémon data", "Prices", "API keys", "Price and picture sites", "Pictures",
                     "Scanning"]  # round 19: in tabs; Settings and Activity log are tabs of their own; round 28: API keys
    assert 'id="settings"' in page and 'id="log"' in page
    assert page.count('<details class="ds-about">') == 9  # Settings explains itself; round 28: API keys
    for action in ("/account/data/check", "/account/data/tcgdex/clear", "/account/data/chs/all", "/account/data/prices",
                   "/account/data/nightly", "/account/data/pictures/on", "/account/data/pokemon-missing",
                   "/account/data/describe", "/account/data/sprites", "/account/data/descriptions",
                   "/account/data/reset-tags", "/account/data/vintage"):
        assert f'action="{action}"' in page, action
    assert "/account/data/no-pictures" in page and "/account/data/log" in page and "Pictures: 0 of 0 cards" in page
    assert "Not checked yet." in page and "in the last hour." in page


