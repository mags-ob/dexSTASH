"""Round 19 (0.19.0)."""
import json

from app import main
from test_app import _entry, _setup_with_set, client  # noqa: F401

XML = "<Cube><Cube time='2026-10-28'><Cube currency='USD' rate='1.2500'/><Cube currency='GBP' rate='0.8000'/></Cube></Cube>"


def _gbp(monkeypatch, xml=XML):
    from app import fx
    fx.refresh(main.conn, get=lambda url: xml, force=True)
    fx.save_currency(main.conn, "GBP")


def test_every_source_is_converted_and_saved_at_the_price_update(client, monkeypatch):
    """Item 9: a price in another currency (round 20: one typed in euros) is converted when prices are worked out,
    and saved with the rate used and in the install's currency; the card page shows only that saved amount."""
    from app import fx, prices
    from test_round15d import _quote
    _setup_with_set(client)
    _entry(client, "t1", "2", "holo")
    main.conn.execute("DELETE FROM price_quotes")
    main.conn.execute("DELETE FROM price_snapshots")
    _quote("t1-2", "holo", "tcgplayer", 10.0)
    main.conn.execute("INSERT INTO typed_prices (card_id, variant, kind, price, currency, typed_at) "
                      "VALUES ('t1-2', 'holo', 'manual', 8.0, 'EUR', '2026-10-28T00:00:00Z')")
    main.conn.execute("DELETE FROM app_meta WHERE key IN ('eur_usd', 'eur_usd_at', 'ecb_rates')")
    fx.load(main.conn)
    with main.db.transaction(main.conn):
        prices.resolve(main.conn, "t1-2")
    row = prices.card_prices(main.conn, "t1-2")[0]
    assert [f["origin"] for f in row["sources"]] == ["tcgplayer"]  # no rate yet: the euro price isn't counted
    try:
        _gbp(monkeypatch)
        with main.db.transaction(main.conn):
            prices.resolve(main.conn, "t1-2")
        row = prices.card_prices(main.conn, "t1-2")[0]
        cm = next(f for f in row["sources"] if f["origin"] == "manual")
        assert cm["price"] == 10.0 and cm["paid"] == {"amount": 8.0, "currency": "EUR", "rate": 1.25}
        assert (row["local_currency"], row["local_rate"], row["price_local"]) == ("GBP", 0.64, 6.4)
        assert cm["local"] == 6.4
        page = client.get("/cards/t1-2").text
        table = page.split("cv-sources")[1].split("</table>")[0]
        assert "£6.40" in table and "€" not in table and "&euro;" not in table and "$" not in table
        # Today's history is saved in pounds too; a later rate doesn't move it.
        hist = main.conn.execute("SELECT local, local_currency FROM price_snapshots WHERE card_id = 't1-2' "
                                 "AND source = 'average'").fetchone()
        assert tuple(hist) == (6.4, "GBP")
        fx.refresh(main.conn, get=lambda url: XML.replace("0.8000", "1.0000"), force=True)
        assert fx.local_rate() == ("GBP", 0.8)
        main.conn.execute("UPDATE price_snapshots SET captured_at = '2026-10-01T00:00:00+00:00'")
        with main.db.transaction(main.conn):
            assert prices.localize_all(main.conn) == 1
        assert prices.card_prices(main.conn, "t1-2")[0]["price_local"] == 8.0  # today's price: tonight's rate
        from app import value
        h = value.History(main.conn, {("t1-2", "holo")})
        assert round(h.at(("t1-2", "holo"), "2026-10-02")[0] * 0.8, 2) == 6.4  # the old day keeps its £6.40
    finally:
        fx.save_currency(main.conn, "USD")


def test_a_currency_change_saves_prices_again_in_the_background(client, monkeypatch):
    from app import fx, prices
    from app.routes import data_sources
    _setup_with_set(client)
    _entry(client, "t1", "1")
    started = []
    monkeypatch.setattr(data_sources, "fetch_rates_soon", lambda fetch=True: started.append(fetch))
    fx.refresh(main.conn, get=lambda url: XML, force=True)
    try:
        client.post("/account/data/currency", data={"currency": "GBP"})
        assert started == [False]  # the rate is already here: only the saved amounts change
    finally:
        fx.save_currency(main.conn, "USD")
    assert "fetch_rates_soon" in open(data_sources.__file__).read() and prices.localize_all


def test_the_nightly_update_fetches_rates_first_and_saves_amounts():
    from pathlib import Path
    src = (Path(main.__file__).parent / "sync.py").read_text()
    body = src[src.index("def refresh_all"):]
    assert body.index("fx.refresh(conn)") < body.index("prices.SOURCES")  # round 20: the sources in their order
    assert body.index("prices.apply_all(conn)") < body.index("prices.localize_all(conn)")
    web_src = (Path(main.__file__).parent / "web.py").read_text()
    assert "fx.usd(" not in web_src and "cardmarket_eur" not in web_src.split("def _price_more")[1].split("\ndef ")[0]


def test_chase_or_add_on_a_preview_replaces_it_in_history():
    """Item 8: a form on /preview/… that leads to the card's page replaces the preview, so Back skips it."""
    from pathlib import Path
    js = (Path(main.__file__).parent / "static" / "js" / "base-nav.js").read_text()
    save = js[js.index("window.dsSave"):js.index('document.addEventListener("submit"')]
    assert 'location.pathname.startsWith("/preview/") && to.pathname.startsWith("/cards/")' in save
    assert "if (fromPreview) { location.replace(resp.url); return; }" in save


def test_select_remove_takes_copies_out_of_the_stash_and_keeps_the_rest(client):
    """Item 7: Remove on the Select bar; chases, notes and history stay, and the history says who removed them."""
    from app import binders, notes, wants
    uid = _setup_with_set(client)
    _entry(client, "t1", "1 x2")
    _entry(client, "t1", "1", "reverse")
    _entry(client, "t1", "2")
    notes.add(main.conn, uid, "t1-1", uid, "keep me")
    wants.add(main.conn, uid, "t1-1", by=uid)
    page = client.get("/collection").text
    assert 'data-open="bulk-remove"' in page and 'data-qty="2"' in page and "data-remove-q" in page
    r = client.post("/collection/bulk", data={"action": "remove", "lines": ["t1-1|normal|NM", "t1-1|reverse|NM"],
                                              "back": "sort=name", "remove_from": "stash"}, follow_redirects=False)
    assert r.status_code == 303 and "sort=name" in r.headers["location"]
    assert "Removed%201%20card%20%283%20copies%29" in r.headers["location"]
    left = [tuple(x) for x in main.conn.execute("SELECT card_id, quantity FROM inventory")]
    assert left == [("t1-2", 1)]
    assert main.conn.execute("SELECT COUNT(*) FROM notes WHERE card_id = 't1-1'").fetchone()[0] == 1
    log = main.conn.execute("SELECT delta, user_id, source FROM inventory_log WHERE card_id = 't1-1' AND delta < 0").fetchall()
    assert sorted(tuple(x) for x in log) == [(-2, uid, "bulk"), (-1, uid, "bulk")]
    # In a binder: only out of the binder keeps the copy, Not in a binder.
    assert main.conn.execute("SELECT COUNT(*) FROM wants WHERE card_id = 't1-1'").fetchone()[0] == 1
    bid = binders.create(main.conn, uid, "Trade", by=uid)
    line = main.conn.execute("SELECT id FROM inventory WHERE card_id = 't1-2'").fetchone()[0]
    binders.move_all(main.conn, uid, [line], bid)
    page = client.get(f"/collection/binders/{bid}?view=grid").text
    assert 'value="binder"' in page and "Only take them out of Trade" in page
    client.post("/collection/bulk", data={"action": "remove", "lines": ["t1-2|normal|NM"], "from_binder": str(bid),
                                          "remove_from": "binder"})
    assert main.conn.execute("SELECT quantity FROM inventory WHERE card_id = 't1-2'").fetchone()[0] == 1
    assert main.conn.execute("SELECT COUNT(*) FROM binder_copies").fetchone()[0] == 0


def test_data_sources_is_in_five_tabs_with_every_section(client):
    """Item 1: the same sections under Cards, Prices, Pictures & scanning, Settings and Activity log."""
    import re
    _setup_with_set(client)
    page = client.get("/account/data").text
    tabs = re.findall(r'data-ds-tab="(\w+)">([^<]+)</button>', page)
    assert tabs == [("cards", "Cards"), ("prices", "Prices"), ("pictures", "Pictures &amp; scanning"),
                    ("settings", "Settings"), ("log", "Activity log")]
    panes = {k: page[page.index(f'data-ds-pane="{k}"'):] for k, _ in tabs}
    for k, ids in {"cards": ("update", "sets", "descriptions"), "prices": ("prices", "cardtrader", "history", "sites"),
                   "pictures": ("pictures", "scanner"), "settings": ("settings", "region", "currency", "vintage", "reset-tags"),
                   "log": ("log",)}.items():
        pane = panes[k].split("data-ds-pane=")[1] if k != "log" else panes[k]
        for i in ids:
            assert f'id="{i}"' in pane.split('<div class="ds-pane"')[0], (k, i)
    assert page.index("ds-tabs") > page.index("<main")  # the running bar stays above the tabs
    assert 'data-ds-pane="prices" hidden' in page and 'data-ds-pane="cards">' in page
    from pathlib import Path
    js = (Path(main.__file__).parent / "static" / "js" / "data-sources.js").read_text()
    assert 'history.replaceState(history.state, "", location.pathname + location.search + "#" + name)' in js
    assert 'target.closest("[data-ds-pane]")' in js


def test_download_my_stash_chases_and_a_binder_as_csv(client):
    """Item 3: one row per printing and condition; a byte order mark for Excel; Collectr's layout reads back in."""
    import csv
    import io
    from app import binders, collectr, wants
    uid = _setup_with_set(client)
    _entry(client, "t1", "1 x2", "reverse", "LP")
    _entry(client, "t1", "2")
    main.conn.execute("UPDATE inventory SET acquired_price = 1.5, note = 'from Misty' WHERE card_id = 't1-1'")
    wants.add(main.conn, uid, "t1-3" if main.conn.execute("SELECT 1 FROM cards WHERE id='t1-3'").fetchone() else "t1-TG01", by=uid)
    main.conn.commit()
    assert 'href="/collection/export.csv"' in client.get("/account").text
    r = client.get("/collection/export.csv")
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="dexstash-stash-' in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert [(x["Name"], x["Printing"], x["Condition"], x["Quantity"]) for x in rows] == [
        ("Bulbasaur", "Reverse Holo", "Lightly Played", "2"), ("Ivysaur", "Normal", "Near Mint", "1")]
    first = rows[0]
    assert (first["Paid each"], first["Paid currency"], first["Copy note"], first["Binder"], first["Added by"]) == (
        "1.50", "USD", "from Misty", "Not in a binder", "misty")
    # Collectr's columns, which the Collectr import reads.
    back = collectr.parse(client.get("/collection/export.csv?format=collectr").content)
    assert [(x.clean_name, x.number, x.variant, x.condition, x.quantity, x.cost) for x in back] == [
        ("Bulbasaur", "1", "reverse", "LP", 2, 1.5), ("Ivysaur", "2", "normal", "NM", 1, None)]
    # One binder: only its copies.
    bid = binders.create(main.conn, uid, "Trade", by=uid)
    line = main.conn.execute("SELECT id FROM inventory WHERE card_id = 't1-2'").fetchone()[0]
    binders.move_all(main.conn, uid, [line], bid)
    rows = list(csv.DictReader(io.StringIO(client.get(f"/collection/binders/{bid}/export.csv").content.decode("utf-8-sig"))))
    assert [(x["Name"], x["Binder"]) for x in rows] == [("Ivysaur", "Trade")]
    assert client.get("/collection/binders/9999/export.csv").status_code == 404
    assert f'/collection/binders/{bid}/export.csv' in client.get(f"/collection/binders/{bid}").text
    # Chases: their own file, no prices paid.
    text = client.get("/chases/export.csv").content.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("Name,English name,Set") and "Paid" not in text and len(text.splitlines()) == 2


def test_home_screen_app_manifest_splash_and_offline_page(client):
    """Item 2: manifest colours and icons, launch images, the offline page and a service worker that stores
    nothing but the offline page's own files."""
    from pathlib import Path
    from app import __version__
    _setup_with_set(client)
    m = client.get("/manifest.webmanifest").json()
    assert (m["id"], m["display"], m["background_color"], m["theme_color"]) == ("/", "standalone", "#111113", "#1c1c1f")
    assert "orientation" not in m and m["description"] and m["categories"]
    assert [s["url"] for s in m["shortcuts"]][-2:] == ["/search", "/collection"]
    page = client.get("/").text
    assert page.count('rel="apple-touch-startup-image"') == 17 and 'sizes="167x167"' in page
    assert '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">' in page
    static = Path(main.__file__).parent / "static"
    for href in __import__("re").findall(r'apple-touch-startup-image" media="[^"]+" href="/static/([^"?]+)', page):
        assert (static / href).exists(), href
    sw = client.get("/sw.js")
    assert sw.headers["content-type"].startswith("application/javascript") and sw.headers["cache-control"] == "no-cache"
    js = sw.text
    assert f'dexstash-offline-{__version__}' in js and 'e.request.mode !== "navigate"' in js
    assert js.count("cache.put") == 0 and "c.addAll(FILES)" in js  # only the offline files are ever stored
    client.cookies.clear()
    off = client.get("/offline")
    assert off.status_code == 200 and "Can't reach your" in off.text and "Try again" in off.text
    js = (static / "js" / "offline.js").read_text()
    assert "setInterval(check, 10000)" in js and 'indexedDB.open("dexstash-photos", 1)' in js
    base = (static / "js" / "base-3.js").read_text()
    assert 'navigator.serviceWorker.register("/sw.js")' in base and '"htmx:sendError"' in base


def test_pages_send_a_strict_content_policy_and_templates_have_no_inline_script(client):
    """Item 4c: only the app's own script files run; a template may not add an inline script or handler."""
    import re
    from pathlib import Path
    _setup_with_set(client)
    policy = client.get("/").headers["content-security-policy"]
    assert "script-src 'self';" in policy and "unsafe-inline" not in policy.split("style-src")[0]
    assert client.get("/login").headers.get("content-security-policy") == policy
    templates = Path(main.__file__).parent / "templates"
    for t in templates.glob("*.html"):
        text = t.read_text()
        assert not re.search(r"<script(?![^>]*\bsrc=)(?![^>]*type=\"application/json\")[^>]*>", text), t.name
        assert not re.search(r"\son[a-z]+\s*=\s*[\"']", text), t.name
    js = (Path(main.__file__).parent / "static" / "js").glob("*.js")
    for f in js:
        assert not re.search(r"""\son(click|submit|change|input|error|load)=["\\]""", f.read_text()), f.name


# --- 4b: one time format ----------------------------------------------------------------------------------------

import re  # noqa: E402
from pathlib import Path  # noqa: E402

APP = Path(__file__).resolve().parent.parent / "app"
STAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


def test_code_writes_times_only_through_clock():
    """A time is stored only as clock.py writes it. New code that makes one another way fails here."""
    wrong = []
    for path in APP.rglob("*.py"):
        if path.name == "clock.py":
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if path.name != "db.py" and "datetime('now')" in line:  # db.py's old steps run before step 14
                wrong.append(f"{path.name}:{n} datetime('now')")
            if re.search(r"datetime\.now\(timezone\.utc\)\.isoformat\(\)|\.isoformat\(timespec=|utcnow\(|strftime\(\"%Y-%m-%d[ T]%H", line):
                wrong.append(f"{path.name}:{n} {line.strip()}")
            if "time.time()" in line and re.search(r"execute|_meta\(", line):
                wrong.append(f"{path.name}:{n} seconds since 1970 saved")
    for path in APP.glob("*.sql"):
        if "datetime('now')" in path.read_text():
            wrong.append(f"{path.name}: DEFAULT (datetime('now'))")
    assert not wrong, wrong


def test_step_14_rewrites_old_times_and_old_defaults_follow(tmp_path):
    from app import db
    conn = db.connect(tmp_path / "dexstash.db")
    conn.executescript((APP / "schema.sql").read_text().replace("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')", "datetime('now')"))
    conn.executescript((APP / "catalog.sql").read_text())
    conn.execute("PRAGMA user_version = 13")
    conn.execute("INSERT INTO users (username, password_hash, created_at) VALUES ('misty', 'x', '2026-10-28 19:56:42')")
    conn.execute("INSERT INTO app_meta (key, value) VALUES ('nightly_last_at', '2026-10-28T19:56:42.123456+00:00')")
    conn.execute("INSERT INTO app_meta (key, value) VALUES ('scanner_seen', '1793217402.5')")
    conn.execute("INSERT INTO app_meta (key, value) VALUES ('a_day', '2026-10-28')")
    conn.execute("CREATE TABLE worker_jobs (id INTEGER PRIMARY KEY, key TEXT NOT NULL, label TEXT NOT NULL, kind TEXT NOT NULL, "
                 "arg TEXT, status TEXT NOT NULL DEFAULT 'done', done INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL "
                 "DEFAULT 0, step TEXT, result TEXT, error TEXT, created_at TEXT NOT NULL, finished_at TEXT)")  # before round 23
    conn.execute("INSERT INTO worker_jobs (key, label, kind, created_at, finished_at) VALUES ('k', 'l', 'recheck', 1793217402, NULL)")
    conn.execute("INSERT INTO tcgdex_cache (url, body, fetched_at) VALUES ('u', '{}', 1793217402.9)")
    db.init(conn)
    assert db.version(conn) == db.LATEST
    meta = dict(conn.execute("SELECT key, value FROM app_meta").fetchall())
    assert meta["nightly_last_at"] == "2026-10-28T19:56:42Z" and meta["scanner_seen"] == "2026-10-28T19:56:42Z"
    assert meta["a_day"] == "2026-10-28"  # a date stays a date
    assert conn.execute("SELECT created_at FROM users").fetchone()[0] == "2026-10-28T19:56:42Z"
    assert conn.execute("SELECT created_at FROM jobs").fetchone()[0] == "2026-10-28T19:56:42Z"  # moved by step 17
    assert conn.execute("SELECT fetched_at FROM tcgdex_cache").fetchone()[0] == "2026-10-28T19:56:42Z"
    # The table keeps its old default; what it fills in is rewritten as it's saved.
    conn.execute("INSERT INTO users (username, password_hash) VALUES ('brock', 'x')")
    assert STAMP.match(conn.execute("SELECT created_at FROM users WHERE username = 'brock'").fetchone()[0])
    db.init(conn)  # a restart changes nothing


def test_every_time_the_app_saved_is_in_one_format(client):
    """After a run through adding cards, chases, tags and prices, every stored time is '2026-10-28T19:56:42Z'."""
    _setup_with_set(client)
    _entry(client, "t1", "2", "holo")
    client.post("/chases", data={"card_id": "t1-3"})
    old_time = re.compile(r"^\d{4}-\d\d-\d\d[ T]\d\d:\d\d")
    wrong = []
    for schema in ("main", "catalog"):
        for (table,) in main.conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table' "
                                          "AND name NOT LIKE 'sqlite_%'").fetchall():
            for row in main.conn.execute(f'SELECT * FROM {schema}."{table}"').fetchall():
                for key in row.keys():
                    v = row[key]
                    if isinstance(v, str) and old_time.match(v) and not STAMP.match(v):
                        wrong.append(f"{table}.{key} = {v}")
    assert not wrong, wrong[:10]


def test_price_links_cardtrader_when_priced_cardmarket_like_the_others(client):
    """CardTrader's link shows when the price update found a CardTrader price; Cardmarket follows TCGplayer's and
    PriceCharting's rules: a search until you save the card's own Cardmarket link, which then opens instead."""
    from test_round15d import _quote
    _setup_with_set(client)
    _entry(client, "t1", "2", "holo")
    page = client.get("/cards/t1-2").text
    assert "Search Cardmarket" in page and 'data-search-site="cardmarket"' in page and "cardtrader.com/en/cards/" not in page  # round 27: only a CardTrader search
    _quote("t1-2", "", "cardtrader", 3.0)
    main.conn.execute("UPDATE price_quotes SET ref = 4242 WHERE card_id = 't1-2' AND source = 'cardtrader'")
    page = client.get("/cards/t1-2").text
    assert 'href="https://www.cardtrader.com/en/cards/4242"' in page
    r = client.post("/cards/t1-2/link/cardmarket", data={"url": "https://www.cardmarket.com/en/Pokemon/Products/Singles/X/Y"},
                    follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/cards/t1-2").text
    assert "Search Cardmarket" not in page and page.count("cardmarket.com/en/Pokemon/Products/Singles/X/Y") >= 1
    assert ">Cardmarket &#8599;</a>" in page


def test_cardmarket_prices_are_off_for_now(client, monkeypatch):
    """0.19.2: Cardmarket's trend price isn't counted, shown or kept in history, even with a euro rate.
    Round 20: its code is gone too."""
    from app import prices, value
    from test_round15d import _quote
    assert not hasattr(prices, "CARDMARKET_PRICES") and not hasattr(prices, "CARDMARKET")
    _setup_with_set(client)
    _entry(client, "t1", "2", "holo")
    main.conn.execute("DELETE FROM price_quotes")
    _quote("t1-2", "holo", "tcgplayer", 10.0)
    main.conn.execute("UPDATE cards SET cardmarket_eur = 80.0 WHERE id = 't1-2'")
    _gbp(monkeypatch)
    try:
        with main.db.transaction(main.conn):
            prices.resolve(main.conn, "t1-2")
        row = prices.card_prices(main.conn, "t1-2")[0]
        assert [f["origin"] for f in row["sources"]] == ["tcgplayer"] and row["price"] == 10.0
        assert prices.as_rows({"pricing": {"cardmarket": {"trend": 80.0}}, "variants": {"normal": True}}) == []
        assert value._priced({"now": None, "eur": 80.0, "card_id": "t1-2", "variant": ""}) is None
    finally:
        from app import fx
        fx.save_currency(main.conn, "USD")
