"""CardTrader's long token is saved and Save says what happened on the key's row (#64); Data sources shows the
latest full price run only, the older ones on their own page (#65)."""
import json

import pytest

from app import cardtrader, keys, main, sync, upgrades
from test_app import _login, _setup_with_set, client  # noqa: F401

# A CardTrader token is a signed JWT: header, payload and an RS256 signature, well over 500 characters.
LONG_TOKEN = "eyJhbGciOiJSUzI1NiJ9." + "a" * 220 + "." + "b" * 342 + "Xk9Q"


@pytest.fixture
def key_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(keys, "folder", tmp_path)
    monkeypatch.delenv(cardtrader.TOKEN_ENV, raising=False)
    return tmp_path


def test_a_long_cardtrader_token_is_saved_and_read_back(client, key_folder, monkeypatch):
    _setup_with_set(client)
    monkeypatch.setattr(cardtrader, "test", lambda: (True, "Works (Misty's shop)."))
    assert len(LONG_TOKEN) > 500
    r = client.post("/account/data/keys/cardtrader", data={"key": LONG_TOKEN}, follow_redirects=False)
    assert "key_ok=1" in r.headers["location"] and r.headers["location"].endswith("#key-cardtrader")
    assert keys.get("cardtrader") == LONG_TOKEN and cardtrader.token() == LONG_TOKEN
    page = client.get("/account/data").text
    assert "CardTrader: no key" not in page and "&bull;&bull;&bull;&bull;Xk9Q" in page and LONG_TOKEN not in page


def test_spaces_and_line_breaks_from_a_paste_are_dropped(key_folder):
    keys.save("cardtrader", "  eyJhbG.ciOi\nJSUz I1Ni\r\n")
    assert keys.get("cardtrader") == "eyJhbG.ciOiJSUzI1Ni"


def test_an_empty_or_far_too_long_key_is_not_saved(key_folder):
    with pytest.raises(ValueError, match="Not saved: the box was empty"):
        keys.save("cardtrader", " \n ")
    with pytest.raises(ValueError, match="Not saved: that's longer than any key"):
        keys.save("cardtrader", "a" * (keys.MAX_LENGTH + 1))
    assert keys.get("cardtrader") is None


def _row(page: str, site: str) -> str:
    start = page.index(f'id="key-{site}"')
    end = page.find('id="key-', start + 10)
    return page[start:end if end > 0 else page.index("</details>", start)]


def test_save_says_what_happened_on_the_keys_row(client, key_folder, monkeypatch):
    _setup_with_set(client)
    monkeypatch.setattr(cardtrader, "test", lambda: (True, "Works (Misty's shop)."))
    page = client.post("/account/data/keys/cardtrader", data={"key": LONG_TOKEN}).text
    row = _row(page, "cardtrader")
    assert '<p class="ok" role="status"' in row and "Saved. Works (Misty&#39;s shop)." in row
    assert '<p class="" role="status">Saved.' not in page  # not at the top of the page, out of view
    # A key the site refuses is saved, and the row says so in red.
    monkeypatch.setattr(cardtrader, "test", lambda: (False, "CardTrader refused this token."))
    row = _row(client.post("/account/data/keys/cardtrader", data={"key": LONG_TOKEN}).text, "cardtrader")
    assert '<p class="error" role="status"' in row and "Saved. CardTrader refused this token." in row
    # Nothing pasted: red on the row, and nothing stored.
    row = _row(client.post("/account/data/keys/pricecharting", data={"key": "  "}).text, "pricecharting")
    assert '<p class="error" role="status"' in row and "Not saved: the box was empty." in row
    assert keys.get("pricecharting") is None


def test_a_long_env_token_moves_at_start_up(key_folder, monkeypatch):
    monkeypatch.setenv(cardtrader.TOKEN_ENV, LONG_TOKEN)
    upgrades.move_keys(main.conn)
    monkeypatch.delenv(cardtrader.TOKEN_ENV)
    assert keys.get("cardtrader") == LONG_TOKEN and keys.info("cardtrader")["last4"] == "Xk9Q"


def _runs(statuses: list[str]) -> None:
    """Full price runs, oldest first, one a day."""
    main.conn.execute("DELETE FROM price_runs")
    main.conn.commit()
    for day, status in enumerate(statuses, start=1):
        errors = 0 if status == "ok" else 1
        sync.record_run(main.conn, f"2026-11-{day:02d}T03:00:00Z", status, {"kind": "full", "sources": {
            "tcgcsv": {"sets": day, "errors": 0, "first_errors": []},
            "cardtrader": {"cards": 0, "errors": errors, "first_errors": ["CardTrader answered 503"] * errors}}})


def _runs_block(page: str) -> str:
    start = page.index('class="ds-runs"')
    return page[start:page.index("</details>", start)]


def test_data_sources_shows_the_latest_run_only(client):
    _setup_with_set(client)
    _runs(["ok", "ok", "ok"])
    block = _runs_block(client.get("/account/data").text)
    assert block.count('class="ds-run"') == 1 and "3 sets read" in block and "2 sets read" not in block
    assert "Last price update: all fine" in block and block.startswith('class="ds-runs" style="margin-top:10px">')  # closed
    assert '<a href="/account/data/price-updates">Older price updates (2) &rsaquo;</a>' in block


def test_no_older_link_with_one_run(client):
    _setup_with_set(client)
    _runs(["ok"])
    assert "Older price updates" not in _runs_block(client.get("/account/data").text)


def test_a_failed_latest_run_still_opens_with_its_problems(client):
    _setup_with_set(client)
    _runs(["ok", "partial"])
    page = client.get("/account/data").text
    assert '<details class="ds-runs" style="margin-top:10px" open>' in page
    assert "some problems" in _runs_block(page) and "CardTrader answered 503" in _runs_block(page)


def test_older_price_updates_lists_the_rest_newest_first(client):
    _setup_with_set(client)
    _runs(["ok", "partial", "ok", "ok"])
    page = client.get("/account/data/price-updates").text
    assert "Older price updates" in page and page.count('class="ds-run"') == 3 and "4 sets read" not in page
    assert page.index("3 sets read") < page.index("2 sets read") < page.index("1 sets read")
    assert "CardTrader answered 503" in page


def test_older_price_updates_with_none(client):
    _setup_with_set(client)
    _runs(["ok"])
    assert "No older price updates yet." in client.get("/account/data/price-updates").text


def test_older_price_updates_only_for_the_owner(client):
    _setup_with_set(client)
    client.post("/account/users", data={"username": "sam", "password": "squirtle99"})
    assert _login("sam", "squirtle99").get("/account/data/price-updates").status_code == 403


def test_run_summaries_kept_stay_at_thirty(client):
    _setup_with_set(client)
    main.conn.execute("DELETE FROM price_runs")
    main.conn.commit()
    for day in range(1, 33):
        sync.record_run(main.conn, f"2026-10-{day:02d}T03:00:00Z", "ok", {"kind": "full", "sources": {}})
    assert sync.run_count(main.conn) == sync.RUNS_KEPT == 30
    assert len(sync.runs(main.conn, sync.RUNS_KEPT, skip=1)) == 29
    assert json.dumps(sync.runs(main.conn, 1)[0]["ran_at"]) == '"2026-10-32T03:00:00Z"'
