"""The Home page: the collection's numbers, what was added lately, and cards
that need a look."""

import sqlite3
from datetime import date, timedelta

from . import clock, display, prices, value
from .inventory import VARIANTS

CHANGE_DAYS = 30
MOVERS = 3  # Home's movers each way
POKEDOKU_URL = "https://pokedoku.com/"  # a link out only: nothing is read from the site


def _today() -> date:
    return display.today()


def change(conn: sqlite3.Connection, collection_id: int, today: date | None = None) -> dict | None:
    """How the value of the cards you have now moved since the price update
    closest to 30 days ago: {'amount', 'percent', 'since'}. Lines that had no
    price then are left out of both sides, so adding cards isn't a price rise.
    None when there's no earlier update to compare with."""
    today = today or _today()
    # History only gets a row when a price changes, so the update days come from the run log too.
    days = [r[0] for r in conn.execute(
        """SELECT DISTINCT date(ps.captured_at) FROM price_snapshots ps
           JOIN inventory i ON i.card_id = ps.card_id AND i.variant = ps.variant
           WHERE i.user_id = :cid AND ps.source IN ('tcgplayer', 'average') AND date(ps.captured_at) < :today
           UNION SELECT date(ran_at) FROM price_runs WHERE date(ran_at) < :today AND COALESCE(status, 'ok') <> 'failed'
             AND date(ran_at) >= (SELECT MIN(date(ps.captured_at)) FROM price_snapshots ps
                                  JOIN inventory i ON i.card_id = ps.card_id AND i.variant = ps.variant
                                  WHERE i.user_id = :cid AND ps.source IN ('tcgplayer', 'average'))""",
        {"cid": collection_id, "today": today.isoformat()})]
    if not days:
        return None
    target = today - timedelta(days=CHANGE_DAYS)
    since = min(days, key=lambda d: (abs((date.fromisoformat(d) - target).days), d))
    then = now = 0.0
    for r in conn.execute(
        """SELECT i.quantity, i.condition, p.price AS now,
                  COALESCE((SELECT ps.price FROM price_snapshots ps WHERE ps.card_id = i.card_id AND ps.variant = i.variant
                     AND ps.source = 'average' AND date(ps.captured_at) <= :since
                   ORDER BY ps.captured_at DESC LIMIT 1),
                  (SELECT ps.price FROM price_snapshots ps WHERE ps.card_id = i.card_id AND ps.variant = i.variant
                     AND ps.source = 'tcgplayer' AND date(ps.captured_at) <= :since
                   ORDER BY ps.captured_at DESC LIMIT 1)) AS then_price
           FROM inventory i
           JOIN prices p ON p.card_id = i.card_id AND p.variant = i.variant
           WHERE i.user_id = :cid""", {"cid": collection_id, "since": since}):
        if r["then_price"] is None:
            continue
        share = prices.share(r["condition"])
        then += r["then_price"] * r["quantity"] * share
        now += r["now"] * r["quantity"] * share
    amount = round(now - then, 2)
    return {"amount": amount, "percent": round(100 * amount / then, 1) if then else None, "since": since}


def top_card(conn: sqlite3.Connection, collection_id: int) -> dict | None:
    """The most valuable card you have (one copy, its printing's price)."""
    row = conn.execute(
        """SELECT c.id, c.name, c.number, c.image_url, s.name AS set_name, s.logo_url, i.variant, p.price
           FROM inventory i JOIN cards c ON c.id = i.card_id JOIN sets s ON s.set_id = c.set_id
           JOIN prices p ON p.card_id = i.card_id AND p.variant = i.variant
           WHERE i.user_id = ? AND i.condition <> 'DMG' ORDER BY p.price DESC LIMIT 1""", (collection_id,)).fetchone()
    return dict(row, variant_label=VARIANTS.get(row["variant"])) if row else None


def stats(conn: sqlite3.Connection, collection_id: int) -> dict:
    value = prices.collection_value(conn, collection_id)  # Spent left Home in round 24 (it's on My Stash › Spending)
    return {"cards": value["cards"], "value": value["value"], "estimated": value["estimated"], "change": change(conn, collection_id),
            "top": top_card(conn, collection_id)}


def movers(conn: sqlite3.Connection, collection_id: int, today: date | None = None) -> dict:
    """Today's biggest movers for Home: the MOVERS cards up and down most since the day before, by dollar change."""
    m = value.movers(conn, collection_id, "1d", today=today)
    return {"up": m["up"][:MOVERS], "down": m["down"][:MOVERS], "start": m["start"]}


def recently_added(conn: sqlite3.Connection, collection_id: int, limit: int = 8) -> list[dict]:
    """The last cards added that you still have, newest first, with who added them."""
    out, seen = [], set()
    for r in conn.execute(
        """SELECT l.card_id, l.at, l.variant, c.name, c.number, c.image_url, s.name AS set_name, s.logo_url,
                  COALESCE(u.username, CASE WHEN l.user_id IS NULL THEN NULL ELSE 'a removed user' END) AS by
           FROM inventory_log l JOIN cards c ON c.id = l.card_id JOIN sets s ON s.set_id = c.set_id
           LEFT JOIN users u ON u.id = l.user_id
           WHERE l.collection_id = ? AND l.delta > 0
             AND EXISTS (SELECT 1 FROM inventory i WHERE i.user_id = l.collection_id AND i.card_id = l.card_id)
           ORDER BY l.id DESC LIMIT 200""", (collection_id,)):
        if r["card_id"] in seen:
            continue
        seen.add(r["card_id"])
        out.append(dict(r))
        if len(out) == limit:
            break
    return out


STALE_DAYS = 90  # A price typed in (or last read) longer ago than this is worth a look


REASONS = {"no_price": "No price", "stale": "Stale price", "old_pc": "Price over 90 days old"}


def to_review(conn: sqlite3.Connection, collection_id: int, show: str | None = None) -> list[dict]:
    """Cards you have or chase (as single cards) worth a look, one line a card and reason: a printing with no price
    from any source and nothing to borrow (or a chased card with no priced printing), a printing whose newest
    downloaded price is older than the stale setting (prices.stale_days), and a PriceCharting price typed over 90
    days ago, since it never updates by itself. show: one reason only."""
    states = prices.printing_states(conn, collection_id)
    by_card: dict[str, list[str]] = {}
    for (card_id, _), state in sorted(states.items()):
        for reason in ("no_price", "stale"):
            if reason in state and reason not in by_card.get(card_id, []):
                by_card.setdefault(card_id, []).append(reason)
    cutoff = clock.since(timedelta(days=STALE_DAYS))
    for (card_id,) in conn.execute(
            """SELECT DISTINCT i.card_id FROM inventory i
               JOIN prices p ON p.card_id = i.card_id AND p.variant = i.variant AND p.origin = ?
               JOIN typed_prices t ON t.card_id = i.card_id AND t.variant = i.variant AND t.kind = 'pricecharting'
               WHERE i.user_id = ? AND t.typed_at < ?""", (prices.PRICECHARTING, collection_id, cutoff)):
        if "no_price" not in by_card.get(card_id, []):
            by_card.setdefault(card_id, []).append("old_pc")
    if not by_card:
        return []
    marks = ",".join("?" * len(by_card))
    cards = {r["id"]: dict(r) for r in conn.execute(
        f"""SELECT c.id, c.name, c.number, c.image_url, s.name AS set_name, s.logo_url FROM cards c
            JOIN sets s ON s.set_id = c.set_id WHERE c.id IN ({marks})""", list(by_card))}
    out = [dict(cards[cid], reason=REASONS[r], key=r) for cid, reasons in by_card.items() if cid in cards for r in reasons
           if show in (None, "", r)]
    order = list(REASONS)
    return sorted(out, key=lambda r: (order.index(r["key"]), r["name"]))
