"""Round 8: batch screen (in test_app's scan tests), small fixes, moving around, Find, links found for you,
faster loading, value over time and Share my binder."""
from conftest import template_with_assets, with_assets  # noqa: E402

import json
import re
import time

import pytest

from app import main
from app import web
from app.routes import search as search_pages, shared as shared_pages
from test_app import FakeAPI, FakeSearchAPI, _entry, _setup_with_set, client  # noqa: F401  (the client fixture)


# --- step 7: small fixes ------------------------------------------------------------

def test_my_cards_tab_bar_is_centred_on_every_tab_and_scan_page_has_no_instructions(client):
    _setup_with_set(client)
    page = with_assets(client.get("/collection").text)  # round 25b: same place on every tab, fits a phone
    assert "nav.seg.stash-tabs { width:fit-content; max-width:100%; margin-left:auto; margin-right:auto; }" in page
    assert '<nav class="seg stash-tabs"' in page and ".stash-tabs a { flex:1 1 0; min-width:0;" in page
    assert "Take photos of your cards" not in with_assets(client.get("/scans").text)


# --- step 2: moving around ------------------------------------------------------------

def test_pages_keep_their_place_and_pop_ups_replace_the_page(client):
    _setup_with_set(client)
    for path in ("/search", "/collection", "/collection/sets", "/collection/binders", "/chases", "/spending", "/sets/t1"):
        assert "data-keep-place" in with_assets(client.get(path).text).split("<main")[0], path
    card = with_assets(client.get("/cards/t1-1").text)
    assert "data-keep-place" not in card.split("<main")[0]  # the card page always opens at the top
    assert 'history.scrollRestoration = "manual"' in card and "location.replace(resp.url)" in card
    assert '<a class="muted" href="/sets/t1">' in card  # the set name opens the set (A4)
    assert "url.searchParams.delete('open')" in card  # going back never reopens a pop-up


def test_back_to_find_acts_like_the_phones_back(client):
    _setup_with_set(client)
    page = with_assets(client.get("/cards/t1-1?from=find").text)
    assert "data-back-link" in page and 'history.back()' in page  # round 10: plain back for every page
    preview = with_assets(client.get("/preview/en/old-1").text)
    assert 'href="/set-preview/en/old"' in preview  # a set that isn't stored opens its preview


# --- step 3: Find ------------------------------------------------------------------------

class ManyAPI(FakeSearchAPI):
    """60 Pikachu in a big set, each priced when looked up, plus Flabébé."""

    def __init__(self):
        self.priced = []

    def list_sets(self):
        return super().list_sets() + [{"id": "big", "name": "Big Set", "cardCount": {"official": 60, "total": 60}}]

    def search_cards(self, local_id=None, name=None):
        cards = [{"id": f"big-{i}", "localId": str(i), "name": "Pikachu", "image": None} for i in range(1, 61)]
        cards += [{"id": "big-99", "localId": "99", "name": "Flabébé", "image": None},
                  {"id": "old-25", "localId": "25", "name": "Pikachu V", "image": None}]
        if local_id:
            want = local_id if isinstance(local_id, list) else [local_id]
            cards = [c for c in cards if c["localId"] in want]
        if name:
            cards = [c for c in cards if name.lower() in c["name"].lower()]  # TCGdex matches names as written
        return cards

    def get_card(self, card_id):
        self.priced.append(card_id)
        return {"id": card_id, "variants": {"normal": True},
                "pricing": {"tcgplayer": {"unit": "USD", "normal": {"marketPrice": 2.0}}}}


def test_find_shows_24_at_a_time_and_prices_only_those(client, monkeypatch):
    fake = ManyAPI()
    monkeypatch.setattr(web, "api", fake)
    _setup_with_set(client)
    fake.priced.clear()  # (loading the test set looked its cards up)
    page = client.get("/search", params={"q": "pikachu", "lang": "en"}).text
    assert '<span id="find-count">24 of 61</span>' in page and page.count('href="/preview/en/') == 24
    assert len(set(fake.priced)) == 0  # round 20b: Find looks nothing up (it was 24, and 200 before)
    token = re.search(r"/search/more\?t=([\w-]+)&amp;offset=24", page).group(1)
    more = client.get(f"/search/more?t={token}&offset=24").text
    assert more.count('href="/preview/en/') == 24 and "offset=48" in more and "48 of 61" in more
    last = client.get(f"/search/more?t={token}&offset=48").text
    assert last.count('href="/preview/en/') == 13 and "data-more" not in last and '<span id="find-count" hx-swap-oob="true">61</span>' in last
    # Someone else can't read these results, and an old token just shows nothing.
    assert client.get("/search/more?t=nope&offset=24").text == ""


def test_accents_dont_matter_anywhere(client, monkeypatch):
    from app import collectr, display, spelling
    assert display.fold("Pokémon") == display.fold("POKEMON") == "pokemon" and display.fold("ガ") == "ガ"
    assert collectr.fold("Flabébé") == "flabebe" and spelling._plain("Flabébé") == "flabebe"
    fake = ManyAPI()
    monkeypatch.setattr(web, "api", fake)
    _setup_with_set(client)
    main.conn.execute("INSERT INTO sets (set_id, language, name, printed_total, total) VALUES ('big', 'en', 'Big Set', 99, 99)")
    main.conn.execute("INSERT INTO cards (id, set_id, number, sort_number, is_main_set, name) "
                      "VALUES ('big-99', 'big', '99', 99, 1, 'Flabébé')")
    main.conn.commit()
    monkeypatch.setattr(search_pages, "_accent_words", {})
    assert search_pages._accent_queries("flabebe 99", "flabebe") == ["flabebe 99", "Flabébé 99"]
    page = client.get("/search", params={"q": "flabebe", "lang": "en"}).text
    assert "/cards/big-99" in page or "/preview/en/big-99" in page


def test_close_matches_when_name_and_number_find_nothing(client, monkeypatch):
    monkeypatch.setattr(web, "api", ManyAPI())
    _setup_with_set(client)
    page = client.get("/search", params={"q": "pikachu 99", "lang": "en"}).text
    assert "Close matches" in page and "#99 in other sets" not in page  # #99 is Flabébé: not a close name
    assert "Pikachu in other sets" in page and page.count('href="/preview/en/big-') == 12
    page = client.get("/search", params={"q": "pikachoo 25", "lang": "en"}).text
    assert "#25 in other sets" in page and "/preview/en/old-25" in page


# --- step 4 (links found for you) was removed in round 15b: links are never looked for or read.

from app import prices  # noqa: E402,F401


def _own(client, *numbers):
    for n in numbers:
        _entry(client, "t1", n, "normal")


# --- step 6: faster loading ----------------------------------------------------------------

from app import catalog, tcgdex  # noqa: E402


class GraphQLAPI(FakeAPI):
    """Answers the set's cards in one GraphQL request; get_card is counted."""

    lang = "en"

    def __init__(self, names=("Bulbasaur", "Ivysaur", "Venusaur")):
        self.names, self.asked = names, []

    def set_cards(self, set_id):
        return [{"id": f"{set_id}-{n}", "localId": n, "name": name, "rarity": "Rare", "types": ["Grass"],
                 "variants": {"normal": True, "reverse": False, "holo": True}}
                for n, name in zip(("1", "2", "TG01"), self.names)]

    def get_card(self, card_id):
        self.asked.append(card_id)
        return {"id": card_id, "rarity": "Common", "variants": {"normal": True, "reverse": True},
                "pricing": {"tcgplayer": {"normal": {"marketPrice": 1.5, "productId": 42}}}}


def test_a_set_loads_in_one_graphql_request_and_kept_cards_still_get_prices(client):
    _setup_with_set(client)
    _own(client, "1")
    api = GraphQLAPI()
    catalog.cache_set(main.conn, api, "t1")
    assert api.asked == ["t1-1"]  # only the card you have, for its price and product number
    row = main.conn.execute("SELECT rarity, has_holo, types, tcgplayer_id FROM cards WHERE id = 't1-2'").fetchone()
    assert (row["rarity"], row["has_holo"], row["types"]) == ("Rare", 1, "Grass")
    assert main.conn.execute("SELECT price FROM prices WHERE card_id = 't1-1'").fetchone()[0] == 1.5
    # GraphQL that doesn't agree with the set's own list (another language, say): card by card as before.
    api = GraphQLAPI(names=("Fushigidane", "Ivysaur", "Venusaur"))
    catalog.cache_set(main.conn, api, "t1")
    assert sorted(api.asked) == ["t1-1", "t1-2", "t1-TG01"]


def test_saved_tcgdex_replies_last_a_week():
    cache = tcgdex.DiskCache(max_age=60)
    cache.enabled = True
    cache.put("en/cards/x-1", {"id": "x-1", "name": "Test"})
    assert cache.get("en/cards/x-1") == {"id": "x-1", "name": "Test"}
    main.conn.execute("UPDATE tcgdex_cache SET fetched_at = fetched_at - 120 WHERE url = 'en/cards/x-1'")
    main.conn.commit()
    assert cache.get("en/cards/x-1") is None  # too old: asked again
    assert tcgdex.TCGdex("en").set_cards("bad id!") is None  # odd ids never reach the query


# --- step 5: value over time ----------------------------------------------------------------

from datetime import date, timedelta  # noqa: E402

from app import value  # noqa: E402

TODAY = date(2026, 10, 14)


def _snap(card_id, variant, price, days_ago, source="tcgplayer"):
    main.conn.execute("INSERT INTO price_snapshots (card_id, variant, source, price, captured_at) VALUES (?, ?, ?, ?, ?)",
                      (card_id, variant, source, price, f"{(TODAY - timedelta(days=days_ago)).isoformat()} 08:00:00"))


def _price_now(card_id, variant, price):
    main.conn.execute("INSERT OR REPLACE INTO prices (card_id, variant, origin, price, updated_at) "
                      "VALUES (?, ?, 'tcgplayer', ?, '2026-10-14')", (card_id, variant, price))


def test_value_chart_counts_todays_cards_at_each_days_prices(client):
    user_id = _setup_with_set(client)
    _own(client, "1", "2")
    main.conn.execute("DELETE FROM price_snapshots")
    _snap("t1-1", "normal", 10.0, 60)
    _snap("t1-1", "normal", 12.0, 20)
    _snap("t1-2", "normal", 5.0, 10)  # first priced later: counts at its first price before that
    _snap("t1-2", "normal", 4.0, 2, source="found_pricecharting")  # a marked price only counts without TCGdex's
    _price_now("t1-1", "normal", 15.0)
    _price_now("t1-2", "normal", 0.5)
    main.conn.commit()
    s = value.series(main.conn, user_id, "3m", today=TODAY)
    assert s["points"][0] == ((TODAY - timedelta(days=60)).isoformat(), 15.0)  # 10 + 5
    assert s["points"][-1] == (TODAY.isoformat(), 15.5)
    assert (s["change"], s["percent"]) == (0.5, 3.3)
    one_month = value.series(main.conn, user_id, "1m", today=TODAY)
    assert one_month["points"][0][1] == 15.0 and (TODAY - timedelta(days=20)).isoformat() in dict(one_month["points"])
    m = value.movers(main.conn, user_id, "3m", today=TODAY)
    assert [r["card_id"] for r in m["up"]] == ["t1-1"] and m["up"][0]["then"] == 10.0
    assert m["down"] == []  # Ivysaur is under $1 now
    page = client.get("/value?range=1y&by=pct").text
    assert "Top movers" in page and 'class="vchart"' in page and "History starts" in page
    assert 'href="/value"' in client.get("/").text
    chart = client.get("/cards/t1-1/history?variant=normal&range=all").text
    assert "Normal price" in chart and "<circle" in chart
    assert 'id="price-chart"' in client.get("/cards/t1-1").text


def test_marked_prices_get_history_and_chases_show_their_move(client):
    user_id = _setup_with_set(client)
    _own(client, "1")
    client.post("/cards/t1-1/extras", data={"manual_price": "7.50"})
    rows = main.conn.execute("SELECT source, price FROM price_snapshots WHERE card_id = 't1-1'").fetchall()
    assert sorted((r["source"], r["price"]) for r in rows) == [("average", 7.5), ("manual", 7.5)]  # round 18: and the average
    client.post("/wants/add", data={"card_id": "t1-2", "set_id": "t1", "lang": "en"})
    main.conn.execute("DELETE FROM price_snapshots WHERE card_id = 't1-2'")
    _snap("t1-2", "normal", 3.0, 40)
    _price_now("t1-2", "normal", 4.2)
    main.conn.commit()
    assert value.chase_moves(main.conn, user_id) == {"t1-2": 1.2}
    assert "▲ $1.20 (30 days)" in client.get("/chases").text
    # History is only kept for cards you have and single chases.
    main.conn.execute("INSERT INTO price_snapshots (card_id, variant, source, price) VALUES ('t1-TG01', 'normal', 'tcgplayer', 1)")
    prices.prune(main.conn)
    assert not main.conn.execute("SELECT 1 FROM price_snapshots WHERE card_id = 't1-TG01'").fetchone()
    assert main.conn.execute("SELECT 1 FROM price_snapshots WHERE card_id = 't1-2'").fetchone()


def test_sunday_roll_up_keeps_one_row_per_week_then_month(client):
    _setup_with_set(client)
    _own(client, "1")
    main.conn.execute("DELETE FROM price_snapshots")
    for days_ago in range(0, 500):
        _snap("t1-1", "normal", 1 + days_ago / 100, days_ago)
    main.conn.commit()
    value.rollup(main.conn, TODAY)
    days = [r[0] for r in main.conn.execute("SELECT date(captured_at) FROM price_snapshots ORDER BY captured_at")]
    recent = [d for d in days if d >= (TODAY - timedelta(days=30)).isoformat()]
    assert len(recent) == 31  # round 12: the last 30 days stay daily (the default)
    weekly = [d for d in days if (TODAY - timedelta(days=395)).isoformat() <= d < (TODAY - timedelta(days=37)).isoformat()]
    assert 50 <= len(weekly) <= 66  # weeks, cut at month ends
    monthly = [d for d in days if d < (TODAY - timedelta(days=430)).isoformat()]
    assert len(monthly) <= 3


# --- step 8: Share my binder, with access codes ----------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from app import binders, notes, shared  # noqa: E402


def _shared_binder(client):
    user_id = _setup_with_set(client)
    _own(client, "1", "2", "TG01")
    a = binders.create(main.conn, user_id, "Show binder", user_id)
    b = binders.create(main.conn, user_id, "Other binder", user_id)
    lines = {r["card_id"]: r["id"] for r in main.conn.execute("SELECT id, card_id FROM inventory")}
    binders.move(main.conn, user_id, lines["t1-1"], 1, a, None)
    binders.move(main.conn, user_id, lines["t1-2"], 1, b, None)
    # Private things about the shared card: a note, what was paid, where it came from, condition.
    main.conn.execute("UPDATE inventory SET acquired_price = 123.45, origin = 'trade', condition = 'LP' WHERE card_id = 't1-1'")
    main.conn.commit()
    notes.add(main.conn, user_id, "t1-1", user_id, "secret note xyz")
    client.post(f"/collection/binders/{a}/share", data={"on": "1"})
    page = client.post(f"/collection/binders/{a}/codes", data={"name": "Mike"}).text  # back on the binder's Share sheet
    code = re.search(r'id="new-code"[^>]*>([A-Z0-9]{4}-[A-Z0-9]{4})<', page).group(1)
    assert code not in client.get(f"/collection/binders/{a}").text  # shown once
    token = main.conn.execute("SELECT share_token FROM binders WHERE id = ?", (a,)).fetchone()[0]
    return user_id, a, b, token, code


def _visitor():
    shared_pages.share_limit = main.auth.Lockout(limit=10, window=600)
    return TestClient(main.app)


def test_a_shared_binder_needs_a_code_and_shows_only_whats_public(client):
    user_id, a, b, token, code = _shared_binder(client)
    v = _visitor()
    first = v.get(f"/b/{token}")
    assert "Enter your access code" in first.text and "Bulbasaur" not in first.text
    assert first.headers["X-Robots-Tag"] == "noindex, nofollow" and "set-cookie" not in first.headers
    assert v.post(f"/b/{token}/code", data={"code": "AAAA-AAAA"}).status_code == 401
    r = v.post(f"/b/{token}/code", data={"code": code.lower().replace("-", " ")}, follow_redirects=False)
    cookies = r.headers.get_list("set-cookie")
    assert len(cookies) == 1 and cookies[0].startswith("dexstash_pass=") and f"Path=/b/{token}" in cookies[0] and "HttpOnly" in cookies[0]
    page = v.get(f"/b/{token}").text
    assert "Bulbasaur" in page and "Ivysaur" not in page  # only this binder's cards
    for private in ("secret note xyz", "123.45", "Trade", "Lightly Played", "misty", "Other binder"):
        assert private not in page, private
    card = v.get(f"/b/{token}/card/t1-1").text
    for private in ("secret note xyz", "123.45", "Lightly Played", "misty"):
        assert private not in card, private
    assert v.get(f"/b/{token}/card/t1-2").status_code == 404  # in another binder
    assert v.get(f"/b/{token}/picture/t1-2").status_code == 404
    assert v.get("/card-photos/anything.jpg", follow_redirects=False).status_code in (303, 401, 403)
    assert "Last used" in client.get(f"/collection/binders/{a}").text
    assert 'class="head-btn share-btn on"' in client.get(f"/collection/binders/{a}").text
    assert 'class="badge-shared"' in client.get("/collection/binders").text
    # Your own tags never reach visitors, not even through the address.
    client.post("/cards/t1-1/tags/add", data={"tag": "selling soon"})
    assert "selling soon" not in v.get(f"/b/{token}").text
    assert "Bulbasaur" in v.get(f"/b/{token}?tag=selling soon").text  # ignored, not a filter
    # A saved copy of your own photo (a card added by hand) is never served to visitors.
    main.conn.execute("UPDATE cards SET saved_picture = 'abc.jpg', saved_from = '', saved_src = '/card-photos/x.jpg' WHERE id = 't1-1'")
    main.conn.commit()
    assert shared_pages._private_saved(main.conn.execute("SELECT * FROM cards WHERE id = 't1-1'").fetchone())
    assert [u for u, _ in shared_pages._public_pictures(token, "t1-1")] == [f"/b/{token}/picture/t1-1?sprite=1"]
    main.conn.execute("UPDATE cards SET saved_src = 'https://assets.tcgdex.net/en/x/1/high.webp' WHERE id = 't1-1'")
    assert shared_pages._public_pictures(token, "t1-1")[0][0] == f"/b/{token}/picture/t1-1"


def test_revoke_off_new_link_and_tampering_all_stop_a_visitor(client):
    user_id, a, b, token, code = _shared_binder(client)
    v = _visitor()
    v.post(f"/b/{token}/code", data={"code": code})
    assert "Bulbasaur" in v.get(f"/b/{token}").text
    # A pass for binder A doesn't open binder B, even with B shared and the same cookie.
    client.post(f"/collection/binders/{b}/share", data={"on": "1"})
    token_b = main.conn.execute("SELECT share_token FROM binders WHERE id = ?", (b,)).fetchone()[0]
    pass_a = shared.make_pass(web.SECRET_KEY, a, 1)
    assert shared.check_pass(main.conn, web.SECRET_KEY, {"id": b}, pass_a) is None
    assert "Enter your access code" in v.get(f"/b/{token_b}").text
    assert shared.check_code(main.conn, b, code, web.SECRET_KEY) is None  # A's code doesn't work on B
    assert shared.check_code(main.conn, a, code, web.SECRET_KEY) is not None
    # A tampered cookie is refused.
    assert shared.check_pass(main.conn, web.SECRET_KEY, {"id": a}, pass_a[:-2] + "xx") is None
    # Turning sharing off stops every code; on again keeps the link and the codes.
    client.post(f"/collection/binders/{a}/share", data={"on": ""})
    assert v.get(f"/b/{token}").status_code == 404
    client.post(f"/collection/binders/{a}/share", data={"on": "1"})
    assert "Bulbasaur" in v.get(f"/b/{token}").text
    # Revoke stops the remembered device at once.
    code_id = main.conn.execute("SELECT id FROM binder_codes WHERE binder_id = ?", (a,)).fetchone()[0]
    client.post(f"/collection/binders/{a}/codes/{code_id}/revoke")
    assert "Enter your access code" in v.get(f"/b/{token}").text
    # New link: the old one is gone.
    client.post(f"/collection/binders/{a}/share/new")
    assert v.get(f"/b/{token}").status_code == 404
    # Codes are kept only as salted scrypt hashes.
    stored = main.conn.execute("SELECT code_hash FROM binder_codes").fetchone()[0]
    assert stored.startswith("scrypt$") and shared.normalise(code) not in stored


def test_new_code_panel_history_and_clear(client):
    user_id, a, b, token, code = _shared_binder(client)
    # The new code's panel comes from a normal post (not the pop-up's fetch, which loaded the page twice and
    # used up the one-time code), and offers the link and code together.
    page = client.get(f"/collection/binders/{a}").text
    assert 'action="/collection/binders/%d/codes" class="sh-row" data-native' % a in page
    client.post(f"/collection/binders/{a}/share", data={"on": "1"})
    page = client.post(f"/collection/binders/{a}/codes", data={"name": "Card club"}).text
    new = re.search(r'id="new-code"[^>]*>([A-Z0-9]{4}-[A-Z0-9]{4})<', page).group(1)
    assert "Copy link + code" in page and f"/b/{token}&#10;Access code: {new}" in page.replace("\n", "&#10;")
    # Revoke moves Mike from the list to the history; Clear empties the history and forgets revoked codes.
    mike = main.conn.execute("SELECT id FROM binder_codes WHERE name = 'Mike'").fetchone()[0]
    client.post(f"/collection/binders/{a}/codes/{mike}/revoke")
    page = client.get(f"/collection/binders/{a}").text
    assert "Code history" in page and "revoked by" in page and page.count("added by") == 2
    assert f"/codes/{mike}/revoke" not in page and "/revoke" in page  # Card club can still be revoked
    client.post(f"/collection/binders/{a}/codes/history/clear")
    page = client.get(f"/collection/binders/{a}").text
    assert "Code history" not in page and "Card club" in page
    assert main.conn.execute("SELECT COUNT(*) FROM binder_codes WHERE binder_id = ?", (a,)).fetchone()[0] == 1


def test_wrong_codes_and_unknown_links_are_slowed_down(client):
    user_id, a, b, token, code = _shared_binder(client)
    v = _visitor()
    for _ in range(6):
        v.post(f"/b/{token}/code", data={"code": "ZZZZ-ZZZZ"})
    for i in range(4):
        v.get(f"/b/not-a-real-link-{i}")
    assert v.post(f"/b/{token}/code", data={"code": code}).status_code == 429  # even the right code waits
    assert v.get(f"/b/{token}").status_code == 429


def test_shared_binder_filters_and_sorts(client):
    user_id, a, b, token, code = _shared_binder(client)
    lines = {r["card_id"]: r["id"] for r in main.conn.execute("SELECT id, card_id FROM inventory")}
    binders.move(main.conn, user_id, lines["t1-TG01"], 1, a, None)
    v = _visitor()
    v.post(f"/b/{token}/code", data={"code": code})
    names = lambda html: re.findall(r'<span class="n">([^<]+)</span>', html)  # noqa: E731
    assert names(v.get(f"/b/{token}?sort=name").text) == ["Bulbasaur", "Venusaur"]
    assert names(v.get(f"/b/{token}?q=venu").text) == ["Venusaur"]
    assert names(v.get(f"/b/{token}?q=tg01").text) == ["Venusaur"]
    assert names(v.get(f"/b/{token}?printing=holo").text) == []
