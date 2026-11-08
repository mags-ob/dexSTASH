"""Round 26 (0.26.0): fixes from the audits and small changes. A deleted copy leaves its binders, no "inf" prices,
shared binders and busy Wi-Fi, scan languages, the eBay link, My Stash's "Did you mean", tags open Find, two types
in Describe, the welcome step, settings, and the lighter purchase list."""
import re
import sqlite3
from pathlib import Path

import pytest

from app import binders, collectr, db, main, prices, scanning, scans, settings, spending, web
from conftest import with_assets
from test_app import _csv, _entry, _fake_pokeapi, _row, _setup_with_set, client  # noqa: F401
from test_round8 import _shared_binder, _visitor

ROOT = Path(__file__).resolve().parent.parent


# --- 1. a deleted copy leaves its binders ---------------------------------------------------------

def _line(card_id):
    return main.conn.execute("SELECT id FROM inventory WHERE card_id = ?", (card_id,)).fetchone()[0]


def test_a_deleted_copys_binder_slot_does_not_pass_to_the_next_card(client):
    uid = _setup_with_set(client)
    _entry(client, "t1", "1")
    shelf = binders.create(main.conn, uid, "For trade", uid)
    binders.move(main.conn, uid, _line("t1-1"), 1, shelf, None)
    old_id = _line("t1-1")
    _entry(client, "t1", "-1")  # deleted
    assert not main.conn.execute("SELECT 1 FROM binder_copies WHERE inventory_id = ?", (old_id,)).fetchone()
    _entry(client, "t1", "2")  # SQLite may give it the deleted copy's id
    assert not main.conn.execute("SELECT 1 FROM binder_copies WHERE binder_id = ?", (shelf,)).fetchone()


def test_step_18_adds_the_trigger_and_clears_left_over_slots(tmp_path):
    conn = db.connect(tmp_path / "old.db")
    db.init(conn)
    assert db.LATEST >= 18 and db.version(conn) == db.LATEST
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = 'inventory_gone'").fetchone()
    conn.execute("DROP TRIGGER inventory_gone")
    conn.execute("INSERT INTO binder_copies (inventory_id, binder_id, qty) VALUES (999, 1, 1)")  # an older mix-up
    db._step_18(conn)
    assert not conn.execute("SELECT 1 FROM binder_copies").fetchone()
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = 'inventory_gone'").fetchone()


# --- 2. prices that aren't numbers ------------------------------------------------------------------

@pytest.mark.parametrize("text", ["inf", "Infinity", "1e400", "nan", "-inf"])
def test_price_boxes_refuse_infinity_and_nan(client, text):
    _setup_with_set(client)
    _entry(client, "t1", "1")
    r = client.post("/cards/t1-1/details", data={"variant": "normal", "condition": "NM", "paid": text})
    assert r.status_code == 400 and "isn't a price" in r.text or "negative" in r.text
    r = client.post("/purchases", data={"label": "Packs", "cost": text, "packs": "1"})
    assert r.status_code == 400
    assert main.conn.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 0


def test_collectr_inf_cost_and_bad_quantity():
    good, bad = collectr.parse(_csv(_row("Base Set", "Pikachu", "58/102", cost="inf"),
                                    _row("Base Set", "Raichu", "14/102", qty="abc")))
    assert good.cost is None and not good.skip
    assert bad.skip == 'Quantity "abc" isn\'t a number'


# --- 3. shared binders and one busy Wi-Fi ---------------------------------------------------------------

def test_right_codes_never_lock_and_a_saved_pass_gets_in_while_locked(client):
    user_id, a, b, token, code = _shared_binder(client)
    v = _visitor()
    for _ in range(12):  # many people on one Wi-Fi, each typing the right code
        assert v.post(f"/b/{token}/code", data={"code": code}, follow_redirects=False).status_code == 303
    assert "Bulbasaur" in v.get(f"/b/{token}").text
    from app.routes import shared as shared_pages
    other = main.TestClient(main.app) if hasattr(main, "TestClient") else __import__("fastapi.testclient").testclient.TestClient(main.app)
    for _ in range(11):  # someone guessing, from the same address
        other.post(f"/b/{token}/code", data={"code": "AAAA-AAAA"})
    assert other.post(f"/b/{token}/code", data={"code": code}).status_code == 429  # the guesser is locked out
    assert "Bulbasaur" in v.get(f"/b/{token}").text  # a device with a saved pass still gets in
    assert shared_pages.share_limit.locked_for(shared_pages._SHARE_KEY, "testclient")


# --- 4. a scan's language from one stray character ------------------------------------------------------

def test_one_stray_character_does_not_change_the_language():
    english = ["Pikachu", "HP 60", "Weakness", "Retreat", "SVI EN 063/198"]
    for stray in ("一", "ロ", "点"):
        assert scans.detect_language(english + [stray]) == ("en", True)
    assert scans.detect_language(["Pikachu", "HP 60", "ーー"]) == (None, False)  # long-vowel marks aren't kana
    assert scans.detect_language(["피카츄", "HP 60"]) == ("ko", True)  # three Hangul letters still count
    assert scans.detect_language(["SVI EN 063/198", "ピカチュウ"]) == ("en", True)  # the printed EN code wins


def test_unsure_photos_also_try_the_usual_language_and_english(client):
    uid = _setup_with_set(client)
    batch = scans.create_batch(main.conn, uid, uid) if hasattr(scans, "create_batch") else None
    blank = {"batch_id": batch if isinstance(batch, int) else (batch or {}).get("id", 0), "id": 0, "language": None}
    langs, sure = scans._languages_to_try(main.conn, blank, ["皮卡丘"])
    assert langs == ["zh-cn", "zh-tw", "en"] and not sure


# --- 5. a scan row keeps its photo address --------------------------------------------------------------

def test_scan_rows_draw_the_photo_eagerly_with_one_address():
    text = (ROOT / "app/templates/_scan_item.html").read_text()
    assert 'alt="Your photo" loading="lazy"' not in text and text.count('src="/scans/photo/{{ s.id }}?v={{ s.photo[:12] }}"') >= 2
    js = (ROOT / "app/static/js/scan-batch.js").read_text()
    assert 'img[src^="/scans/photo/"]' in js and "img.replaceWith(same)" in js


# --- 6. a tapped picture gets bigger ----------------------------------------------------------------------

def test_the_big_picture_is_the_large_web_one():
    from app import card_pictures
    pics = [("/card-pictures/x.webp", ""), ("https://assets.tcgdex.net/en/sv/sv1/1/low.webp", "")]
    assert card_pictures.big_url(pics) == "https://assets.tcgdex.net/en/sv/sv1/1/high.webp"
    assert card_pictures.big_url([("/card-photos/mine.jpg", "Your photo")]) == "/card-photos/mine.jpg"
    assert card_pictures.big_url([]) is None


# --- 7. the Home Screen app's colours follow the look ---------------------------------------------------

def test_manifest_colours_follow_light_and_dark(client):
    _setup_with_set(client)
    assert client.get("/manifest.webmanifest").json()["theme_color"] == "#1c1c1f"
    light = client.get("/manifest.webmanifest?mode=light").json()
    assert light["theme_color"] == "#ffffff" and light["background_color"] == "#f5f5f7"
    css = (ROOT / "app/static/css/base.css").read_text()
    assert "body::before" in css and "env(safe-area-inset-top, 0px)" in css


# --- 8. filter chips replace the page ---------------------------------------------------------------------

def test_same_page_chips_replace_the_page_instead_of_adding_one():
    js = (ROOT / "app/static/js/base-nav.js").read_text()
    assert 'a.chip, .active-filters a, .seg.set-sort a, a[data-replace]' in js
    assert "to.pathname !== location.pathname" in js and "location.replace(to.href)" in js


# --- 9. compare a possible match with your photo -----------------------------------------------------------

def test_scan_match_tiles_have_a_compare_button():
    text = (ROOT / "app/templates/_scan_found.html").read_text()
    assert 'class="ts-zoom" data-compare="{{ big_url(tp) }}"' in text and 'data-photo="/scans/photo/' in text
    assert "Use this card" in (ROOT / "app/static/js/base-nav.js").read_text()


# --- 13. Search eBay sold -----------------------------------------------------------------------------------

def test_ebay_link():
    en = prices.ebay_link("Pikachu", "63", "Scarlet & Violet", "en", "ebay.com", 198)
    assert en.startswith("https://www.ebay.com/sch/183454/i.html?_nkw=Pikachu+063%2F198+Scarlet+%26+Violet&")
    assert "LH_Sold=1" in en and "_sop=13" in en and "campid" not in en and "_udhi" not in en
    ja = prices.ebay_link("Pikachu", "25", "Pokémon 151", "ja", "ebay.co.uk", 165)
    assert ja.startswith("https://www.ebay.co.uk/") and "Japanese" in ja and "151" not in ja.split("&")[0].split("+", 2)[-1]
    assert prices.ebay_link("Pikachu", "SV001", site="evil.example").startswith("https://www.ebay.com/")
    assert prices.full_number("TG05", 30) == "TG05"


def test_ebay_site_setting_and_card_page_link(client):
    _setup_with_set(client)
    assert "Search eBay sold" in client.get("/cards/t1-1").text
    assert client.post("/account/data/ebay", data={"site": "evil.example"}).status_code == 400
    client.post("/account/data/ebay", data={"site": "ebay.co.uk"})
    assert prices.ebay_site(main.conn) == "ebay.co.uk"
    assert "https://www.ebay.co.uk/sch/183454/" in client.get("/cards/t1-1").text
    assert 'id="ebay"' in client.get("/account/data").text


# --- 14, 17b, 19. words on pages ----------------------------------------------------------------------------

def test_scanning_languages_said_and_preview_line_gone(client):
    _setup_with_set(client)
    assert "For Korean cards, tap the photo's language tag and pick KO." in client.get("/account/data").text
    assert "For Korean cards" in (ROOT / "docs/scanning.md").read_text()
    assert "Preview only" not in (ROOT / "app/templates/card.html").read_text()


def test_welcome_first_cards_is_import_or_not_now():
    text = (ROOT / "app/templates/welcome.html").read_text()
    assert 'value="collectr"' in text and 'value="search"' not in text and 'value="scan"' not in text


# --- 15. Did you mean, in My Stash ---------------------------------------------------------------------------

def test_my_stash_suggests_a_spelling(client, monkeypatch):
    _setup_with_set(client)
    monkeypatch.setattr(web.names, "available", lambda: True)
    monkeypatch.setattr(web.names, "species_id", lambda text: 94 if text.casefold() == "gengar" else None)
    monkeypatch.setattr(web.names, "english_names", lambda: ["Gengar", "Pikachu"])
    page = client.get("/collection?q=gengr&sort=name").text
    assert "Did you mean" in page and "/collection?q=Gengar&amp;sort=name" in page
    assert "Did you mean" not in client.get("/collection?q=gengar").text


# --- 16. a tag opens Find ---------------------------------------------------------------------------------------

def test_card_tags_open_find():
    assert '<a href="/search?{{ qs(tag=t) }}"' in (ROOT / "app/templates/_card_tags.html").read_text()


# --- 17. Describe: up to two types --------------------------------------------------------------------------------

def test_describe_two_types(tmp_path):
    from app import describe
    get, _ = _fake_pokeapi()
    d = describe.Describe(tmp_path / "describe.db", tmp_path / "names.csv", tmp_path, get)
    d.build()
    names = lambda **k: [r["name"] for r in d.search(**k)]  # noqa: E731
    assert names(kind=["fairy"]) == ["Clefairy", "Ralts"]
    assert names(kind=["fairy", "psychic"]) == ["Ralts"]  # a Pokémon must have both
    assert describe.Describe.read(kind=["fairy", "psychic", "fire"])["kinds"] == ["fairy", "psychic"]  # two at most


# --- 20. the app's memory cap ------------------------------------------------------------------------------------------

def test_inside_scanning_needs_the_memory():
    assert scanning.too_little_memory(lambda: 1024 ** 3).startswith("This container has 1.0 GB")
    assert scanning.too_little_memory(lambda: 2 * 1024 ** 3) is None and scanning.too_little_memory(lambda: None) is None
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "mem_limit: ${DEXSTASH_APP_MEMORY:-2g}" in compose and "mem_reservation: 192m" in compose


def test_scanning_switch_says_why_inside_is_unavailable(client, monkeypatch):
    _setup_with_set(client)
    monkeypatch.setattr(scanning, "memory_limit", lambda: 1024 ** 3)
    monkeypatch.setattr(web, "SCANNER_LOCKED", False)
    monkeypatch.setattr(web, "SCANNER_MODE", "off")
    page = client.get("/account/data").text
    assert "On isn't available: This container has 1.0 GB" in page
    with pytest.raises(ValueError):
        web.set_scanning(True)


# --- 22, 23. settings --------------------------------------------------------------------------------------------------

def test_every_setting_the_app_reads_is_known():
    used = set()
    for path in list((ROOT / "app").rglob("*.py")) + [ROOT / "docker-compose.yml"]:
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r"\bDEXSTASH_([A-Z][A-Z0-9_]*)", text))
        used |= set(re.findall(r"settings\.(?:get|on)\(\"([A-Z][A-Z0-9_]*)\"", text))
    assert used - set(settings.KNOWN) == set()
    assert "LINK_FINDER" not in settings.KNOWN


@pytest.mark.parametrize("value, expected", [("1", True), ("true", True), ("Yes", True), (" on ", True),
                                             ("0", False), ("false", False), ("NO", False), ("off", False),
                                             ("", True)])
def test_on_off_spellings(monkeypatch, value, expected):
    monkeypatch.setenv("DEXSTASH_TCGCSV", value)
    assert settings.on("TCGCSV") is expected


# --- 25. the purchase list ------------------------------------------------------------------------------------------------

def test_purchase_choices_match_the_full_list(client):
    uid = _setup_with_set(client)
    client.post("/purchases", data={"label": "Booster box", "cost": "100", "packs": "36"})
    client.post("/purchases", data={"label": "Three packs", "cost": "12", "packs": "3"})
    plain = spending.purchase_choices(main.conn, uid)
    full = spending.purchase_summaries(main.conn, uid)
    assert [p["id"] for p in plain] == [p["id"] for p in full] and {"label", "cost", "currency", "kind"} <= set(plain[0])
    _entry(client, "t1", "1")
    assert "Booster box" in client.get("/collection").text


# --- 21, 24. CI and Windows --------------------------------------------------------------------------------------------------

def test_tests_run_on_push_and_before_an_image():
    tests_yml = (ROOT / ".github/workflows/tests.yml").read_text()
    image_yml = (ROOT / ".github/workflows/image.yml").read_text()
    assert "push:" in tests_yml and "workflow_call:" in tests_yml
    assert "uses: ./.github/workflows/tests.yml" in image_yml and "needs: [tests, version]" in image_yml
    assert 'tzdata>=2024.1; sys_platform == "win32"' in (ROOT / "requirements.txt").read_text()


def test_page_assets_still_load(client):
    _setup_with_set(client)
    assert "--danger-text" in with_assets(client.get("/").text)
