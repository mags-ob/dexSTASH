"""#116: arts matched by the illustration, for every card. The Enhanced Search store keeps a second fingerprint
per picture (the illustration only); arts group on it with the same artist; rainbow and gold versions join their
art as cards of their own; arts exist for every Pokémon (catalog.card_arts) for Find (#95)."""
import numpy as np
import pytest

from app import arts, chase, chase_pages, main, pictures, upgrades
from test_app import FakeEncoder, _card_png, _setup_with_set, client  # noqa: F401

DIM = pictures.DIM


def _pair(score: float) -> tuple:
    """Two unit vectors whose dot product is score."""
    a = np.zeros(DIM, np.float32)
    b = np.zeros(DIM, np.float32)
    a[0] = 1.0
    b[0], b[1] = score, (1 - score * score) ** 0.5
    return a, b


def _joined(score: float, artist_a="Ken Sugimori", artist_b="Ken Sugimori", rarity_a=None, rarity_b=None,
            whole: float | None = None) -> bool:
    """Whether two cards whose illustrations score `score` come out as one art."""
    a, b = _pair(score)
    cards = [{"card_id": "x-1", "lang": "en", "illustrator": artist_a, "rarity": rarity_a, "release_date": "2020-01-01"},
             {"card_id": "ja:x-1", "lang": "ja", "illustrator": artist_b, "rarity": rarity_b, "release_date": "2020-01-01"}]
    vectors = dict(zip(("x-1", "ja:x-1"), _pair(whole))) if whole is not None else {}
    out = chase.group(cards, vectors, [], art={"x-1": a, "ja:x-1": b})
    return len(out) == 1


# --- AC1: one store, two fingerprints ----------------------------------------------------------------------

def test_index_keeps_both_fingerprints_row_for_row(tmp_path):
    v = np.eye(DIM, dtype=np.float32)
    index = pictures.Index("en", tmp_path)
    index.put([{"card": "a", "set": "s", "key": "k1"}, {"card": "b", "set": "s", "key": "k2"}], v[:2], v[10:12])
    index.put([{"card": "c", "set": "s", "key": "k3"}], v[2:3])  # no illustration yet: zeros
    index.put([{"card": "h", "set": "s", "key": "hand:photo.jpg"}], v[3:4])  # a photo you added never gets one
    assert index.art_count() == 2 and [r["key"] for r in index.missing_art()] == ["k3"]
    index.put_art(["k3"], v[12:13])
    index.put([{"card": "a", "set": "s", "key": "k1"}], v[5:6], v[15:16])  # the same picture again replaces both
    assert index.keep_only(lambda r: r["card"] != "b") == 1
    index.save()
    again = pictures.Index("en", tmp_path)
    by_card = {r["card"]: i for i, r in enumerate(again.rows)}
    assert again.art[by_card["a"]][15] == 1 and again.vectors[by_card["a"]][5] == 1
    assert again.art[by_card["c"]][12] == 1 and not again.art[by_card["h"]].any()
    assert again.missing_art() == [] and again.art_count() == 2


def test_illustrations_written_for_other_rows_are_made_again(tmp_path, monkeypatch):
    v = np.eye(DIM, dtype=np.float32)
    index = pictures.Index("en", tmp_path)
    index.put([{"card": "a", "set": "s", "key": "k1"}], v[:1], v[1:2])
    index.save()
    np.save(tmp_path / "en.art.npy", np.zeros((5, DIM), np.float16))  # out of step with the rows
    again = pictures.Index("en", tmp_path)
    assert len(again.rows) == 1 and again.art_count() == 0 and len(again.missing_art()) == 1
    # The illustrations' file is not a language of its own.
    monkeypatch.setattr(pictures, "INDEX", tmp_path)
    assert pictures.languages() == ["en"]
    again.drop()
    assert not (tmp_path / "en.art.npy").exists()


# --- AC3/AC4: the rule ---------------------------------------------------------------------------------------

def test_same_artist_joins_at_the_illustration_bar_only():
    assert _joined(0.93) and not _joined(0.92)
    assert not _joined(0.99, artist_b="Atsuko Nishida")  # different artists never
    assert _joined(0.95, artist_b=None) and not _joined(0.94, artist_b=None)  # no artist: a stricter bar
    assert _joined(0.86, rarity_b="Rare Rainbow") and not _joined(0.86)  # a rainbow version of the art
    assert _joined(0.86, rarity_a="Hyper rare") and not _joined(0.84, rarity_b="Rare Rainbow")
    assert not _joined(0.90, artist_b="Atsuko Nishida", rarity_b="Rare Rainbow")


def test_whole_card_score_isnt_used_when_both_have_illustrations():
    assert not _joined(0.80, whole=0.99)


def test_a_card_without_an_illustration_yet_keeps_the_old_rule():
    a, b = _pair(0.95)
    cards = [{"card_id": "x-1", "lang": "en", "illustrator": "A"}, {"card_id": "x-2", "lang": "en", "illustrator": "A"}]
    assert len(chase.group(cards, {"x-1": a, "x-2": b}, [], art={"x-1": a})) == 1  # whole card, 0.92
    assert len(chase.group(cards, {"x-1": a, "x-2": b}, [])) == 1  # no illustrations at all: as before


# --- AC5: your fixes win -------------------------------------------------------------------------------------

def test_your_fixes_win_over_the_illustration():
    a, b = _pair(0.99)
    c = np.zeros(DIM, np.float32)
    c[5] = 1
    cards = [{"card_id": i, "lang": "en", "illustrator": "A"} for i in ("x-1", "x-2", "x-3")]
    art = {"x-1": a, "x-2": b, "x-3": c}
    fixes = [{"card_id": "x-2", "join_card_id": None}, {"card_id": "x-3", "join_card_id": "x-1"}]
    out = chase.group(cards, {}, fixes, art=art)
    assert sorted(sorted(o["cards"]) for o in out) == [["x-1", "x-3"], ["x-2"]]


# --- AC7: the pairs judged by hand in the test (19 Nov 2026) ----------------------------------------------

# (what, illustration score, whole-card score, same artist, rarity of the second card, judged the same art)
JUDGED = [
    ("Ancient Origins 43 / 92", 0.66, 0.94, True, None, False),
    ("Battle Styles 97 / SWSH109", 0.84, 0.92, True, None, False),
    ("Lost Thunder 121 / 203", 0.71, 0.93, True, None, False),
    ("Fates Collide 56 / XY130", 0.79, 0.91, True, None, False),
    ("Battle Styles 97 / 154", 0.89, 0.80, False, None, False),
    ("Battle Styles 154 / SWSH109", 0.93, 0.81, False, None, False),
    ("Lost Thunder 203 / Unified Minds 225", 0.92, 0.85, True, None, False),
    ("Lost Thunder 121 / Unified Minds 225", 0.923, 0.84, True, None, False),
    ("Unified Minds 225 / 245 (rainbow)", 0.881, 0.78, True, "Rare Rainbow", True),
    ("Lost Thunder 203 / 223 (rainbow)", 0.928, 0.80, True, "Rare Rainbow", True),
    ("The same art in Japanese", 0.93, 0.74, True, None, True),
    ("The same art in Taiwan Chinese", 0.99, 0.91, True, None, True),
]


@pytest.mark.parametrize("what,score,whole,same,rarity,judged", JUDGED, ids=[j[0] for j in JUDGED])
def test_the_judged_pairs_come_out_as_judged(what, score, whole, same, rarity, judged):
    assert _joined(score, artist_b="Ken Sugimori" if same else "Kagemaru Himeno", rarity_b=rarity, whole=whole) == judged


# --- AC2 and AC6: Taiwan twins, following from the store, the fill through the queue ---------------------

@pytest.fixture
def store(client, tmp_path, monkeypatch):  # noqa: F811
    """An empty Enhanced Search store in a temporary folder."""
    monkeypatch.setattr(pictures, "INDEX", tmp_path / "index")
    pictures.forget_loaded()
    yield tmp_path / "index"
    pictures.forget_loaded()
    main.conn.executescript("DELETE FROM card_arts; DELETE FROM index_cards;")


def _card(dex, card_id, lang, illustrator="Ken Sugimori", rarity="Rare", image="https://assets.example/x"):
    main.conn.execute("""INSERT INTO pokemon_cards (dex_id, card_id, lang, set_id, set_name, number, name, illustrator,
                         rarity, image_url, variants) VALUES (?, ?, ?, 's', 'Set', '1', 'Larvitar', ?, ?, ?, '["normal"]')""",
                      (dex, card_id, lang, illustrator, rarity, image))


def test_taiwan_cards_borrow_the_japanese_fingerprints_and_following_fetches_nothing(store):
    a, b = _pair(0.97)
    pictures.index_for("en").put([{"card": "sm7-1", "set": "sm7", "key": "k1"}], a.reshape(1, DIM), a.reshape(1, DIM))
    pictures.index_for("ja").put([{"card": "ja:SM7-1", "set": "ja:SM7", "key": "k2"}], b.reshape(1, DIM), b.reshape(1, DIM))
    pictures.index_for("en").save()
    pictures.index_for("ja").save()
    for card_id, lang in (("sm7-1", "en"), ("ja:SM7-1", "ja"), ("zh-tw:SM7-1", "zh-tw"), ("zh-tw:SM7-99", "zh-tw")):
        _card(246, card_id, lang)
    main.conn.commit()

    fetched = []

    def fetch(url):
        fetched.append(url)
        return _card_png((30, 180, 40))
    whole, art = chase.fingerprints(main.conn, 246, FakeEncoder(), fetch=fetch, folder=store / "pokemon", art=True)
    assert np.allclose(art["zh-tw:SM7-1"], art["ja:SM7-1"]) and np.allclose(whole["zh-tw:SM7-1"], whole["ja:SM7-1"])
    assert len(fetched) == 1 and "zh-tw:SM7-99" in art  # only the Taiwan card with no Japanese twin was fetched
    assert chase.regroup(main.conn, 246, (whole, art)) == 2  # three cards of one art, and the one without a twin
    assert chase.twin("zh-tw:SM7-1") == "ja:SM7-1" and chase.twin("ja:SM7-1") is None


def test_illustrations_are_filled_one_picture_at_a_time_and_pick_up_after_a_restart(store, monkeypatch):
    v = np.eye(DIM, dtype=np.float32)
    index = pictures.index_for("en")
    index.put([{"card": f"c-{i}", "set": "s", "key": f"k{i}", "url": f"https://assets.example/{i}"} for i in range(10)],
              v[:10])
    index.put([{"card": "h", "set": "s", "key": "hand:p.jpg"}], v[20:21])
    active, most, fetched = [0], [0], []

    def fetch(url):
        active[0] += 1
        most[0] = max(most[0], active[0])
        fetched.append(url)
        if url.endswith("/3"):
            active[0] -= 1
            raise OSError("not found")
        active[0] -= 1
        return _card_png((30, 180, 40))
    monkeypatch.setattr(pictures, "FILL_SAVE_EVERY", 8)
    made, failed = pictures.fill_illustrations(FakeEncoder(), fetch=fetch)
    assert (made, failed) == (9, 1) and most[0] == 1 and len(fetched) == 10
    pictures.forget_loaded()  # a restart: only the one that failed is tried again
    fetched.clear()
    assert pictures.fill_illustrations(FakeEncoder(), fetch=fetch) == (0, 1) and fetched == ["https://assets.example/3"]
    stats = pictures.stats()
    assert stats["illustrations"] == 9 and stats["illustrations_wanted"] == 10


def test_without_illustrations_the_store_fill_does_no_fetching(store):
    assert pictures.fill_illustrations(FakeEncoder(), fetch=lambda url: pytest.fail("fetched")) == (0, 0)


# --- arts for every Pokémon, and the lookups #95 uses -------------------------------------------------------

def test_every_pokemon_gets_arts_and_lookups_answer_from_the_database(client, store):  # noqa: F811
    a, b = _pair(0.96)
    far = np.zeros(DIM, np.float32)
    far[9] = 1
    rainbow = _pair(0.88)[1]
    pictures.index_for("en").put([{"card": "sm8-1", "set": "sm8", "key": "k1"}, {"card": "sm8-2", "set": "sm8", "key": "k2"},
                                  {"card": "sm8-223", "set": "sm8", "key": "k3"}],
                                 np.stack([a, far, rainbow]), np.stack([a, far, rainbow]))
    pictures.index_for("ja").put([{"card": "ja:SM8-1", "set": "ja:SM8", "key": "k4"}], b.reshape(1, DIM), b.reshape(1, DIM))
    pictures.index_for("en").save()
    pictures.index_for("ja").save()
    for lang, card, rarity in (("en", "sm8-1", "Rare"), ("en", "sm8-2", "Rare"), ("en", "sm8-223", "Rare Rainbow"),
                               ("ja", "SM8-1", "R"), ("zh-tw", "SM8-1", "R")):
        main.conn.execute("""INSERT INTO index_cards (lang, id, set_id, local_id, name, folded, rarity, illustrator, dex_ids)
                             VALUES (?, ?, 'sm8', '1', 'Larvitar', 'larvitar', ?, 'Ken Sugimori', '[246]')""", (lang, card, rarity))
    main.conn.commit()
    assert not arts.built(main.conn)
    assert arts.rebuild(main.conn, names=None) == 4  # sm8-1, its Japanese and Taiwan versions, and the rainbow one
    found = arts.lookup(main.conn, ["sm8-1", "ja:SM8-1", "zh-tw:SM8-1", "sm8-223", "sm8-2", "nope"])
    assert set(found) == {"sm8-1", "ja:SM8-1", "zh-tw:SM8-1", "sm8-223"}  # cards in no art are left out
    assert len({f["art"] for f in found.values()}) == 1 and found["sm8-223"]["secret"] and not found["sm8-1"]["secret"]
    assert arts.members(main.conn, "ja:SM8-1") == ["ja:SM8-1", "sm8-1", "sm8-223", "zh-tw:SM8-1"]
    assert arts.members(main.conn, "sm8-2") == ["sm8-2"]

    # A followed Pokémon's arts (with your fixes) come first.
    user_id = _setup_with_set(client)
    chase.follow(main.conn, user_id, 246, "Larvitar", user_id)
    for card_id, lang in (("sm8-1", "en"), ("ja:SM8-1", "ja")):
        _card(246, card_id, lang)
    chase.take_out(main.conn, 246, "ja:SM8-1", user_id)
    chase.regroup(main.conn, 246, chase.fingerprints(main.conn, 246, None, art=True))
    followed = arts.lookup(main.conn, ["sm8-1", "ja:SM8-1", "sm8-223"])
    assert followed["sm8-1"]["art"].startswith("f") and followed["ja:SM8-1"]["art"] != followed["sm8-1"]["art"]
    assert followed["sm8-223"]["art"].startswith("c")  # not on the followed page: from every Pokémon's arts
    assert arts.members(main.conn, "ja:SM8-1") == ["ja:SM8-1"]


# --- the rainbow label, Data sources, the upgrade ----------------------------------------------------------

def test_a_rainbow_version_shows_its_rarity_in_gold_in_the_art_sheet(client):  # noqa: F811
    user_id = _setup_with_set(client)
    chase.follow(main.conn, user_id, 248, "Tyranitar", user_id)
    _card(248, "sm8-203", "en", image=None)
    _card(248, "sm8-223", "en", rarity="Rare Rainbow", image=None)
    main.conn.commit()
    chase.put_with(main.conn, 248, "sm8-223", "sm8-203", user_id)
    chase.regroup(main.conn, 248, ({}, {}))
    assert chase_pages._recolour("Rare Rainbow") == "Rainbow Rare" and chase_pages._recolour("Rare Holo") is None
    html = client.get("/pokemon/248/art", params={"card": "sm8-203"}).text
    assert '<span class="as-gold">Rainbow Rare</span>' in html and html.count('class="as-gold"') == 1
    assert "Each card counts on its own" in html


def test_the_upgrade_fills_illustrations_when_enhanced_search_is_on(monkeypatch):
    from app import web
    started = []
    monkeypatch.setattr(web, "_pictures_ready", lambda: True)
    monkeypatch.setattr(web, "_index_pictures", lambda label="": started.append(label) or True)
    task = next(t for t in upgrades.TASKS if t.run is upgrades.illustrations)
    assert task.slow
    task.run(main.conn)
    assert started == ["Fingerprinting illustrations"]
    monkeypatch.setattr(web, "_pictures_ready", lambda: False)
    task.run(main.conn)
    assert len(started) == 1  # without Enhanced Search nothing changes


def test_data_sources_counts_illustrations(client, store):  # noqa: F811
    _setup_with_set(client)
    v = np.eye(DIM, dtype=np.float32)
    pictures.index_for("en").put([{"card": "a", "set": "s", "key": "k1"}, {"card": "b", "set": "s", "key": "k2"}],
                                 v[:2], np.stack([v[3], np.zeros(DIM, np.float32)]))
    pictures.index_for("en").save()
    main.conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('pictures_on', '1')")
    main.conn.commit()
    try:
        html = client.get("/account/data").text
    finally:
        main.conn.execute("DELETE FROM app_meta WHERE key = 'pictures_on'")
        main.conn.commit()
    assert "Illustrations (for arts)" in html and "1 of 2" in html
    assert pictures.stats()["illustrations"] == 1
