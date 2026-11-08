"""CardTrader prices through CardTrader's own API, for hosts who give a token.

The token is the host's, entered on Data sources › Prices › API keys (round 28, keys.py; before that
DEXSTASH_CARDTRADER_TOKEN in .env, which still works). It is a full key to that CardTrader account: stored
encrypted outside the database, never logged, shown, backed up or put in an error message. With no token nothing here contacts CardTrader.

What it does, during the price update:
- Pokémon expansions (weekly). Each of our sets is matched to one: by code (TCGdex's id lowercased, then without
  a mainland Chinese set's trailing "c"), then by name. A set with no match is listed on Data sources to pick.
- An expansion's cards (weekly): single cards carry their collector number, which matches our card's number.
- Listings (each run): one request per expansion, about the cheapest 25 per card. The price kept is the median of
  the Near Mint (or Mint) listings in the card's language (0.20.1; before, the lowest). A printing with no Near
  Mint listing gets no price. A listing that doesn't say its language is left out and counted. Graded and
  sealed listings are skipped. Listings marked reverse holo or 1st edition price those printings.
- Poké Ball and Master Ball printings are their own expansions on CardTrader ("p-pre", "csv8-p"…), as are the
  mainland Chinese reverse holos ("-r"); those give that printing's price.

CardTrader allows about one listings request a second; this waits a second between requests, and stops for
the night when told to slow down twice. An expansion that wasn't read keeps last night's prices: only the
printings that were read are replaced.
"""

import json
import logging
import re
import sqlite3
import time
from datetime import timedelta

from . import clock, db

log = logging.getLogger("dexstash.cardtrader")

API = "https://api.cardtrader.com/api/v2"
TOKEN_ENV = "DEXSTASH_CARDTRADER_TOKEN"
POKEMON = 5  # CardTrader's game id
EXPANSIONS_EVERY = timedelta(days=7)
BLUEPRINTS_EVERY = timedelta(days=7)
PAUSE = 1.0  # seconds between requests
NEAR_MINT = {"mint", "near mint"}
# Our language -> the values CardTrader writes in a listing's pokemon_language
LANGUAGES = {"en": {"en"}, "ja": {"jp", "ja"}, "zh-cn": {"zh-cn"}, "zh-tw": {"zh-tw"}}
# Expansions that are one printing of another: code pattern -> our variant
PRINTING_CODES = (("p-{}", "pokeball"), ("{}-p", "pokeball"), ("m-{}", "masterball"), ("{}-m", "masterball"),
                  ("{}-r", "reverse"))
STATUS_KEY = "cardtrader_last_run"
TOO_HIGH = 20  # a price over 20x TCGplayer's (and $20 more) for the same printing is a parked listing: skipped
MARKED = {"reverse", "first_ed", "pokeball", "masterball"}  # printings priced only by their own listings


class Refused(Exception):
    """CardTrader said no to the token (expired, revoked or mistyped)."""


class Busy(Exception):
    """CardTrader asked to slow down (429)."""


def token() -> str | None:
    """The token entered on Data sources (round 28; keys.py), else one still in .env."""
    from . import keys
    return keys.get("cardtrader")


def test() -> tuple[bool, str]:
    """Data sources' Test: one call to CardTrader's /info with the token."""
    try:
        info = fetch("/info")
    except Refused:
        return False, "CardTrader refused this token. Make a new one on CardTrader (Settings › API)."
    except Busy:
        return False, "CardTrader asked to slow down. Try again in a minute."
    name = (info or {}).get("name") if isinstance(info, dict) else None
    return True, f"Works{' (' + name + ')' if name else ''}."


def _now() -> str:
    return clock.now()


def _older(stamp: str | None, age: timedelta) -> bool:
    return not stamp or stamp < clock.since(age)


def fetch(path: str, params: dict | None = None):
    """One API call as JSON. Raises Refused on 401/403 and Busy on 429. The token goes in the header only, so it
    is never part of a URL or an error."""
    from . import sites
    key = token()
    if not key:
        raise Refused("no token")
    with sites.client(timeout=60, follow_redirects=False) as client:
        resp = client.get(f"{API}{path}", params=params or {}, headers={"Authorization": f"Bearer {key}"})
    if resp.status_code in (401, 403):
        raise Refused(f"CardTrader answered {resp.status_code}")
    if resp.status_code == 429:
        raise Busy("CardTrader asked to slow down")
    if resp.status_code >= 400:
        raise RuntimeError(f"CardTrader answered {resp.status_code} for {path}")
    from . import keys
    keys.used_soon("cardtrader")
    return resp.json()


def _meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO app_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                 (key, value))


def _norm(text: str | None) -> str:
    from .tcgcsv import _norm as norm
    return norm(text)


def _number(text) -> str:
    """'245', '245/204', '045' and 'SV045' all compare as their digits and letters without leading zeros."""
    text = str(text or "").split("/")[0].strip().lower()
    return re.sub(r"^0+(?=\w)", "", re.sub(r"[^a-z0-9]", "", text))


# --- expansions and sets --------------------------------------------------------------------------------------

def load_expansions(conn: sqlite3.Connection, get=fetch) -> int:
    rows = [e for e in get("/expansions") or [] if isinstance(e, dict) and e.get("game_id") == POKEMON and e.get("id")]
    if not rows:
        return 0
    now = _now()
    with db.transaction(conn):
        conn.execute("DELETE FROM ct_expansions")
        conn.executemany("INSERT INTO ct_expansions (id, code, name, fetched_at) VALUES (?, ?, ?, ?)",
                         [(int(e["id"]), str(e.get("code") or "").lower(), str(e.get("name") or ""), now) for e in rows])
    return len(rows)


# Sets CardTrader names its own way (from her expansions list, 26 Oct 2026): our set id -> CardTrader code.
ALIASES = {"swsh10.5": "pkmgo", "svp": "svpromo", "2021swsh": "mc21", "2024sv": "m24",
           "ja:m-p": "mp-promo", "ja:pmcg5": "gls"}
# TCGdex sets that are part of another set on CardTrader: Trainer Gallery, Galarian Gallery, Shiny Vault and
# Classic Collection cards are in the main set's expansion there.
SUBSET = re.compile(r"(tg|gg|sv|cc|-c)$")


def _codes(set_id: str) -> list[str]:
    """The CardTrader codes one of our sets may have. Mainland Chinese: 'zh-cn:CBB4C' -> cbb4c, cbb4;
    'zh-cn:CS2.5C' -> cs2.5, cs25; 'zh-cn:CSVE1C1' (first half) -> csve1. 'ja:SV8' -> sv8."""
    alias = ALIASES.get(set_id.lower())
    code = (set_id.split(":", 1)[1] if ":" in set_id else set_id).lower()
    out = [alias] if alias else []
    out.append(code)
    if set_id.lower().startswith(("zh-cn:", "chs:")):
        half = re.match(r"^(.+)c\d$", code)
        base = half.group(1) if half else code[:-1] if code.endswith("c") else code
        out += [base, base.replace(".", "")]
    return list(dict.fromkeys(out))


def parent_set(conn: sqlite3.Connection, set_id: str) -> sqlite3.Row | None:
    """The main set a gallery / vault / classic collection set belongs to on CardTrader ('swsh9tg' -> swsh9)."""
    if ":" in set_id or not SUBSET.search(set_id):
        return None
    return conn.execute("SELECT set_id, name FROM sets WHERE set_id = ?", (SUBSET.sub("", set_id),)).fetchone()


def match_set(conn: sqlite3.Connection, set_id: str, name: str | None) -> int | None:
    """The CardTrader expansion for one of our sets, matched once and remembered (a pick on Data sources wins)."""
    row = conn.execute("SELECT expansion_id, picked, checked_at FROM ct_sets WHERE set_id = ?", (set_id,)).fetchone()
    if row and (row["picked"] or (row["expansion_id"] is not None and not _older(row["checked_at"], EXPANSIONS_EVERY))):
        return row["expansion_id"]  # (an unmatched set is tried again each time: it's only a look in our own table)
    found = None
    for code in _codes(set_id):
        hit = conn.execute("SELECT id FROM ct_expansions WHERE code = ?", (code,)).fetchone()
        if hit:
            found = hit[0]
            break
    if found is None and name:
        want = {_norm(name), _norm(f"EX {name}")}  # the e-Reader-era EX sets: 'Delta Species' is 'EX Delta Species'
        for e in conn.execute("SELECT id, name FROM ct_expansions ORDER BY id").fetchall():
            if _norm(e["name"]) in want:
                found = e["id"]
                break
    if found is None and (parent := parent_set(conn, set_id)) is not None:
        found = match_set(conn, parent["set_id"], parent["name"])
    conn.execute("INSERT INTO ct_sets (set_id, expansion_id, picked, checked_at) VALUES (?, ?, 0, ?) "
                 "ON CONFLICT(set_id) DO UPDATE SET expansion_id = excluded.expansion_id, checked_at = excluded.checked_at",
                 (set_id, found, _now()))
    return found


def pick(conn: sqlite3.Connection, set_id: str, expansion_id: int | None) -> None:
    """Data sources: the expansion you picked for a set (None: CardTrader doesn't have it). Commits."""
    conn.execute("INSERT INTO ct_sets (set_id, expansion_id, picked, checked_at) VALUES (?, ?, 1, ?) "
                 "ON CONFLICT(set_id) DO UPDATE SET expansion_id = excluded.expansion_id, picked = 1, "
                 "checked_at = excluded.checked_at", (set_id, expansion_id, _now()))
    conn.commit()


def printings_of(conn: sqlite3.Connection, expansion_id: int) -> list[tuple[int, str]]:
    """[(expansion id, variant)]: the expansion itself ('' = any printing) and its Poké Ball, Master Ball and
    reverse holo expansions."""
    row = conn.execute("SELECT code FROM ct_expansions WHERE id = ?", (expansion_id,)).fetchone()
    out = [(expansion_id, "")]
    if row and row[0]:
        for pattern, variant in PRINTING_CODES:
            hit = conn.execute("SELECT id FROM ct_expansions WHERE code = ?", (pattern.format(row[0]),)).fetchone()
            if hit:
                out.append((hit[0], variant))
    return out


def unmatched(conn: sqlite3.Connection) -> list[dict]:
    """Sets with kept cards that have no CardTrader expansion yet, for the pick list on Data sources."""
    from . import prices
    return [dict(r) for r in conn.execute(
        f"""SELECT s.set_id, s.name, s.language FROM ct_sets t JOIN sets s ON s.set_id = t.set_id
            WHERE t.expansion_id IS NULL AND t.picked = 0
              AND EXISTS (SELECT 1 FROM cards c WHERE c.set_id = s.set_id AND c.id IN ({prices.KEPT_SQL}))
            ORDER BY s.language, s.name""")]


def expansions(conn: sqlite3.Connection) -> list[dict]:
    """Every Pokémon expansion, for the pick list."""
    return [dict(r) for r in conn.execute("SELECT id, code, name FROM ct_expansions ORDER BY name")]


# --- cards and listings -------------------------------------------------------------------------------------

def _collector_number(blueprint: dict) -> str | None:
    props = blueprint.get("fixed_properties") or {}
    if not isinstance(props, dict):
        return None
    for key, value in props.items():
        if "collector_number" in key or key == "number":
            return str(value)
    return None


def load_blueprints(conn: sqlite3.Connection, expansion_id: int, get=fetch) -> int:
    """An expansion's single cards (sealed products and other items have no collector number)."""
    rows = []
    for b in get("/blueprints/export", {"expansion_id": expansion_id}) or []:
        if not isinstance(b, dict) or not b.get("id"):
            continue
        number = _collector_number(b)
        if number is None:
            continue
        tcg = b.get("tcg_player_id")
        rows.append((int(b["id"]), expansion_id, number, str(b.get("name") or ""),
                     int(tcg) if isinstance(tcg, (int, str)) and str(tcg).isdigit() else None, _now()))
    with db.transaction(conn):
        conn.execute("DELETE FROM ct_blueprints WHERE expansion_id = ?", (expansion_id,))
        conn.executemany("INSERT OR REPLACE INTO ct_blueprints (blueprint_id, expansion_id, number, name, tcgplayer_id, "
                         "fetched_at) VALUES (?, ?, ?, ?, ?, ?)", rows)
        if not rows:  # remember the visit, so an expansion with no single cards isn't asked again until next week
            conn.execute("INSERT OR REPLACE INTO ct_blueprints (blueprint_id, expansion_id, number, name, tcgplayer_id, "
                         "fetched_at) VALUES (?, ?, NULL, NULL, NULL, ?)", (-expansion_id, expansion_id, _now()))
    return len(rows)


def _blueprints_fresh(conn, expansion_id: int) -> bool:
    row = conn.execute("SELECT MIN(fetched_at) FROM ct_blueprints WHERE expansion_id = ?", (expansion_id,)).fetchone()
    return not _older(row[0] if row else None, BLUEPRINTS_EVERY)


def _blueprint_for(blueprints: list[dict], card: dict) -> int | None:
    """The blueprint with the card's TCGplayer product id when both have one, else the one with the card's
    number; of several, the one whose name matches."""
    if card.get("tcgplayer_id"):
        hit = next((b for b in blueprints if b.get("tcgplayer_id") == card["tcgplayer_id"]), None)
        if hit:
            return hit["blueprint_id"]
    number = _number(card["number"])
    same = [b for b in blueprints if b["number"] is not None and _number(b["number"]) == number]
    if card.get("subset"):  # in the main set's expansion, a number alone can be a different card
        names = {_norm(card.get("name")), _norm(card.get("twin_name"))} - {""}
        same = [b for b in same if _norm(b["name"]) in names]
        if not same:
            # A classic collection card keeps its first printing's number on CardTrader ('NVI 101' for 30th
            # Celebration's N, our 021): the one card of that name numbered with a set code.
            coded = [b for b in blueprints if _norm(b["name"]) in names and re.match(r"^[A-Za-z][A-Za-z0-9.]*\s+\S", b["number"] or "")]
            same = coded if len(coded) == 1 else []
    elif len(same) > 1:
        names = {_norm(card.get("name")), _norm(card.get("twin_name"))} - {""}
        named = [b for b in same if _norm(b["name"]) in names]
        same = named or same
    if not same:
        # A promo numbered with its set code on CardTrader ('MEP 101', ours 101): the one card of that name.
        names = {_norm(card.get("name")), _norm(card.get("twin_name"))} - {""}
        tail = [b for b in blueprints if b["number"] and _norm(b["name"]) in names
                and _number(b["number"].split()[-1]) == number]
        same = tail if len(tail) == 1 else []
    if same and card.get("by_hand"):  # a card you added yourself: its number is yours, so the name must agree too
        names = {_norm(card.get("name")), _norm(card.get("twin_name"))} - {""}
        same = [b for b in same if _norm(b["name"]) in names]
    return same[0]["blueprint_id"] if same else None


def _listing_language(props: dict) -> str | None:
    for key, value in props.items():
        if key.endswith("language") and isinstance(value, str):
            return value.lower()
    return None


def _printing(props: dict) -> str:
    """Which printing a listing is: 'reverse' or 'first_ed' when CardTrader marks it so, else '' (the card's own)."""
    if props.get("pokemon_reverse") is True:
        return "reverse"
    if props.get("first_edition") is True:
        return "first_ed"
    return ""


def near_mint(listings: list[dict], lang: str, printing: str | None = "", skipped: dict | None = None
              ) -> tuple[float, str, bool] | None:
    """(price, currency, False): the median of the Near Mint (or Mint) listings in the card's language (the average
    of the middle two for an even count); graded and sealed ones skipped. The API sends about the cheapest 25
    listings a card, so it's the median of those. A listing that doesn't say its language is left out and counted
    in skipped['no_language']. printing: '' the card's own printing, 'reverse' or 'first_ed' the listings CardTrader
    marks so, None any (a Poké Ball expansion's own cards). None when there's no Near Mint listing."""
    wanted = LANGUAGES.get(lang, {lang})
    offers: list[tuple[float, str]] = []
    for item in listings or []:
        if not isinstance(item, dict) or item.get("graded"):
            continue
        props = item.get("properties_hash") or {}
        if not isinstance(props, dict) or props.get("sealed"):
            continue
        if printing is not None and _printing(props) != printing:
            continue
        if str(props.get("condition") or "").lower() not in NEAR_MINT:
            continue
        language = _listing_language(props)
        if language is None:
            if skipped is not None:
                skipped["no_language"] = skipped.get("no_language", 0) + 1
            continue
        if language not in wanted:
            continue
        price = item.get("price") or {}
        cents = price.get("cents") if isinstance(price, dict) else None
        if not isinstance(cents, (int, float)) or cents <= 0:
            continue
        offers.append((cents / 100, str(price.get("currency") or "USD").upper()))
    if not offers:
        return None
    currency = max({c for _, c in offers}, key=lambda c: sum(1 for _, x in offers if x == c))  # one currency
    amounts = sorted(a for a, c in offers if c == currency)
    mid = len(amounts) // 2
    median = amounts[mid] if len(amounts) % 2 else (amounts[mid - 1] + amounts[mid]) / 2
    return round(median, 2), currency, False


def _believable(conn, card_id: str, variant: str, price: float, currency: str = "USD") -> bool:
    """False for a price far above TCGplayer's for the same printing ('' = the card's own: normal or holo),
    compared in dollars. A price with no rate to dollars yet is kept (it isn't counted until there is one)."""
    from . import fx
    usd, _ = fx.in_usd(price, currency)
    if usd is None:
        return True
    price = usd
    names = (variant,) if variant else ("normal", "holo", "")
    row = conn.execute(f"""SELECT MAX(price) FROM price_quotes WHERE card_id = ? AND source = 'tcgplayer'
                           AND variant IN ({", ".join("?" * len(names))})""", (card_id, *names)).fetchone()
    tcg = row[0] if row else None
    return not tcg or price <= tcg * TOO_HIGH or price - tcg <= 20


def _listings(get, expansion_id: int, lang: str) -> dict:
    """{blueprint id: [listings]} for one expansion, asking for the card's language first."""
    code = {"en": "en", "ja": "jp", "zh-cn": "zh-CN", "zh-tw": "zh-TW"}.get(lang)
    body = None
    if code:
        try:
            body = get("/marketplace/products", {"expansion_id": expansion_id, "language": code})
        except RuntimeError:  # an unknown filter: ask without it (near_mint() still checks the language)
            body = None
    if not body:  # nothing in that language (or a code CardTrader writes differently): all of them
        body = get("/marketplace/products", {"expansion_id": expansion_id})
    return {str(k): v for k, v in body.items()} if isinstance(body, dict) else {}


# --- the run ------------------------------------------------------------------------------------------------

def _kept_by_set(conn) -> dict[tuple[str, str, str], list[dict]]:
    from . import prices
    out: dict[tuple[str, str, str], list[dict]] = {}
    for r in conn.execute(
            f"""SELECT c.id, c.number, c.name, c.twin_name, c.tcgplayer_id, c.hand_added_by IS NOT NULL AS by_hand,
                       s.set_id, s.name AS set_name, s.language
                FROM cards c JOIN sets s ON s.set_id = c.set_id
                WHERE c.id IN ({prices.KEPT_SQL}) ORDER BY s.set_id, c.id"""):
        out.setdefault((r["set_id"], r["set_name"], r["language"]), []).append(dict(r))
    return out


LAST_RUN: dict[str, int] = {}  # the last run's counts (no_language, slowed, too_high), for the run summary


def _write(conn, cards: list[dict], read: set[str], found: dict) -> None:
    """Replace a set's CardTrader prices for the printings that were read; the rest keep last night's."""
    if not read:
        return
    now = _now()
    marks = ",".join("?" * len(read))
    with db.transaction(conn):
        conn.executemany(f"DELETE FROM price_quotes WHERE card_id = ? AND source = 'cardtrader' AND variant IN ({marks})",
                         [(c["id"], *sorted(read)) for c in cards])
        conn.executemany("INSERT OR REPLACE INTO price_quotes (card_id, variant, source, price, currency, basis, ref, "
                         "updated_at, url) VALUES (?, ?, 'cardtrader', ?, ?, ?, ?, ?, ?)",
                         [(cid, v, p, cur, "any" if anyc else "nm", bp, now, card_url(bp))
                          for (cid, v), (p, cur, anyc, bp) in found.items()])


def run(conn: sqlite3.Connection, get=fetch, sleep=time.sleep, only: set[str] | None = None) -> tuple[int, list[str]]:
    """Price every kept card CardTrader has. Returns (cards priced, errors). With no token: (0, []), nothing sent.
    only: just these sets' kept cards (round 27: the short run after you add cards, and a scan batch's matches);
    the full update's summary on Data sources is left as it was."""
    if get is fetch and not token():
        if only is None:
            _set_meta(conn, STATUS_KEY, json.dumps({"at": _now(), "token": False}))
            conn.commit()
        return 0, []
    errors: list[str] = []
    priced: set[str] = set()
    busy = 0
    skipped: dict[str, int] = {}
    if only is None:
        LAST_RUN.clear()
    try:
        if _older(_meta(conn, "cardtrader_expansions_at"), EXPANSIONS_EVERY) or \
                not conn.execute("SELECT 1 FROM ct_expansions LIMIT 1").fetchone():
            if load_expansions(conn, get):
                _set_meta(conn, "cardtrader_expansions_at", _now())
            sleep(PAUSE)
        for (set_id, set_name, lang), cards in _kept_by_set(conn).items():
            if only is not None and set_id not in only:
                continue
            expansion = match_set(conn, set_id, set_name)
            if expansion is None:
                continue
            found: dict[tuple[str, str], tuple[float, str, bool, int]] = {}
            read: set[str] = set()  # the printings whose listings were read: only those are replaced
            subset = parent_set(conn, set_id) is not None
            for exp_id, variant in printings_of(conn, expansion):
                try:
                    if not _blueprints_fresh(conn, exp_id):
                        load_blueprints(conn, exp_id, get)
                        sleep(PAUSE)
                    blueprints = [dict(r) for r in conn.execute(
                        "SELECT blueprint_id, number, name, tcgplayer_id FROM ct_blueprints WHERE expansion_id = ? "
                        "AND number IS NOT NULL",
                        (exp_id,))]
                    if not blueprints:
                        continue
                    listings = _listings(get, exp_id, lang)
                    sleep(PAUSE)
                except Busy:
                    busy += 1
                    skipped["slowed"] = skipped.get("slowed", 0) + 1
                    if busy >= 2:
                        _write(conn, cards, read, found)
                        raise
                    sleep(30)
                    continue
                # The set's own expansion: the card's printing, and its reverse holo and 1st edition as CardTrader
                # marks them. A Poké Ball or Master Ball expansion: that printing, whatever else is marked.
                kinds = (("", ""), ("reverse", "reverse"), ("first_ed", "first_ed")) if not variant else ((variant, None),)
                read.update(ours for ours, _ in kinds)
                for card in cards:
                    blueprint = _blueprint_for(blueprints, {**card, "subset": subset})
                    if not blueprint:
                        continue
                    for ours, printing in kinds:
                        best = near_mint(listings.get(str(blueprint), []), lang, printing, skipped)
                        if best and _believable(conn, card["id"], ours, best[0], best[1]):
                            found[(card["id"], ours)] = (*best, blueprint)
                        elif best:
                            skipped["too_high"] = skipped.get("too_high", 0) + 1
            _write(conn, cards, read, found)
            priced.update(cid for cid, _ in found)
    except Refused as e:
        log.warning("CardTrader refused the token: %s", e)
        errors.append("CardTrader refused the token. Make a new one on CardTrader and put it in .env.")
        if only is not None:
            return len(priced), errors
        _set_meta(conn, STATUS_KEY, json.dumps({"at": _now(), "token": True, "refused": True, "priced": len(priced)}))
        conn.commit()
        return len(priced), errors
    except Busy:
        errors.append("CardTrader asked to slow down; the rest waits for the next update.")
    except Exception as e:  # a network problem: keep what's stored, try again next time
        log.warning("CardTrader: %s", e)
        errors.append(f"CardTrader: {e}")
    if only is not None:
        conn.commit()
        return len(priced), errors
    LAST_RUN.update(skipped)
    _set_meta(conn, STATUS_KEY, json.dumps({"at": _now(), "token": True, "refused": False, "priced": len(priced),
                                            "errors": len(errors), **skipped}))
    conn.commit()
    log.info("CardTrader: %d cards priced, %d problems", len(priced), len(errors))
    return len(priced), errors


def status(conn: sqlite3.Connection) -> dict:
    """For Data sources: whether a token is set, whether CardTrader took it, cards priced, last run."""
    last = _meta(conn, STATUS_KEY)
    last = json.loads(last) if last else None
    return {"token": bool(token()), "last": last, "refused": bool(token() and last and last.get("refused")),
            "priced": conn.execute("SELECT COUNT(DISTINCT card_id) FROM price_quotes WHERE source = 'cardtrader'").fetchone()[0],
            "matched": conn.execute("SELECT COUNT(*) FROM ct_sets WHERE expansion_id IS NOT NULL").fetchone()[0],
            "unmatched": unmatched(conn)}


def side_line(conn: sqlite3.Connection, card_id: str, variant: str | None = None) -> dict | None:
    """The card page's CardTrader line: the printing's own price, else the card's. {price, any_condition, at, url}.
    url opens the card on CardTrader (its blueprint); nothing is read from it."""
    from . import fx
    rows = {r["variant"]: r for r in conn.execute(
        "SELECT variant, price, currency, basis, updated_at, ref FROM price_quotes WHERE card_id = ? AND source = 'cardtrader'",
        (card_id,))}
    r = rows.get(variant) if variant in MARKED else (rows.get(variant or "") or rows.get(""))
    if r is None:
        return None
    return {"price": round(fx.to_usd(r["price"], r["currency"]), 2), "any_condition": r["basis"] == "any",
            "at": r["updated_at"], "url": card_url(r["ref"])}


def card_link(conn: sqlite3.Connection, card_id: str) -> str | None:
    """The card's page on CardTrader when the price update found a CardTrader price for any of its printings."""
    for (ref,) in conn.execute("SELECT ref FROM price_quotes WHERE card_id = ? AND source = 'cardtrader' "
                               "AND price IS NOT NULL ORDER BY variant = '' DESC, variant", (card_id,)):
        if card_url(ref):
            return card_url(ref)
    return None


def card_url(blueprint_id: int | None) -> str | None:
    """A card's page on CardTrader's English site (without /en/ it opens in Italian). Only for a blueprint the price update matched (a negative id is a placeholder
    for an expansion with no cards)."""
    return f"https://www.cardtrader.com/en/cards/{blueprint_id}" if blueprint_id and blueprint_id > 0 else None
