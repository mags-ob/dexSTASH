"""Pokémon colour themes: the app's accent colours come from a Pokémon's
sprite, and Home shows the sprite.

Sprites are PokeAPI's (github.com/PokeAPI/sprites), downloaded once into
data/sprites/ when an admin asks on Data sources. They're Nintendo and Game
Freak artwork: they stay on the NAS and never go in a public repo. Each
sprite's palette is worked out once with Pillow and kept in pokemon_palette.

Each login picks one Pokémon to keep, or a new one every day. The daily pick
is shared by every login, stored per day (so restarts and both phones agree)
and never the same Pokémon two days in a row.
"""

import colorsys
import datetime as dt
import json
import logging
import random
import sqlite3
from pathlib import Path

from . import db, display

log = logging.getLogger("dexstash.themes")

SPRITE_URL = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{shiny}{id}.png"
SPECIES = 1025       # #001 Bulbasaur to #1025 Pecharunt
DEFAULT = 1          # everyone starts on Bulbasaur
SHINY_ODDS = 10      # "Shiny colours now and then": one day in ten
FOLDER = db.DB_PATH.parent / "sprites"

WHITE, INK, SURFACE_DARK = (255, 255, 255), (29, 29, 31), (28, 28, 31)
LIGHT_BG, DARK_BG = (245, 245, 247), (17, 17, 19)

# Bulbasaur's colours, so the app has its default theme before any sprite is downloaded.
BULBASAUR = {
    "swatches": ["#399494", "#62d5b4", "#83eec5", "#184a4a", "#73ac31"],
    "light": {"accent": "#399494", "on": "#1d1d1f", "ink": "#328282", "soft": "#e3f0f0", "bg": "#ecf0f2",
              "second": "#73ac31", "tint": "#e0edd2"},
    "dark": {"accent": "#399494", "on": "#1d1d1f", "ink": "#399494", "soft": "#23393b", "bg": "#13191b",
             "second": "#73ac31", "tint": "#344424"},
}


# --- colour maths (WCAG contrast) ---------------------------------------------

def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(v))) for v in c)


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lum(c) -> float:
    def f(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = c
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b) -> float:
    if isinstance(a, str):
        a = _rgb(a)
    if isinstance(b, str):
        b = _rgb(b)
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _mix(a, b, t: float):
    return tuple(a[i] * t + b[i] * (1 - t) for i in range(3))


def _fit(c, against, need: float, darker: bool):
    """Darken or lighten c, keeping its hue, until it reaches need against `against`."""
    h, l, s = colorsys.rgb_to_hls(*[v / 255 for v in c])
    c = _rgb(_hex(c))  # judged as it will be drawn, rounded to whole values
    while contrast(c, against) < need and 0 < l < 1:
        l = max(0.0, l - 0.01) if darker else min(1.0, l + 0.01)
        c = _rgb(_hex(tuple(v * 255 for v in colorsys.hls_to_rgb(h, l, s))))
    return c


def _swatches(image, n: int = 5) -> list[tuple]:
    """Up to n colours of a sprite, most used first: (rgb, count, h, l, s)."""
    from PIL import Image
    im = image.convert("RGBA")
    raw = im.tobytes()
    px = [tuple(raw[i:i + 3]) for i in range(0, len(raw), 4) if raw[i + 3] > 200]
    if not px:
        return []
    q = Image.new("RGB", (len(px), 1))
    q.putdata(px)
    q = q.quantize(colors=12, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()
    out = []
    for count, i in sorted(q.getcolors(), reverse=True):
        rgb = tuple(pal[i * 3:i * 3 + 3])
        h, l, s = colorsys.rgb_to_hls(*[v / 255 for v in rgb])
        if l < 0.12 or l > 0.92:  # outlines and highlights
            continue
        if any(sum(abs(a - b) for a, b in zip(rgb, o[0])) < 60 for o in out):
            continue
        out.append((rgb, count, h, l, s))
        if len(out) == n:
            break
    return out


def palette(image) -> dict | None:
    """Theme colours from a sprite: an accent (vivid, covering a fair part of
    the sprite) and a second colour, with text colours that stay readable."""
    p = _swatches(image)
    if not p:
        return None
    total = sum(x[1] for x in p)
    big = [x for x in p if x[1] / total >= 0.08 and not (x[3] > 0.78 and x[4] < 0.85)] or p
    acc = max(big, key=lambda x: (x[4] ** 1.2) * ((x[1] / total) ** 0.6) * (1 - abs(x[3] - 0.5)))
    rest = [x for x in p if x is not acc]

    def hue_gap(a, b):
        d = abs(a - b)
        return min(d, 1 - d)
    second = max(rest, key=lambda x: (hue_gap(x[2], acc[2]) > 0.08) * x[4] * (0.35 + x[1] / total)) if rest else acc
    a = acc[0]
    on = WHITE if contrast(a, WHITE) >= contrast(a, INK) else INK  # button text
    a_light = _fit(a, on, 4.5, on == WHITE) if contrast(a, on) < 4.5 else a
    ink = _fit(a, WHITE, 4.5, True)                 # the accent as text on white
    a_dark = _fit(a, SURFACE_DARK, 4.5, False)      # the accent on dark surfaces
    on_dark = INK if contrast(a_dark, INK) >= contrast(a_dark, WHITE) else WHITE
    if on_dark == INK:  # the button text is a shade off the dark surface, so check it too
        a_dark = _fit(a_dark, INK, 4.5, False)
    return {
        "swatches": [_hex(x[0]) for x in p],
        "light": {"accent": _hex(a_light), "on": _hex(on), "ink": _hex(ink), "soft": _hex(_mix(a, WHITE, .14)),
                  "bg": _hex(_mix(a, LIGHT_BG, .05)), "second": _hex(second[0]), "tint": _hex(_mix(second[0], WHITE, .22))},
        "dark": {"accent": _hex(a_dark), "on": _hex(on_dark), "ink": _hex(a_dark), "soft": _hex(_mix(a, SURFACE_DARK, .24)),
                 "bg": _hex(_mix(a, DARK_BG, .06)), "second": _hex(second[0]), "tint": _hex(_mix(second[0], SURFACE_DARK, .28))},
    }


def with_own(colours: dict, surface) -> dict:
    """Adds the owned-ring colour: the theme's second colour, so the ring looks
    different from buttons (the accent). It's made readable against the page,
    and moved away from the accent when the two are too alike."""
    accent = _rgb(colours["accent"])
    own = _rgb(colours.get("second") or colours["accent"])
    if sum(abs(a - b) for a, b in zip(own, accent)) < 90:  # one-colour Pokémon: a deeper shade of the accent instead
        own = _fit(accent, surface, 7.0, surface == WHITE)
    own = _fit(own, surface, 3.0, surface == WHITE)
    on = WHITE if contrast(own, WHITE) >= contrast(own, INK) else INK
    return {**colours, "own": _hex(own), "own_on": _hex(on)}


def _hls(c):
    return colorsys.rgb_to_hls(*[v / 255 for v in c])


def _from_hls(h, l, s):
    return _rgb(_hex(tuple(v * 255 for v in colorsys.hls_to_rgb(h, l, s))))


def _text_on(c):
    return WHITE if contrast(c, WHITE) >= contrast(c, INK) else INK


def _readable(c, need: float = 4.5):
    """c with the text colour that reads best on it, c nudged until that text passes."""
    on = _text_on(c)
    if contrast(c, on) < need:
        c = _fit(c, on, need, on == WHITE)
    return c, on


def roles(colours: dict, swatches: list[str], dark_mode: bool = False) -> dict:
    """The five theme roles from a palette: dark (Home banner,
    selection bar), main (buttons, the current tab), second (value and price boxes,
    banners, active chips), light (page tint, chips) and pop (Add, Update prices,
    spinners). Each comes with a text colour that passes 4.5:1 on it."""
    main = _rgb(colours["accent"])
    h, l, s = _hls(main)
    dark = _from_hls(h, 0.2 if not dark_mode else 0.17, min(s, 0.4))
    dark = _fit(dark, WHITE, 7.0, True)
    def gap(a, b):
        d = abs(a - b)
        return min(d, 1 - d)
    swatches = [_rgb(x) for x in swatches or []]
    # pop: the palette's own contrast colour (the eyes, the flame), else a warm colour that stands off main.
    other = _rgb(colours.get("second") or colours["accent"])
    oh, ol, os_ = _hls(other)
    picks = [(gap(xh, h) * xs, c) for c in swatches for xh, xl, xs in [_hls(c)]
             if xs >= 0.35 and 0.25 <= xl <= 0.72 and gap(xh, h) >= 0.12]
    if gap(oh, h) >= 0.12 and os_ >= 0.35 and 0.25 <= ol <= 0.72:
        pop = other
    elif picks:
        pop = max(picks)[1]
    else:
        pop = _from_hls(0.6 if (h < 0.12 or h > 0.92) else 0.0, 0.58, 0.8)  # red, or blue for a red Pokémon
    ph = _hls(pop)[0]
    # second: a deeper colour from the sprite in main's family (a purple Pokémon's indigo), else main deepened.
    near = [c for c in swatches if gap(_hls(c)[0], h) < 0.15 and gap(_hls(c)[0], ph) >= 0.1 and _hls(c)[2] >= 0.2
            and sum(abs(x - y) for x, y in zip(c, main)) >= 40]
    if near:
        second = min(near, key=lambda c: abs(_hls(c)[1] - 0.42))
        sh, sl, ss = _hls(second)
    else:
        sh, sl, ss = h, l, s
    second = _fit(_from_hls(sh, min(sl, 0.42), max(ss, 0.3)), WHITE, 4.5, True)
    white_pop = _fit(pop, WHITE, 4.5, True)  # white text reads best on a bright pop, if it stays bright
    if _hls(pop)[1] - _hls(white_pop)[1] <= 0.18:
        pop, on_pop = white_pop, WHITE
    else:
        pop, on_pop = _readable(pop)
    if dark_mode:
        light = _rgb(colours.get("soft") or _hex(_mix(main, SURFACE_DARK, .24)))
        chip = _mix(main, SURFACE_DARK, .32)
        page = _mix(main, DARK_BG, .06)
    else:
        light = _mix(main, WHITE, .12)
        chip = _mix(main, WHITE, .2)
        page = _mix(main, WHITE, .08)
    return {"dark": _hex(dark), "on_dark": _hex(WHITE), "second_bg": _hex(second), "on_second": _hex(WHITE),
            "light": _hex(light), "chip": _hex(chip), "page": _hex(page), "pop": _hex(pop), "on_pop": _hex(on_pop)}


# --- sprites and palettes -----------------------------------------------------

def sprite_path(species_id: int, shiny: bool = False, folder: Path | None = None) -> Path:
    return (folder or FOLDER) / ("shiny" if shiny else "") / f"{species_id}.png"


def store_palette(conn: sqlite3.Connection, species_id: int, shiny: bool, path: Path) -> bool:
    from PIL import Image
    with Image.open(path) as im:
        p = palette(im)
    if p is None:
        return False
    conn.execute("INSERT OR REPLACE INTO pokemon_palette (species_id, shiny, swatches, light, dark, extracted_at) "
                 "VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
                 (species_id, int(shiny), json.dumps(p["swatches"]), json.dumps(p["light"]), json.dumps(p["dark"])))
    return True


def download(conn: sqlite3.Connection, job=None, get=None, ids=None, shiny: bool = True,
             folder: Path | None = None) -> str:
    """Fetch the sprites not here yet (each is downloaded once) and work out
    the palettes not worked out yet. Runs as a Data sources job."""
    from . import sites
    get = get or (lambda url: sites.get(url, timeout=30, follow_redirects=True))
    ids = list(ids or range(1, SPECIES + 1))
    kinds = [False, True] if shiny else [False]
    have = {(r[0], bool(r[1])) for r in conn.execute("SELECT species_id, shiny FROM pokemon_palette")}
    todo = [(i, s) for s in kinds for i in ids]
    fetched = failed = 0
    for n, (species_id, is_shiny) in enumerate(todo):
        if job is not None and n % 25 == 0:
            job.progress(n, len(todo), "shiny sprites" if is_shiny else "sprites")
        path = sprite_path(species_id, is_shiny, folder)
        try:
            if not path.exists():
                resp = get(SPRITE_URL.format(shiny="shiny/" if is_shiny else "", id=species_id))
                if resp.status_code != 200:
                    failed += 1
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(resp.content)
                fetched += 1
            if (species_id, is_shiny) not in have:
                store_palette(conn, species_id, is_shiny, path)
                conn.commit()
        except Exception as e:  # one bad sprite doesn't stop the rest
            log.warning("sprite %s%s: %s", species_id, " shiny" if is_shiny else "", e)
            failed += 1
    if job is not None:
        job.progress(len(todo), len(todo))
    return f"{fetched} sprites downloaded" + (f", {failed} couldn't be fetched" if failed else "")


def counts(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT SUM(shiny = 0), SUM(shiny = 1), MAX(extracted_at) FROM pokemon_palette").fetchone()
    return {"normal": row[0] or 0, "shiny": row[1] or 0, "at": row[2]}


def get_palette(conn: sqlite3.Connection, species_id: int, shiny: bool = False) -> dict | None:
    row = conn.execute("SELECT * FROM pokemon_palette WHERE species_id = ? AND shiny = ?",
                       (species_id, int(shiny))).fetchone()
    if row is None:
        return None
    return {"swatches": json.loads(row["swatches"]), "light": json.loads(row["light"]), "dark": json.loads(row["dark"])}


# --- settings and the daily pick -----------------------------------------------

def user_setting(conn: sqlite3.Connection, user_id: int) -> dict:
    row = conn.execute("SELECT mode, species_id FROM user_theme WHERE user_id = ?", (user_id,)).fetchone()
    return {"mode": row["mode"], "species_id": row["species_id"]} if row else {"mode": "fixed", "species_id": DEFAULT}


def save_user_setting(conn: sqlite3.Connection, user_id: int, mode: str, species_id: int) -> None:
    if mode not in ("fixed", "daily"):
        raise ValueError("Pick a theme.")
    if not 1 <= int(species_id) <= SPECIES:
        raise ValueError("That isn't a Pokémon number.")
    conn.execute("INSERT INTO user_theme (user_id, mode, species_id) VALUES (?, ?, ?) "
                 "ON CONFLICT(user_id) DO UPDATE SET mode = excluded.mode, species_id = excluded.species_id",
                 (user_id, mode, int(species_id)))
    conn.commit()


def shared_settings(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT t.*, u.username AS updated_by_name FROM theme_settings t "
                       "LEFT JOIN users u ON u.id = t.updated_by WHERE t.id = 1").fetchone()
    if row is None:
        return {"only_owned": False, "shiny_sometimes": False, "updated_by_name": None, "updated_at": None}
    return {"only_owned": bool(row["only_owned"]), "shiny_sometimes": bool(row["shiny_sometimes"]),
            "updated_by_name": row["updated_by_name"], "updated_at": row["updated_at"]}


def save_shared_settings(conn: sqlite3.Connection, user_id: int, only_owned: bool, shiny_sometimes: bool) -> None:
    current = shared_settings(conn)
    if (current["only_owned"], current["shiny_sometimes"]) == (only_owned, shiny_sometimes):
        return
    conn.execute("INSERT INTO theme_settings (id, only_owned, shiny_sometimes, updated_by, updated_at) "
                 "VALUES (1, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now')) ON CONFLICT(id) DO UPDATE SET only_owned = excluded.only_owned, "
                 "shiny_sometimes = excluded.shiny_sometimes, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                 (int(only_owned), int(shiny_sometimes), user_id))
    conn.commit()


def owned_species(conn: sqlite3.Connection, names) -> set[int]:
    """Pokémon on cards anyone has, by species number. Trainer and energy cards match nothing."""
    found = set()
    if not names.available():
        return found
    rows = conn.execute("SELECT DISTINCT c.name, c.twin_name FROM inventory i JOIN cards c ON c.id = i.card_id "
                        "WHERE i.quantity > 0").fetchall()
    for r in rows:
        for text in (r["name"], r["twin_name"]):
            species = names.species_id(text) if text else None
            if species:
                found.add(species)
                break
    return found


def daily_pick(conn: sqlite3.Connection, today: dt.date, owned=None, rng: random.Random | None = None) -> dict:
    """Today's shared Pokémon, picked on the first request of the day. owned()
    gives the species on owned cards, asked only when that option is on."""
    day = today.isoformat()
    row = conn.execute("SELECT species_id, shiny FROM daily_pokemon WHERE day = ?", (day,)).fetchone()
    if row:
        return {"species_id": row["species_id"], "shiny": bool(row["shiny"])}
    rng = rng or random.Random()
    settings = shared_settings(conn)
    with db.transaction(conn):
        row = conn.execute("SELECT species_id, shiny FROM daily_pokemon WHERE day = ?", (day,)).fetchone()
        if row:  # another request got there first
            return {"species_id": row["species_id"], "shiny": bool(row["shiny"])}
        last = conn.execute("SELECT species_id FROM daily_pokemon WHERE day < ? ORDER BY day DESC LIMIT 1",
                            (day,)).fetchone()
        pool = {r[0] for r in conn.execute("SELECT species_id FROM pokemon_palette WHERE shiny = 0")} or {DEFAULT}
        if settings["only_owned"] and owned is not None:
            mine = set(owned()) & pool
            pool = mine or pool
        if last and len(pool) > 1:
            pool.discard(last["species_id"])
        species = rng.choice(sorted(pool))
        shiny = settings["shiny_sometimes"] and rng.randrange(SHINY_ODDS) == 0 and \
            get_palette(conn, species, True) is not None
        conn.execute("INSERT INTO daily_pokemon (day, species_id, shiny) VALUES (?, ?, ?)", (day, species, int(shiny)))
    return {"species_id": species, "shiny": shiny}


def for_user(conn: sqlite3.Connection, user_id: int, names=None, today: dt.date | None = None,
             folder: Path | None = None) -> dict:
    """The theme to draw for a login: its Pokémon, colours and sprite (None until downloaded)."""
    setting = user_setting(conn, user_id)
    if setting["mode"] == "daily":
        pick = daily_pick(conn, today or display.today(),
                          (lambda: owned_species(conn, names)) if names is not None else None)
    else:
        pick = {"species_id": setting["species_id"], "shiny": False}
    species, shiny = pick["species_id"], pick["shiny"]
    colours = get_palette(conn, species, shiny)
    if colours is None:  # not downloaded yet: Bulbasaur's colours
        species, shiny = DEFAULT, False
        colours = get_palette(conn, DEFAULT) or BULBASAUR
    has_sprite = sprite_path(species, shiny, folder).exists()
    return {"species_id": species, "shiny": shiny, "mode": setting["mode"], "name": species_name(names, species),
            "light": with_roles(colours, "light"), "dark": with_roles(colours, "dark"), "swatches": colours.get("swatches") or [],
            "sprite": (f"/sprites/{'shiny/' if shiny else ''}{species}.png" if has_sprite else None)}


def with_roles(colours: dict, mode: str) -> dict:
    """A palette's light or dark colours with the owned ring and the five roles added."""
    base = with_own(colours[mode], WHITE if mode == "light" else SURFACE_DARK)
    return {**base, **roles(colours[mode], colours.get("swatches") or [], mode == "dark")}


def species_name(names, species_id: int) -> str:
    try:
        name = names.english_name(species_id) if names is not None and names.available() else None
    except Exception:  # the names list couldn't be downloaded
        name = None
    return name or ("Bulbasaur" if species_id == DEFAULT else f"#{species_id:03d}")
