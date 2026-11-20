"""Trade scans (#81): two sides (Your cards, theirs), totals, Compare, and Finish trade."""
import json
from pathlib import Path

from app import binders, db, inventory, main, scans
from app.routes import scans as scan_routes
from test_app import JPEG, _scan_setup, _setup_with_set, client  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


def _trade(client, name="Brock"):
    """Start a trade with name; gives back the batch id."""
    r = client.post("/scans/trade", data={"trading_with": name}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("?camera=1&side=mine")
    return int(r.headers["location"].split("?")[0].rsplit("/", 1)[1])


def _photo(batch_id, card_id, side, quantity=1, name=None):
    """A photo already read and matched to card_id, on one side of the trade. Gives back its id."""
    owner = main.conn.execute("SELECT collection_id FROM scan_batches WHERE id = ?", (batch_id,)).fetchone()[0]
    number = card_id.rsplit("-", 1)[1]
    guess = [{"id": card_id, "number": number, "name": name or {"1": "Bulbasaur", "2": "Ivysaur"}.get(number, "Venusaur"),
              "image": None, "set_id": "t1", "set_name": "Test Set", "score": 5, "name_score": 1, "sure": True, "lang": "en"}]
    scan_id = main.conn.execute(
        "INSERT INTO scans (batch_id, collection_id, created_by, status, guesses_json, language, checked, side, quantity) "
        "VALUES (?, ?, ?, 'ready', ?, 'en', 1, ?, ?)", (batch_id, owner, owner, json.dumps(guess), side, quantity)).lastrowid
    main.conn.commit()
    return scan_id


def _own(owner, card_id, quantity, variant="normal", condition="NM"):
    inventory.set_quantity(main.conn, owner, card_id, variant, condition, quantity, by=owner)
    main.conn.commit()


def _qty(owner, card_id, variant="normal", condition="NM"):
    row = main.conn.execute("SELECT quantity FROM inventory WHERE user_id = ? AND card_id = ? AND variant = ? "
                            "AND condition = ?", (owner, card_id, variant, condition)).fetchone()
    return row[0] if row else 0


def _prices(monkeypatch, by_card):
    """Tiles priced from by_card (card id -> price, None for no price), without looking anything up."""
    def tile(scan, batch):
        if not scans.matched(scan):
            return None
        price = by_card.get(scan["guesses"][0]["id"])
        return {"known": True, "price": price, "variant": "normal", "label": "Normal", "condition": "NM",
                "quantity": scan["quantity"] or 1}
    monkeypatch.setattr(scan_routes, "_tile", tile)
    monkeypatch.setitem(scan_routes.templates.env.globals, "scan_tile", tile)


# --- the database step ----------------------------------------------------------------------------------------

def test_step_20_adds_the_trade_columns(tmp_path):
    conn = db.connect(tmp_path / "new.db")
    db.init(conn)
    assert db.version(conn) == db.LATEST == 20
    columns = lambda table: {r[1] for r in conn.execute(f"PRAGMA main.table_info({table})")}  # noqa: E731
    assert "trade_with" in columns("scan_batches") and {"side", "from_line"} <= columns("scans")
    assert "note" in columns("inventory_log")


# --- AC1: Start a trade -----------------------------------------------------------------------------------------

def test_start_a_trade_asks_who_and_names_the_batch(client):
    _setup_with_set(client)
    page = client.get("/scans").text
    assert "Start a trade" in page and 'action="/scans/trade"' in page and "Trading with" in page
    batch_id = _trade(client, "  Brock  ")
    batch = main.conn.execute("SELECT name, trade_with FROM scan_batches WHERE id = ?", (batch_id,)).fetchone()
    assert tuple(batch) == ("Trade with Brock", "Brock")
    assert "&#8644; Trade with Brock" in client.get("/scans").text


def test_a_trade_needs_a_name(client):
    _setup_with_set(client)
    r = client.post("/scans/trade", data={"trading_with": " "}, follow_redirects=False)
    assert "/scans?error=" in r.headers["location"]
    assert main.conn.execute("SELECT COUNT(*) FROM scan_batches").fetchone()[0] == 0


# --- AC2: two sides, named, switched any time -------------------------------------------------------------------

def test_the_sides_are_named_after_who_its_with(client):
    _setup_with_set(client)
    batch_id = _trade(client)
    page = client.get(f"/scans/{batch_id}").text
    assert "Your cards" in page and "Brock&#39;s cards" in page
    assert 'data-show="mine"' in page and 'data-cam-side="mine" class="on"' in page  # starts on Your cards
    page = client.get(f"/scans/{batch_id}?side=theirs").text
    assert 'data-show="theirs"' in page and 'data-cam-side="theirs" class="on"' in page


def test_a_photo_goes_on_the_side_it_was_taken_for(client, monkeypatch):
    owner, _, worker, _ = _scan_setup(client, monkeypatch)
    monkeypatch.setattr(worker, "start", lambda wait=False: None)
    batch_id = _trade(client)
    for side in ("theirs", "", "nonsense"):
        client.post(f"/scans/{batch_id}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg"))],
                    data={"side": side}, headers={"X-Scan-Upload": "1"})
    assert [r[0] for r in main.conn.execute("SELECT side FROM scans WHERE batch_id = ? ORDER BY id", (batch_id,))] == \
        ["theirs", "mine", "mine"]
    plain = int(client.post("/scans", follow_redirects=False).headers["location"].split("?")[0].rsplit("/", 1)[1])
    client.post(f"/scans/{plain}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg"))], data={"side": "theirs"},
                headers={"X-Scan-Upload": "1"})
    assert main.conn.execute("SELECT side FROM scans WHERE batch_id = ?", (plain,)).fetchone()[0] is None


def test_a_photo_sent_later_keeps_its_side():
    queue = (ROOT / "app" / "static" / "photo-queue.js").read_text()
    assert "put: (batch, id, blob, side)" in queue and 'body.append("side", rec.side)' in queue
    page = (ROOT / "app" / "static" / "js" / "scan-batch.js").read_text()
    assert "dsPhotos.put(BATCH, rec.id, blob, rec.side)" in page and "send(s.blob, null, camSide)" in page


# --- AC3: totals and the difference ---------------------------------------------------------------------------

def test_each_side_has_a_total_and_the_difference(client, monkeypatch):
    _setup_with_set(client)
    batch_id = _trade(client)
    _photo(batch_id, "t1-1", "mine", quantity=2)
    _photo(batch_id, "t1-2", "theirs")
    _photo(batch_id, "t1-TG01", "theirs")
    _prices(monkeypatch, {"t1-1": 1.5, "t1-2": 10.0, "t1-TG01": None})
    totals = scan_routes._trade_totals(scans.get_batch(main.conn, main.conn.execute("SELECT id FROM users").fetchone()[0],
                                                       batch_id))
    assert totals["mine"]["total"] == 3.0 and totals["mine"]["cards"] == 1
    assert totals["theirs"]["total"] == 10.0 and totals["theirs"]["no_price"] == 1 and totals["difference"] == 7.0
    page = client.get(f"/scans/{batch_id}").text
    assert "Brock's cards are worth <b>$7.00 more</b>" in page and "1 no price" in page
    assert "Compare both sides" in page


# --- AC4: stash counts, and which copy leaves --------------------------------------------------------------------

def test_your_cards_say_how_many_you_have(client, monkeypatch):
    owner = _setup_with_set(client)
    _own(owner, "t1-1", 2)
    batch_id = _trade(client)
    _photo(batch_id, "t1-1", "mine")
    _photo(batch_id, "t1-2", "mine")
    _prices(monkeypatch, {})
    page = client.get(f"/scans/{batch_id}").text
    assert "In stash &times;2" in page and "Not in your stash" in page


def test_with_several_copies_you_pick_the_one_that_leaves(client, monkeypatch):
    owner = _setup_with_set(client)
    _own(owner, "t1-1", 2)
    _own(owner, "t1-1", 1, "reverse", "LP")
    batch_id = _trade(client)
    scan_id = _photo(batch_id, "t1-1", "mine")
    _prices(monkeypatch, {})
    view = client.get(f"/scans/item/{scan_id}/details", headers={"HX-Request": "true"}).text
    assert "You have this card 2 ways. Pick the one you're trading." in view and 'name="from_line"' in view
    batch = scans.get_batch(main.conn, owner, batch_id)
    assert scans.trade_problems(main.conn, batch)  # Finish trade waits for the pick
    lp = main.conn.execute("SELECT id FROM inventory WHERE card_id = 't1-1' AND condition = 'LP'").fetchone()[0]
    client.post(f"/scans/item/{scan_id}/save", data={"variant": "normal", "condition": "NM", "quantity": 1,
                                                     "from_line": lp})
    scan = scans.get_scan(main.conn, owner, scan_id)
    assert scan["from_line"] == lp and (scan["variant"], scan["condition"]) == ("reverse", "LP")
    assert scans.trade_problems(main.conn, batch) == []


def test_with_one_copy_it_is_picked_for_you(client, monkeypatch):
    owner = _setup_with_set(client)
    _own(owner, "t1-2", 3)
    batch_id = _trade(client)
    scan_id = _photo(batch_id, "t1-2", "mine")
    _prices(monkeypatch, {})
    batch = scans.get_batch(main.conn, owner, batch_id)
    assert scans.leaving_line(main.conn, scans.get_scan(main.conn, owner, scan_id), batch)["quantity"] == 3
    view = client.get(f"/scans/item/{scan_id}/details", headers={"HX-Request": "true"}).text
    assert "Leaves your stash" in view and "(you have 3)" in view and "Pick the one" not in view


def test_another_collections_copy_cant_be_picked(client):
    owner = _setup_with_set(client)
    batch_id = _trade(client)
    scan = scans.get_scan(main.conn, owner, _photo(batch_id, "t1-1", "mine"))
    try:
        scans.set_from_line(main.conn, scan, scans.get_batch(main.conn, owner, batch_id), 999)
        raise AssertionError("a copy you don't have was taken")
    except ValueError:
        pass


# --- AC5: Compare both sides -------------------------------------------------------------------------------------

def test_compare_lists_both_sides_and_closes(client, monkeypatch):
    _setup_with_set(client)
    batch_id = _trade(client)
    _photo(batch_id, "t1-1", "mine")
    _photo(batch_id, "t1-2", "theirs")
    _prices(monkeypatch, {"t1-1": 2.0, "t1-2": 5.0})
    page = client.get(f"/scans/{batch_id}").text
    compare = page[page.index('id="compare-sheet"'):page.index('id="finish-sheet"')]
    assert "Your cards &middot; 1" in compare and "Brock&#39;s cards &middot; 1" in compare
    assert "Bulbasaur" in compare and "Ivysaur" in compare and "$5.00" in compare and 'aria-label="Close"' in compare


# --- AC6 and AC7: Finish trade's sheet ---------------------------------------------------------------------------

def test_finish_warns_about_cards_with_no_price(client, monkeypatch):
    owner = _setup_with_set(client)
    binders.create(main.conn, owner, "Trades", owner)
    batch_id = _trade(client)
    _photo(batch_id, "t1-1", "mine")
    _photo(batch_id, "t1-2", "theirs")
    _prices(monkeypatch, {"t1-1": 2.0, "t1-2": None})
    page = client.get(f"/scans/{batch_id}").text
    finish = page[page.index('id="finish-sheet"'):]
    finish = finish[:finish.index("</dialog>")]
    assert "1 card has no price" in finish and ">Back</button>" in finish and ">Finish</button>" in finish
    assert "Cards leaving your stash: 0" in finish and "Finish anyway" not in finish
    assert "All of them" in finish and "Trades" in finish and 'value="none">None' in finish


def test_a_trade_has_no_count_line_set_for_all_or_from_line(client, monkeypatch):
    _setup_with_set(client)
    batch_id = _trade(client)
    _photo(batch_id, "t1-1", "mine")
    _prices(monkeypatch, {})
    page = client.get(f"/scans/{batch_id}").text
    assert 'class="bt-sum"' not in page and 'class="bt-from"' not in page and 'data-open-sheet="all-sheet"' not in page
    assert "Finish trade" in page and "data-add-all" not in page


def test_a_plain_batch_is_as_before(client, monkeypatch):
    _setup_with_set(client)
    plain = int(client.post("/scans", follow_redirects=False).headers["location"].split("?")[0].rsplit("/", 1)[1])
    page = client.get(f"/scans/{plain}").text
    assert 'class="bt-sum"' in page and 'class="bt-from"' in page and "Set for all" in page
    assert "Finish trade" not in page and "Your cards" not in page and "Add 0 to My Stash" in page


# --- AC8: finishing ----------------------------------------------------------------------------------------------

def test_finishing_swaps_the_cards_and_notes_the_history(client, monkeypatch):
    owner = _setup_with_set(client)
    trades = binders.create(main.conn, owner, "Trades", owner)
    _own(owner, "t1-1", 2)
    batch_id = _trade(client)
    _photo(batch_id, "t1-1", "mine")
    _photo(batch_id, "t1-TG01", "mine")  # not in your stash: skipped
    theirs = _photo(batch_id, "t1-2", "theirs", quantity=2)
    _prices(monkeypatch, {})
    r = client.post(f"/scans/{batch_id}/finish", data={f"binder-{theirs}": str(trades)}, follow_redirects=False)
    assert r.status_code == 303 and "finished=1" in r.headers["location"], r.headers["location"]
    assert _qty(owner, "t1-1") == 1 and _qty(owner, "t1-2") == 2
    log = main.conn.execute("SELECT card_id, delta, source, origin, note FROM inventory_log WHERE note IS NOT NULL "
                            "ORDER BY id").fetchall()
    assert [tuple(r) for r in log] == [("t1-1", -1, "trade", None, "Trade with Brock"),
                                       ("t1-2", 2, "scan", "trade", "Trade with Brock")]
    line = main.conn.execute("SELECT id FROM inventory WHERE card_id = 't1-2'").fetchone()[0]
    assert binders.placement(main.conn, owner)[line] == {trades: 2}
    assert main.conn.execute("SELECT COUNT(*) FROM scans WHERE batch_id = ? AND status = 'added'", (batch_id,)).fetchone()[0] == 3
    history = client.get("/cards/t1-2").text
    assert "Trade with Brock" in history
    assert "Trade finished" in client.get(f"/scans/{batch_id}?finished=1").text


def test_their_cards_go_in_no_binder_unless_picked(client, monkeypatch):
    owner = _setup_with_set(client)
    binders.create(main.conn, owner, "Trades", owner)
    batch_id = _trade(client)
    theirs = _photo(batch_id, "t1-2", "theirs")
    _prices(monkeypatch, {})
    client.post(f"/scans/{batch_id}/finish", data={f"binder-{theirs}": "none"})
    line = main.conn.execute("SELECT id FROM inventory WHERE card_id = 't1-2'").fetchone()[0]
    assert binders.placement(main.conn, owner)[line] == {None: 1}


def test_a_copy_in_a_binder_leaves_it(client, monkeypatch):
    owner = _setup_with_set(client)
    binder = binders.create(main.conn, owner, "Main", owner)
    _own(owner, "t1-1", 1)
    line = main.conn.execute("SELECT id FROM inventory WHERE card_id = 't1-1'").fetchone()[0]
    binders.put_some(main.conn, owner, {line: 1}, binder)
    main.conn.commit()
    batch_id = _trade(client)
    _photo(batch_id, "t1-1", "mine")
    _prices(monkeypatch, {})
    client.post(f"/scans/{batch_id}/finish", data={})
    assert _qty(owner, "t1-1") == 0
    assert not main.conn.execute("SELECT 1 FROM binder_copies WHERE binder_id = ?", (binder,)).fetchone()


def test_finish_waits_until_the_copy_is_picked(client, monkeypatch):
    owner = _setup_with_set(client)
    _own(owner, "t1-1", 1)
    _own(owner, "t1-1", 1, "reverse", "LP")
    batch_id = _trade(client)
    _photo(batch_id, "t1-1", "mine")
    _prices(monkeypatch, {})
    page = client.get(f"/scans/{batch_id}").text
    assert "Pick which copy of Bulbasaur you&#39;re trading." in page
    r = client.post(f"/scans/{batch_id}/finish", data={}, follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert _qty(owner, "t1-1") == 1 and _qty(owner, "t1-1", "reverse", "LP") == 1  # nothing changed


def test_finish_is_only_for_trades(client):
    _setup_with_set(client)
    plain = int(client.post("/scans", follow_redirects=False).headers["location"].split("?")[0].rsplit("/", 1)[1])
    assert client.post(f"/scans/{plain}/finish", data={}).status_code == 404
