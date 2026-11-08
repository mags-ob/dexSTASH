"""PriceCharting prices through PriceCharting's official API (round 28), for hosts who enter their own API token
(a paid PriceCharting plan) on Data sources › Prices › API keys. With no token nothing here contacts PriceCharting,
and its pages are never read (sites.blocked lets only /api/ through).

What it does, during the price update, for each card someone keeps (English and Japanese):
- Finds the card's PriceCharting product once a week: one search by English name, set name and number. A product
  counts when its name has the card's number (#25) and its set ("console") is the card's set, in the right language
  (PriceCharting's Japanese sets say "Japanese"). A name in brackets is a printing: [Reverse Holo], [1st Edition],
  [Poke Ball], [Master Ball]; none is the card itself. Never a guess: two candidates for one printing is none.
- Reads each matched product's price once a day: the ungraded ("loose") price, in US cents.

The token goes in the request's query (that's how PriceCharting's API takes it), so a request's address is never
logged or put in an error.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from datetime import timedelta

from . import clock

log = logging.getLogger("dexstash.pricecharting")

API = "https://www.pricecharting.com/api"
PAUSE = 1.0  # between requests
MATCH_EVERY = timedelta(days=7)
PRICE_EVERY = timedelta(hours=20)
BRACKETS = {"reverse holo": "reverse", "reverse holofoil": "reverse", "1st edition": "first_ed", "holo": "holo",
            "poke ball": "pokeball", "pokeball": "pokeball", "master ball": "masterball", "masterball": "masterball"}
LAST_RUN: dict = {}


class Refused(Exception):
    """PriceCharting turned the token down."""


def token() -> str | None:
    from . import keys
    return keys.get("pricecharting")


def fetch(path: str, params: dict) -> dict:
    """One API call as JSON. Raises Refused for a token PriceCharting turns down; other failures name no address."""
    from . import keys, sites
    key = token()
    if not key:
        raise Refused("no token")
    try:
        with sites.client(timeout=30, follow_redirects=False) as client:
            resp = client.get(f"{API}/{path}", params={**params, "t": key})
    except Exception as e:
        raise RuntimeError(f"PriceCharting couldn't be reached ({type(e).__name__})") from None
    if resp.status_code in (401, 403):
        raise Refused(f"PriceCharting answered {resp.status_code}")
    if resp.status_code >= 400:
        raise RuntimeError(f"PriceCharting answered {resp.status_code} for {path}")
    data = resp.json()
    if isinstance(data, dict) and data.get("status") == "error":
        message = str(data.get("error-message") or "")
        if "token" in message.lower() or "subscription" in message.lower():
            raise Refused("PriceCharting refused the token")
        raise RuntimeError(f"PriceCharting: {message[:120]}".replace(key, "…"))
    keys.used_soon("pricecharting")
    return data


def test() -> tuple[bool, str]:
    """Data sources' Test: one search."""
    try:
        data = fetch("products", {"q": "pikachu"})
    except Refused:
        return False, "PriceCharting refused this token. Check your subscription includes API access."
    found = len((data or {}).get("products") or [])
    return True, f"Works ({found} products found for a test search)."


def _norm(text: str | None) -> str:
    from .tcgcsv import _norm as norm
    return norm(text)


def _bare(number: str | None) -> str:
    from .prices import bare_number
    return bare_number(number or "")


def printing_of(product_name: str) -> str | None:
    """'' for the card itself, a printing for a name in brackets we know, None for one we don't track."""
    m = re.search(r"\[([^\]]+)\]", product_name or "")
    if not m:
        return ""
    return BRACKETS.get(m.group(1).strip().lower())


def matches(card: dict, products: list[dict]) -> dict[str, dict]:
    """printing -> the one PriceCharting product for a card (see the module's rules)."""
    number = _bare(card["number"])
    wanted_set = _norm(card["set_name"])
    japanese = card["lang"] == "ja"
    found: dict[str, list[dict]] = {}
    for p in products:
        name, console = p.get("product-name") or "", p.get("console-name") or ""
        if not re.search(rf"#0*{re.escape(number)}(?![0-9A-Za-z])", name) or not number:
            continue
        plain_console = _norm(console)
        if ("japanese" in plain_console) != japanese or any(w in plain_console for w in ("chinese", "korean")):
            continue
        if not wanted_set or wanted_set not in plain_console:
            continue
        printing = printing_of(name)
        if printing is None:
            continue
        found.setdefault(printing, []).append(p)
    return {v: ps[0] for v, ps in found.items() if len(ps) == 1}


def _cards(conn: sqlite3.Connection, only: set[str] | None) -> list[dict]:
    from . import prices
    from .web import names
    rows = conn.execute(f"""SELECT c.id, c.number, c.name, c.english_name, c.twin_name, s.set_id, s.language,
                                   s.name AS set_name, s.english_name AS set_english
                            FROM cards c JOIN sets s ON s.set_id = c.set_id
                            WHERE c.id IN ({prices.KEPT_SQL}) AND s.language IN ('en', 'ja')
                              AND c.hand_added_by IS NULL ORDER BY c.id""").fetchall()
    out = []
    for r in rows:
        if only is not None and r["set_id"] not in only:
            continue
        english = r["name"] if r["language"] == "en" else (r["english_name"] or r["twin_name"] or names.to_english(r["name"]))
        set_name = r["set_name"] if r["language"] == "en" else r["set_english"]
        if english and set_name:
            out.append({"id": r["id"], "number": r["number"], "name": english, "set_name": set_name, "lang": r["language"]})
    return out


def run(conn: sqlite3.Connection, sleep=time.sleep, only: set[str] | None = None) -> tuple[int, list[str]]:
    """Match (weekly) and price (daily) every kept card; quotes stored as source 'pricecharting', basis 'api'.
    only: just those sets (the short run after adding cards). Returns (cards priced, errors)."""
    from . import prices
    LAST_RUN.clear()
    if not token():
        return 0, []
    errors: list[str] = []
    searched = priced = 0
    try:
        for card in _cards(conn, only):
            row = conn.execute("SELECT MAX(checked_at) FROM pc_matches WHERE card_id = ?", (card["id"],)).fetchone()
            if not row[0] or row[0] < clock.since(MATCH_EVERY):
                q = " ".join(x for x in (card["name"], card["set_name"], _bare(card["number"]),
                                         "Japanese" if card["lang"] == "ja" else "") if x)
                found = matches(card, (fetch("products", {"q": q}) or {}).get("products") or [])
                searched += 1
                sleep(PAUSE)
                conn.execute("DELETE FROM pc_matches WHERE card_id = ?", (card["id"],))
                conn.executemany("INSERT INTO pc_matches (card_id, variant, product_id, name, console, checked_at) "
                                 "VALUES (?, ?, ?, ?, ?, ?)",
                                 [(card["id"], v, int(p["id"]), p.get("product-name"), p.get("console-name"), clock.now())
                                  for v, p in found.items() if str(p.get("id", "")).isdigit()] or
                                 [(card["id"], "", 0, None, None, clock.now())])  # 0: none found, look again next week
                kept = [v for v in found] or ["-"]
                conn.execute(f"DELETE FROM price_quotes WHERE card_id = ? AND source = ? AND variant NOT IN ({','.join('?' * len(kept))})",
                             (card["id"], prices.PRICECHARTING, *kept))
                conn.commit()
            for m in conn.execute("SELECT variant, product_id FROM pc_matches WHERE card_id = ? AND product_id > 0",
                                  (card["id"],)).fetchall():
                q = conn.execute("SELECT updated_at FROM price_quotes WHERE card_id = ? AND variant = ? AND source = ?",
                                 (card["id"], m["variant"], prices.PRICECHARTING)).fetchone()
                if q and q[0] >= clock.since(PRICE_EVERY):
                    continue
                product = fetch("product", {"id": m["product_id"]})
                sleep(PAUSE)
                cents = product.get("loose-price") if isinstance(product, dict) else None
                if isinstance(cents, (int, float)) and cents > 0:
                    conn.execute("INSERT OR REPLACE INTO price_quotes (card_id, variant, source, price, currency, basis, ref, "
                                 "updated_at) VALUES (?, ?, ?, ?, 'USD', 'api', ?, ?)",
                                 (card["id"], m["variant"], prices.PRICECHARTING, round(cents / 100, 2), m["product_id"], clock.now()))
                    priced += 1
                else:
                    conn.execute("DELETE FROM price_quotes WHERE card_id = ? AND variant = ? AND source = ?",
                                 (card["id"], m["variant"], prices.PRICECHARTING))
                conn.commit()
    except Refused as e:
        errors.append(f"PriceCharting: {e}")
        LAST_RUN["refused"] = True
    except Exception as e:
        log.warning("PriceCharting stopped: %s", e)
        errors.append(f"PriceCharting: {e}")
    LAST_RUN.update({"searched": searched, "priced": priced})
    return priced, errors


def status(conn: sqlite3.Connection) -> dict:
    return {"token": bool(token()),
            "matched": conn.execute("SELECT COUNT(DISTINCT card_id) FROM pc_matches WHERE product_id > 0").fetchone()[0],
            "priced": conn.execute("SELECT COUNT(DISTINCT card_id) FROM price_quotes WHERE source = 'pricecharting'").fetchone()[0]}
