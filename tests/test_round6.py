"""Round 6: Find's spelling help, smart words, saved searches and the vintage setting."""

import pytest

from app import main, saved_searches, smart, spelling
from app import web
from test_app import FakeSearchAPI, _entry, _setup_with_set, client  # noqa: F401  (the client fixture)

NAMES = ["Pikachu", "Charizard", "Bulbasaur", "Squirtle", "Jigglypuff", "Gengar", "Eevee", "Gyarados", "Snorlax",
         "Lucario", "Garchomp", "Greninja", "Rayquaza", "Psyduck", "Alakazam", "Dragonite", "Umbreon", "Vaporeon",
         "Gardevoir", "Mimikyu", "Feraligatr", "Typhlosion", "Sylveon", "Ninetales", "Tyranitar", "Pichu",
         "Wigglytuff", "Porygon-Z", "Mr. Mime", "Farfetch’d"]


@pytest.mark.parametrize("typed,expected", [
    ("pikachoo", "Pikachu"), ("pickachu", "Pikachu"), ("charzard", "Charizard"), ("bulbasuar", "Bulbasaur"),
    ("squirtel", "Squirtle"), ("jigglypuf", "Jigglypuff"), ("gyrados", "Gyarados"), ("snorelax", "Snorlax"),
    ("lucareo", "Lucario"), ("grenija", "Greninja"), ("sydduck", "Psyduck"), ("dragonight", "Dragonite"),
    ("umbrion", "Umbreon"), ("gardavoir", "Gardevoir"), ("feraligator", "Feraligatr"), ("eevie", "Eevee"),
])
def test_misspelt_names(typed, expected):
    assert spelling.best(typed, NAMES) == expected
    assert spelling.suggest(typed, NAMES)[0] == expected


def test_spelling_leaves_right_names_alone():
    assert spelling.best("Pikachu", NAMES) is None and spelling.suggest("pikachu", NAMES) == []
    assert spelling.best("mr mime", NAMES) is None  # same letters, just no dot
    assert spelling.suggest("pika", NAMES)[0] == "Pikachu"  # what you've typed so far comes first


SETS = [{"id": "sv08", "plain_name": "Surging Sparks", "lang": "en"},
        {"id": "base3", "plain_name": "Fossil", "lang": "en"},
        {"id": "sv03.5", "plain_name": "151", "lang": "en"}]


def test_smart_words():
    r = smart.read("pikachu surging sparks", SETS)
    assert (r.rest, r.set["id"]) == ("pikachu", "sv08")
    assert r.chips == [("pikachu", "surging sparks"), ("Surging Sparks", "pikachu")]

    r = smart.read("fossil", SETS)
    assert r.set["id"] == "base3" and r.rest == ""

    r = smart.read("charizard ex shiny full art", SETS)
    assert (r.rest, r.mechanics, r.flags) == ("charizard", ["ex"], {"shiny", "full_art"})
    assert smart.name_ok(r, "Charizard ex") and smart.name_ok(r, "リザードンex") and not smart.name_ok(r, "Charizard")

    r = smart.read("pikachu surging sparcs", SETS, is_pokemon=lambda t: t.lower() == "pikachu")
    assert r.set["id"] == "sv08" and r.rest == "pikachu"  # a misspelt set name at the end

    r = smart.read("charizard 151", SETS)
    assert r.set is None and r.rest == "charizard 151"  # numbers stay numbers

    assert not smart.read("pikachu", SETS).any


def test_vintage_years(client):
    from datetime import date
    assert smart.vintage_year(20, date(2026, 5, 1)) == 2007  # older than 20 years: 2006 and earlier
    assert smart.is_vintage("2006-11-01", 20, date(2026, 5, 1)) and not smart.is_vintage("2007-01-01", 20, date(2026, 5, 1))
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    assert 'name="years" value="20"' in client.get("/account/data").text
    r = client.post("/account/data/vintage", data={"years": "25"})
    assert smart.vintage_years(main.conn) == 25 and 'name="years" value="25"' in r.text


def test_set_name_alone_still_lists_sets(client, monkeypatch):
    monkeypatch.setattr(web, "api", FakeSearchAPI())
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    page = client.get("/search", params={"q": "old set"}).text
    assert "Old Set" in page


def test_saved_searches_belong_to_each_login(client):
    client.post("/setup", data={"code": web.setup_code(), "username": "misty", "password": "pikachu123"})
    client.post("/search/saved", data={"name": "Pikachu promos", "query": "q=pikachu promo&lang=en&junk=1"})
    [s] = saved_searches.all_for(main.conn, main.conn.execute("SELECT id FROM users").fetchone()[0])
    assert s["name"] == "Pikachu promos" and "junk" not in s["href"] and "q=pikachu" in s["href"]
    assert "Pikachu promos" in client.get("/search").text
    client.post("/search/saved/rename", data={"name": "Pikachu promos", "new": "Promos"})
    assert "Promos" in client.get("/search").text
    client.post("/search/saved/delete", data={"name": "Promos"})
    assert "Promos" not in client.get("/search").text


def test_set_page_rarity_sheet_and_popular(client):
    _setup_with_set(client)
    ids = [r[0] for r in main.conn.execute("SELECT id FROM cards WHERE set_id = 't1' ORDER BY id")]
    main.conn.execute("UPDATE cards SET rarity = 'Common' WHERE set_id = 't1'")
    main.conn.execute("UPDATE cards SET rarity = 'Rare Holo' WHERE id = ?", (ids[0],))
    page = client.get("/sets/t1").text
    assert 'name="rarity" value="Rare Holo"' in page and "Popular</a>" not in page  # rarity in the sheet; no prices yet
    assert 'class="set-rar' not in page

    page = client.get("/sets/t1", params={"rarity": "Rare Holo"}).text
    assert "Rare Holo <svg class=\"ic\"" in page and page.count('<div class="ctile') == 1

    for i, card_id in enumerate(ids):
        main.conn.execute("INSERT INTO prices (card_id, variant, origin, price, updated_at) "
                          "VALUES (?, 'normal', 'tcgplayer', ?, '2026-10-12')", (card_id, 1.0 + i))
    main.conn.commit()
    page = client.get("/sets/t1").text  # round 11b: Sort by Value (the default) replaced Popular
    assert "Popular" not in page and 'aria-current="true">Value</a>' in page
    order = [page.index(f"/cards/{i}\"") for i in reversed(ids)]
    assert order == sorted(order)  # dearest first


def test_tags_from_details():
    from app import tags
    detail = {"category": "Pokemon", "hp": 120, "stage": "Stage1", "illustrator": "Mitsuhiro Arita",
              "variants_detailed": [{"type": "reverse", "foil": "pokeball"}, {"type": "holo", "stamp": ["pre-release"]}]}
    assert tags.detail_tags(detail, "Charizard ex") == ["Pokémon", "Stage 1", "ex", "Stamped", "Poké Ball", "Mitsuhiro Arita"]
    assert tags.detail_tags({"category": "Trainer", "trainerType": "Supporter"}, "Boss's Orders") == ["Trainer", "Supporter"]
    assert tags.chs_tags({"cardType": "1", "evolveText": "1阶进化", "pokemonType": "11", "illustratorName": ["REND"]},
                         "太阳伊布ex") == ["Pokémon", "Stage 1", "ex", "REND"]
    assert [tags.mechanic(n) for n in ("Pikachu V", "Pikachu VMAX", "M Charizard-EX", "Mega Lucario ex", "Eevee")] == \
        ["V", "VMAX", "EX", "ex", None]


def test_card_tags_on_the_card_page_and_filters(client):
    from app import tags
    _setup_with_set(client)
    _entry(client, "t1", "1")
    _entry(client, "t1", "2")
    card_id = main.conn.execute("SELECT id FROM cards WHERE set_id = 't1' ORDER BY sort_number").fetchone()[0]
    page = client.get(f"/cards/{card_id}").text
    assert 'id="tags"' in page and "Reverse holo" in page  # worked out from the stored printings

    client.post(f"/cards/{card_id}/tags/add", data={"tag": " #Binder pull "})
    client.post(f"/cards/{card_id}/tags/remove", data={"tag": "Reverse holo"})
    entry = tags.for_card(main.conn, card_id)
    assert ("Binder pull", "mine") in entry["tags"] and "Reverse holo" in entry["hidden"]
    page = client.get(f"/cards/{card_id}").text
    assert "Hidden tags" not in page and 'class="chip tagc on"' in page  # no hidden list (round 7)
    assert client.post(f"/cards/{card_id}/tags/show", data={"tag": "Reverse holo"}).status_code in (404, 405)
    tags.reset(main.conn)  # Data sources › Reset tags
    assert tags.for_card(main.conn, card_id)["hidden"] == []

    # My cards: a Tags section in the Filter sheet, and filtering by it.
    page = client.get("/collection", params={"tag": "Binder pull"}).text
    assert 'name="tag" value="Binder pull"' in page and "#Binder pull" in page and page.count('<div class="ctile') == 1
    assert client.get("/collection", params={"tag": "Nothing like it"}).text.count('<div class="ctile') == 0

    # Find: your tag as a word lists the stored cards with it.
    page = client.get("/search", params={"q": "binder pull"}).text
    assert f"/cards/{card_id}" in page

    client.post(f"/cards/{card_id}/tags/remove", data={"tag": "Binder pull"})  # ✕ on your own tag deletes it
    assert ("Binder pull", "mine") not in tags.for_card(main.conn, card_id)["tags"]


def test_storing_a_set_saves_its_automatic_tags(client, monkeypatch):
    from test_app import FakeAPI
    real = FakeAPI.get_card
    monkeypatch.setattr(FakeAPI, "get_card", lambda self, card_id: dict(real(self, card_id), illustrator="Kagemaru Himeno"))
    _setup_with_set(client)
    assert main.conn.execute("SELECT COUNT(*) FROM card_tags WHERE kind = 'auto' AND tag = 'Kagemaru Himeno'").fetchone()[0] == 3


def _line(card_id, variant="normal", condition="NM"):
    return main.conn.execute("SELECT id FROM inventory WHERE card_id = ? AND variant = ? AND condition = ?",
                             (card_id, variant, condition)).fetchone()[0]


def test_binders(client):
    from app import binders, inventory
    owner = _setup_with_set(client)
    inventory.adjust(main.conn, owner, "t1-1", "normal", "NM", 3)
    inventory.adjust(main.conn, owner, "t1-2", "normal", "NM", 1)

    page = client.get("/collection/binders").text
    assert "Not in a binder" in page and "No binders yet" in page
    r = client.post("/collection/binders", data={"name": "  Misty's   binder "})
    mine = main.conn.execute("SELECT id FROM binders WHERE name = 'Misty''s binder'").fetchone()[0]
    assert r.url.path == f"/collection/binders/{mine}"
    assert "already a binder" in client.post("/collection/binders", data={"name": "misty's binder"}).text

    # Bulk edit on Cards: every copy moves; a new binder can be made on the way.
    client.post("/collection/bulk", data={"action": "binder", "lines": ["t1-2|normal|NM"], "to_binder": "new",
                                          "new_binder": "Bulk box", "back_path": "/collection"})
    bulk = main.conn.execute("SELECT id FROM binders WHERE name = 'Bulk box'").fetchone()[0]
    client.post("/collection/bulk", data={"action": "binder", "lines": ["t1-1|normal|NM"], "to_binder": str(mine)})
    one = _line("t1-1")
    assert binders.placement(main.conn, owner)[one] == {mine: 3}

    # Card page: move 1 of the 3 back out.
    client.post("/cards/t1-1/binder", data={"inventory_id": one, "from_binder": str(mine), "qty": "1", "to_binder": "none"})
    assert binders.placement(main.conn, owner)[one] == {mine: 2, None: 1}
    page = client.get("/cards/t1-1").text
    assert "2 in Misty&#39;s binder · 1 not in a binder" in page  # round 17: on the copy card

    # Each binder's page lists its own copies; the filter on Cards does too.
    page = client.get(f"/collection/binders/{mine}").text
    assert "/cards/t1-1" in page and "/cards/t1-2" not in page and "/collection/binders" in page
    assert "/cards/t1-2" in client.get("/collection", params={"binder": str(bulk)}).text
    assert "/cards/t1-1" in client.get("/collection/binders/none").text
    summary = {b["name"]: b["count"] for b in binders.summaries(main.conn, owner, __import__("app.collection").collection.cards(main.conn, owner)["rows"])}
    assert summary == {"Bulk box": 1, "Misty's binder": 2, "Not in a binder": 1}

    # Fewer copies: Not in a binder goes first, then the binder holding the most.
    inventory.adjust(main.conn, owner, "t1-1", "normal", "NM", -1)
    assert binders.placement(main.conn, owner)[one] == {mine: 2}
    inventory.adjust(main.conn, owner, "t1-1", "normal", "NM", -1)
    assert binders.placement(main.conn, owner)[one] == {mine: 1}

    # Deleting a binder keeps its cards, Not in a binder.
    client.post(f"/collection/binders/{mine}/delete")
    assert binders.placement(main.conn, owner)[one] == {None: 1}
    assert main.conn.execute("SELECT quantity FROM inventory WHERE id = ?", (one,)).fetchone()[0] == 1

    # Scans and imports put just the copies they added.
    inventory.adjust(main.conn, owner, "t1-1", "normal", "NM", 2)
    assert binders.put_some(main.conn, owner, {one: 2}, bulk) == 2
    assert binders.placement(main.conn, owner)[one] == {bulk: 2, None: 1}


def test_recently_added_and_added_filter(client):
    from app import inventory
    owner = _setup_with_set(client)
    inventory.adjust(main.conn, owner, "t1-1", "normal", "NM", 1)
    inventory.adjust(main.conn, owner, "t1-2", "normal", "NM", 1)
    main.conn.execute("UPDATE inventory_log SET at = '2020-01-01 10:00:00' WHERE card_id = 't1-1'")
    main.conn.commit()
    page = client.get("/collection", params={"added": "today"}).text
    assert "/cards/t1-2" in page and "/cards/t1-1" not in page and "Added today" in page
    page = client.get("/collection", params={"sort": "newest"}).text
    assert "Recently added" in page and page.index("/cards/t1-2") < page.index("/cards/t1-1")


# --- Describe: looks-like words, legendary/colour/type words, Bulbapedia (round 6) -----------------

def _describe_data(tmp_path, origins=None, own=None):
    from app import describe
    files = {
        "pokemon_species": "id,identifier,color_id,shape_id,is_baby,is_legendary,is_mythical\n"
                           "50,diglett,3,1,0,0,0\n144,articuno,2,9,0,1,0\n145,zapdos,10,9,0,1,0\n146,moltres,8,9,0,1,0\n"
                           "151,mew,6,6,0,0,1\n249,lugia,9,9,0,1,0\n250,ho-oh,8,9,0,1,0\n581,swanna,9,9,0,0,0\n"
                           "643,reshiram,9,9,0,1,0\n924,tandemaus,9,11,0,0,0\n925,maushold,9,11,0,0,0\n",
        "pokemon_colors": "id,identifier\n2,blue\n3,brown\n6,pink\n8,red\n9,white\n10,yellow\n",
        "pokemon_shapes": "id,identifier\n1,ball\n6,upright\n9,wings\n11,heads\n",
        "types": "id,identifier\n1,normal\n3,flying\n5,ground\n10,fire\n11,water\n13,electric\n14,psychic\n15,ice\n16,dragon\n",
        "pokemon_types": "pokemon_id,type_id,slot\n50,5,1\n144,15,1\n144,3,2\n145,13,1\n145,3,2\n146,10,1\n146,3,2\n"
                         "151,14,1\n249,14,1\n249,3,2\n250,10,1\n250,3,2\n581,11,1\n581,3,2\n643,16,1\n643,10,2\n924,1,1\n925,1,1\n",
        "pokemon_species_flavor_text": "species_id,version_id,language_id,flavor_text\n"
                                       "144,1,9,\"A legendary bird Pokémon that is said to appear to doomed people.\"\n"
                                       "145,1,9,\"A legendary bird Pokémon that is said to appear from clouds.\"\n"
                                       "146,1,9,\"Known as the legendary bird of fire.\"\n"
                                       "581,1,9,\"Swanna start to dance at dusk. It is a white bird.\"\n"
                                       "249,1,9,\"It sleeps in a deep-sea trench.\"\n"
                                       "643,1,9,\"This Pokémon appears in legends. It sends flames into the air.\"\n",
        "pokemon_species_names": "pokemon_species_id,local_language_id,name,genus\n"
                                 "50,9,Diglett,Mole Pokémon\n50,12,地鼠,\n144,9,Articuno,Freeze Pokémon\n145,9,Zapdos,Electric Pokémon\n"
                                 "146,9,Moltres,Flame Pokémon\n151,9,Mew,New Species Pokémon\n249,9,Lugia,Diving Pokémon\n"
                                 "250,9,Ho-Oh,Rainbow Pokémon\n581,9,Swanna,White Bird Pokémon\n643,9,Reshiram,Vast White Pokémon\n"
                                 "924,9,Tandemaus,Couple Pokémon\n924,12,一对鼠,\n924,1,ワッカネズミ,\n"
                                 "925,9,Maushold,Family Pokémon\n925,12,一家鼠,\n925,1,イッカネズミ,\n",
    }

    class Resp:
        def __init__(self, body):
            self.content = body.encode()

        def raise_for_status(self):
            pass

    get = lambda url: Resp(files[url.rsplit("/", 1)[1].removesuffix(".csv")])
    return describe.Describe(tmp_path / "describe.db", tmp_path / "names.csv", tmp_path, get,
                             origins=lambda: origins or {}, own=lambda: own or ({}, {}))


def test_describe_words_work_together(tmp_path):
    d = _describe_data(tmp_path)
    d.build()
    names = lambda q, **k: [r["name"] for r in d.search(q, **k)]
    assert set(names("white bird legendary")) == {"Reshiram", "Lugia"}  # Lugia was 109th before
    assert set(names("red bird legendary fire")) == {"Ho-Oh", "Moltres"}  # red, fire and legendary narrow; bird ranks
    assert names("mythical pink cat") == ["Mew"]  # no word matched: the narrowed list
    found = names("mouse")
    assert {"Maushold", "Tandemaus"} <= set(found[:3]) and "Diglett" not in found  # from the names alone; 地鼠 is a mole
    assert "Diglett" in names("mole")
    r = d.read("White bird legendary")
    assert (r["colour"], r["statuses"], r["tokens"]) == ("white", ["legendary"], ["bird"])
    assert r["chips"] == [("White", "bird legendary"), ("Legendary", "White bird")]
    assert d.read("blue with red eyes")["tokens"] == ["red", "eyes"]  # one colour narrows, the rest are words
    tags = dict(d.tags_for(925))
    assert tags["mouse"] == "root" and tags["white"] == "colour" and tags["Family"] == "genus"
    assert ("winged", "shape") in d.tags_for(249) and ("legendary", "status") in d.tags_for(249)


def test_describe_uses_bulbapedia_and_your_words(tmp_path):
    d = _describe_data(tmp_path, origins={249: "Lugia appears to be based on a plesiosaur and a sea bird, with a dragon's neck."},
                       own=({151: ["kitten"]}, {}))
    d.build()
    assert "Lugia" in [r["name"] for r in d.search("plesiosaur")]
    assert ("plesiosaur", "bulbapedia") not in d.tags_for(249)  # only looks-like words become tags
    assert ("dragon", "bulbapedia") in d.tags_for(249)
    assert [r["name"] for r in d.search("kitten")] == ["Mew"]
    d.update_words(925, [], ["mouse"])  # a hidden word stops matching
    assert "Maushold" not in [r["name"] for r in d.search("mouse")]


def test_bulbapedia_origin_download(tmp_path):
    import httpx
    from app import bulbapedia
    page = ("==Trivia==\nStuff.\n===Origin===\n{{p|Lugia}} appears to be based on a [[wikipedia:Plesiosaur|plesiosaur]] "
            "and a ''sea bird''.<ref>book</ref>\n===Name origin===\nLugia may be a combination of lugeo.\n==In other languages==\n")
    assert bulbapedia.origin_section(page) == "Lugia appears to be based on a plesiosaur and a sea bird."
    assert bulbapedia.title("Farfetch’d") == "Farfetch'd (Pokémon)"
    asked = []

    def get(url):
        asked.append(url)
        if "Mew" in url and len(asked) == 1:
            raise httpx.ConnectError("no route")
        return {"parse": {"wikitext": page}}

    store = bulbapedia.Descriptions(tmp_path / "bulbapedia", get=get, pause=0)
    first = store.download({249: "Lugia", 151: "Mew"})
    assert "couldn't be reached" in first and list(store.load()) == []  # Mew (151) comes first and fails
    assert "ready" in store.download({249: "Lugia", 151: "Mew"}) and set(store.load()) == {151, 249}
    store.download({249: "Lugia", 151: "Mew"})
    assert len(asked) == 3  # picks up where it stopped; nothing fetched twice


def test_pokemon_words_on_find_and_pokemon_page(client, monkeypatch, tmp_path):
    d = _describe_data(tmp_path, own=None)
    from app import pokemon_words
    monkeypatch.setattr(web, "describer", d)
    d.own = lambda: pokemon_words.own_and_hidden(main.conn)
    main.conn.execute("DELETE FROM pokemon_tags")
    main.conn.execute("DELETE FROM pokemon_tag_hidden")
    main.conn.commit()
    d.build()
    _setup_with_set(client)
    client.post("/pokemon/151/words/add", data={"word": "kitten", "back": "/pokemon/151"})
    assert [r["name"] for r in d.search("kitten")] == ["Mew"]
    assert 'name="word" value="kitten"' in client.get("/pokemon/151").text  # the Pokémon page lists its words too
    client.post("/pokemon/925/words/remove", data={"word": "mouse"})
    entry = pokemon_words.for_pokemon(main.conn, d, 925)
    assert "mouse" in entry["hidden"] and ("mouse", "root") not in entry["tags"]
    assert [r["name"] for r in d.search("mouse")] == ["Tandemaus"]  # a removed word stops finding it (round 7)
    pokemon_words.show(main.conn, d, 925, "mouse")
    assert ("mouse", "root") in pokemon_words.for_pokemon(main.conn, d, 925)["tags"]
    page = client.get("/describe", params={"words": "white legendary bird"}).text
    assert "Lugia" in page and "Legendary <svg class=\"ic\"" in page
