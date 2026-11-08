"""Reliability round (21 Oct 2026): items 1-8 of the code review. Nothing changes on screen."""
import threading

import pytest

from app import db, main
from app import web
from tests.test_app import client  # noqa: F401  (the signed-in test app)


# --- 1: each thread has its own connection; a sync page's writes are one transaction ---------------

def test_a_failed_page_keeps_nothing_and_undoes_nothing_else(client, tmp_path):
    conn = db.ThreadConnection(tmp_path / "t.db")
    conn.execute("CREATE TABLE t (x)")
    started, other_done = threading.Event(), threading.Event()

    def failing_page():
        with pytest.raises(ValueError):
            with conn.request():
                conn.execute("INSERT INTO t VALUES (1)")  # half done...
                started.set()
                other_done.wait(5)
                raise ValueError("stop")  # ...and the page fails

    def other_page():
        started.wait(5)
        conn.busy_ms = 5000
        with conn.request():
            pass  # reads only
        conn.execute("SELECT 1")
        other_done.set()

    a, b = threading.Thread(target=failing_page), threading.Thread(target=other_page)
    a.start(), b.start(), a.join(), b.join()
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0  # the failed page's write is gone
    with conn.request():
        conn.execute("INSERT INTO t VALUES (2)")
    with conn.request():
        conn.execute("INSERT INTO t VALUES (3)")
        with conn.request():  # a page inside a page: the outer one decides
            conn.execute("INSERT INTO t VALUES (4)")
    conn.execute("INSERT INTO t VALUES (5)")  # outside a page, it saves at once
    other = db.connect(tmp_path / "t.db")
    assert [r[0] for r in other.execute("SELECT x FROM t ORDER BY x")] == [2, 3, 4, 5]
    assert not conn.in_transaction
    conn.close()


def test_threads_never_share_a_connection(tmp_path):
    conn = db.ThreadConnection(tmp_path / "t.db")
    conn.execute("CREATE TABLE t (x)")  # the file exists, in WAL mode, before the threads open it
    seen = []
    threads = [threading.Thread(target=lambda: seen.append(conn.raw)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({id(c) for c in seen}) == 4  # kept in seen, so a closed one's id can't be reused (round 12b)
    conn.close()


def test_a_page_that_fails_is_undone(client):
    @main.app.post("/_test/fails")
    def fails():
        main.conn.execute("INSERT INTO app_meta (key, value) VALUES ('test_fails', '1')")
        raise ValueError("stop")

    from fastapi.testclient import TestClient
    r = TestClient(main.app, raise_server_exceptions=False).post("/_test/fails")
    assert r.status_code == 500
    assert main.conn.execute("SELECT 1 FROM app_meta WHERE key = 'test_fails'").fetchone() is None


# --- 3: pages don't wait on TCGdex --------------------------------------------------------------

def _counting_api(status=None, error=None):
    import httpx
    from app import tcgdex
    hits = {"n": 0}

    def handler(request):
        hits["n"] += 1
        if error:
            raise error
        return httpx.Response(status or 200, json={"id": "x"})
    client = httpx.Client(base_url="https://tcgdex.test/v2/en", transport=httpx.MockTransport(handler))
    return tcgdex.TCGdex("en", client), hits


def test_a_page_tries_tcgdex_once_and_then_leaves_it_alone(monkeypatch):
    import httpx
    from app import tcgdex
    monkeypatch.setattr(tcgdex, "_down_until", 0.0)
    api, hits = _counting_api(error=httpx.ConnectTimeout("slow"))
    with tcgdex.page_wait():
        with pytest.raises(httpx.TransportError):
            api.get_card("x")
        assert hits["n"] == 1  # no retry while a page waits
        with pytest.raises(tcgdex.Unreachable):
            api.get_card("y")  # TCGdex just timed out: this page doesn't try
        assert hits["n"] == 1
    monkeypatch.setattr(tcgdex, "_down_until", 0.0)
    api, hits = _counting_api(status=404)
    with tcgdex.page_wait():
        with pytest.raises(httpx.HTTPStatusError):
            api.get_card("missing")
    assert tcgdex._down_until == 0.0  # "not found" is an answer, not TCGdex being down


def test_artist_list_waits_an_hour_after_a_failure(monkeypatch):
    from app import tcgdex
    monkeypatch.setattr(tcgdex, "_artists", {})
    main.conn.execute("DELETE FROM app_meta WHERE key = 'tcgdex_artists'")
    calls = []

    class Down:
        def _get(self, path):
            calls.append(path)
            raise RuntimeError("down")
    assert tcgdex.artist_names(Down()) == []
    assert tcgdex.artist_names(Down()) == [] and len(calls) == 1  # the next search doesn't ask again
    tcgdex._artists["failed_at"] -= tcgdex.ARTISTS_RETRY + 1
    tcgdex.artist_names(Down())
    assert len(calls) == 2  # an hour later it does


# --- 4: Chinese pictures download a few at a time -------------------------------------------------

def test_chinese_pictures_download_four_at_a_time(tmp_path):
    import io
    import time as _time
    from PIL import Image
    from app import chinese
    out = io.BytesIO()
    Image.new("RGB", (10, 14), "red").save(out, "PNG")
    now, most = [0], [0]
    lock = threading.Lock()

    def fetch(url, timeout=None):
        with lock:
            now[0] += 1
            most[0] = max(most[0], now[0])
        _time.sleep(0.05)
        with lock:
            now[0] -= 1
        return out.getvalue(), "image/png"
    threads = [threading.Thread(target=chinese.picture, args=("1", str(n)), kwargs={"fetch": fetch, "folder": tmp_path})
               for n in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert most[0] == chinese.DOWNLOADS
    assert len(list(tmp_path.rglob("*.jpg"))) == 12
    assert chinese.kept_picture("1", "3", folder=tmp_path) is not None


# --- 5: nightly backups and the photo zip ------------------------------------------------------------

def test_nightly_backups_keep_the_last_seven(tmp_path):
    from app import backup
    src = db.connect(tmp_path / "live.db")
    src.execute("CREATE TABLE users (id INTEGER)")
    for _ in range(9):
        backup.make(src, tmp_path / "backups", "nightly")
    kept = backup.list_backups(tmp_path / "backups")
    assert len(kept) == 7 and {b["kind"] for b in kept} == {"nightly"} and kept[0]["label"] == "Nightly"


def test_the_nightly_update_makes_a_backup(client, tmp_path, monkeypatch):
    from app import backup
    monkeypatch.setattr(web, "BACKUPS", tmp_path / "b")
    monkeypatch.setattr(main.picture_saver, "poke", lambda **kw: None)
    web._after_nightly()
    assert [b["kind"] for b in backup.list_backups(tmp_path / "b")] == ["nightly"]


def test_download_photos_gives_every_photo_in_one_zip(client, tmp_path, monkeypatch):
    import io
    import zipfile
    from app import handmade
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    monkeypatch.setattr(handmade, "FOLDER", tmp_path / "photos")
    r = client.get("/account/photos.zip", follow_redirects=False)
    assert r.status_code == 303 and "photos=none" in r.headers["location"]
    assert "no photos of your own yet" in client.get(r.headers["location"]).text
    (tmp_path / "photos").mkdir()
    (tmp_path / "photos" / ("a" * 32 + ".jpg")).write_bytes(b"one")
    (tmp_path / "photos" / ("b" * 32 + ".jpg")).write_bytes(b"two")
    r = client.get("/account/photos.zip")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) == ["a" * 32 + ".jpg", "b" * 32 + ".jpg"]
    assert "Download photos" in client.get("/account").text


# --- 6: forms from another site are refused --------------------------------------------------------

def test_a_form_from_another_site_is_refused(client):
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    elsewhere = {"Origin": "https://elsewhere.example"}
    r = client.post("/wants/add", data={"card_id": "x", "set_id": "y"}, headers=elsewhere, follow_redirects=False)
    assert r.status_code == 403 and "another site" in r.text
    r = client.post("/logout", headers={"Referer": "https://elsewhere.example/page"}, follow_redirects=False)
    assert r.status_code == 403
    r = client.post("/b/sometoken/code", data={"code": "1234"}, headers=elsewhere, follow_redirects=False)
    assert r.status_code == 403  # shared binder codes too
    # From this site, or with neither header (a phone or script that doesn't send them): accepted.
    assert client.post("/account/backups", headers={"Origin": "http://testserver"}).status_code == 200
    assert client.post("/account/backups", headers={"Origin": "null", "Referer": "http://testserver/account"}).status_code == 200
    assert client.post("/account/backups").status_code == 200
    assert client.get("/account", headers=elsewhere).status_code == 200  # only forms are checked


# --- 7: htmx ships with the app ---------------------------------------------------------------------

def test_htmx_comes_from_the_app_not_unpkg(client):
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    page = client.get("/account").text
    assert "unpkg.com" not in page and '<script src="/static/htmx-2.0.4.min.js">' in page
    r = client.get("/static/htmx-2.0.4.min.js")
    assert r.status_code == 200 and r.text.startswith("var htmx=") and "max-age" in r.headers["cache-control"]


# --- 8: sign-in limit per address; file names escaped -------------------------------------------------

def test_one_address_trying_many_names_is_limited(client):
    from app import auth
    limit = auth.Lockout()
    for n in range(19):
        limit.failed(f"name{n}", "10.0.0.9")
    assert limit.locked_for("someone", "10.0.0.9") == 0
    limit.failed("name19", "10.0.0.9")
    assert limit.locked_for("someone", "10.0.0.9") > 600  # 20 wrong tries from one address: a wait for every name
    assert limit.locked_for("someone", "10.0.0.10") == 0  # other addresses aren't affected
    limit.succeeded("name1", "10.0.0.9")
    assert limit.locked_for("someone", "10.0.0.9") > 0


def test_a_photo_name_is_shown_as_text(client, monkeypatch):
    from tests.test_round9 import _scan_setup
    owner, batch_id, worker, reader = _scan_setup(client, monkeypatch)
    r = client.post(f"/scans/{batch_id}/photos", files=[("photos", ("<img src=x onerror=alert(1)>.jpg", b"not a photo", "image/jpeg"))],
                    headers={"X-Scan-Upload": "1"})
    assert r.status_code == 400 and "<img" not in r.text and "&lt;img" in r.text
