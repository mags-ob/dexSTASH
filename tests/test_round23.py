"""Round 23 (0.23.0): one job system. One runner per process, yours first, pages first, a restart stops jobs,
and the price run's B6/E3/E6/E8/E9 savings."""
import threading
import time

from app import card_pictures, db, importer, inventory, jobs, main, prices, startup, sync, web
from test_app import FakePricedAPI, _setup_with_set, client  # noqa: F401
from test_round15d import _quote


def _jobs():
    return jobs.Jobs(main.conn, "app", idle=0.05)


def _clear():
    main.conn.execute("DELETE FROM jobs")
    main.conn.commit()


# --- J1/J2: one job at a time, yours first ----------------------------------------------------------------------

def test_one_job_at_a_time_and_yours_go_first(client):
    _clear()
    box, order, gate = _jobs(), [], threading.Event()
    try:
        box.start("long", "A long one", lambda c, job: gate.wait(10) and order.append("long"))
        for _ in range(100):
            if box.current() and not box.current()[0].waiting:
                break
            time.sleep(0.01)
        box.start("later", "Scheduled", lambda c, job: order.append("scheduled"), priority=jobs.SCHEDULED)
        box.start("mine", "Yours", lambda c, job: order.append("yours"))
        assert not box.start("mine", "Yours again", lambda c, job: None)  # one per key
        shown = box.current()
        assert [j.key for j in shown] == ["long", "mine", "later"]  # running, then waiting with yours first
        assert shown[1].waiting and "waiting its turn" in shown[1].describe()
        gate.set()
        box.last("later").wait(10)
        assert order == ["long", "yours", "scheduled"]
        assert {r[0] for r in main.conn.execute("SELECT status FROM jobs")} == {"done"}
    finally:
        box.stop()
        _clear()


def test_a_quick_job_runs_between_a_long_jobs_steps(client):
    _clear()
    box, order = _jobs(), []

    def long(c, job):
        for i in range(3):
            if i == 1:
                box.start("lookups", "Looking up pictures", lambda c2, j2: order.append("quick"), quick=True,
                          priority=jobs.SCHEDULED)
            order.append(f"step {i}")
            job.progress(i, 3)
    try:
        box.start("long", "A long one", long, wait=True)
        assert order == ["step 0", "step 1", "quick", "step 2"]
    finally:
        box.stop()
        _clear()


def test_asked_again_while_running_runs_once_more(client):
    _clear()
    box, runs, gate = _jobs(), [], threading.Event()
    try:
        box.start("pics", "Saving pictures", lambda c, job: gate.wait(10) and runs.append(1), again=True)
        for _ in range(100):
            if not box.current()[0].waiting:
                break
            time.sleep(0.01)
        assert not box.start("pics", "Saving pictures", lambda c, job: runs.append(2), again=True)
        gate.set()
        for _ in range(200):
            if runs == [1, 2] and not box.running("pics"):
                break
            time.sleep(0.02)
        assert runs == [1, 2]
    finally:
        box.stop()
        _clear()


def test_a_failed_job_says_why_and_the_next_one_runs(client):
    _clear()
    box = _jobs()
    try:
        box.start("bad", "Breaks", lambda c, job: 1 / 0, wait=True)
        box.start("good", "Works", lambda c, job: "Fine.", wait=True)
        assert box.last("bad").error == "division by zero" and box.last("good").result == "Fine."
    finally:
        box.stop()
        _clear()


# --- J6: a restart stops jobs, scans carry on ------------------------------------------------------------------

def test_a_restart_stops_every_job_and_an_import_waits_for_you(client):
    owner = _setup_with_set(client)
    _clear()
    now = "2026-10-30T10:00:00Z"
    main.conn.executemany("INSERT INTO jobs (key, label, kind, runs_in, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                          [("prices", "Updating prices", "prices", "app", "running", now),
                           ("chs", "Loading all Chinese sets", "chs", "app", "waiting", now),
                           ("recheck:6", "Grouping", "recheck", "scanner", "waiting", now),
                           ("old", "Done before", "old", "app", "done", now)])
    batch = main.conn.execute("INSERT INTO import_batches (collection_id, filename, status, created_at) "
                              "VALUES (?, 'a.csv', 'matching', ?)", (owner, now)).lastrowid
    main.conn.commit()
    startup._stop_left_over(main.conn)
    rows = {r["key"]: (r["status"], r["error"]) for r in main.conn.execute("SELECT * FROM jobs")}
    assert rows == {"prices": ("stopped", jobs.RESTARTED), "chs": ("stopped", jobs.RESTARTED),
                    "recheck:6": ("stopped", jobs.RESTARTED), "old": ("done", None)}
    assert "stopped by a restart. Start it again when you want it." in client.get("/account/data").text
    page = client.get(f"/imports/{batch}").text
    assert importer.STOPPED in page and "Start matching again" in page
    assert main.conn.execute("SELECT status FROM import_batches WHERE id = ?", (batch,)).fetchone()[0] == "stopped"
    started = []
    web.matcher.start, real = (lambda b, wait=False: started.append(b)), web.matcher.start
    try:
        client.post(f"/imports/{batch}/match")
    finally:
        web.matcher.start = real
    assert started == [batch]
    main.conn.execute("DELETE FROM import_batches")
    _clear()


def test_the_scanner_stops_only_its_running_job_at_its_own_start(client):
    from app import scan_worker
    from test_app import IVYSAUR, FakeReader
    _clear()
    now = "2026-10-30T10:00:00Z"
    main.conn.executemany("INSERT INTO jobs (key, label, kind, runs_in, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                          [("recheck:6", "Grouping", "recheck", "scanner", "running", now),
                           ("recheck:7", "Grouping", "recheck", "scanner", "waiting", now)])
    main.conn.commit()
    scanner = scan_worker.Scanner(main.conn, api_for=lambda lang: web.api, names=web.names, reader=FakeReader(IVYSAUR))
    scanner.jobs.start_runner = lambda: None  # not running them here
    scanner.start()
    assert dict(main.conn.execute("SELECT key, status FROM jobs").fetchall()) == {"recheck:6": "stopped", "recheck:7": "waiting"}
    _clear()


def test_no_catch_up_after_a_restart():
    assert not hasattr(sync, "missed") and "catching up" not in open(sync.__file__).read()


# --- J5: pages first -------------------------------------------------------------------------------------------

def test_background_work_waits_for_pages_but_never_a_page_itself(monkeypatch):
    slept = []
    jobs.page_started()
    try:
        assert jobs.pause_for_pages(longest=0.2, sleep=slept.append) == 0.0  # this thread isn't a job's
        result = []
        thread = threading.Thread(target=lambda: (jobs.mark_background(),
                                                  result.append(jobs.pause_for_pages(longest=0.2, sleep=slept.append))))
        thread.start()
        thread.join()
        assert result[0] >= 0.2 and slept  # waited, up to the longest
    finally:
        jobs.page_ended()
    monkeypatch.setitem(jobs._pages, "ended", time.monotonic() - 10)
    thread = threading.Thread(target=lambda: (jobs.mark_background(), result.append(jobs.pause_for_pages(sleep=slept.append))))
    thread.start()
    thread.join()
    assert result[1] == 0.0  # no page: no wait


def test_pages_count_but_status_checks_dont(client, monkeypatch):
    _setup_with_set(client)
    seen = []
    monkeypatch.setattr(jobs, "page_started", lambda: seen.append(jobs.pages_now()))
    monkeypatch.setattr(jobs, "page_ended", lambda: None)
    client.get("/collection")
    assert len(seen) == 1
    client.get("/account/data/running")
    client.get("/prices/status")
    client.get("/healthz")
    assert len(seen) == 1


def test_a_download_in_a_job_waits_for_pages(monkeypatch):
    from app import sites
    waited = []
    monkeypatch.setattr(jobs, "pause_for_pages", lambda *a, **k: waited.append(1) or 0.0)
    with sites.client() as c:
        hooks = c.event_hooks["request"]
    for hook in hooks:
        try:
            hook(type("R", (), {"url": "https://api.tcgdex.net/v2/en/sets"})())
        except Exception:
            pass
    assert waited


# --- B6, E3, E8: the price run --------------------------------------------------------------------------------

def test_tcgdex_is_asked_only_for_what_tcgcsv_doesnt_cover_and_weekly_for_all(client):
    owner = _setup_with_set(client)
    inventory.adjust(main.conn, owner, "t1-1", "normal", "NM", 1)
    inventory.adjust(main.conn, owner, "t1-2", "holo", "NM", 1)
    main.conn.execute("UPDATE cards SET image_url = 'https://x/p.png' WHERE id IN ('t1-1', 't1-2')")
    _quote("t1-1", "normal", "tcgplayer", 1.0, basis="tcgcsv")
    _quote("t1-2", "normal", "tcgplayer", 1.0, basis="tcgcsv")  # not the printing you have
    main.conn.execute("DELETE FROM app_meta WHERE key = ?", (sync.TCGDEX_ALL_AT,))
    main.conn.commit()
    rows, everything = sync.tcgdex_due(main.conn)
    assert everything and {r["id"] for r in rows} == {"t1-1", "t1-2"}
    sync.tcgdex_all_done(main.conn)
    rows, everything = sync.tcgdex_due(main.conn)
    assert not everything and {r["id"] for r in rows} == {"t1-2"}
    main.conn.execute("UPDATE cards SET image_url = NULL WHERE id = 't1-1'")  # no picture: asked
    assert {r["id"] for r in sync.tcgdex_due(main.conn)[0]} == {"t1-1", "t1-2"}
    main.conn.execute("DELETE FROM app_meta WHERE key = ?", (sync.TCGDEX_ALL_AT,))
    main.conn.commit()


def test_the_full_update_works_each_card_out_once(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakePricedAPI(normal_price=0.75))
    owner = _setup_with_set(client)
    inventory.adjust(main.conn, owner, "t1-1", "normal", "NM", 1)
    main.conn.commit()
    calls = []
    real = prices.resolve
    monkeypatch.setattr(prices, "resolve", lambda conn, card_id: calls.append(card_id) or real(conn, card_id))
    sync.refresh_all(main.conn, lambda lang: web.api)
    assert calls.count("t1-1") == 1
    assert main.conn.execute("SELECT price FROM prices WHERE card_id = 't1-1' AND variant = 'normal'").fetchone()[0] == 0.75


def test_todays_history_isnt_rewritten_when_the_rate_didnt_change(client):
    owner = _setup_with_set(client)
    inventory.adjust(main.conn, owner, "t1-1", "normal", "NM", 1)
    main.conn.execute("INSERT INTO price_snapshots (card_id, variant, source, price, captured_at) "
                      "VALUES ('t1-1', 'normal', 'tcgplayer', 2.0, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))")
    with db.transaction(main.conn):
        prices.localize_all(main.conn)
    assert main.conn.execute("SELECT local FROM price_snapshots WHERE card_id = 't1-1'").fetchone()[0] == 2.0
    before = main.conn.raw.total_changes
    with db.transaction(main.conn):
        assert prices.localize_all(main.conn) == 0
    assert main.conn.raw.total_changes == before
    main.conn.execute("DELETE FROM price_snapshots")
    main.conn.commit()


# --- E6: the picture saver wakes only for a change ---------------------------------------------------------------

def test_the_picture_saver_wakes_only_when_whats_kept_changed(client, monkeypatch):
    owner = _setup_with_set(client)
    pokes = []
    saver = card_pictures.Checker(enabled=True, jobs=web.background)
    monkeypatch.setattr(saver, "poke", lambda **k: pokes.append(1))
    assert not saver.poke_if_changed(main.conn)  # the first look notes where things are
    assert not saver.poke_if_changed(main.conn)
    inventory.adjust(main.conn, owner, "t1-1", "normal", "NM", 1)
    main.conn.commit()
    assert saver.poke_if_changed(main.conn) and pokes == [1]
    assert not saver.poke_if_changed(main.conn)
    client.post("/account/theme", data={})  # a form that changes nothing kept
    assert not saver.poke_if_changed(main.conn)


def test_due_pictures_are_picked_in_the_query(client):
    owner = _setup_with_set(client)
    inventory.adjust(main.conn, owner, "t1-1", "normal", "NM", 1)
    inventory.adjust(main.conn, owner, "t1-2", "normal", "NM", 1)
    main.conn.execute("UPDATE cards SET picture_checked_at = '2026-01-01T00:00:00Z', saved_picture = 'a.webp', "
                      "saved_from = '' WHERE id = 't1-1'")
    main.conn.execute("UPDATE cards SET picture_checked_at = '2026-01-01T00:00:00Z', saved_picture = 'b.webp', "
                      "saved_from = 'Japanese version' WHERE id = 't1-2'")
    main.conn.commit()
    assert card_pictures.due(main.conn) == []
    assert card_pictures.due(main.conn, recheck=True) == ["t1-2"]  # a borrowed picture, looked for again weekly


# --- E9: the scanner looks only when something changed ---------------------------------------------------------

def test_the_scanner_skips_its_full_look_when_nothing_changed(client):
    from app import scan_worker
    from test_app import IVYSAUR, FakeReader
    scanner = scan_worker.Scanner(main.conn, api_for=lambda lang: web.api, names=web.names, reader=FakeReader(IVYSAUR))
    scanner._followed_at = time.time()
    assert scanner._changed()  # the first look
    assert not scanner._changed()  # nothing written by anyone else since
    other = db.connect()
    try:
        other.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('poke', '1')")
    finally:
        other.close()
    assert scanner._changed()
