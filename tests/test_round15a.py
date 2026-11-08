"""Round 15a: scan review fixes, the box icon, and the exact TCGplayer link from TCGCSV's product id."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import main, prices, web
from tests.test_app import JPEG, _scan_setup, _setup_with_set, client  # noqa: F401

APP = Path(__file__).resolve().parent.parent / "app"
NODE = shutil.which("node")


def _inline_scripts(html: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)]


def _parses(source: str, tmp_path: Path, name: str) -> str | None:
    """None when Node accepts the script, else Node's message."""
    path = tmp_path / f"{name}.js"
    path.write_text(source)
    run = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True)
    return None if run.returncode == 0 else run.stderr.strip()


def _check_page(html: str, tmp_path: Path, name: str) -> None:
    scripts = _inline_scripts(html)
    for i, script in enumerate(scripts):
        assert "&#34;" not in script and "&quot;" not in script, f"{name} script {i} has HTML-escaped quotes"
        error = _parses(script, tmp_path, f"{name}-{i}")
        assert error is None, f"{name} script {i} doesn't parse:\n{error}"


def test_node_is_there_so_the_script_checks_never_skip():
    assert NODE, "Node is needed for the inline-script checks (tests.yml installs it)"


def test_the_scan_card_view_script_parses(client, monkeypatch, tmp_path):
    # Round 13 put `| forceescape` inside this script, which turned "$0.00" into &#34;$0.00&#34; and threw the
    # whole block away: Change match, the full-screen picture taps, the price chips and the tabs did nothing.
    owner, batch_id, worker, _ = _scan_setup(client, monkeypatch)
    client.post(f"/scans/{batch_id}/photos", files=[("photos", ("a.jpg", JPEG, "image/jpeg"))],
                headers={"X-Scan-Upload": "1"})
    worker.start(wait=True)
    scan_id = main.conn.execute("SELECT id FROM scans").fetchone()[0]
    details = client.get(f"/scans/item/{scan_id}/details", headers={"HX-Request": "true"}).text
    assert "data-cv-price" in details
    _check_page(details, tmp_path, "scan-details")
    _check_page(client.get(f"/scans/item/{scan_id}").text, tmp_path, "scan-details-page")
    _check_page(client.get(f"/scans/{batch_id}").text, tmp_path, "scan-batch")


def test_other_script_pages_parse(client, tmp_path):
    _setup_with_set(client)
    client.post("/cards/t1-2/inventory", data={"variant": "normal", "condition": "NM", "quantity": 1})
    for name, url in (("card", "/cards/t1-2"), ("search", "/search?q=1"), ("home", "/"), ("my-cards", "/collection")):
        page = client.get(url)
        assert page.status_code == 200, url
        _check_page(page.text, tmp_path, name)


def test_forceescape_only_inside_attributes():
    # It's right in an HTML attribute (data-pics="…") and wrong inside a <script> block.
    for path in (APP / "templates").glob("*.html"):
        for script in _inline_scripts(path.read_text()):
            assert "forceescape" not in script, f"{path.name}: forceescape inside a script"


def test_full_screen_sheets_leave_room_for_the_status_bar():
    css = (APP / "static" / "css" / "base.css").read_text()
    assert "dialog.ts .ts-head { padding-top:max(12px, env(safe-area-inset-top)); }" in css  # the scan card view
    for rule in (".psheet-head {", "dialog.lightbox .lb-x {", ".cam {"):  # the other full-screen ones already did
        part = css[css.index(rule):]
        assert "safe-area-inset-top" in part[:part.index("}")], rule


# --- The box icon -----------------------------------------------------------------------------------------

def test_icons_are_the_box_and_change_address_when_they_change(client):
    _setup_with_set(client)
    page = client.get("/").text
    for name in ("icon-180", "favicon-32"):
        assert re.search(rf'/static/{name}\.png\?v=[0-9a-f]+', page), f"{name} has no version"
    assert re.search(r'/manifest\.webmanifest\?v=[0-9a-f]+', page)
    manifest = client.get("/manifest.webmanifest").json()
    assert {i["sizes"] for i in manifest["icons"]} >= {"192x192", "512x512"}
    assert all("?v=" in i["src"] for i in manifest["icons"])


def test_my_stash_is_the_box_icon_with_its_name(client):
    """Round 15a made it icon only; round 17 adds "My Stash" under it, like the other tabs."""
    _setup_with_set(client)
    page = client.get("/").text
    tab = re.search(r'<a[^>]*href="/collection"[^>]*>.*?</a>', page, re.S)
    assert tab, "no My Stash link"
    link = tab.group(0)
    assert "<svg" in link and re.sub(r"<[^>]+>", "", link).strip() == "My Stash"


# --- TCGplayer link from TCGCSV's product id --------------------------------------------------------------

def test_the_tcgplayer_button_uses_tcgcsv_product_id(client):
    _setup_with_set(client)
    client.get("/cards/t1-2")
    main.conn.execute("UPDATE cards SET pricing_json = NULL, tcgplayer_id = 4242 WHERE id = 't1-2'")
    url, exact = prices.tcgplayer_link(None, "Ivysaur", "2", product_id=4242)
    assert (url, exact) == ("https://www.tcgplayer.com/product/4242", True)
    card = main.conn.execute("SELECT * FROM cards WHERE id = 't1-2'").fetchone()
    links = web._links(card, "en", None, our_id="t1-2")
    assert links["tcg_url"] == "https://www.tcgplayer.com/product/4242" and links["tcg_exact"]


def test_tcgdex_id_still_wins_and_no_id_still_searches():
    pricing = {"tcgplayer": {"normal": {"productId": 7}}}
    assert prices.tcgplayer_link(pricing, "Ivysaur", "2", product_id=4242)[0].endswith("/product/7")
    url, exact = prices.tcgplayer_link(None, "Ivysaur", "2")
    assert "/search/" in url and not exact


def test_a_card_not_kept_yet_uses_the_tcgcsv_table(client):
    _setup_with_set(client)
    main.conn.execute("INSERT OR REPLACE INTO tcgcsv_cards (card_id, product_id, group_id, image_url) "
                      "VALUES ('t1-9', 999, 1, NULL)")
    links = web._links({"id": "t1-9", "name": "Venusaur", "number": "9"}, "en", None, our_id="t1-9")
    assert links["tcg_url"] == "https://www.tcgplayer.com/product/999"


def test_version_is_015():
    from app import __version__
    assert tuple(int(x) for x in __version__.split(".")[:2]) >= (0, 15)
