"""What you spent: single cards (a price paid per copy, on the inventory row)
and purchases such as packs, whose cost is shared across the cards pulled.

An amount is kept in the currency it was typed in (a pull's own cost in its purchase's),
shown as typed; totals are worked out in dollars, converting each amount at today's rate, and shown in
the chosen currency like every price."""

import sqlite3

from . import display, fx, inventory

# What a logged purchase was. 'before' is the one "Spent before we started tracking" line.
KINDS = {"packs": "Packs", "box": "Box", "single": "Single card", "other": "Other"}
BEFORE = "before"


def _currency(old_amount, old_currency: str | None, new_amount, currency: str | None = None) -> str:
    """The currency a saved amount is in: the one it had when the amount didn't change, else the one typed in."""
    if old_amount is not None and new_amount is not None and round(old_amount, 2) == round(new_amount, 2) and old_currency:
        return old_currency
    return currency or fx.setting()


def create_purchase(conn: sqlite3.Connection, user_id: int, label: str, cost: float, packs: int = 1,
                    set_id: str | None = None, purchased_on: str | None = None, by: int | None = None,
                    commit: bool = True, kind: str = "packs", currency: str | None = None) -> int:
    if cost < 0 or packs < 1:
        raise ValueError("Cost can't be negative and there has to be at least one pack.")
    kind = kind if kind in KINDS or kind == BEFORE else "other"
    cur = conn.execute(
        "INSERT INTO purchases (user_id, label, set_id, packs, cost, purchased_on, kind, created_by, currency) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, label.strip() or "Purchase", set_id or None, packs, round(cost, 2), purchased_on or None, kind, by,
         currency or fx.setting()),
    )
    if commit:
        conn.commit()
    return cur.lastrowid


def get_purchase(conn: sqlite3.Connection, user_id: int, purchase_id: int) -> dict | None:
    row = conn.execute(
        "SELECT p.*, s.name AS set_name, u.username AS created_by_name FROM purchases p "
        "LEFT JOIN sets s ON s.set_id = p.set_id LEFT JOIN users u ON u.id = p.created_by "
        "WHERE p.id = ? AND p.user_id = ?",
        (purchase_id, user_id),
    ).fetchone()
    return dict(row) if row else None


def delete_purchase(conn: sqlite3.Connection, user_id: int, purchase_id: int) -> None:
    """Forget the purchase. The cards stay in the collection."""
    if get_purchase(conn, user_id, purchase_id) is None:
        return
    conn.execute("DELETE FROM pulls WHERE purchase_id = ?", (purchase_id,))
    conn.execute("DELETE FROM purchases WHERE id = ?", (purchase_id,))
    conn.commit()


def add_pull(conn: sqlite3.Connection, user_id: int, purchase_id: int, card_id: str, variant: str,
             condition: str, quantity: int = 1, by: int | None = None) -> int:
    """Record cards pulled from a purchase and add them to the collection.
    A negative quantity takes them back out. Returns the pulled quantity."""
    row = conn.execute(
        "SELECT id, quantity FROM pulls WHERE purchase_id = ? AND card_id = ? AND variant = ? AND condition = ?",
        (purchase_id, card_id, variant, condition),
    ).fetchone()
    current = row["quantity"] if row else 0
    new = max(current + quantity, 0)
    if new == current:
        return current
    if new == 0:
        conn.execute("DELETE FROM pulls WHERE id = ?", (row["id"],))
    elif row:
        conn.execute("UPDATE pulls SET quantity = ? WHERE id = ?", (new, row["id"]))
    else:
        conn.execute(
            "INSERT INTO pulls (purchase_id, card_id, variant, condition, quantity) VALUES (?, ?, ?, ?, ?)",
            (purchase_id, card_id, variant, condition, new),
        )
    inventory.adjust(conn, user_id, card_id, variant, condition, new - current, by=by, source="pull",
                     origin="pack")  # commits
    return new


def set_pull_cost(conn: sqlite3.Connection, purchase_id: int, pull_id: int, cost_each: float | None) -> None:
    conn.execute("UPDATE pulls SET cost_each = ? WHERE id = ? AND purchase_id = ?",
                 (None if cost_each is None else round(cost_each, 2), pull_id, purchase_id))
    conn.commit()


def pulls(conn: sqlite3.Connection, purchase: dict) -> list[dict]:
    """The purchase's pulls, each with what it cost ('cost_each', set or shared)
    and what it's worth now ('price', TCGplayer market for that printing)."""
    rows = [dict(r) for r in conn.execute(
        """SELECT pl.*, c.name, c.number, c.sort_number, c.image_url, s.logo_url,
                  (SELECT price FROM prices p WHERE p.card_id = pl.card_id AND p.variant = pl.variant) AS price
           FROM pulls pl JOIN cards c ON c.id = pl.card_id JOIN sets s ON s.set_id = c.set_id
           WHERE pl.purchase_id = ? ORDER BY c.set_id, c.sort_number, c.number""",
        (purchase["id"],),
    )]
    fixed = sum(r["cost_each"] * r["quantity"] for r in rows if r["cost_each"] is not None)
    shared = sum(r["quantity"] for r in rows if r["cost_each"] is None)
    share = max(purchase["cost"] - fixed, 0) / shared if shared else 0
    for r in rows:
        r["set_by_hand"] = r["cost_each"] is not None
        if r["cost_each"] is None:
            r["cost_each"] = share
        r["variant_label"] = inventory.VARIANTS[r["variant"]]
    return rows


def purchase_choices(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """The purchases for a "which purchase" list (My Stash, a scan batch): one plain query, no pulls worked out.
    Spending uses purchase_summaries for the full figures."""
    return [dict(r) for r in conn.execute(
        "SELECT id, label, cost, currency, purchased_on, kind FROM purchases WHERE user_id = ? "
        "ORDER BY COALESCE(purchased_on, created_at) DESC, id DESC", (user_id,))]


def purchase_summaries(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    out = []
    for row in conn.execute(
        "SELECT p.*, s.name AS set_name, u.username AS created_by_name FROM purchases p "
        "LEFT JOIN sets s ON s.set_id = p.set_id LEFT JOIN users u ON u.id = p.created_by "
        "WHERE p.user_id = ? ORDER BY COALESCE(p.purchased_on, p.created_at) DESC, p.id DESC",
        (user_id,),
    ).fetchall():
        p = dict(row)
        ps = pulls(conn, p)
        p["cards"] = sum(r["quantity"] for r in ps)
        p["value"] = sum((r["price"] or 0) * r["quantity"] for r in ps)
        p["cost_usd"] = fx.to_usd(p["cost"], p["currency"])
        out.append(p)
    return out


def set_paid(conn: sqlite3.Connection, user_id: int, card_id: str, variant: str, condition: str,
             price: float | None, currency: str | None = None) -> None:
    """The price paid per copy for single cards bought outright. A price on a
    line with no 'where you got it' marks it as bought as a single."""
    old = conn.execute("SELECT acquired_price, acquired_currency FROM inventory WHERE user_id = ? AND card_id = ? "
                       "AND variant = ? AND condition = ?", (user_id, card_id, variant, condition)).fetchone()
    cur = _currency(old["acquired_price"] if old else None, old["acquired_currency"] if old else None, price, currency)
    conn.execute(
        "UPDATE inventory SET acquired_price = ?, acquired_currency = ?, origin = CASE WHEN ? IS NOT NULL AND origin IS NULL "
        "THEN 'single' ELSE origin END WHERE user_id = ? AND card_id = ? AND variant = ? AND condition = ?",
        (None if price is None else round(price, 2), cur, price, user_id, card_id, variant, condition),
    )
    conn.commit()


def pull_costs(conn: sqlite3.Connection, user_id: int) -> dict[tuple, tuple[float, int]]:
    """(card_id, variant, condition) -> (total cost in dollars, quantity) of cards pulled from purchases."""
    out: dict[tuple, tuple[float, int]] = {}
    for p in conn.execute("SELECT * FROM purchases WHERE user_id = ?", (user_id,)).fetchall():
        for r in pulls(conn, dict(p)):
            key = (r["card_id"], r["variant"], r["condition"])
            cost, qty = out.get(key, (0.0, 0))
            out[key] = (cost + fx.to_usd(r["cost_each"] * r["quantity"], p["currency"]), qty + r["quantity"])
    return out


def pulled_quantities(conn: sqlite3.Connection, user_id: int) -> dict[tuple, int]:
    """(card_id, variant, condition) -> how many copies came from logged purchases."""
    return {(r[0], r[1], r[2]): r[3] for r in conn.execute(
        "SELECT pl.card_id, pl.variant, pl.condition, SUM(pl.quantity) FROM pulls pl JOIN purchases p ON p.id = pl.purchase_id "
        "WHERE p.user_id = ? GROUP BY pl.card_id, pl.variant, pl.condition", (user_id,))}


def paid_lines(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Prices paid typed on cards, as spending lines. Copies that are pulls of a logged
    purchase are left out (the purchase already counts them), so nothing counts twice.
    A line is dated by its acquired date, else when the card was first added."""
    pulled = pulled_quantities(conn, user_id)
    out = []
    for r in conn.execute(
            """SELECT i.card_id, i.variant, i.condition, i.quantity, i.acquired_price, i.acquired_date,
                      i.acquired_currency AS currency,
                      c.name, c.number, c.image_url, c.twin_name, c.set_id, s.name AS set_name, s.logo_url,
                      (SELECT MIN(l.at) FROM inventory_log l WHERE l.collection_id = i.user_id AND l.card_id = i.card_id
                         AND l.variant = i.variant AND l.condition = i.condition AND l.delta > 0) AS added_at
               FROM inventory i JOIN cards c ON c.id = i.card_id JOIN sets s ON s.set_id = c.set_id
               WHERE i.user_id = ? AND i.acquired_price IS NOT NULL""", (user_id,)):
        d = dict(r)
        d["copies"] = d["quantity"] - pulled.get((d["card_id"], d["variant"], d["condition"]), 0)
        if d["copies"] <= 0:
            continue
        d["amount"] = round(d["acquired_price"] * d["copies"], 2)  # as typed, in d["currency"]
        d["amount_usd"] = fx.to_usd(d["amount"], d["currency"])
        d["date"] = d["acquired_date"] or d["added_at"]
        d["variant_label"] = inventory.VARIANTS.get(d["variant"], d["variant"])
        out.append(d)
    return out


def before_tracking(conn: sqlite3.Connection, user_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM purchases WHERE user_id = ? AND kind = ? ORDER BY id LIMIT 1", (user_id, BEFORE)).fetchone()
    return dict(row) if row else None


def set_before_tracking(conn: sqlite3.Connection, user_id: int, cost: float | None, on: str | None, by: int | None) -> None:
    """The one "Spent before we started tracking" line; no amount removes it."""
    current = before_tracking(conn, user_id)
    if cost is None or cost <= 0:
        if current:
            delete_purchase(conn, user_id, current["id"])
        return
    if current:
        conn.execute("UPDATE purchases SET cost = ?, purchased_on = ?, currency = ? WHERE id = ?",
                     (round(cost, 2), on or None, _currency(current["cost"], current["currency"], cost), current["id"]))
        conn.commit()
    else:
        create_purchase(conn, user_id, "Spent before we started tracking", cost, purchased_on=on, by=by, kind=BEFORE)


def _purchase_date(p: dict) -> str | None:
    return p.get("purchased_on") or p.get("created_at")


def totals(conn: sqlite3.Connection, user_id: int, today=None) -> dict:
    """Spent in all (purchases plus prices paid on cards, never counting a pull twice),
    and this month and this year in the app's time zone (without the before-tracking line)."""
    today = today or display.today()
    month, year = f"{today.year:04d}-{today.month:02d}", f"{today.year:04d}"
    purchases = [dict(p, cost=fx.to_usd(p["cost"], p["currency"]))
                 for p in conn.execute("SELECT * FROM purchases WHERE user_id = ?", (user_id,))]
    packs = sum(p["cost"] for p in purchases)
    # "Before we started tracking" counts in the total only, never in a month or year.
    entries = [(p["cost"], _purchase_date(p)) for p in purchases if p["kind"] != BEFORE]
    lines = paid_lines(conn, user_id)
    entries += [(l["amount_usd"], l["date"]) for l in lines]
    singles = round(sum(l["amount_usd"] for l in lines), 2)
    keys = [(cost, display.month_key(date)) for cost, date in entries]
    return {"packs": round(packs, 2), "singles": singles, "total": round(packs + singles, 2),
            "month": round(sum(c for c, k in keys if k == month), 2),
            "year": round(sum(c for c, k in keys if k.startswith(year)), 2)}


def by_month(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Spending grouped by month, newest first: [{key, label, total, items}]. Each item is a
    logged purchase or a card's paid line. The "before tracking" line isn't in here."""
    items = []
    for p in purchase_summaries(conn, user_id):
        if p["kind"] != BEFORE:
            items.append({**p, "type": "purchase", "date": _purchase_date(p), "amount": p["cost"], "amount_usd": p["cost_usd"]})
    for l in paid_lines(conn, user_id):
        items.append({**l, "type": "card"})
    months: dict[str, dict] = {}
    for item in items:
        key = display.month_key(item["date"]) or "0000-00"
        m = months.setdefault(key, {"key": key, "label": display.month_label(key) if key != "0000-00" else "No date",
                                    "total": 0.0, "items": []})
        m["items"].append(item)
        m["total"] += item["amount_usd"] or 0  # in dollars, shown in the chosen currency
    for m in months.values():
        m["items"].sort(key=lambda i: str(i["date"] or ""), reverse=True)
        m["total"] = round(m["total"], 2)
    return [months[k] for k in sorted(months, reverse=True)]

def _line_quantities(conn: sqlite3.Connection, user_id: int, lines: list[tuple[str, str, str]]) -> list[tuple]:
    """(card_id, variant, condition, quantity) for the lines you still have."""
    out = []
    for card_id, variant, condition in lines:
        row = conn.execute("SELECT quantity FROM inventory WHERE user_id = ? AND card_id = ? AND variant = ? AND condition = ?",
                           (user_id, card_id, variant, condition)).fetchone()
        if row:
            out.append((card_id, variant, condition, row["quantity"]))
    return out


def set_paid_many(conn: sqlite3.Connection, user_id: int, lines: list[tuple[str, str, str]], amount: float | None,
                  split: bool = False, currency: str | None = None) -> int:
    """The price paid for several lines at once: amount per copy, or with split
    one total shared evenly across every copy (rounded to the cent). None clears
    it. Returns how many copies changed."""
    found = _line_quantities(conn, user_id, lines)
    copies = sum(q for *_, q in found)
    if not copies:
        return 0
    each = None if amount is None else round(amount / copies, 2) if split else round(amount, 2)
    for card_id, variant, condition, _ in found:
        conn.execute(
            "UPDATE inventory SET acquired_price = ?, acquired_currency = ?, origin = CASE WHEN ? IS NOT NULL "
            "AND origin IS NULL THEN 'single' ELSE origin END WHERE user_id = ? AND card_id = ? AND variant = ? AND condition = ?",
            (each, currency or fx.setting(), each, user_id, card_id, variant, condition))
    conn.commit()
    return copies


def add_lines(conn: sqlite3.Connection, user_id: int, purchase_id: int, lines: list[tuple[str, str, str]]) -> int:
    """Put cards you already have into a spending entry, so their cost is its
    share of the entry. Nothing is added to the collection. A copy is in one
    entry at a time, so it leaves any other entry, and a price paid set on the
    card itself is cleared. Returns how many copies were added."""
    if get_purchase(conn, user_id, purchase_id) is None:
        raise ValueError("Unknown spending entry.")
    found = _line_quantities(conn, user_id, lines)
    for card_id, variant, condition, quantity in found:
        conn.execute("DELETE FROM pulls WHERE card_id = ? AND variant = ? AND condition = ? AND purchase_id IN "
                     "(SELECT id FROM purchases WHERE user_id = ?)", (card_id, variant, condition, user_id))
        conn.execute("INSERT INTO pulls (purchase_id, card_id, variant, condition, quantity) VALUES (?, ?, ?, ?, ?)",
                     (purchase_id, card_id, variant, condition, quantity))
        conn.execute("UPDATE inventory SET acquired_price = NULL WHERE user_id = ? AND card_id = ? AND variant = ? "
                     "AND condition = ?", (user_id, card_id, variant, condition))
    conn.commit()
    return sum(q for *_, q in found)


def record_pull(conn: sqlite3.Connection, purchase_id: int, card_id: str, variant: str, condition: str,
                quantity: int, cost_each: float | None = None, currency: str | None = None) -> None:
    """Cards already added (Review your batch) that came from this purchase: they become its
    pulls without being added again. A paid price typed on the row becomes the pull's own cost
    (in the purchase's currency)."""
    bought_in = (conn.execute("SELECT currency FROM purchases WHERE id = ?", (purchase_id,)).fetchone() or ["USD"])[0]
    if cost_each is not None and currency and currency != bought_in:
        converted = fx.convert(cost_each, currency, bought_in)
        cost_each = cost_each if converted is None else converted
    row = conn.execute("SELECT id, quantity, cost_each FROM pulls WHERE purchase_id = ? AND card_id = ? AND variant = ? "
                       "AND condition = ?", (purchase_id, card_id, variant, condition)).fetchone()
    if row:
        conn.execute("UPDATE pulls SET quantity = ?, cost_each = COALESCE(?, cost_each) WHERE id = ?",
                     (row["quantity"] + quantity, None if cost_each is None else round(cost_each, 2), row["id"]))
    else:
        conn.execute("INSERT INTO pulls (purchase_id, card_id, variant, condition, quantity, cost_each) VALUES (?, ?, ?, ?, ?, ?)",
                     (purchase_id, card_id, variant, condition, quantity, None if cost_each is None else round(cost_each, 2)))
    conn.commit()
