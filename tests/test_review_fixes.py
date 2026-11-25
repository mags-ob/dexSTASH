"""#123: fixes from the whole-app review (outside sites, scan prices, safety, speed), with #121 (TCG Pocket cards on
Pokémon pages)."""
import ast
import threading
from pathlib import Path

import httpx
import pytest

from app import backup, chase, db, extras, jobs, main, prices, scans, search, tcgdex, upgrades, web
from app.routes import scans as scan_routes
from app.routes import shared as shared_pages
from test_app import JPEG, _login, _setup_with_set, client  # noqa: F401
from test_round8 import _shared_binder, _visitor
from test_trade_scan import _photo, _trade

ROOT = Path(__file__).resolve().parent.parent


# --- E1: Find's filtered searches ----------------------------------------------------------------------------

def test_a_pages_pool_keeps_the_pages_quick_try():
    seen = []
    with tcgdex.page_wait():
        with jobs.pool(2) as pool:
            seen += list(pool.map(lambda _: tcgdex.in_page(), range(4)))
    with jobs.pool(2) as pool:  # outside a page, nothing is carried
        seen_after = list(pool.map(lambda _: tcgdex.in_page(), range(4)))
    assert seen == [True] * 4 and seen_after == [False] * 4


class ManyCards:
    """TCGdex with 300 'Pikachu' cards in one set, none stored here; counts card lookups."""

    def __init__(self):
        self.asked = []
        self.lock = threading.Lock()

    def list_sets(self):
        return [{"id": "big", "name": "Big", "cardCount": {"official": 300}}]

    def search_cards(self, **kwargs):
        return [{"id": f"big-{n}", "localId": str(n), "name": "Pikachu"} for n in range(1, 301)]

    def get_card_cached(self, card_id):
        with self.lock:
            self.asked.append(card_id)
        return {"rarity": "Rare" if int(card_id.split("-")[1]) % 2 else "Common", "hp": 60}

    get_card = get_card_cached


def test_a_filtered_search_asks_tcgdex_about_at_most_96_cards_a_step():
    api, capped = ManyCards(), []
    found = search.find_cards(api, "pikachu", rarity="Rare", capped=capped)
    assert len(api.asked) == search.MAX_DETAILS == 96 and capped == [True]
    assert found and all(r["rarity"] == "Rare" for r in found)
    # Looking further asks about the next 96; the first 96 come from the saved replies in the real client.
    api2 = ManyCards()
    search.find_cards(api2, "pikachu", rarity="Rare", deeper=2)
    assert len(api2.asked) == 192
    # Cards whose details are stored here cost no request, so they don't count.
    api3, capped3 = ManyCards(), []
    search.find_cards(api3, "pikachu", rarity="Rare", capped=capped3,
                      stored=lambda cid: {"rarity": "Rare", "types": ["Lightning"]} if int(cid.split("-")[1]) <= 250 else None)
    assert len(api3.asked) == 50 and capped3 == []


def test_find_shows_a_plain_message_when_tcgdex_fails(client, monkeypatch, caplog):
    _setup_with_set(client)

    class Refusing:
        def __getattr__(self, name):
            def fail(*args, **kwargs):
                raise httpx.HTTPStatusError("403 Forbidden (proxy says no)", request=httpx.Request("GET", "https://x"),
                                            response=httpx.Response(403))
            return fail
    monkeypatch.setattr(web, "api_for", lambda lang: Refusing())
    monkeypatch.setattr(web, "api", Refusing())
    page = client.get("/search?q=pikachu&lang=en").text
    assert "Couldn&#39;t reach TCGdex. Try again in a minute." in page and "403 Forbidden" not in page
    assert "403 Forbidden" in caplog.text  # the detail is in the Activity log


# --- E2: every thread that can reach another site takes turns --------------------------------------------------

# Threads that never send a request to another site, so they don't need to take turns.
ALLOWED = {
    ("app/jobs.py", "run"),             # jobs.thread itself: marks the thread first
    ("app/jobs.py", "self._loop"),      # the job runner: marks itself (mark_background in _loop)
    ("app/pictures.py", "watch"),       # unloads the picture model when idle
    ("app/scanning.py", "self._keep_running"),  # watches the scanner process
    ("app/web.py", "child.stop"),       # stops the scanner process
}


def test_every_thread_that_can_reach_another_site_takes_turns():
    found = set()
    for path in sorted((ROOT / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "threading.Thread":
                target = next((ast.unparse(k.value) for k in node.keywords if k.arg == "target"), "?")
                found.add((path.relative_to(ROOT).as_posix(), target))
    assert found <= ALLOWED, found - ALLOWED


def test_jobs_thread_marks_its_thread_as_background():
    seen = []
    jobs.thread(lambda: seen.append(jobs.in_background()), "test").join()
    assert seen == [True] and not jobs.in_background()


# --- SC3, P8, T1: scan tile prices ------------------------------------------------------------------------------

def test_tile_prices_stop_after_a_few_failures(monkeypatch):
    tiles = scan_routes.TilePrices()
    tries = []

    def failing(lang, card_id, quiet=True):
        tries.append(card_id)
        raise httpx.ConnectError("TCGdex is down")
    monkeypatch.setattr(scan_routes, "_offer", failing)
    for n in range(6):
        tiles.waiting[f"en:c-{n}"] = ("en", f"c-{n}", 1)
    tiles.run()
    assert len(tries) == tiles.FAILURES == 3 and not tiles.waiting
    assert tiles.get("en", "c-9", 1) is None and not tiles.waiting  # resting: nothing new is queued


def test_tile_prices_forget_old_entries(monkeypatch):
    tiles = scan_routes.TilePrices()
    tiles.offers["old"] = (-10_000.0, {})
    tiles._keep("new", {"price_map": {}})
    assert set(tiles.offers) == {"new"}


def test_a_typed_price_reaches_the_scan_tile_and_the_trade_total(client):
    _setup_with_set(client)
    batch_id = _trade(client)
    _photo(batch_id, "t1-2", "theirs")
    client.get(f"/scans/{batch_id}")
    scan_routes.tile_prices.wait()
    assert "$7.00" not in client.get(f"/scans/{batch_id}").text
    extras.save(main.conn, "t1-2", None, manual_price=7.0, variant="normal")
    main.conn.commit()
    assert "$7.00" in client.get(f"/scans/{batch_id}").text


# --- T2: a whole-card price on the scan's printing ---------------------------------------------------------------

def test_a_whole_card_price_goes_on_the_printing_the_scan_shows(client):
    owner = _setup_with_set(client)
    main.conn.execute("UPDATE cards SET has_normal = 1, has_holo = 1 WHERE id = 't1-1'")
    main.conn.execute("INSERT INTO price_quotes (card_id, variant, source, price, currency, basis, updated_at) "
                      "VALUES ('t1-1', '', 'cardtrader', 3.0, 'USD', 'nm', '2026-11-25T00:00:00Z')")
    prices.resolve(main.conn, "t1-1")
    assert not main.conn.execute("SELECT 1 FROM prices WHERE card_id = 't1-1'").fetchone()  # two printings: no guess
    batch_id = _trade(client)
    scan_id = _photo(batch_id, "t1-1", "theirs")
    main.conn.execute("UPDATE scans SET variant = 'holo' WHERE id = ?", (scan_id,))
    scans.keep_matches(main.conn, [{"scan_id": scan_id, "our": "t1-1"}])
    prices.resolve(main.conn, "t1-1")
    assert [tuple(r) for r in main.conn.execute("SELECT variant, price FROM prices WHERE card_id = 't1-1'")] == [("holo", 3.0)]


# --- M1, P4, P5, M2, SC1: safety ----------------------------------------------------------------------------------

def test_clearing_the_collection_makes_a_backup_first(client, monkeypatch, tmp_path):
    monkeypatch.setattr(web, "BACKUPS", tmp_path / "backups")
    _setup_with_set(client)
    page = client.post("/account/clear", data={"confirm": "CLEAR"}).text
    [made] = backup.list_backups(web.BACKUPS)
    assert made["kind"] == "clear" and made["label"] == "Before clearing" and made["name"] in page


def test_only_an_admin_removes_missing_cards_on_import_and_a_backup_comes_first(client, monkeypatch, tmp_path):
    monkeypatch.setattr(web, "BACKUPS", tmp_path / "backups")
    owner = _setup_with_set(client)
    client.post("/account/users", data={"username": "sam", "password": "squirtle99"})
    batch = main.conn.execute("INSERT INTO import_batches (collection_id, created_by, status) VALUES (?, ?, 'ready')",
                              (owner, owner)).lastrowid
    main.conn.commit()
    sam = _login("sam", "squirtle99")
    assert "remove_missing" not in sam.get(f"/imports/{batch}").text
    assert sam.post(f"/imports/{batch}/apply", data={"remove_missing": "1"}).status_code == 403
    assert backup.list_backups(web.BACKUPS) == []
    client.post(f"/imports/{batch}/apply", data={"remove_missing": "1"})
    assert [b["kind"] for b in backup.list_backups(web.BACKUPS)] == ["import"]


def test_wrong_current_passwords_count_towards_the_lockout(client):
    _setup_with_set(client)
    for _ in range(web.lockout.limit):
        assert "isn&#39;t right" in client.post("/account/password", data={"current": "nope", "new": "x" * 10,
                                                                          "again": "x" * 10}).text
    page = client.post("/account/password", data={"current": "pikachu123", "new": "x" * 10, "again": "x" * 10}).text
    assert "Too many wrong passwords" in page


def test_more_than_50_photos_in_one_upload_is_refused(client):
    _setup_with_set(client)
    batch_id = _trade(client)
    files = [("photos", (f"p{n}.jpg", JPEG, "image/jpeg")) for n in range(scans.MAX_PHOTOS + 1)]
    r = client.post(f"/scans/{batch_id}/photos", files=files)
    assert r.status_code == 413 and "at most 50" in r.text
    assert not main.conn.execute("SELECT 1 FROM scans WHERE batch_id = ?", (batch_id,)).fetchone()


def test_the_card_extras_redirect_escapes_the_card_id(client):
    _setup_with_set(client)
    r = client.post("/cards/t1-1/extras", data={"manual_price": "lots", "variant": "normal"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/cards/t1-1?extra_error=")
    from app.routes import cards as card_pages
    r = card_pages._card_extras_save("a#b", {"id": None}, b"", None, None, "lots", "", "normal")
    assert r.headers["location"].startswith("/cards/a%23b?")


def test_add_to_pokemon_sets_saves_every_tick_or_none(client, monkeypatch):
    owner = _setup_with_set(client)
    for dex in (1, 2):
        main.conn.execute("INSERT INTO followed_pokemon (collection_id, dex_id, name, added_at) VALUES (?, ?, ?, 'now')",
                          (owner, dex, f"Pokémon {dex}"))
    main.conn.commit()
    calls = []

    def add_then_fail(conn, dex, row, by, commit=True):
        calls.append(dex)
        conn.execute("INSERT INTO pokemon_cards (dex_id, card_id, lang, set_id, how) VALUES (?, 't1-1', 'en', 't1', 'manual')",
                     (dex,))
        if len(calls) == 2:
            raise RuntimeError("disk full")
    monkeypatch.setattr(chase, "add_card", add_then_fail)
    with pytest.raises(RuntimeError):
        client.post("/cards/t1-1/pokemon-sets", data={"dex": ["1", "2"]})
    assert calls == [1, 2]
    assert not main.conn.execute("SELECT 1 FROM pokemon_cards WHERE card_id = 't1-1'").fetchone()


# --- F2, #121: Pokémon card lists --------------------------------------------------------------------------------

class Refused:
    """TCGdex answering every request with 403."""
    lang = "en"

    class client:
        @staticmethod
        def get(path, params=None):
            return httpx.Response(403, request=httpx.Request("GET", "https://api.tcgdex.net/v2/en" + path))

    def search_cards(self, **kwargs):
        raise httpx.HTTPStatusError("403", request=httpx.Request("GET", "https://x"), response=httpx.Response(403))


def test_a_refused_pokemon_list_says_tcgdex_couldnt_be_reached():
    try:
        chase.list_briefs(Refused(), 437, ["Bronzong"])
    except httpx.HTTPStatusError:
        pass
    else:
        raise AssertionError("a list TCGdex refused should not look empty")


class WithPocket:
    lang = "en"

    class client:
        @staticmethod
        def get(path, params=None):
            body = [{"id": "dp5-14", "localId": "14", "name": "Bronzong"}, {"id": "A2-108", "localId": "108", "name": "Bronzong"}]
            return httpx.Response(200, json=body, request=httpx.Request("GET", "https://api.tcgdex.net/v2/en" + path))


def test_pokemon_card_lists_leave_out_tcg_pocket_cards():
    assert [b["id"] for b in chase.list_briefs(WithPocket(), 437, ["Bronzong"])] == ["dp5-14"]


def test_the_upgrade_removes_pocket_cards_already_in_pokemon_sets(tmp_path):
    conn = db.connect(tmp_path / "old.db")
    db.init(conn)
    for card_id, set_id in (("dp5-14", "dp5"), ("A2-108", "A2"), ("ja:A1-211", "ja:A1")):
        conn.execute("INSERT INTO pokemon_cards (dex_id, card_id, lang, set_id) VALUES (437, ?, 'en', ?)", (card_id, set_id))
    conn.execute("INSERT INTO art_fixes (dex_id, card_id, join_card_id, at) VALUES (437, 'A2-108', 'dp5-14', 'now')")
    conn.commit()
    upgrades.no_pocket_again(conn)
    assert [r[0] for r in conn.execute("SELECT card_id FROM pokemon_cards")] == ["dp5-14"]
    assert not conn.execute("SELECT 1 FROM art_fixes").fetchone()
    assert [r[0] for r in conn.execute("SELECT card_id FROM art_cards")] == ["dp5-14"]
    assert any(t.number == 13 and t.run is upgrades.no_pocket_again for t in upgrades.TASKS)


# --- SH2, SH5: shared binders -----------------------------------------------------------------------------------

def test_shared_pictures_offer_the_sprite_only_when_it_is_there(client, monkeypatch):
    _, _, _, token, _ = _shared_binder(client)
    assert all("sprite=1" not in u for u, _ in shared_pages._public_pictures(token, "t1-1"))
    monkeypatch.setattr(shared_pages, "_sprite_for", lambda name: "/sprites/1.png")
    assert ("/b/" + token + "/picture/t1-1?sprite=1", "") in shared_pages._public_pictures(token, "t1-1")


def test_a_shared_binder_page_builds_the_binder_once(client, monkeypatch):
    from app import collection
    _, _, _, token, code = _shared_binder(client)
    v = _visitor()
    v.post(f"/b/{token}/code", data={"code": code})
    built = []
    real = collection.cards
    monkeypatch.setattr(collection, "cards", lambda *a, **k: built.append(1) or real(*a, **k))
    assert "Bulbasaur" in v.get(f"/b/{token}").text
    assert len(built) == 1


# --- P2, P7 -----------------------------------------------------------------------------------------------------

def test_the_data_folder_changes_owner_only_when_it_must():
    entry = (ROOT / "deploy" / "entrypoint.sh").read_text()
    assert 'stat -c %u:%g /srv/data' in entry and entry.index("stat -c") < entry.index("chown -R")

