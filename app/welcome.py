"""The first-run wizard: what it has saved, what it downloads, and Home's Getting started card.

The first admin goes through /welcome once, after their first sign-in, before any other page (app/routes/welcome.py).
Its state is in app_meta: setup_done ('1' once Continue is pressed after Finish's downloads, or database step 10
for an install already in use), setup_preparing (after Finish, until Continue: the downloads' job keys), setup_step (the furthest step reached), setup_choices (what the steps picked, as JSON) and getting_started
('show' after Finish, 'hidden' once Home's card is hidden)."""
import json
import sqlite3

from . import card_index, themes

# Tests sign in to a set-up install unless they test the wizard (then they put this back to None).
_override: bool | None = None

STEPS = 6  # 1 password, 2 basics, 3 cards, 4 downloads, 5 first cards, 6 someone else (Finish)
# The addresses the first admin can reach before Finish (signing out, and the wizard itself).
OPEN_PATHS = ("/welcome", "/logout")

# Step 3's languages, in the order shown. The TCGdex ones get Find's card catalog; mainland Chinese sets come from
# the Chinese datasets instead (TCGdex has none).
LANGUAGES = {"en": "English", "ja": "Japanese", "zh-tw": "Chinese (Taiwan)", "zh-cn": "Chinese (mainland)", "ko": "Korean"}
CATALOG_LANGUAGES = ("en", "ja", "zh-tw", "ko")

# Estimated downloads, shown next to each choice so people can pick: (MB downloaded, MB kept, how long).
# The card catalog keeps about 3.5 MB per 10,000 cards (measured with its own tables and name index).
SIZES = {
    "catalog-en": (8, 7, "a few minutes"),
    "catalog-ja": (8, 7, "about 5 to 10 minutes"),
    "catalog-zh-tw": (2, 2, "a few minutes"),
    "catalog-ko": (1, 1, "a minute or two"),
    "pokemon": (13, 13, "a few minutes"),          # the Pokédex (about 9 MB) and sprites (about 4 MB)
    "descriptions": (25, 2, "about 20 minutes"),     # one Bulbapedia page a second; only the Origin text is kept
    "chinese": (26, 2, "a few minutes"),             # the datasets file, deleted once the sets are kept
    "enhanced": (1335, 395, "hours"),                # the 335 MB model, about 1 GB of pictures (not kept), 60 MB of fingerprints
}


def _meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _save(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO app_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                 (key, value))


def done(conn: sqlite3.Connection) -> bool:
    """Setup is finished, so every page opens as usual."""
    if _override is not None:
        return _override
    return _meta(conn, "setup_done") == "1"


def step(conn: sqlite3.Connection) -> int:
    """The furthest step reached (1 to STEPS)."""
    value = _meta(conn, "setup_step")
    return min(max(int(value), 1), STEPS) if value and value.isdigit() else 1


def reached(conn: sqlite3.Connection, number: int) -> None:
    """Step `number` is next; the furthest step only moves forward."""
    _save(conn, "setup_step", str(max(step(conn), min(number, STEPS))))


DEFAULT_CHOICES = {"languages": ["en"], "pokemon": True, "descriptions": True, "enhanced": False, "first": ""}


def choices(conn: sqlite3.Connection) -> dict:
    try:
        saved = json.loads(_meta(conn, "setup_choices") or "{}")
    except ValueError:
        saved = {}
    return {**DEFAULT_CHOICES, **(saved if isinstance(saved, dict) else {})}


def save_choices(conn: sqlite3.Connection, **changes) -> dict:
    now = {**choices(conn), **changes}
    _save(conn, "setup_choices", json.dumps(now))
    return now


def collected(conn: sqlite3.Connection) -> list[str]:
    """The languages step 3 ticked, in LANGUAGES order."""
    picked = choices(conn)["languages"]
    return [lang for lang in LANGUAGES if lang in picked]


def preparing(conn: sqlite3.Connection) -> list[str] | None:
    """After Finish, until Continue: the basics' job keys (Getting your stash ready). None before Finish."""
    value = _meta(conn, "setup_preparing")
    return json.loads(value) if value else None


def start_preparing(conn: sqlite3.Connection, keys: list[str]) -> None:
    _save(conn, "setup_preparing", json.dumps(keys))


def finish(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM app_meta WHERE key = 'setup_preparing'")
    _save(conn, "setup_done", "1")
    _save(conn, "getting_started", "show")


def total(keys: list[str]) -> tuple[int, int]:
    """MB downloaded and MB kept for these SIZES keys."""
    return sum(SIZES[k][0] for k in keys), sum(SIZES[k][1] for k in keys)


def size_text(key: str) -> str:
    down, kept, time = SIZES[key]
    if down == kept:
        return f"About {mb(down)}, {time}."
    return f"About {mb(down)} downloaded, {mb(kept)} kept, {time}."


def mb(n: int) -> str:
    return f"{n / 1000:.1f} GB" if n >= 1000 else f"{n} MB"


# --- Home's Getting started card ------------------------------------------------------------------------

def hide(conn: sqlite3.Connection) -> None:
    _save(conn, "getting_started", "hidden")


def getting_started(conn: sqlite3.Connection, user, describe_ready: bool) -> dict | None:
    """The card's items, or None when it isn't shown: only to the admin who did the setup, only on an install
    that went through the wizard, and until Hide."""
    if not user.get("owner") or _meta(conn, "getting_started") != "show":
        return None
    items = [{"label": "Basics set", "done": done(conn)}]
    langs = [lang for lang in collected(conn) if lang in CATALOG_LANGUAGES]
    if card_index.enabled() and langs:
        kept = {r["lang"]: r for r in card_index.status(conn)}
        busy = card_index.building()
        ready = all(lang in kept and lang not in busy for lang in langs)
        note = ", ".join(f"{LANGUAGES[lang]} {kept[lang]['sets']} sets" for lang in langs if lang in kept)
        items.append({"label": "Card catalog downloaded", "done": ready, "note": None if ready else note or "starting"})
    sprites = themes.counts(conn)["normal"]
    items.append({"label": "Pokémon data downloaded", "done": describe_ready and sprites >= themes.SPECIES})
    copies = conn.execute("""SELECT COALESCE(SUM(i.quantity), 0) FROM inventory i JOIN users u ON u.id = i.user_id
                             WHERE COALESCE(u.collection_id, u.id) = ?""", (user["collection_id"],)).fetchone()[0]
    items.append({"label": "Add 10 cards", "done": copies >= 10, "note": f"{copies} so far" if 0 < copies < 10 else None,
                  "href": "/search"})
    binders = conn.execute("SELECT COUNT(*) FROM binders WHERE collection_id = ?", (user["collection_id"],)).fetchone()[0]
    items.append({"label": "Make a binder", "done": binders > 0, "href": "/collection/binders"})
    picked_theme = conn.execute("SELECT 1 FROM user_theme WHERE user_id = ?", (user["id"],)).fetchone() is not None
    return {"items": items, "done": sum(1 for i in items if i["done"]), "theme_hint": sprites > 0 and not picked_theme}
