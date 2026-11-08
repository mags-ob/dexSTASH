"""Round 17 (0.17.0): card page Option D, binder pages, Pokémon eras, My Stash, scanning off and fixes."""
import re
from pathlib import Path

from app import main, recent, web
from test_app import _entry, _setup_with_set, client  # noqa: F401
from test_round8 import ManyAPI

ROOT = Path(__file__).resolve().parent.parent


# --- 8: Recently viewed --------------------------------------------------------------------------------------

def test_recently_viewed_shows_nine():
    assert recent.SHOWN == 9


# --- 10: search found nothing ---------------------------------------------------------------------------------

def test_a_filter_that_leaves_nothing_offers_a_way_out(client, monkeypatch):
    monkeypatch.setattr(web, "api", ManyAPI())
    _setup_with_set(client)
    page = client.get("/search", params={"q": "99", "lang": "en", "set": "t1", "scan": "", "fix": ""}).text
    assert "Your filter left nothing" in page and "Search without the filter" in page
    assert "#99 in other sets, newest first" in page and "/preview/en/big-99" in page
    href = re.search(r'href="([^"]+)" data-unfiltered', page).group(1)
    assert "set=" not in href and "q=99" in href
    assert "Add it by hand" in page  # also with a set filter on now


def test_a_number_alone_in_a_set_lists_that_number_elsewhere(client, monkeypatch):
    monkeypatch.setattr(web, "api", ManyAPI())
    _setup_with_set(client)
    page = client.get("/search", params={"q": "25", "lang": "en", "set": "t1"}).text
    assert 'No <span class="cno">#25</span> in' in page and "/preview/en/old-25" in page


def test_name_and_number_keep_their_two_groups(client, monkeypatch):
    monkeypatch.setattr(web, "api", ManyAPI())
    _setup_with_set(client)
    page = client.get("/search", params={"q": "pikachu 99", "lang": "en"}).text
    assert "Nothing has that name and number together." in page and "Pikachu in other sets" in page


# --- 7: searching your stash by English name finds Japanese and Chinese cards ---------------------------------

def test_stash_search_by_english_name_finds_other_languages(client, monkeypatch):
    from test_app import SAMPLE_NAMES
    monkeypatch.setattr(web, "names", SAMPLE_NAMES)
    _setup_with_set(client)
    for n in ("1", "2"):
        _entry(client, "t1", n)
    main.conn.execute("UPDATE cards SET name = 'リザードン' WHERE id = 't1-1'")
    main.conn.execute("UPDATE cards SET name = '噴火龍' WHERE id = 't1-2'")
    main.conn.commit()
    page = client.get("/collection", params={"q": "charizard"}).text
    assert "/cards/t1-1" in page and "/cards/t1-2" in page
    page = client.get("/collection", params={"q": "リザードン"}).text  # typing the Japanese name still works
    assert "/cards/t1-1" in page and "/cards/t1-2" not in page
    main.conn.execute("UPDATE cards SET name = 'Bulbasaur' WHERE id = 't1-2'")
    main.conn.commit()
    assert "/cards/t1-2" not in client.get("/collection", params={"q": "charizard"}).text


# --- 6: back button ---------------------------------------------------------------------------------------------

def test_every_form_that_comes_back_replaces_the_page():
    """Round 17: not only pop-ups and forms marked data-replace. A form that returns to the same page replaces it;
    one that goes elsewhere adds that page; a page drawn from the post (an error) is shown without a new step."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "base-nav.js").read_text()
    listener = js[js.index('document.addEventListener("submit"'):]
    listener = listener[:listener.index("});")]
    assert 'hasAttribute("data-replace")' not in listener and '!(form.closest("dialog")' not in listener
    assert "location.replace(resp.url)" in js and "location.assign(resp.url)" in js and "document.write(html)" in js


# --- 5: scanning off and the in-app switch --------------------------------------------------------------------

class FakeChild:
    started = stopped = 0
    refused = None

    def start(self):
        FakeChild.started += 1

    def stop(self, timeout=10):
        FakeChild.stopped += 1

    def running(self):
        return True


def _switchable(monkeypatch):
    from app import scanning
    monkeypatch.setattr(web, "SCANNER_LOCKED", False)
    monkeypatch.setattr(web, "SCANNER_MODE", "off")
    monkeypatch.setattr(web, "SCANNER_EXTERNAL", False)
    monkeypatch.setattr(web, "scanner_child", None)
    monkeypatch.setattr(scanning, "Child", FakeChild)
    monkeypatch.setattr(web.light_work, "start", lambda: None)
    monkeypatch.setattr(web.light_work, "enabled", web.light_work.enabled)
    monkeypatch.setattr(web.picture_lookups, "enabled", web.picture_lookups.enabled)
    FakeChild.started = FakeChild.stopped = 0


def test_scanning_off_hides_the_scan_tab(client, monkeypatch):
    _setup_with_set(client)
    assert 'href="/scans"' in client.get("/").text
    monkeypatch.setattr(web, "SCANNER_MODE", "off")
    page = client.get("/").text
    assert 'href="/scans"' not in page and "My Stash" in page


def test_the_scanning_switch_turns_the_reader_on_and_off(client, monkeypatch):
    _setup_with_set(client)
    _switchable(monkeypatch)
    page = client.get("/account/data").text
    assert 'action="/account/data/scanning"' in page and "Set in .env" not in page
    r = client.post("/account/data/scanning", data={"on": "1"}, follow_redirects=False)
    assert r.status_code == 303 and "#scanner" in r.headers["location"]
    assert web.SCANNER_MODE == "inside" and web.SCANNER_EXTERNAL and FakeChild.started == 1
    assert web.conn.execute("SELECT value FROM app_meta WHERE key = 'scanning'").fetchone()[0] == "inside"
    assert 'href="/scans"' in client.get("/").text
    client.post("/account/data/scanning", data={"on": "0"})
    assert web.SCANNER_MODE == "off" and web.scanner_child is None and not web.SCANNER_EXTERNAL
    assert web.conn.execute("SELECT value FROM app_meta WHERE key = 'scanning'").fetchone()[0] == "off"
    assert 'href="/scans"' not in client.get("/").text
    assert "Account › Data sources › Scanning" in web._scan_problem()


def test_dot_env_wins_over_the_switch(client, monkeypatch):
    _setup_with_set(client)
    monkeypatch.setattr(web, "SCANNER_LOCKED", True)
    page = client.get("/account/data").text
    assert "Set in .env" in page and "disabled" in page
    before = web.SCANNER_MODE
    r = client.post("/account/data/scanning", data={"on": "0"}, follow_redirects=False)
    assert "changed+there" in r.headers["location"] or "changed%20there" in r.headers["location"]
    assert web.SCANNER_MODE == before


# --- 9: You got it! -------------------------------------------------------------------------------------------

def _chased(client):
    from app import celebrate
    celebrate.clear()
    _setup_with_set(client)
    card = main.conn.execute("SELECT id FROM cards WHERE set_id = 't1' ORDER BY id").fetchone()["id"]
    client.post("/wants/add", data={"card_id": card.split(":")[-1], "set_id": "t1", "lang": "en"})
    return card, main.conn.execute("SELECT id FROM wants").fetchone()["id"]


def test_the_first_copy_of_a_chase_brings_the_pop_up_once(client):
    card, want_id = _chased(client)
    assert "data-got" not in client.get(f"/cards/{card}").text
    _entry(client, "t1", "1")
    page = client.get(f"/cards/{card}").text
    assert "data-got" in page and "data-got" in client.get("/").text  # only asking /got uses it up
    got = client.get("/got").text
    assert "You got" in got and "Stop chasing it?" in got and f'value="{want_id}"' in got and "got-confetti" in got
    assert "data-ask" not in got  # no shop links or photos to lose
    assert client.get("/got").text == "" and "data-got" not in client.get("/").text
    _entry(client, "t1", "1")  # a second copy: no pop-up again
    assert client.get("/got").text == ""


def test_yes_stops_chasing_and_asks_first_with_a_chase_box(client):
    card, want_id = _chased(client)
    client.post(f"/cards/{card}/chase/links", data={"url": "https://example.com/listing"})
    _entry(client, "t1", "1")
    got = client.get("/got").text
    assert "data-ask" in got and "shop links and photos are deleted too" in got
    client.post("/chases/stop", data={"want_id": str(want_id)})
    assert main.conn.execute("SELECT COUNT(*) FROM wants").fetchone()[0] == 0


def test_moving_copies_or_an_unchased_card_brings_nothing(client):
    from app import celebrate, inventory
    card, want_id = _chased(client)
    other = main.conn.execute("SELECT id FROM cards WHERE set_id = 't1' ORDER BY id").fetchall()[1]["id"]
    client.post(f"/cards/{other}/adjust", data={"variant": "normal", "condition": "NM", "delta": "1"})
    assert client.get("/got").text == ""
    user = main.conn.execute("SELECT id FROM users").fetchone()[0]
    inventory.adjust(main.conn, user, card, "normal", "NM", 1, by=None)  # no person: nobody to show it to
    inventory.move(main.conn, user, card, "normal", "NM", "reverse", "NM", by=user)
    assert not celebrate._waiting


def test_a_scan_added_by_htmx_asks_for_the_pop_up():
    js = (ROOT / "app" / "static" / "js" / "base-nav.js").read_text()
    assert 'addEventListener("dsGot", showGot)' in js and "stopImmediatePropagation" in js
    src = (ROOT / "app" / "routes" / "scans.py").read_text()
    assert 'response.headers["HX-Trigger"] = "dsGot"' in src


# --- 3: Pokémon sets by era -----------------------------------------------------------------------------------

def test_eras_place_cards_by_their_own_set_date():
    from app import eras
    assert eras.era_of("2023-09-22", "en") == ("sv", "Scarlet & Violet")
    assert eras.era_of("2023-02-01", "en")[0] == "swsh" and eras.era_of("2023-02-01", "ja")[0] == "sv"  # Japan first
    assert eras.era_of("2023-02-01", "zh-cn")[0] == "swsh" and eras.era_of("2023-02-01", "zh-tw")[0] == "sv"
    assert eras.era_of("1999-01-09", "en") == eras.OLDER and eras.era_of(None, "en") == eras.UNKNOWN


def test_a_new_english_series_becomes_a_row(client):
    from app import eras
    _setup_with_set(client)
    main.conn.execute("INSERT INTO sets (set_id, language, name, series, release_date, printed_total, total) VALUES "
                      "('zz1', 'en', 'Future One', 'Future Era', '2027-03-01', 1, 1), "
                      "('zz2', 'en', 'Pocket', 'Pokémon TCG Pocket', '2027-04-01', 1, 1)")
    known = eras.known(main.conn)
    assert known[0][:3] == ("futureera", "Future Era", "2027-03-01") and all(e[1] != "Pokémon TCG Pocket" for e in known)
    assert eras.era_of("2027-05-01", "en", known)[0] == "futureera"


def test_the_pokemon_page_shows_era_rows_and_view_all(client, monkeypatch):
    from app import chase
    from test_app import _chase_rows, _vec
    user_id = _setup_with_set(client)
    chase.follow(main.conn, user_id, 35, "Clefairy", user_id)
    chase.save_cards(main.conn, 35, _chase_rows())
    chase.regroup(main.conn, 35, {"sv03.5-035": _vec(1, 0, 0), "base1-5": _vec(0, 1, 0)})
    page = client.get("/pokemon/35").text
    assert 'class="era-rows"' in page and "Scarlet &amp; Violet <small>&middot; 1 art &middot; 0 owned" in page
    assert page.index("Scarlet &amp; Violet") < page.index(">Older <small>")  # newest era first
    assert "View all &rsaquo;" in page and "era=sv" in page
    assert '<b>Language</b>' in page and "lang-chips big-chips" not in page  # the language is in Filters now
    for show in ("Owned", "Missing", "♥ Chased", "Full art", "Promo"):
        assert f">{show}</label>" in page
    page = client.get("/pokemon/35", params={"era": "sv"}).text
    assert 'class="pgrid" id="arts"' in page and "Every era" in page and "<h2>Scarlet &amp; Violet</h2>" in page
    assert "Base Set" not in page.split('id="arts"')[1]  # only that era's arts
    assert "No arts match." in client.get("/pokemon/35", params={"show": "promo"}).text


# --- 2: binder pages -------------------------------------------------------------------------------------------

def test_binder_pages_put_one_copy_in_each_pocket_with_empty_sleeves():
    from app import binder_pages
    rows = [{"card_id": "a", "name": "Bulbasaur", "number": "1", "sort_number": 1, "variant": "normal", "set_id": "s",
             "release_date": "2020", "quantity": 3, "price": 1, "added_order": 1},
            {"card_id": "b", "name": "Abra", "number": "2", "sort_number": 2, "variant": "normal", "set_id": "s",
             "release_date": "2020", "quantity": 2, "price": 5, "added_order": 2}]
    pockets = binder_pages.pockets(rows, "set")
    assert [p["card_id"] for p in pockets] == ["a", "a", "a", "b", "b"]
    pages = binder_pages.pages(pockets, 4)
    assert len(pages) == 2 and pages[1][1:] == [None, None, None]
    assert [p["card_id"] for p in binder_pages.pockets(rows, "price")][:2] == ["b", "b"]
    assert [p["card_id"] for p in binder_pages.pockets(rows, "name")][0] == "b"
    assert binder_pages.pages([], 9) == [[None] * 9]
    assert binder_pages.page_of(pockets, 4, lambda r: r["card_id"] == "b") == (1, 3)


def test_a_binder_opens_as_pages_and_remembers_the_grid(client):
    from app import binders
    from test_round8 import _own
    user_id = _setup_with_set(client)
    _own(client, "1", "2", "TG01")
    a = binders.create(main.conn, user_id, "Show binder", user_id)
    for r in main.conn.execute("SELECT id FROM inventory").fetchall():
        binders.move(main.conn, user_id, r["id"], 1, a, None)
    page = client.get(f"/collection/binders/{a}").text
    assert 'class="bp-page l9"' in page and page.count('class="bp-pocket empty"') == 6 and "Page 1 of 1" in page
    assert "3 cards &middot;" in page and "sorted by set and number" in page and 'id="binder-gear"' in page
    assert "binder-more" not in page  # the gear replaced the ⋯ menu
    client.post(f"/collection/binders/{a}/settings", data={"name": "Best cards", "layout": "4", "sort": "name"})
    assert main.conn.execute("SELECT name, layout, sort FROM binders WHERE id = ?", (a,)).fetchone()[:] == ("Best cards", 4, "name")
    page = client.get(f"/collection/binders/{a}", params={"find": "venusaur"}).text
    assert 'class="bp-page l4"' in page and "bp-pocket hit" in page and "sorted by name" in page
    assert "No card matching" in client.get(f"/collection/binders/{a}", params={"find": "mewtwo"}).text
    grid = client.get(f"/collection/binders/{a}", params={"view": "grid"}).text
    assert "bp-page" not in grid and 'aria-current="page">Grid</a>' in grid and "data-select-toggle" in grid
    assert "bp-page" not in client.get(f"/collection/binders/{a}").text  # remembered for this login
    client.get(f"/collection/binders/{a}", params={"view": "pages"})
    loose = client.get("/collection/binders/none").text
    assert "bp-page" in loose and "share-sheet" not in loose and 'name="name"' not in loose.split('id="binder-gear"')[1]
    client.post("/collection/binders/none/settings", data={"layout": "12", "sort": "price"})
    assert 'class="bp-page l12"' in client.get("/collection/binders/none").text
    r = client.post(f"/collection/binders/{a}/settings", data={"layout": "7", "sort": "name"}, follow_redirects=False)
    assert "done=" in r.headers["location"]


def test_the_shared_link_shows_the_owners_pages_and_the_grid(client):
    from app import binder_pages
    from test_round8 import _shared_binder, _visitor
    user_id, a, b, token, code = _shared_binder(client)
    binder_pages.save(main.conn, user_id, a, 4, "set")
    v = _visitor()
    v.post(f"/b/{token}/code", data={"code": code})
    page = v.get(f"/b/{token}").text
    assert 'class="bp-page l4"' in page and f'data-card="/b/{token}/card/' in page and "secret note xyz" not in page
    assert "123.45" not in page and "Grid</a>" in page
    assert '<span class="n">' in v.get(f"/b/{token}", params={"view": "grid"}).text
    assert '<span class="n">' in v.get(f"/b/{token}", params={"q": "bulba"}).text  # old search links: the grid


# --- 1: card page, Option D -----------------------------------------------------------------------------------

def _line(card_id="t1-1"):
    return [dict(r) for r in main.conn.execute("SELECT * FROM inventory WHERE card_id = ? ORDER BY id", (card_id,))]


def test_the_card_page_has_tabs_copy_cards_and_a_price_chip(client):
    _setup_with_set(client)
    client.post("/cards/t1-1/extras", data={"manual_price": "4"})
    client.post("/cards/t1-1/adjust", data={"variant": "normal", "condition": "NM", "delta": "2"})
    client.post("/cards/t1-1/notes", data={"text": "from the league"})
    page = client.get("/cards/t1-1").text
    assert "Your price · today" in page and 'class="cv-chip"' in page
    assert 'data-tab-btn="notes">Notes &amp; history &middot; 1</button>' in page and 'data-tab="notes" hidden' in page
    assert "<b>Normal &times; 2</b>" in page and 'data-open="edit-' in page and 'action="/cards/t1-1/copy"' in page
    assert 'class="pr-chip have"' in page and "In your stash</span><strong>" not in page and "To review:" not in page
    assert 'class="cv-link add" data-open="mod-links"' in page
    assert "Not in your stash" in client.get("/cards/t1-2").text


def test_a_card_with_no_price_says_so_in_things_to_check(client):
    _setup_with_set(client)
    client.post("/cards/t1-1/adjust", data={"variant": "normal", "condition": "NM", "delta": "1"})
    page = client.get("/cards/t1-1").text
    assert "This card has no price for any printing" in page and "Type a price" in page
    assert "This card has no price for any printing" not in client.get("/cards/t1-2").text  # owned cards only


def test_the_edit_sheet_changes_all_or_one(client):
    from app import binders
    owner = _setup_with_set(client)
    client.post("/cards/t1-1/adjust", data={"variant": "normal", "condition": "NM", "delta": "3"})
    [line] = _line()
    box = binders.create(main.conn, owner, "Box", owner)
    # All: printing, condition, binder, paid, origin and the copy note.
    client.post("/cards/t1-1/copy", data={"inventory_id": line["id"], "scope": "all", "to_variant": "reverse",
                                          "to_condition": "LP", "binder": str(box), "origin": "trade", "paid": "2",
                                          "note": "  binder   page 3 "})
    [line] = _line()
    assert (line["variant"], line["condition"], line["quantity"], line["origin"], line["acquired_price"], line["note"]) == \
        ("reverse", "LP", 3, "trade", 2.0, "binder page 3")
    assert binders.placement(main.conn, owner)[line["id"]] == {box: 3}
    # One: only the binder for a copy that keeps its printing and condition.
    client.post("/cards/t1-1/copy", data={"inventory_id": line["id"], "scope": "one", "to_variant": "reverse",
                                          "to_condition": "LP", "binder": "none", "from_binder": str(box)})
    assert binders.placement(main.conn, owner)[line["id"]] == {box: 2, None: 1}
    # One into another condition: a new line, with its own details.
    client.post("/cards/t1-1/copy", data={"inventory_id": line["id"], "scope": "one", "to_variant": "reverse",
                                          "to_condition": "NM", "binder": "", "from_binder": str(box), "note": "the good one"})
    lines = {(l["variant"], l["condition"]): l for l in _line()}
    assert lines[("reverse", "LP")]["quantity"] == 2 and lines[("reverse", "NM")]["quantity"] == 1
    assert lines[("reverse", "NM")]["note"] == "the good one" and lines[("reverse", "LP")]["note"] == "binder page 3"
    assert binders.placement(main.conn, owner)[lines[("reverse", "NM")]["id"]] == {box: 1}  # stayed in its binder
    # Remove: one, then all.
    client.post("/cards/t1-1/copy", data={"inventory_id": lines[("reverse", "LP")]["id"], "scope": "one", "action": "remove"})
    assert {(l["variant"], l["condition"]): l["quantity"] for l in _line()}[("reverse", "LP")] == 1
    client.post("/cards/t1-1/copy", data={"inventory_id": lines[("reverse", "NM")]["id"], "action": "remove"})
    assert [(l["variant"], l["condition"]) for l in _line()] == [("reverse", "LP")]
    assert client.post("/cards/t1-1/copy", data={"inventory_id": 999, "action": "remove"}).status_code == 404


def test_a_chase_is_a_card_with_where_to_buy(client):
    from app import wants
    owner = _setup_with_set(client)
    wants.add(main.conn, owner, "t1-1", by=owner)
    page = client.get("/cards/t1-1").text
    assert 'class="copy chase-card" id="chase"' in page and "Where to buy" in page and "Paste a shop link" in page
    assert "since " in page and "★" not in page.split('id="chase"')[1].split("</section>")[0]


# --- 0.17.1: a price for a card you don't have -----------------------------------------------------------------

def test_a_card_you_dont_have_shows_any_price_we_know(client, monkeypatch):
    from app import prices
    _setup_with_set(client)
    main.conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('eur_usd', '1.1128')")

    class OnlyCardmarket:
        lang = "en"

        def get_card(self, card_id):
            return {"id": card_id, "variants": {"holo": True}, "pricing": {"cardmarket": {"trend": 8.6}}}
    monkeypatch.setattr(web, "api_for", lambda lang: OnlyCardmarket())
    page = client.get("/cards/t1-TG01").text
    assert "$9.57" not in page  # round 20: Cardmarket's price isn't used
    # TCGCSV's price from the last run comes before TCGdex's.
    main.conn.execute("INSERT INTO tcgcsv_prices (card_id, variant, market, low, mid, updated_at) "
                      "VALUES ('t1-TG01', 'holo', 4.25, 4.0, 5.0, '2026-10-28T08:00:00+00:00')")
    assert "$4.25" in client.get("/cards/t1-TG01").text
    assert prices.as_rows(OnlyCardmarket().get_card("x")) == []  # tiles and Find: no TCGplayer price, none

