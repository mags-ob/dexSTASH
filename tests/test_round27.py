"""Round 27 (0.27.0): prices in scan review before Add, smarter Search links, English names beside Japanese and
Chinese set names, chase hearts on tiles, Find's grid prices matching the card page, and round 26's test fixes."""
from pathlib import Path

from app import cardtrader, db, english, main, prices, scans, sync, tcgcsv, tile_prices, web
from app.routes import scans as scan_routes
from test_app import JPEG, _entry, _scan_setup, _setup_with_set, client  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


def _tcgcsv_set(group=300, name="Neo Discovery", abbreviation="N2", products=(), printings=()):
    main.conn.execute("INSERT INTO tcgcsv_groups (category, group_id, name, abbreviation) VALUES (3, ?, ?, ?)",
                      (group, name, abbreviation))
    main.conn.execute("INSERT INTO tcgcsv_group_reads (group_id, category, products_at, prices_at) VALUES (?, 3, '2099', '2099')",
                      (group,))
    main.conn.executemany("INSERT INTO tcgcsv_products (category, group_id, product_id, number, name, market) "
                          "VALUES (3, ?, ?, ?, ?, ?)", [(group, *p) for p in products])
    main.conn.executemany("INSERT INTO tcgcsv_product_prices (group_id, product_id, subtype, market) VALUES (?, ?, ?, ?)",
                          [(group, *p) for p in printings])
    main.conn.commit()


# --- 27.5 one tile price ---------------------------------------------------------------------------------------

def test_tyranitar_shows_its_unlimited_price_not_the_1st_editions(client):
    _setup_with_set(client)
    _tcgcsv_set(products=[(12, "12", "Tyranitar", 400.0)], printings=[(12, "1st edition holofoil", 400.0), (12, "holofoil", 148.0)])
    found, waiting = tile_prices.for_cards(main.conn, [{"key": "k", "id": "neo2-12", "lang": "en", "set_id": "neo2",
                                                        "set_name": "Neo Discovery", "number": "12", "english": "Tyranitar"}])
    assert found == {"k": 148.0} and not waiting
    assert tile_prices.set_values(main.conn, (3, 300)) == {"12": 148.0}  # sorting by value uses the price shown


def test_a_kept_card_shows_the_printing_you_have(client):
    uid = _setup_with_set(client)
    main.conn.execute("INSERT INTO prices (card_id, variant, origin, price, updated_at) VALUES "
                      "('t1-2', 'normal', 'tcgplayer', 4.0, '2026-11-01'), ('t1-2', 'reverse', 'tcgplayer', 9.0, '2026-11-01')")
    main.conn.commit()
    card = [{"key": "t1-2", "id": "t1-2"}]
    assert tile_prices.for_cards(main.conn, card)[0] == {"t1-2": 4.0}  # nobody has it: Normal, the main printing
    main.conn.execute("INSERT INTO inventory (user_id, card_id, variant, condition, quantity) VALUES (?, 't1-2', 'reverse', 'NM', 1)", (uid,))
    assert tile_prices.for_cards(main.conn, card, uid)[0] == {"t1-2": 9.0}  # yours: the one you have, as the card page


def test_an_unread_set_waits_and_is_asked_for_once(client, monkeypatch):
    _setup_with_set(client)
    main.conn.execute("INSERT INTO tcgcsv_groups (category, group_id, name, abbreviation) VALUES (3, 301, 'Far Set', 'FAR')")
    main.conn.commit()
    found, waiting = tile_prices.for_cards(main.conn, [{"key": i, "id": f"far-{i}", "lang": "en", "set_id": "far",
                                                        "set_name": "Far Set", "number": str(i)} for i in (1, 2)])
    assert found == {} and waiting == {("en", "far", "Far Set")}


def test_waiting_tiles_fill_in_from_search_prices(client):
    user = {"collection_id": _setup_with_set(client)}
    from app.routes import search as search_routes
    search_routes._waiting_tiles.keep(user, {"en|far-1": {"id": "far-1", "lang": "en", "set_id": "far", "set_name": "Far Set",
                                                           "number": "1", "english": "A"}})
    main.conn.execute("INSERT INTO tcgcsv_groups (category, group_id, name, abbreviation) VALUES (3, 302, 'Far Set', 'FAR')")
    main.conn.commit()
    assert client.get("/search/prices", params={"k": "en|far-1"}).json() == {}  # still being read
    main.conn.execute("INSERT INTO tcgcsv_group_reads (group_id, category, products_at, prices_at) VALUES (302, 3, '2099', '2099')")
    main.conn.execute("INSERT INTO tcgcsv_products (category, group_id, product_id, number, name, market) VALUES (3, 302, 1, '1', 'A', 2.5)")
    main.conn.commit()
    assert client.get("/search/prices", params={"k": "en|far-1"}).json() == {"en|far-1": "$2.50"}
    assert client.get("/search/prices", params={"k": "en|nope"}).json() == {}  # not one of yours


def test_pending_tile_says_checking_price_and_loads_the_script(client):
    _setup_with_set(client)
    macros = (ROOT / "app/templates/_macros.html").read_text()
    assert "Checking price…" in macros and "data-price-pending" in macros
    assert "find-prices.js" in (ROOT / "app/templates/search.html").read_text()
    assert "/search/prices?" in (ROOT / "app/static/js/find-prices.js").read_text()


# --- 27.4 chase hearts ---------------------------------------------------------------------------------------------

def test_a_chased_card_has_a_heart_and_a_rose_ring(client):
    uid = _setup_with_set(client)
    main.conn.execute("INSERT INTO wants (user_id, card_id) VALUES (?, 't1-2')", (uid,))
    main.conn.commit()
    page = client.get("/sets/t1").text
    assert page.count('class="star-ck"') == 1 and "ctile chased" in page
    from app.icons import icon
    assert str(icon("heart-on", 13)) in page and str(icon("star-on", 14)) not in page
    css = (ROOT / "app/static/css/base.css").read_text()
    assert ".ctile.chased .pic > img" in css and "background:var(--chase)" in css


def test_find_marks_chased_results(client, monkeypatch):
    from app.routes import search as search_routes
    uid = _setup_with_set(client)
    main.conn.execute("INSERT INTO wants (user_id, card_id) VALUES (?, 't1-2')", (uid,))
    rows = [{"id": "t1-2", "lang": "en", "set_id": "t1", "number": "2"}, {"id": "t1-1", "lang": "en", "set_id": "t1", "number": "1"}]
    search_routes._price_results(rows, {"collection_id": uid})
    assert [r["chased"] for r in rows] == [True, False]


# --- 27.2 Search links ---------------------------------------------------------------------------------------------

def test_each_site_gets_its_own_words():
    tcg, exact = prices.tcgplayer_link(None, "Pikachu ex", "77", "Twilight Masquerade", "en", printed_total=167)
    assert not exact and tcg.endswith("q=Pikachu+ex+077+Twilight+Masquerade")
    jp, _ = prices.tcgplayer_link(None, "Charizard ex", "201", "Pokemon Card 151", "ja", printed_total=165)
    assert "/pokemon-japan/" in jp and jp.endswith("q=Charizard+ex+201+Pokemon+Card+151")
    assert prices.pricecharting_link("Pikachu ex", "077", "Twilight Masquerade").endswith("q=Pikachu+ex+Twilight+Masquerade+77")
    assert prices.pricecharting_link("Charizard ex", "201", "Pokemon Card 151", "ja").endswith("q=Charizard+ex+Pokemon+Card+151+201+Japanese")
    assert prices.cardtrader_search_link("Iono", "Paldea Evolved") == "https://www.cardtrader.com/en/search?q=Iono+Paldea+Evolved"
    # Japanese or Chinese text never goes in: a set with no English name drops out, the rest stays
    assert prices.pricecharting_link("Pikachu", "51", "补充包 共逐荣光", "zh-cn").endswith("q=Pikachu+51+Chinese")
    assert prices.cardtrader_search_link("ピカチュウ", "") == "https://www.cardtrader.com/en/search?q="
    assert prices.bare_number("TG05") == "TG05" and prices.printed_number("5", 30) == "05"


def test_card_page_and_scan_review_have_a_cardtrader_link(client):
    _setup_with_set(client)
    page = client.get("/cards/t1-2").text
    assert "https://www.cardtrader.com/en/search?q=Ivysaur+Test+Set" in page and "Search CardTrader" in page
    assert "ct_url" in (ROOT / "app/templates/_scan_details.html").read_text()
    assert "links.ct_url" in (ROOT / "app/templates/_public_card.html").read_text()


def test_a_cardtrader_price_links_to_its_card_page(client):
    _setup_with_set(client)
    main.conn.execute("INSERT INTO price_quotes (card_id, variant, source, price, currency, basis, ref, updated_at, url) "
                      "VALUES ('t1-2', '', 'cardtrader', 3.8, 'USD', 'nm', 555, '2026-11-01', 'https://www.cardtrader.com/en/cards/555')")
    main.conn.commit()
    links = web._links({"id": "t1-2", "name": "Ivysaur", "number": "2", "set_name": "Test Set", "set_id": "t1"}, "en", None)
    assert links["ct_url"] == "https://www.cardtrader.com/en/cards/555" and links["ct_exact"]


# --- 27.1 scan prices before Add -----------------------------------------------------------------------------------

def test_a_matched_scan_is_kept_and_priced_in_the_background(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg"))], headers={"X-Scan-Upload": "1"})
    worker.start(wait=True)
    batch = scans.get_batch(main.conn, owner, batch_id)
    assert [m["our"] for m in scans.unpriced_matches(main.conn, batch)] == ["t1-2"]
    ran, refreshed = [], []
    monkeypatch.setattr(web.background, "start", lambda key, label, fn, **kw: ran.append((key, label)) or fn(main.conn, None) or True)
    monkeypatch.setattr(sync, "refresh_sets", lambda conn, api_for, set_ids, **kw: refreshed.append(set(set_ids)) or (0, []))
    scan_routes._asked_prices.clear()
    client.get(f"/scans/{batch_id}")
    assert ran == [(f"scan-prices:{batch_id}", "Prices for scan batch 'batch %d': 1 card, 1 set" % batch_id)] \
        or ran[0][0] == f"scan-prices:{batch_id}"
    assert refreshed == [{"t1"}]
    kept = {r[0] for r in main.conn.execute(prices.KEPT_SQL)}
    assert "t1-2" in kept and scans.unpriced_matches(main.conn, batch) == []
    client.get(f"/scans/{batch_id}")
    assert len(ran) == 1  # once
    scan_id = main.conn.execute("SELECT id FROM scans WHERE batch_id = ?", (batch_id,)).fetchone()[0]
    main.conn.execute("UPDATE scans SET status = 'skipped' WHERE id = ?", (scan_id,))
    assert "t1-2" not in {r[0] for r in main.conn.execute(prices.KEPT_SQL)}  # skipped: no longer kept


def test_nothing_is_priced_while_photos_are_still_being_read(client, monkeypatch):
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    real_start = worker.start
    monkeypatch.setattr(worker, "start", lambda wait=False: None)  # the upload would start reading in the background
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg"))], headers={"X-Scan-Upload": "1"})
    assert scans.unpriced_matches(main.conn, scans.get_batch(main.conn, owner, batch_id)) == []  # still waiting to be read
    real_start(wait=True)
    assert [m["our"] for m in scans.unpriced_matches(main.conn, scans.get_batch(main.conn, owner, batch_id))] == ["t1-2"]


def test_short_run_reads_cardtrader_for_those_sets_only(client, monkeypatch):
    _setup_with_set(client)
    asked = []
    monkeypatch.setattr(cardtrader, "run", lambda conn, sleep=None, only=None, **kw: asked.append(only) or (0, []))
    monkeypatch.setenv("DEXSTASH_TCGCSV", "0")
    sync.refresh_sets(main.conn, lambda lang: web.api, ["t1"])
    assert asked == [{"t1"}]


def test_cardtrader_without_a_token_sends_nothing_and_leaves_the_summary(client, monkeypatch):
    _setup_with_set(client)
    monkeypatch.delenv("DEXSTASH_CARDTRADER_TOKEN", raising=False)
    assert cardtrader.run(main.conn, only={"t1"}) == (0, [])
    assert cardtrader._meta(main.conn, cardtrader.STATUS_KEY) is None


def test_scan_details_lists_each_source(client):
    text = (ROOT / "app/templates/_scan_details.html").read_text()
    assert "info.lines" in text and "Checking CardTrader…" in text and "No price found yet" in text


# --- 27.3 English names ----------------------------------------------------------------------------------------------

def _ja_set(set_id="ja:SV1a", name="強化拡張パック トリプレットビート"):
    main.conn.execute("INSERT OR REPLACE INTO sets (set_id, language, name, printed_total, total) VALUES (?, 'ja', ?, 73, 73)",
                      (set_id, name))


def test_a_japanese_set_gets_tcgcsvs_name_without_its_code(client):
    _setup_with_set(client)
    _ja_set()
    main.conn.execute("INSERT INTO tcgcsv_groups (category, group_id, name, abbreviation) VALUES (85, 900, 'SV1a: Triplet Beat', 'SV1a')")
    english.refresh(main.conn)
    assert main.conn.execute("SELECT english_name FROM sets WHERE set_id = 'ja:SV1a'").fetchone()[0] == "Triplet Beat"
    assert web._set_label("ja:SV1a", "強化拡張パック トリプレットビート") == "Triplet Beat · 強化拡張パック トリプレットビート"
    english.set_shown(main.conn, "english")
    assert web._set_label("ja:SV1a", "強化拡張パック トリプレットビート") == "Triplet Beat"
    english.set_shown(main.conn, "original")
    assert web._set_label("ja:SV1a", "強化拡張パック トリプレットビート") == "強化拡張パック トリプレットビート"
    assert web._set_label("t1", "Test Set") == "Test Set"  # English sets as they are


def test_a_chinese_set_gets_cardtraders_name_and_an_unknown_one_stays(client):
    _setup_with_set(client)
    main.conn.execute("INSERT INTO sets (set_id, language, name, printed_total, total) VALUES ('zh-tw:SV9', 'zh-tw', '對戰搭檔', 100, 100)")
    main.conn.execute("INSERT INTO sets (set_id, language, name, printed_total, total) VALUES ('zh-tw:X1', 'zh-tw', '未知', 10, 10)")
    main.conn.execute("INSERT INTO ct_expansions (id, code, name, fetched_at) VALUES (77, 'sv9tw', 'Battle Partners', '2026')")
    main.conn.execute("INSERT INTO ct_sets (set_id, expansion_id, checked_at) VALUES ('zh-tw:SV9', 77, '2026')")
    assert english.refresh(main.conn) == 1
    assert web._set_label("zh-tw:SV9", "對戰搭檔") == "Battle Partners · 對戰搭檔"
    assert web._set_label("zh-tw:X1", "未知") == "未知"  # never a made-up name


def test_mainland_sets_keep_their_code_names_with_the_original(client):
    _setup_with_set(client)
    assert web._set_label("zh-cn:CBB4C", "宝石包 第四弹") == "Gem Pack 4 · 宝石包 第四弹"


def test_names_setting_on_data_sources(client):
    _setup_with_set(client)
    page = client.get("/account/data").text
    assert "Names on Japanese and Chinese cards" in page and 'value="both" selected' in page
    assert client.post("/account/data/names", data={"how": "nope"}).status_code == 400
    client.post("/account/data/names", data={"how": "english"})
    assert english.shown(main.conn) == "english"


def test_find_finds_a_japanese_set_by_its_english_name(client):
    _setup_with_set(client)
    _ja_set()
    main.conn.execute("UPDATE sets SET english_name = 'Triplet Beat' WHERE set_id = 'ja:SV1a'")
    english.changed()
    assert english.labels(main.conn)["names"]["ja:SV1a"] == "Triplet Beat"


def test_step_19(tmp_path):
    conn = db.connect(tmp_path / "old.db")
    db.init(conn)
    assert db.LATEST == 19 and db.version(conn) == 19
    assert "english_name" in {r[1] for r in conn.execute("PRAGMA main.table_info(sets)")}
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'scan_matches'").fetchone()


# --- round 26 results -----------------------------------------------------------------------------------------------

def test_a_tag_search_shows_only_its_cards(client):
    _setup_with_set(client)
    _entry(client, "t1", "1")
    client.get("/cards/t1-1")  # now in Recently viewed
    assert "recent-views" in client.get("/search").text
    assert "recent-views" not in client.get("/search", params={"tag": "demo"}).text


def test_describe_keeps_its_size(client):
    css = (ROOT / "app/static/css/base.css").read_text()
    assert "dialog.sheet.describe[open] { height:88vh; }" in css


def test_light_mode_gets_ios_light_status_bar(client):
    _setup_with_set(client)
    main.conn.execute("UPDATE users SET mode = 'light'")
    main.conn.commit()
    assert 'apple-mobile-web-app-status-bar-style" content="default"' in client.get("/").text
    assert "background:#1d1d1f; pointer-events:none" not in (ROOT / "app/static/css/base.css").read_text()
