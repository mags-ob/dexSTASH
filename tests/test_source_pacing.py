"""#77: the app's own error page when TCGdex doesn't answer, and background requests paced per site."""
import threading

import pytest

from app import jobs, main, sites, web
from test_app import OfflineAPI, _setup_with_set, client  # noqa: F401


def test_a_card_tcgdex_cant_load_gets_the_apps_own_page(client, monkeypatch, caplog):
    _setup_with_set(client)
    monkeypatch.setitem(web._other_apis, "ja", OfflineAPI())
    r = client.get("/preview/ja/neo2-036")
    assert r.status_code == 502 and r.headers["x-dexstash-page"] == "error"
    assert "Couldn't load this card" in r.text and "Your server and your stash are fine" in r.text
    assert 'href="http://testserver/preview/ja/neo2-036"' in r.text and "data-go-back" in r.text
    assert 'class="tabs' in r.text or "has-tabs" in r.text  # the header and tabs, not a bare page
    assert "neo2-036" in caplog.text  # the reason is logged
    # A background (htmx) request still gets the plain error
    r = client.get("/preview/ja/neo2-036", headers={"HX-Request": "true"})
    assert r.status_code == 502 and "x-dexstash-page" not in r.headers and r.json()["detail"].startswith("Couldn't load")


def test_a_set_tcgdex_cant_load_says_set(client, monkeypatch):
    _setup_with_set(client)
    monkeypatch.setitem(web._other_apis, "ja", OfflineAPI())
    r = client.get("/set-preview/ja/neo2")
    assert r.status_code == 502 and "Couldn't load this set" in r.text


def test_the_offline_page_is_only_for_a_server_that_cant_be_reached(client):
    js = client.get("/sw.js").text
    assert '[502, 503, 504].includes(r.status) && !r.headers.has("X-Dexstash-Page")' in js
    assert main.APP_ERROR == {"X-Dexstash-Page": "error"}


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.lock = threading.Lock()

    def now(self):
        return self.t

    def sleep(self, s):
        with self.lock:
            self.t += s


def _in_background(monkeypatch, on=True):
    monkeypatch.setattr(jobs, "in_background", lambda: on)
    monkeypatch.setattr(sites, "_turns", {})


def test_background_requests_take_turns_per_site(monkeypatch):
    _in_background(monkeypatch)
    clock = Clock()

    def five():
        for _ in range(5):
            sites.wait_turn("https://api.tcgdex.net/v2/en/cards/x", now=clock.now, sleep=lambda s: None)

    # 20 requests from 4 threads to one site: each gets its own slot, PACE a second
    threads = [threading.Thread(target=five) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sites._turns["api.tcgdex.net"].next_at == pytest.approx(1000.0 + 20 / sites.PACE)
    # Another site has its own queue
    assert sites.wait_turn("https://tcgcsv.com/x", now=clock.now, sleep=lambda s: None) == 0.0


def test_a_wait_is_the_time_to_the_sites_next_turn(monkeypatch):
    _in_background(monkeypatch)
    clock = Clock()
    waits = [sites.wait_turn("https://api.tcgdex.net/a", now=clock.now, sleep=clock.sleep) for _ in range(3)]
    assert waits == pytest.approx([0.0, 0.2, 0.2]) and clock.t == pytest.approx(1000.4)


def test_a_site_that_says_slow_down_pauses_only_background_requests(monkeypatch):
    _in_background(monkeypatch)
    clock = Clock()
    url = "https://api.tcgdex.net/v2/ja/cards/x"
    sites.answered(url, 429, None, now=clock.now)
    assert sites.wait_turn(url, now=clock.now, sleep=clock.sleep) == sites.FIRST_PAUSE
    sites.answered(url, 429, None, now=clock.now)  # still refusing: twice as long
    assert sites._turns["api.tcgdex.net"].paused_until == clock.t + 2 * sites.FIRST_PAUSE
    sites.answered(url, 200, now=clock.now)
    assert sites._turns["api.tcgdex.net"].pause == 0.0  # a good answer ends the doubling
    sites.answered("https://tcgcsv.com/x", 503, "12", now=clock.now)  # Retry-After wins
    assert sites._turns["tcgcsv.com"].paused_until == clock.t + 12
    # A page's own request never waits, and its answers don't pause anything
    monkeypatch.setattr(jobs, "in_background", lambda: False)
    assert sites.wait_turn(url, now=clock.now, sleep=clock.sleep) == 0.0
    sites.answered("https://pokeapi.co/x", 429, now=clock.now)
    assert "pokeapi.co" not in sites._turns


def test_every_client_waits_its_turn_and_reports_answers(monkeypatch):
    import httpx
    seen = []
    monkeypatch.setattr(sites, "wait_turn", lambda url: seen.append(("turn", str(url))))
    monkeypatch.setattr(sites, "answered", lambda url, status, retry: seen.append(("answer", status, retry)))
    transport = httpx.MockTransport(lambda request: httpx.Response(429, headers={"Retry-After": "5"}))
    with sites.client(transport=transport) as c:
        c.get("https://api.tcgdex.net/v2/en/cards/x")
    assert seen == [("turn", "https://api.tcgdex.net/v2/en/cards/x"), ("answer", 429, "5")]
