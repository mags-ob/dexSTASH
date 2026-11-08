"""Round 11b: shared binder pictures, value over time, Safari panel, camera, heart spinner, Find pictures,
Sort on set pages, no Paste buttons."""
from conftest import template_with_assets, with_assets  # noqa: E402

import re

from app import card_pictures, main
from app import web
from app.routes import shared as shared_pages
from test_app import _setup_with_set, client  # noqa: F401  (the client fixture)
from test_round8 import _shared_binder, _visitor


# --- 1. shared binder pictures -------------------------------------------------------------------

def _pass(token, code):
    v = _visitor()
    v.post(f"/b/{token}/code", data={"code": code})
    return v


def test_shared_binder_shows_chinese_dataset_pictures(client, monkeypatch):
    user_id, a, b, token, code = _shared_binder(client)
    v = _pass(token, code)
    main.conn.execute("UPDATE cards SET image_url = '/chs-img/7/141' WHERE id = 't1-1'")
    main.conn.commit()
    asked = []
    monkeypatch.setattr(web.chinese, "picture", lambda cid, n, **kw: asked.append((cid, n)) or b"\xff\xd8chs")
    pics = [u for u, _ in shared_pages._public_pictures(token, "t1-1")]
    assert pics[0] == f"/b/{token}/picture/t1-1?chs=1" and not any(u.startswith("/chs-img") for u in pics)
    r = v.get(f"/b/{token}/picture/t1-1?chs=1")
    assert r.status_code == 200 and r.content == b"\xff\xd8chs" and asked == [("7", "141")]
    assert v.get(f"/b/{token}/picture/t1-2?chs=1").status_code == 404  # not in this binder
    assert _visitor().get(f"/b/{token}/picture/t1-1?chs=1").status_code == 404  # no pass
    # Its saved copy (copied from /chs-img) is public too.
    main.conn.execute("UPDATE cards SET saved_picture = 'c.jpg', saved_from = '', saved_src = '/chs-img/7/141' WHERE id = 't1-1'")
    assert not shared_pages._private_saved(main.conn.execute("SELECT * FROM cards WHERE id = 't1-1'").fetchone())
    assert shared_pages._public_pictures(token, "t1-1")[0][0] == f"/b/{token}/picture/t1-1"


def test_shared_binder_never_shows_photos_or_scans(client):
    user_id, a, b, token, code = _shared_binder(client)
    for src, label in (("/card-photos/x.jpg", ""), ("https://example.com/a.jpg", "Your photo"), ("scan", card_pictures.SCAN), (None, "")):
        main.conn.execute("UPDATE cards SET saved_picture = 'abc.jpg', saved_from = ?, saved_src = ? WHERE id = 't1-1'", (label, src))
        assert shared_pages._private_saved(main.conn.execute("SELECT * FROM cards WHERE id = 't1-1'").fetchone()), (src, label)
    web.extras.save(main.conn, "t1-1", user_id, photo="/card-photos/mine.jpg")
    pics = [u for u, _ in shared_pages._public_pictures(token, "t1-1")]
    assert pics == [f"/b/{token}/picture/t1-1?sprite=1"]


def test_shared_binder_uses_the_same_sources_as_the_app(client):
    user_id, a, b, token, code = _shared_binder(client)
    main.conn.execute("INSERT OR REPLACE INTO picture_lookups (card_id, asked_at, looked_at, found, url, source) "
                      "VALUES ('t1-1', '2026-10-20', '2026-10-20', 1, 'https://tcgplayer-cdn.tcgplayer.com/product/9_200w.jpg', 'TCGplayer picture')")
    main.conn.commit()
    pics = [u for u, _ in shared_pages._public_pictures(token, "t1-1")]
    assert "https://tcgplayer-cdn.tcgplayer.com/product/9_200w.jpg" in pics


def test_shared_preview_uses_the_large_limitless_picture():
    pics = card_pictures.large([(card_pictures.limitless("ja:SV8-1"), "")])
    assert pics[0][0].endswith("_R_JP_LG.png")
    from pathlib import Path
    assert "card_pictures.large(_public_pictures(" in (Path(main.__file__).parent / "routes" / "shared.py").read_text()


# --- 5. heart spinner and 8. no Paste ------------------------------------------------------------

def test_icon_buttons_swap_the_icon_for_the_spinner(client):
    _setup_with_set(client)
    base = with_assets(client.get("/").text)
    assert ".ds-working.ds-icon-only > svg { visibility:hidden; }" in base
    assert 'btn.classList.add("ds-icon-only")' in base and 'querySelectorAll("button")' in base


def test_no_paste_buttons(client):
    _setup_with_set(client)
    page = client.get("/cards/t1-1").text
    assert "data-paste" not in page and "paste-btn" not in page and 'name="link"' in page


# --- 3. Safari panel and 4. camera -----------------------------------------------------------------

def test_scan_panel_rows_scroll_with_a_mouse_and_no_gap(client):
    _setup_with_set(client)
    base = with_assets(client.get("/").text)
    assert 'window.matchMedia("(pointer: fine)")' in base and '".ts-row, .sd-others"' in base and "ds-arrow" in base
    assert "width:min(460px, 100%); max-width:100%;" in base and "width:min(460px, 100vw)" not in base


def test_camera_failures_explain_log_and_fall_back(client, caplog):
    from pathlib import Path
    _setup_with_set(client)
    page = template_with_assets("scan_batch.html")
    assert "Take photos with the phone camera" in page and "Try the live camera again" in page
    assert "Settings &rsaquo; Apps &rsaquo; Safari &rsaquo; Camera &rsaquo; Allow" in page
    assert 'BlackVideo' in page and "await playing()" in page
    assert 'cam.querySelector(".cam-guide").hidden = true;  // shown once the picture plays' in page
    with caplog.at_level("WARNING", logger="dexstash"):
        r = client.post("/scans/camera-problem", json={"name": "NotAllowedError<script>", "detail": "Permission denied"},
                        headers={"User-Agent": "Mozilla/5.0 (iPhone)"})
    assert r.status_code == 204
    assert any("NotAllowedErrorscript Permission denied | Mozilla/5.0 (iPhone)" in m for m in caplog.messages)


# --- 2. value over time ---------------------------------------------------------------------------

def _snap(card_id, variant, price, days_ago, source="tcgplayer", basis=None, today=None):
    from datetime import timedelta
    from test_round8 import TODAY
    main.conn.execute("INSERT INTO price_snapshots (card_id, variant, source, price, captured_at, basis) VALUES (?, ?, ?, ?, ?, ?)",
                      (card_id, variant, source, price, f"{((today or TODAY) - timedelta(days=days_ago)).isoformat()} 08:00:00", basis))


def test_switching_to_tcgcsv_is_not_a_move(client):
    from app import value
    from test_round8 import TODAY, _own, _price_now
    user_id = _setup_with_set(client)
    _own(client, "1", "2")
    main.conn.execute("DELETE FROM price_snapshots")
    # t1-1: TCGdex said 10 then 12; TCGCSV starts at 6 (its own scale) and moves to 6.6: a 10% rise.
    _snap("t1-1", "normal", 10.0, 20)
    _snap("t1-1", "normal", 12.0, 10)
    _snap("t1-1", "normal", 6.0, 3, basis="tcgcsv")
    _snap("t1-1", "normal", 6.6, 1, basis="tcgcsv")
    _price_now("t1-1", "normal", 6.6)
    main.conn.commit()
    m = value.movers(main.conn, user_id, "1m", today=TODAY)
    [row] = m["up"]
    assert row["card_id"] == "t1-1" and row["then"] == 5.0 and row["now"] == 6.6  # 10 scaled by 6/12, not a fall from 10
    m = value.movers(main.conn, user_id, "1w", today=TODAY)
    assert [r["then"] for r in m["up"]] == [6.0] and not m["down"]
    points = [v for _, v in value.series(main.conn, user_id, "1m", today=TODAY)["points"]]
    assert set(points) <= {5.0, 6.0, 6.6} and max(points) == 6.6  # 12 rebased to 6: no step down at the switch


def test_the_first_tcgcsv_rows_are_marked_by_date(client):
    from app import db
    _setup_with_set(client)
    main.conn.execute("DELETE FROM price_snapshots")
    main.conn.execute("INSERT INTO app_meta (key, value) VALUES ('tcgcsv_groups_at', '2026-10-20T03:00:00+00:00') "
                      "ON CONFLICT (key) DO UPDATE SET value = excluded.value")
    main.conn.execute("DELETE FROM app_meta WHERE key = 'tcgcsv_basis_marked'")
    main.conn.execute("INSERT INTO tcgcsv_prices (card_id, variant, market, updated_at) VALUES ('t1-1', 'normal', 6, '2026-10-20')")
    main.conn.execute("INSERT INTO price_snapshots (card_id, variant, source, price, captured_at) VALUES "
                      "('t1-1', 'normal', 'tcgplayer', 12, '2026-10-18T03:00:00'), ('t1-1', 'normal', 'tcgplayer', 6, '2026-10-20T03:05:00'), "
                      "('t1-2', 'normal', 'tcgplayer', 3, '2026-10-20T03:05:00')")
    main.conn.commit()
    db._migrate(main.conn)
    got = [tuple(r) for r in main.conn.execute("SELECT card_id, price, basis FROM price_snapshots ORDER BY card_id, captured_at")]
    assert got == [("t1-1", 12.0, None), ("t1-1", 6.0, "tcgcsv"), ("t1-2", 3.0, None)]
    main.conn.execute("DELETE FROM tcgcsv_prices")
    main.conn.execute("DELETE FROM app_meta WHERE key LIKE 'tcgcsv%'")
    main.conn.commit()


def test_stale_prices_are_flagged(client):
    from datetime import date
    from app import value
    from test_round8 import _own
    user_id = _setup_with_set(client)
    _own(client, "1")
    main.conn.execute("DELETE FROM price_snapshots")
    main.conn.execute("DELETE FROM price_runs")
    today = date(2026, 10, 20)
    _snap("t1-1", "normal", 5.0, 5, today=today)
    for d in ("2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19"):
        main.conn.execute("INSERT INTO price_runs (ran_at) VALUES (?)", (f"{d}T08:00:00Z",))
    main.conn.commit()
    f = value.freshness(main.conn, user_id, today)
    assert f == {"last": "2026-10-15", "stale": True, "runs": 4}
    _snap("t1-1", "normal", 5.5, 1, today=today)
    assert value.freshness(main.conn, user_id, today)["stale"] is False


def test_link_priced_cards_are_movers(client):
    from app import value
    from test_round8 import TODAY, _own
    user_id = _setup_with_set(client)
    _own(client, "1")
    main.conn.execute("DELETE FROM price_snapshots")
    main.conn.execute("DELETE FROM prices")
    main.conn.execute("INSERT INTO prices (card_id, variant, origin, price, updated_at) "
                      f"VALUES ('t1-1', 'normal', '{web.prices.PRICECHARTING}', 9.0, '2026-10-14')")
    _snap("t1-1", "normal", 6.0, 20, source="link")
    _snap("t1-1", "normal", 9.0, 2, source="link")
    main.conn.commit()
    m = value.movers(main.conn, user_id, "1m", today=TODAY)
    assert [(r["card_id"], r["then"], r["now"]) for r in m["up"]] == [("t1-1", 6.0, 9.0)]


# --- 7. Sort on set pages ---------------------------------------------------------------------------

def test_set_sort_value_number_name_and_remembered(client):
    uid = _setup_with_set(client)
    main.conn.execute("INSERT INTO prices (card_id, variant, origin, price, updated_at) VALUES "
                      "('t1-2', 'normal', 'tcgplayer', 4.0, '2026-10-20'), ('t1-2', 'holo', 'tcgplayer', 9.0, '2026-10-20'), "
                      "('t1-1', 'normal', 'tcgplayer', 5.0, '2026-10-20')")
    main.conn.commit()
    order = lambda page: [m for m in re.findall(r'href="/cards/(t1-\w+)"', page) if m.startswith("t1-")]  # noqa: E731
    page = client.get("/sets/t1").text
    assert "Popular" not in page and 'aria-current="true">Value</a>' in page
    assert list(dict.fromkeys(order(page))) == ["t1-1", "t1-2", "t1-TG01"]  # $5, $4 (round 27: the price shown, Normal, not the dearest printing), no price
    page = client.get("/sets/t1", params={"sort": "name"}).text
    assert list(dict.fromkeys(order(page))) == ["t1-1", "t1-2", "t1-TG01"]  # Bulbasaur, Ivysaur, Venusaur
    page = client.get("/sets/t1").text  # remembered
    assert 'aria-current="true">Name</a>' in page
    page = client.get("/sets/t1", params={"sort": "number"}).text
    assert list(dict.fromkeys(order(page))) == ["t1-1", "t1-2", "t1-TG01"]
    client.get("/sets/t1", params={"sort": "value"})


def test_set_values_come_from_tcgcsv_for_a_set_nobody_keeps(client, monkeypatch):
    from app import tcgcsv
    _setup_with_set(client)
    main.conn.execute("INSERT INTO tcgcsv_groups (category, group_id, name, abbreviation) VALUES (3, 300, 'Far Set', 'FAR')")
    main.conn.execute("INSERT INTO tcgcsv_group_reads (group_id, category, products_at, prices_at) VALUES (300, 3, '2099-01-01', '2099-01-01')")
    main.conn.execute("INSERT INTO tcgcsv_products (category, group_id, product_id, number, name, market) VALUES "
                      "(3, 300, 1, '1', 'A', 2.0), (3, 300, 2, '2', 'B', 30.0), (3, 300, 3, '2', 'B (Holo)', 40.0)")
    main.conn.commit()
    started = []
    monkeypatch.setattr(web, "_read_set_later", lambda *a: started.append(a))
    assert web._set_values("en", "far", "Far Set") == {"1": 2.0, "2": 30.0}  # round 27: the card's own product, not the dearest
    assert started == []
    assert web._set_values("en", "nowhere", "Nowhere") == {} and started == [("en", "nowhere", "Nowhere")]
    assert tcgcsv.group_for(main.conn, "ja", "far") is None  # another category


def test_set_preview_has_sort(client, monkeypatch):
    _setup_with_set(client)
    page = (__import__("pathlib").Path(main.__file__).parent / "templates" / "set_preview.html").read_text()
    assert 'set_grid_view(grid, request.query_params.get("rarity", ""), set_sort)' in page and "set_values.get(" in page


# --- 6. Find grid pictures ---------------------------------------------------------------------------

def test_lookup_finds_a_tcgcsv_product_picture_for_a_set_nobody_keeps(client, monkeypatch):
    from app import tcgcsv
    from test_round9 import PairAPI
    _setup_with_set(client)
    monkeypatch.setenv("DEXSTASH_TCGCSV", "1")
    calls = []
    files = {
        "https://tcgcsv.com/tcgplayer/3/groups": {"results": [{"groupId": 610, "name": "Trainer Kit: Lycanroc", "abbreviation": "TK-HS-R"}]},
        "https://tcgcsv.com/tcgplayer/85/groups": {"results": []},
        "https://tcgcsv.com/tcgplayer/3/610/products": {"results": [
            {"productId": 7001, "name": "Professor Kukui", "imageUrl": "https://tcgplayer-cdn.tcgplayer.com/product/7001_200w.jpg",
             "extendedData": [{"name": "Number", "value": "16/30"}]}]},
        "https://tcgcsv.com/tcgplayer/3/610/prices": {"results": [{"productId": 7001, "subTypeName": "Normal", "marketPrice": 0.5}]},
    }
    monkeypatch.setattr(tcgcsv, "fetch", lambda url: calls.append(url) or files[url])
    main.conn.execute("DELETE FROM app_meta WHERE key = 'tcgcsv_groups_at'")
    main.conn.commit()
    card_pictures.ask(main.conn, ["tk-hs-r-16"])
    apis = {"en": PairAPI({"tk-hs-r-16": {"name": "Professor Kukui", "localId": "16", "set": {"name": "HS—Lycanroc"}}})}
    found = card_pictures.lookup(main.conn, "tk-hs-r-16", apis.get, lambda name, lang: name, pause=0)
    assert found == ("https://tcgplayer-cdn.tcgplayer.com/product/7001_200w.jpg", "TCGplayer picture")
    assert all(u.startswith("https://tcgcsv.com/") for u in calls)
    # The set's list is kept: the next card of that set costs nothing.
    calls.clear()
    card_pictures.ask(main.conn, ["tk-hs-r-17"])
    apis["en"].cards["tk-hs-r-17"] = {"name": "x", "localId": "17", "set": {"name": "HS—Lycanroc"}}
    assert card_pictures.lookup(main.conn, "tk-hs-r-17", apis.get, lambda name, lang: name, pause=0) is None and calls == []


def test_mainland_tcgdex_cards_borrow_the_japanese_picture(client):
    from test_round9 import PairAPI
    _setup_with_set(client)
    assert card_pictures.more(main.conn, "zh-cn:SV8a-236")[-1] == (card_pictures.limitless("ja:SV8a-236"), "Japanese version")
    card_pictures.ask(main.conn, ["zh-cn:SV8a-236"])
    apis = {"zh-cn": PairAPI({"SV8a-236": {"name": "x", "localId": "236"}}),
            "ja": PairAPI({"SV8a-236": {"name": "x", "image": "https://assets.tcgdex.net/ja/SV/SV8a/236"}})}
    found = card_pictures.lookup(main.conn, "zh-cn:SV8a-236", apis.get, lambda name, lang: None, pause=0)
    assert found == ("https://assets.tcgdex.net/ja/SV/SV8a/236/low.webp", "Japanese version")
    # A dataset card (its own picture and set codes) has no Japanese pair.
    main.conn.execute("INSERT OR IGNORE INTO sets (set_id, language, name, printed_total, total) VALUES ('zh-cn:CSV7C', 'zh-cn', 'x', 9, 9)")
    main.conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name, image_url, source) "
                      "VALUES ('zh-cn:CSV7C-141', 'zh-cn:CSV7C', '141', 141, 1, 'x', '/chs-img/7/141', 'chs')")
    assert card_pictures.more(main.conn, "zh-cn:CSV7C-141") == []


def test_one_failing_lookup_does_not_stop_the_queue(client, monkeypatch):
    _setup_with_set(client)
    card_pictures.ask(main.conn, ["bad-1", "good-1"])
    seen = []

    def lookup(conn, card_id, *a, **k):
        seen.append(card_id)
        if card_id == "bad-1":
            raise RuntimeError("boom")
        conn.execute("UPDATE picture_lookups SET looked_at = '2026-10-20', found = 0 WHERE card_id = ?", (card_id,))
        conn.commit()
    monkeypatch.setattr(card_pictures, "lookup", lookup)
    worker = card_pictures.LookupWorker(lambda lang: None, lambda n, l: None, pause=0)
    worker.poke(wait=True)
    assert sorted(seen) == ["bad-1", "good-1"]
    assert main.conn.execute("SELECT COUNT(*) FROM picture_lookups WHERE looked_at IS NULL").fetchone()[0] == 0
