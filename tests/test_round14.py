"""Round 14: the open-source round."""
import re
from pathlib import Path

from starlette.routing import Match

from app import main

ROOT = Path(__file__).resolve().parent.parent


def _routes():
    """The app's pages in the order they are tried (each app/routes module's router holds its own)."""
    out = []
    for r in main.app.routes:
        out.extend(r.original_router.routes if hasattr(r, "original_router") else [r])
    return out


def _before():
    """tests/routes.txt: the table from before main.py was split, in the order it was tried, with the pages
    round 14 adds (each added there by hand)."""
    return [tuple(line.split(" ")) for line in (ROOT / "tests" / "routes.txt").read_text().splitlines() if line]


def _pattern(path: str) -> str:
    return "".join(".+" if part.endswith(":path}") else "[^/]+" if part.startswith("{") else re.escape(part)
                   for part in re.split(r"(\{[^}]+\})", path))


def test_splitting_main_kept_every_address():
    """Item 1: the pages moved to app/routes/ with the same addresses, methods and names."""
    now = [(",".join(sorted(getattr(r, "methods", None) or [])) or "MOUNT", r.path, getattr(r, "name", ""))
           for r in _routes() if not r.path.startswith("/_test/")]  # pages other tests add for themselves
    assert sorted(now) == sorted(_before())


def test_no_address_is_answered_by_a_different_page_than_before():
    """The routers are tried one after another, so an address could reach a page from another file first
    (like /cards/new and /cards/{card_id}). Each address still goes to the page it went to before."""
    before = [b for b in _before() if b[0] != "MOUNT"]
    for methods, path, _ in before:
        sample = re.sub(r"\{[^}]+\}", "1", path)
        for method in methods.split(","):
            expected = next(n for m, p, n in before if method in m.split(",") and re.fullmatch(_pattern(p), sample))
            scope = {"type": "http", "path": sample, "method": method, "root_path": ""}
            got = next(r.name for r in _routes() if getattr(r, "methods", None) and r.matches(scope)[0] == Match.FULL)
            assert got == expected, (method, sample)


# --- 3: the old name is gone ----------------------------------------------------------------------
# The old name is out of the code and its history; the Checks workflow's private word list (a repository secret,
# see tools/check_rules.py) keeps it, and anyone's personal details, from coming back.

OLD = "oldapp"  # any name other than dexstash stands in for the old one
SKIP_DIRS = {".git", "data", "dist", "__pycache__", ".pytest_cache", "caddy", ".venv", "venv"}


def test_a_setting_with_another_prefix_is_named_in_the_log(caplog):
    from app import settings
    env = {"OLDAPP_SECRET_KEY": "x" * 40, "OLDAPP_PROXY_IPS": "1.2.3.4", "DEXSTASH_PROXY_IPS": "1.2.3.4",
           "DUCKDNS_TOKEN": "t", "DEXSTASH_HTTPS_ONLY": "1", "PATH": "/bin"}
    assert settings.misnamed(env) == [("OLDAPP_PROXY_IPS", "DEXSTASH_PROXY_IPS"),
                                      ("OLDAPP_SECRET_KEY", "DEXSTASH_SECRET_KEY")]
    with caplog.at_level("ERROR", logger="dexstash"):
        settings.check_names(env)
    assert "OLDAPP_SECRET_KEY in .env is not used" in caplog.text and "Rename it to DEXSTASH_SECRET_KEY" in caplog.text
    assert "You can delete the OLDAPP_PROXY_IPS line" in caplog.text
    assert settings.misnamed({"DEXSTASH_SECRET_KEY": "x"}) == []


def test_backups_from_before_the_rename_get_the_new_name(tmp_path):
    from app import backup
    old = OLD + "-20260101-000000-manual.db"
    (tmp_path / old).write_bytes(b"1")
    (tmp_path / (OLD + "-20260102-000000-upgrade.db")).write_bytes(b"2")
    (tmp_path / "dexstash-20260102-000000-upgrade.db").write_bytes(b"new")  # never written over
    (tmp_path / "notes.txt").write_text("not a backup")
    assert backup.adopt_old_names(tmp_path) == 1
    assert (tmp_path / "dexstash-20260101-000000-manual.db").read_bytes() == b"1"
    assert (tmp_path / "dexstash-20260102-000000-upgrade.db").read_bytes() == b"new"
    assert (tmp_path / "notes.txt").exists()
    assert {b["name"] for b in backup.list_backups(tmp_path)} >= {"dexstash-20260101-000000-manual.db"}


# --- 5: price and picture site switches, one User-Agent -----------------------------------------------

import httpx  # noqa: E402
import pytest  # noqa: E402

from app import db, extras, sites, web  # noqa: E402
from test_app import client, _setup_with_set  # noqa: E402,F401


@pytest.fixture
def switches(monkeypatch):
    """The switches pinned for one test: all on, then each test turns some off."""
    state = {s: True for s in sites.SITES}
    monkeypatch.setattr(sites, "_override", state)
    return state


def test_one_user_agent_for_every_site(switches):
    from app import __version__
    seen = []
    transport = httpx.MockTransport(lambda request: seen.append(request.headers["user-agent"]) or httpx.Response(200))
    with sites.client(transport=transport, headers={"User-Agent": "Mozilla/5.0"}) as c:
        c.get("https://api.tcgdex.net/v2/en/sets")
        c.get("https://tcgcsv.com/tcgplayer/3/groups")
    assert seen == [f"dexSTASH/{__version__} (+https://github.com/mags-ob/dexSTASH)"] * 2
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text()
        assert "Mozilla" not in text and "httpx.get(" not in text and "httpx.Client(" not in text or path.name == "sites.py", path


def test_a_site_that_is_off_gets_no_request_not_even_a_redirect(switches):
    switches["limitless"] = False
    sent = []

    def answer(request):
        sent.append(str(request.url))
        if "tcgdex" in str(request.url):
            return httpx.Response(302, headers={"location": "https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/x.png"})
        return httpx.Response(200)
    with sites.client(transport=httpx.MockTransport(answer), follow_redirects=True) as c:
        with pytest.raises(sites.Off):
            c.get("https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/x.png")
        with pytest.raises(sites.Off):
            c.get("https://assets.tcgdex.net/x")  # its redirect to Limitless is refused too
    assert sent == ["https://assets.tcgdex.net/x"]
    assert sites.site_of("https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/tpc/SV1/SV1_1_R_JP_SM.png") == "limitless"
    assert sites.site_of("https://raw.githubusercontent.com/duanxr/PTCG-CHS-Datasets/main/x.json") == "chinese"
    assert sites.site_of("https://raw.githubusercontent.com/PokeAPI/sprites/master/x.png") is None  # always on
    assert sites.site_of("https://assets.tcgdex.net/en/sv/sv1/1/low.webp") is None


def test_limitless_chinese_and_bulbapedia_switches(switches, tmp_path):
    from app import bulbapedia, card_pictures, chinese
    jp = card_pictures.limitless("ja:SV1a-001")
    assert jp and card_pictures._unique([(jp, "Japanese")]) == [(jp, "Japanese")]
    switches["limitless"] = switches["chinese"] = switches["bulbapedia"] = False
    assert card_pictures._unique([(jp, "Japanese"), ("/card-pictures/x.jpg", "saved")]) == [("/card-pictures/x.jpg", "saved")]
    with pytest.raises(sites.Off):
        chinese.picture("12", "3", fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)), folder=tmp_path)
    (tmp_path / "12").mkdir()
    (tmp_path / "12" / "3.jpg").write_bytes(b"kept")
    assert chinese.picture("12", "3", folder=tmp_path) == tmp_path / "12" / "3.jpg"  # one already here still shows
    assert "off in Data sources" in chinese.load_all(None, fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)))
    got = bulbapedia.Descriptions(tmp_path / "b", get=lambda url: (_ for _ in ()).throw(AssertionError(url)), pause=0)
    assert "off in Data sources" in got.download({1: "Bulbasaur"})


def _database_at_step_4(path, with_user: bool):
    conn = db.connect(path)
    db.init(conn)
    conn.execute("DELETE FROM app_meta WHERE key LIKE 'site_%'")
    conn.execute("PRAGMA user_version = 4")
    if with_user:
        conn.execute("INSERT INTO users (username, password_hash, collection_id) VALUES ('misty', 'x', 1)")
    conn.commit()
    return conn


def test_an_install_with_accounts_keeps_every_site_on_and_later_updates_change_nothing(tmp_path, switches):
    conn = _database_at_step_4(tmp_path / "old.db", with_user=True)
    db.init(conn)  # the round 14 update
    rows = dict(conn.execute("SELECT key, value FROM app_meta WHERE key LIKE 'site_%'").fetchall())
    assert rows | {f"site_{s}": "1" for s in sites.SITES} == rows  # (and the two round 15b removed, unused)
    sites.set_on(conn, "limitless", False)
    conn.commit()
    conn.execute("PRAGMA user_version = 4")  # as if step 5 ran again: it never turns a switch back on
    db.init(conn)
    assert conn.execute("SELECT value FROM app_meta WHERE key = 'site_limitless'").fetchone()[0] == "0"


def test_a_new_install_starts_with_every_site_off(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "new.db")
    db.init(conn)
    rows = dict(conn.execute("SELECT key, value FROM app_meta WHERE key LIKE 'site_%'").fetchall())
    assert all(rows[f"site_{s}"] == "0" for s in sites.SITES)
    monkeypatch.setattr(sites, "_override", None)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "new.db")
    monkeypatch.setattr(sites, "_cache_at", 0.0)
    real = db.connect
    monkeypatch.setattr(db, "connect", lambda path=None, autocommit=True: real(path or tmp_path / "new.db", autocommit))
    assert sites.states() == {s: False for s in sites.SITES}
    sites.set_on(conn, "limitless", True)
    conn.commit()
    assert sites.on("limitless") and not sites.on("chinese")


def test_data_sources_switches(client, switches):
    _setup_with_set(client)
    page = client.get("/account/data").text
    assert '<h2 id="sites">Price and picture sites</h2>' in page and 'name="limitless" value="1" checked' in page
    assert 'name="pricecharting"' not in page and 'name="tcgplayer"' not in page  # round 15b: never read
    r = client.post("/account/data/sites", data={"limitless": "1"})
    assert "Off: Chinese card datasets, Bulbapedia. Nothing was deleted." in r.text
    assert switches == {"limitless": True, "chinese": False, "bulbapedia": False}
    assert 'name="chinese" value="1">' in r.text and 'name="limitless" value="1" checked' in r.text
    assert "Load all Chinese sets" not in r.text and "Download descriptions" not in r.text


# --- 6: one paste box for a price link --------------------------------------------------------------

def test_one_paste_box_works_out_the_site(client, switches, monkeypatch):
    _setup_with_set(client)
    page = client.get("/cards/t1-1").text
    assert "Paste a price link for this card" in page and 'name="tcgplayer_url"' not in page
    r = client.post("/cards/t1-1/extras", data={"link": "https://www.pricecharting.com/game/pokemon-test/bulbasaur-1"})
    assert "Saved the PriceCharting link." in r.text
    r = client.post("/cards/t1-1/extras", data={"link": "https://www.cardmarket.com/en/Pokemon/Products/Singles/x"})
    assert "Saved the cardmarket.com link." in r.text
    extra = extras.get(main.conn, "t1-1")
    assert extra["pricecharting_url"].endswith("/bulbasaur-1") and extra["other_url"].startswith("https://www.cardmarket.com/")
    # Each saved link shows by its site's name, to open or remove.
    assert '>PriceCharting &#8599;</a>' in r.text and '>cardmarket.com &#8599;</a>' in r.text
    assert 'name="remove" value="pricecharting"' in r.text and 'name="remove" value="other"' in r.text
    r = client.post("/cards/t1-1/extras", data={"link": "https://www.tcgplayer.com/product/77"})
    assert "Saved the TCGplayer link." in r.text
    assert extras.get(main.conn, "t1-1")["tcgplayer_url"] == "https://www.tcgplayer.com/product/77"  # kept
    r = client.post("/cards/t1-1/extras", data={"link": "", "remove": "other"})
    assert "Removed the cardmarket.com link." in r.text and extras.get(main.conn, "t1-1")["other_url"] is None
    r = client.post("/cards/t1-1/extras", data={"link": "not a link"})
    assert "That isn&#39;t a link." in r.text


# --- 7: the version -------------------------------------------------------------------------------

def test_the_version_is_shown_and_matches_the_image_label(client):
    from app import REPO_URL, __version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
    docker = (ROOT / "Dockerfile").read_text()
    assert f'org.opencontainers.image.version="{__version__}"' in docker
    assert f'org.opencontainers.image.source="{REPO_URL}"' in docker
    _setup_with_set(client)
    page = client.get("/account").text
    assert f"dexSTASH {__version__}" in page and REPO_URL in page and "Only while an admin has them switched on" in page


# --- 2: the logo --------------------------------------------------------------------------------------

def test_the_pixel_card_logo_is_everywhere(client):
    from PIL import Image
    static = ROOT / "app" / "static"
    for name, size in (("favicon-32.png", 32), ("icon-180.png", 180), ("icon-192.png", 192), ("icon-512.png", 512)):
        assert Image.open(static / name).size == (size, size), name
    assert 'shape-rendering="crispEdges"' in (static / "logo.svg").read_text()
    _setup_with_set(client)
    page = client.get("/account").text
    assert page.count('/static/logo.svg?v=') == 2  # the header and About (round 25: My Stash's menu link and tab are a line icon)
    icons = client.get("/manifest.webmanifest").json()["icons"]
    assert [(i["sizes"], i["purpose"]) for i in icons] == [("192x192", "any"), ("512x512", "any"), ("512x512", "maskable"),
                                                           ("512x512", "monochrome")]  # round 19: Android's too
    for path in [*(ROOT / "app").rglob("*.py"), *(ROOT / "app" / "templates").glob("*.html")]:
        assert "logo" not in path.read_text().lower() or "gengar" not in path.read_text().lower(), path


# --- 8: GitHub Actions, ready but not run -------------------------------------------------------------

def test_workflows_run_only_when_asked():
    import yaml
    tests = yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text())
    image = yaml.safe_load((ROOT / ".github" / "workflows" / "image.yml").read_text())
    assert set(tests[True]) == {"push", "pull_request", "workflow_dispatch", "workflow_call"}  # yaml reads "on" as True; round 26: on push, and before an image
    assert image[True]["release"] == {"types": ["published"]}  # nothing is built until a release is published
    assert image["jobs"]["image"]["strategy"]["matrix"]["arch"] == ["amd64", "arm64"]


# --- Item 4: docs for strangers -------------------------------------------------------------------------------

DOCS = ["README.md", "DEPLOY.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", "NOTICE.md", "docs/architecture.md",
        "docs/configuration.md", "docs/updating.md", "docs/backups.md", "docs/scanning.md"]


def test_every_link_and_picture_in_the_docs_is_there():
    missing = []
    for name in DOCS:
        text = (ROOT / name).read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)\s]+)\)", text) + re.findall(r'src="([^"]+)"', text):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            if not (ROOT / name).parent.joinpath(target.split("#")[0]).exists():
                missing.append(f"{name}: {target}")
    assert missing == []


def test_no_dated_design_history_in_the_repository():
    """Design history stays out of the repository (personal details are tools/check_rules.py's job)."""
    assert not (ROOT / "pokemon-card-app.md").exists()
    assert not [p for p in (ROOT / "docs").glob("*.md") if re.search(r"\d{4}-\d{2}-\d{2}", p.name)]
    assert not re.search(r"\bround \d", (ROOT / "README.md").read_text() + (ROOT / "DEPLOY.md").read_text(), re.I)


def test_the_diagrams_match_the_code():
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    import make_diagrams
    f = make_diagrams.facts()
    overview = (ROOT / "docs" / "architecture-overview.svg").read_text()
    detailed = (ROOT / "docs" / "architecture-detailed.svg").read_text()
    assert f"{len(f['routes'])} page modules, {f['route_total']} routes" in overview
    assert f"{len(f['tables'])} tables" in overview and f"{len(f['tables'])} tables" in detailed
    assert f"{f['route_total']} routes in {len(f['routes'])} modules" in detailed
    for name, _ in make_diagrams.SWITCHED:
        assert name in overview
    assert [n for n, _ in make_diagrams.SWITCHED] == [label for label, _ in sites.SITES.values()]
