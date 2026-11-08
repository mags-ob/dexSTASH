"""Round 16 (0.16.0): the first-run wizard."""
import pytest
from fastapi.testclient import TestClient

from app import __version__, auth, card_index, db, main, web, welcome
from app.routes import welcome as pages
from test_app import _entry, _setup_with_set, client  # noqa: F401


@pytest.fixture
def fresh(client, monkeypatch):
    """A new install: no accounts, and the wizard not done."""
    monkeypatch.setattr(welcome, "_override", None)
    monkeypatch.setattr(web.sites, "_override", dict(web.sites._override))  # step 3 switches sites; put them back after
    main.conn.execute("DELETE FROM app_meta WHERE key IN ('setup_done', 'setup_step', 'setup_choices', 'getting_started', 'setup_preparing')")
    main.conn.commit()
    return client


@pytest.fixture
def started(monkeypatch):
    """What Finish starts, recorded instead of run."""
    calls = []
    monkeypatch.setattr(card_index, "start", lambda api, **k: calls.append(("catalog", getattr(api, "lang", "en"))) or True)
    monkeypatch.setattr(pages, "start_pokemon_data", lambda with_descriptions=True: calls.append(("pokemon", with_descriptions)) or True)
    monkeypatch.setattr(pages, "start_enhanced_search", lambda: calls.append(("enhanced",)) or True)
    monkeypatch.setattr(pages.background, "start", lambda key, label, fn, wait=False, **k: calls.append((key,)) or True)
    monkeypatch.setattr(pages.web.updater, "start", lambda *a, **k: calls.append(("prices",)) or True)
    monkeypatch.setattr(pages.background, "last", lambda key: _Done(key))  # round 23: the basics are done at once
    monkeypatch.setattr(pages.pictures, "available", lambda: True)
    return calls


class _Done:
    waiting = running = False
    error = None

    def __init__(self, key):
        self.label = f"Downloading {key}"


def _finish(client, data=None):
    """Finish (step 6), then Continue once Getting your stash ready is done (round 23)."""
    r = client.post("/welcome/6", data=data or {}, follow_redirects=False)
    assert r.headers["location"] == "/welcome?step=7", r.text[:200]
    return client.post("/welcome/7", follow_redirects=False)


def _admin(client):
    r = client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"},
                    follow_redirects=False)
    assert r.status_code == 303


def _password(client):
    r = client.post("/welcome/1", data={"current": "pikachu123", "new": "togepi-2026", "again": "togepi-2026"},
                    follow_redirects=False)
    assert r.headers["location"] == "/welcome?step=2"


def _through(client, langs=("en",), sites=(), first=""):
    _password(client)
    client.post("/welcome/2", data={"zone": "America/New_York", "currency": "USD"})
    client.post("/welcome/3", data={**{f"lang-{lang}": "1" for lang in langs}, **{f"site-{s}": "1" for s in sites},
                                    "nightly": "1", "at": "03:30"})
    client.post("/welcome/4", data={"pokemon": "1"})
    client.post("/welcome/5", data={"first": first})


# --- when it shows -----------------------------------------------------------------------------------------

def test_the_first_admin_gets_the_wizard_before_any_page(fresh):
    _admin(fresh)
    assert fresh.get("/", follow_redirects=False).headers["location"] == "/welcome"
    for page in ("/collection", "/search", "/account", "/account/data"):
        assert fresh.get(page, follow_redirects=False).headers["location"] == "/welcome", page
    r = fresh.get("/prices/status", headers={"HX-Request": "true"})
    assert r.status_code == 204 and r.headers["HX-Redirect"] == "/welcome"
    page = fresh.get("/welcome").text
    assert "Step 1 of 6" in page and "Change your password" in page and "Skip" not in page
    assert 'class="tabs"' not in page  # no way round it
    assert fresh.post("/logout", follow_redirects=False).headers["location"] == "/login"


def test_an_install_in_use_never_sees_it(client):
    _setup_with_set(client)
    _entry(client, "t1", "1")
    main.conn.execute("DELETE FROM app_meta WHERE key = 'setup_done'")
    db._step_10(main.conn)
    assert welcome._meta(main.conn, "setup_done") == "1"
    # One login and nothing in it yet (an install made this morning): the wizard shows.
    main.conn.execute("DELETE FROM app_meta WHERE key = 'setup_done'")
    main.conn.execute("DELETE FROM inventory")
    db._step_10(main.conn)
    assert welcome._meta(main.conn, "setup_done") is None
    # Two logins: in use.
    auth.create_user(main.conn, "brock", "onix-rocks1", collection_id=main.conn.execute("SELECT id FROM users").fetchone()[0])
    db._step_10(main.conn)
    assert welcome._meta(main.conn, "setup_done") == "1"
    assert db.LATEST >= 10


def test_someone_else_waits_for_the_admin(fresh):
    _admin(fresh)
    owner = main.conn.execute("SELECT id FROM users").fetchone()[0]
    auth.create_user(main.conn, "brock", "onix-rocks1", collection_id=owner)
    brock = TestClient(main.app)
    brock.post("/login", data={"username": "brock", "password": "onix-rocks1"})
    assert brock.get("/", follow_redirects=False).headers["location"] == "/welcome"
    page = brock.get("/welcome").text
    assert "Your admin is still setting up" in page and "Change your password" not in page
    assert brock.post("/welcome/2", data={"zone": "UTC", "currency": "USD"}).status_code == 403


# --- step 1: the password ------------------------------------------------------------------------------------

def test_the_password_is_changed_first(fresh):
    _admin(fresh)
    bad = {"current": "wrong-one", "new": "togepi-2026", "again": "togepi-2026"}
    assert "current password isn" in fresh.post("/welcome/1", data=bad).text
    same = {"current": "pikachu123", "new": "pikachu123", "again": "pikachu123"}
    assert "not the one you have" in fresh.post("/welcome/1", data=same).text
    short = {"current": "pikachu123", "new": "short", "again": "short"}
    assert fresh.post("/welcome/1", data=short).status_code == 400
    # No later step before it.
    assert fresh.post("/welcome/2", data={"zone": "UTC", "currency": "USD"}).status_code == 409
    _password(fresh)
    assert auth.authenticate(main.conn, "misty", "togepi-2026") and not auth.authenticate(main.conn, "misty", "pikachu123")
    assert "Step 2 of 6" in fresh.get("/welcome").text  # still signed in on this device
    # The password step is never shown again, and Back stops at step 2.
    assert "Change your password" not in fresh.get("/welcome?step=1").text
    assert fresh.post("/welcome/1", data={"current": "togepi-2026", "new": "x" * 10, "again": "x" * 10}).status_code == 409


def test_an_admin_from_env_is_told_to_take_the_password_out(fresh, monkeypatch):
    monkeypatch.setenv("DEXSTASH_ADMIN_USER", "misty")
    monkeypatch.setenv("DEXSTASH_ADMIN_PASSWORD", "pikachu123")
    web._first_account()
    fresh.post("/login", data={"username": "misty", "password": "pikachu123"})
    page = fresh.get("/welcome").text
    assert "Change your password" in page and "DEXSTASH_ADMIN_PASSWORD" in page
    assert "pikachu123" not in page  # names only, never the value


# --- the other steps -----------------------------------------------------------------------------------------

def test_each_step_saves_and_finish_starts_the_downloads(fresh, started, monkeypatch):
    monkeypatch.setattr(card_index, "enabled", lambda: True)
    _admin(fresh)
    _password(fresh)
    fresh.post("/welcome/2", data={"zone": "America/Chicago", "currency": "EUR"})
    assert web.display.zone().key == "America/Chicago" and web.fx.setting() == "EUR"
    assert "Pick at least one language" in fresh.post("/welcome/3", data={"nightly": "1"}).text
    fresh.post("/welcome/3", data={"lang-en": "1", "lang-ja": "1", "lang-zh-cn": "1", "site-chinese": "1",
                                   "site-bulbapedia": "1", "nightly": "1", "at": "03:30"})
    assert web.sites.on("chinese") and web.sites.on("bulbapedia") and not web.sites.on("limitless")
    assert web.sync.nightly_settings(main.conn)["time"] == "03:30"
    page = fresh.get("/welcome?step=4").text
    for text in ("Card catalog for Find: English", "Card catalog for Find: Japanese", "Pokémon data", "About 13 MB",
                 "Bulbapedia descriptions", "Mainland Chinese sets", "About 26 MB downloaded, 2 MB kept",
                 "Enhanced Search", "1.3 GB downloaded", "data-wz-sum"):
        assert text in page, text
    assert "Card catalog for Find: Chinese (mainland)" not in page  # those come from the datasets
    fresh.post("/welcome/4", data={"pokemon": "1", "descriptions": "1"})
    fresh.post("/welcome/5", data={"first": "search"})
    r = fresh.post("/welcome/6", data={"username": "brock", "password": "onix-rocks1"}, follow_redirects=False)
    assert r.headers["location"] == "/welcome?step=7" and not welcome.done(main.conn)
    # The basics, one after another in this order; Bulbapedia's descriptions wait for Continue.
    started[:] = [c for c in started if c != ("currency",)]  # step 2's prices in your currency
    assert started == [("first-basics",), ("pokemon", False), ("catalog", "en"), ("catalog", "ja"), ("chs",)]
    page = fresh.get("/welcome").text
    assert "Getting your stash ready" in page and "Continue" in page and "Downloading card-index:ja" in page
    assert fresh.get("/search", follow_redirects=False).headers["location"] == "/welcome"  # no leaving meanwhile
    r = fresh.post("/welcome/7", follow_redirects=False)
    assert r.headers["location"] == "/"  # round 26: step 5 offers only Collectr
    assert started[5:] == [("prices",), ("pokemon", True)] and ("enhanced",) not in started  # left unticked
    assert welcome.done(main.conn) and auth.authenticate(main.conn, "brock", "onix-rocks1")
    assert fresh.get("/", follow_redirects=False).status_code == 200
    assert fresh.get("/welcome", follow_redirects=False).headers["location"] == "/"


def test_a_partner_login_with_a_problem_stays_on_the_last_step(fresh, started):
    _admin(fresh)
    _through(fresh)
    r = fresh.post("/welcome/6", data={"username": "brock", "password": "short"})
    assert r.status_code == 400 and "at least" in r.text and 'value="brock"' in r.text
    assert not welcome.done(main.conn) and started == [("currency",)]
    assert _finish(fresh).headers["location"] == "/"
    assert auth.user_count(main.conn) == 1


def test_choices_follow_the_server(fresh, started, monkeypatch):
    """No Bulbapedia row without the site; no Enhanced Search or Scan without scanning."""
    monkeypatch.setattr(web, "SCANNER_MODE", "off")
    _admin(fresh)
    _through(fresh)
    page = fresh.get("/welcome?step=4").text
    assert "Bulbapedia descriptions" not in page and "Enhanced Search" not in page and "Mainland Chinese" not in page
    assert 'value="scan"' not in fresh.get("/welcome?step=5").text
    fresh.post("/welcome/4", data={"pokemon": "1", "descriptions": "1", "enhanced": "1"})
    _finish(fresh)
    assert ("enhanced",) not in started and ("chs",) not in started


# --- Home's Getting started card -----------------------------------------------------------------------------

def test_getting_started_ticks_itself_and_hides(fresh, started, monkeypatch):
    _admin(fresh)
    _through(fresh)
    _finish(fresh)
    page = fresh.get("/").text
    assert "Getting started &middot; 1 of 4" in page  # the catalog line only shows with the catalog on
    monkeypatch.setattr(card_index, "enabled", lambda: True)
    monkeypatch.setattr(card_index, "building", lambda: {"en"})
    page = fresh.get("/").text
    assert "1 of 5" in page and "Card catalog downloaded" in page
    fresh.post("/sets", data={"set_id": "t1"})
    _entry(fresh, "t1", "1 x3")
    assert "3 so far" in fresh.get("/").text
    fresh.post("/collection/binders", data={"name": "Trades"})
    assert fresh.post("/welcome/hide", follow_redirects=False).headers["location"] == "/"
    assert "Getting started" not in fresh.get("/").text


def test_no_card_on_installs_that_skipped_the_wizard(client):
    _setup_with_set(client)
    assert "Getting started" not in client.get("/").text


def test_sizes_are_shown_for_every_download():
    for key in welcome.SIZES:
        assert welcome.size_text(key).startswith("About ")
    assert welcome.total(["pokemon", "chinese"]) == (39, 15)


def test_version():
    assert tuple(int(x) for x in __version__.split(".")[:2]) >= (0, 16)
