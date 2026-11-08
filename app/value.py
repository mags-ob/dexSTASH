"""Value over time: the collection's value chart, top movers, a card's price chart, a chase's
move, and how much price history is kept.

The chart shows the cards you have today at each day's prices, so adding a card isn't counted as a rise. A
line's price on a day is its latest TCGplayer price at or before that day (TCGdex's when there is one, else the
marked price that stood in for it: your link or price, a found link, ≈ English or ≈ another printing). Before a
line's first price, its first price is used.
"""

import bisect
import sqlite3
from datetime import date, timedelta

from . import display, fx, prices
from .inventory import VARIANTS

RANGES = {"1m": 30, "3m": 91, "1y": 365, "all": None, "1w": 7, "1d": 1}  # 1w: Home's cheaper chases, 1d: its movers (no chart button)
RANGE_LABELS = {"1m": "1M", "3m": "3M", "1y": "1Y", "all": "All"}
DEFAULT_RANGE = "3m"
MIN_PRICE = 1.0       # top movers leave out cards under $1 now
MOVERS_SHOWN = 5
CHASE_DAYS = 30
SOURCE_LABELS = {"tcgplayer": "TCGplayer", "link": "PriceCharting", "manual": "Your price", "cardtrader": "CardTrader",
                 "found_tcgplayer": "TCGplayer (found)", "found_pricecharting": "PriceCharting (found)",
                 "english": "≈ English", "printing": "≈ another printing",
                 "cardmarket": "Cardmarket", "average": "Average"}
MOVER_LABELS = dict(SOURCE_LABELS, found_tcgplayer="TCGplayer link", found_pricecharting="PriceCharting")  # short, for rows


def _range(key: str) -> str:
    return key if key in RANGES else DEFAULT_RANGE


def _start(today: date, key: str, first: date | None) -> date:
    days = RANGES[_range(key)]
    start = today - timedelta(days=days) if days else (first or today)
    return max(start, first) if first else start


def _rebase(rows: list[tuple]) -> list[tuple]:
    """When a printing's TCGplayer price switches from TCGdex's to TCGCSV's, the jump isn't a move.
    The TCGdex prices before the switch are scaled so the last one meets TCGCSV's first: the line keeps its
    shape, and TCGCSV's first price is the new baseline. rows: (day, price, source, basis), oldest first."""
    tcg = [i for i, x in enumerate(rows) if x[2] == "tcgplayer"]
    switch = next((n for n, i in enumerate(tcg) if rows[i][3] == "tcgcsv"), None)
    if not switch:  # none, or TCGCSV from the start
        return rows
    before, first = rows[tcg[switch - 1]][1], rows[tcg[switch]][1]
    if not before or rows[tcg[switch - 1]][3] == "tcgcsv":
        return rows
    ratio = first / before
    out = list(rows)
    for i in tcg[:switch]:
        out[i] = (rows[i][0], round(rows[i][1] * ratio, 2), rows[i][2], rows[i][3])
    return out


class History:
    """Price history of some (card, printing) pairs, as sorted (day, price, source) lists. TCGdex's price
    wins on a day it has one; otherwise the latest marked price. A price saved in the install's currency on its day
    is used as that amount (in dollars at the current saved rate, so it shows as saved), so a past price doesn't
    move with the exchange rate."""

    def __init__(self, conn: sqlite3.Connection, pairs: set[tuple[str, str]] | None = None, card_ids=None):
        self.rows: dict[tuple[str, str], list[tuple[str, float, str]]] = {}
        code, rate = fx.local_rate()
        ids = sorted(card_ids if card_ids is not None else {c for c, _ in pairs or ()})
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in conn.execute(
                    f"""SELECT card_id, variant, source, price, date(captured_at) AS day, captured_at, basis, local,
                               local_currency FROM price_snapshots
                        WHERE card_id IN ({','.join('?' * len(chunk))}) ORDER BY captured_at""", chunk):
                key = (r["card_id"], r["variant"])
                if pairs is not None and key not in pairs:
                    continue
                price = r["price"]
                if r["local"] is not None and r["local_currency"] == code and rate:
                    price = r["local"] / rate
                self.rows.setdefault(key, []).append((r["day"], price, r["source"], r["basis"]))
        for key, rows in self.rows.items():
            self.rows[key] = [x[:3] for x in _rebase(rows)]
        self.avg = {k: [x for x in v if x[2] == prices.AVERAGE] for k, v in self.rows.items()}
        self.tcg = {k: [x for x in v if x[2] == "tcgplayer"] for k, v in self.rows.items()}
        self.other = {k: [x for x in v if x[2] not in ("tcgplayer", prices.AVERAGE)] for k, v in self.rows.items()}
        self._adays = {k: [x[0] for x in v] for k, v in self.avg.items()}
        self._days = {k: [x[0] for x in v] for k, v in self.tcg.items()}
        self._odays = {k: [x[0] for x in v] for k, v in self.other.items()}

    def first_day(self) -> date | None:
        days = [v[0][0] for v in self.rows.values() if v]
        return date.fromisoformat(min(days)) if days else None

    def first_of(self, key, sources, start: str) -> tuple[float, str, str] | None:
        """(price, source, day) to compare with: the latest price from those sources at or before start, else the
        first one after it, for a history that starts inside the range."""
        rows = [x for x in self.rows.get(key, ()) if x[2] in sources]
        if prices.AVERAGE in sources:  # The average's history first, then what came before it
            average = [x for x in rows if x[2] == prices.AVERAGE]
            older = [x for x in rows if x[2] != prices.AVERAGE]
            before = [x for x in average if x[0] <= start] or [x for x in older if x[0] <= start]
            x = before[-1] if before else (average[0] if average else (older[0] if older else None))
            return (x[1], x[2], x[0]) if x else None
        before = [x for x in rows if x[0] <= start]
        x = before[-1] if before else (rows[0] if rows else None)
        return (x[1], x[2], x[0]) if x else None

    def at(self, key, day: str, backfill: bool = True) -> tuple[float, str] | None:
        """(price, source) of a pair on a day ('YYYY-MM-DD'): its average that day; before the average
        was kept, its TCGplayer price, else the other price that stood in."""
        avg = self.avg.get(key) or []
        k = bisect.bisect_right(self._adays.get(key, []), day)
        if k:
            return avg[k - 1][1], avg[k - 1][2]
        tcg = self.tcg.get(key) or []
        i = bisect.bisect_right(self._days.get(key, []), day)
        if i:
            return tcg[i - 1][1], tcg[i - 1][2]
        other = self.other.get(key) or []
        j = bisect.bisect_right(self._odays.get(key, []), day)
        if j:
            return other[j - 1][1], other[j - 1][2]
        if backfill:
            rows = self.rows.get(key) or []
            return (rows[0][1], rows[0][2]) if rows else None
        return None


def _days(conn, start: date, today: date) -> list[str]:
    """Days to draw: each price update in the range (and change days), plus the two ends."""
    days = {r[0] for r in conn.execute("SELECT DISTINCT date(ran_at) FROM price_runs WHERE date(ran_at) BETWEEN ? AND ? "
                                       "AND COALESCE(status, 'ok') <> 'failed'",
                                       (start.isoformat(), today.isoformat()))}
    days |= {r[0] for r in conn.execute("SELECT DISTINCT date(captured_at) FROM price_snapshots WHERE date(captured_at) BETWEEN ? AND ?",
                                        (start.isoformat(), today.isoformat()))}
    days |= {start.isoformat(), today.isoformat()}
    return sorted(days)


def _lines(conn, collection_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT i.card_id, i.variant, i.condition, SUM(i.quantity) AS quantity, p.price AS now
           FROM inventory i LEFT JOIN prices p ON p.card_id = i.card_id AND p.variant = i.variant
           WHERE i.user_id = ? GROUP BY i.card_id, i.variant, i.condition""", (collection_id,))]


# The finished chart is kept and worked out again only when something it is drawn from changed:
# your cards or today's prices (the lines), price history, a price update, or the day.
_saved: dict[tuple[int, str], tuple[tuple, dict]] = {}


def _history_mark(conn) -> tuple:
    return tuple(conn.execute("""SELECT MAX(rowid), COUNT(*), TOTAL(price), MAX(captured_at), (SELECT MAX(ran_at) FROM price_runs)
                                 FROM price_snapshots""").fetchone())


def forget() -> None:
    """Drop the saved charts (after the history is thinned or cleared)."""
    _saved.clear()


def series(conn: sqlite3.Connection, collection_id: int, key: str = DEFAULT_RANGE, today: date | None = None) -> dict:
    """The value chart: {'points': [(day, value)], 'start', 'first', 'change', 'percent'}."""
    today = today or display.today()
    lines = _lines(conn, collection_id)
    mark = (today.isoformat(), tuple(tuple(ln.values()) for ln in lines), _history_mark(conn))
    saved = _saved.get((collection_id, _range(key)))
    if saved and saved[0] == mark:
        return dict(saved[1])
    data = _series(conn, lines, key, today)
    _saved[(collection_id, _range(key))] = (mark, data)
    return dict(data)


def _series(conn: sqlite3.Connection, lines: list[dict], key: str, today: date) -> dict:
    hist = History(conn, {(ln["card_id"], ln["variant"]) for ln in lines})
    first = hist.first_day()
    start = _start(today, key, first)
    points = []
    if first:
        for day in _days(conn, start, today):
            total = 0.0
            for ln in lines:
                if day == today.isoformat() and ln["now"] is not None:
                    price = ln["now"]  # today: the price shown everywhere else
                else:
                    found = hist.at((ln["card_id"], ln["variant"]), day)
                    price = found[0] if found else None
                if price is not None:
                    total += price * ln["quantity"] * prices.share(ln["condition"])
            points.append((day, round(total, 2)))
    change = round(points[-1][1] - points[0][1], 2) if len(points) > 1 else None
    percent = round(100 * change / points[0][1], 1) if change is not None and points[0][1] else None
    return {"points": points, "start": start.isoformat(), "first": first.isoformat() if first else None,
            "change": change, "percent": percent, "range": _range(key)}


def _sources(mark: str | None, basis: str | None) -> tuple[str, ...] | None:
    """The history a current price's move is read from, the same source the price comes from
    now. None for ≈ another printing, which is an estimate and isn't a move."""
    if mark in prices.APPROX:
        return None
    # The average's history, and before it was kept, the source the price came from.
    if mark in prices.TYPED:
        return (prices.AVERAGE, "link", "manual")
    if mark == prices.CARDTRADER:  # A switch to CardTrader isn't read as a move
        return (prices.AVERAGE, prices.history_source(mark))
    return (prices.AVERAGE, "tcgplayer")


def _priced(r: dict) -> dict | None:
    """A printing's price now, where its move is read from, and its label: TCGplayer, CardTrader, your
    price, a found link. None for a printing with no price."""
    if r["now"] is not None:
        sources = _sources(r["mark"], r["basis"])
        return {"now": r["now"], "key": (r["card_id"], r["variant"]), "sources": sources, "rate": None} if sources else None
    return None


def _then(hist: "History", p: dict, start: str) -> tuple[float, str, str] | None:
    found = hist.first_of(p["key"], p["sources"], start)
    if found and p["rate"]:
        found = (round(found[0] * p["rate"], 2), *found[1:])
    return found


def movers(conn: sqlite3.Connection, collection_id: int, key: str = DEFAULT_RANGE, by: str = "usd",
           of: str = "cards", today: date | None = None) -> dict:
    """Top movers: {'up': [...], 'down': [...], 'start', 'thin'}, each row with then, now, change, percent and the
    price's source. Cards under MIN_PRICE now are left out. Each printing is compared with its
    own history from the source its price comes from now (TCGplayer, CardTrader, your price, a found link);
    when that history starts inside the range, from its first price. 'thin' is the day most
    printings' history starts, when that is inside the range."""
    today = today or display.today()
    if of == "chases":
        cards = {r["card_id"]: dict(r) for r in conn.execute(
            """SELECT w.card_id, c.name, c.number, c.twin_name, c.set_id, s.name AS set_name, NULL AS variant
               FROM wants w JOIN cards c ON c.id = w.card_id JOIN sets s ON s.set_id = c.set_id
               WHERE w.user_id = ?""", (collection_id,))}
        printings = {}
        for r in conn.execute("""SELECT card_id, variant, price AS now, origin AS mark, basis FROM prices
                                 WHERE card_id IN (SELECT card_id FROM wants WHERE user_id = ?)""",
                              (collection_id,)):
            printings.setdefault(r["card_id"], []).append(dict(r))
        hist = History(conn, card_ids=set(cards))
        groups = [(c, printings.get(c["card_id"]) or [dict(c, now=None, mark=None, basis=None, variant="")])
                  for c in cards.values()]
    else:
        rows = [dict(r) for r in conn.execute(
            """SELECT DISTINCT i.card_id, i.variant, c.name, c.number, c.twin_name, c.set_id, s.name AS set_name,
                      p.price AS now, p.origin AS mark, p.basis
               FROM inventory i JOIN cards c ON c.id = i.card_id JOIN sets s ON s.set_id = c.set_id
               LEFT JOIN prices p ON p.card_id = i.card_id AND p.variant = i.variant
               WHERE i.user_id = ?""", (collection_id,))]
        hist = History(conn, {(r["card_id"], r["variant"]) for r in rows} | {(r["card_id"], "") for r in rows})
        groups = [(r, [r]) for r in rows]
    start = _start(today, key, hist.first_day()).isoformat()
    days = RANGES[_range(key)]
    range_start = (today - timedelta(days=days)).isoformat() if days else None
    out, firsts = [], []
    for card, group in groups:
        # A chase: its cheapest printing now against the cheapest then, as the Chases tab shows.
        moves = []
        for r in group:
            p = _priced(r)
            found = _then(hist, p, start) if p else None
            if found:
                moves.append((p, found))
        if not moves:
            continue
        p, found = min(moves, key=lambda m: m[0]["now"])
        now, then = p["now"], min(m[1][0] for m in moves)
        firsts.append(min(m[1][2] for m in moves))
        if now < MIN_PRICE or then == now:
            continue
        change = round(now - then, 2)
        since = found[2] if found[2] > start else None
        out.append(dict(card, now=now, then=then, change=change, percent=round(100 * change / then, 1) if then else None,
                        source=MOVER_LABELS.get(found[1], found[1]), since=since,
                        variant_label=VARIANTS.get(card.get("variant") or "", "")))
    thin = None
    if range_start and firsts:
        firsts.sort()
        middle = firsts[len(firsts) // 2]
        thin = middle if middle > range_start else None
    sort = (lambda m: m["percent"] or 0) if by == "pct" else (lambda m: m["change"])
    return {"up": sorted([m for m in out if m["change"] > 0], key=sort, reverse=True),
            "down": sorted([m for m in out if m["change"] < 0], key=sort), "start": start, "thin": thin}


def card_series(conn: sqlite3.Connection, card_id: str, variant: str, key: str = DEFAULT_RANGE,
                today: date | None = None) -> dict:
    """One printing's price over the range: {'points': [(day, price, source label)], ...}."""
    today = today or display.today()
    hist = History(conn, {(card_id, variant)})
    first = hist.first_day()
    points = []
    if first:
        start = _start(today, key, first)
        spreads = conn.execute("SELECT substr(captured_at, 1, 10), low, high FROM price_snapshots WHERE card_id = ? "
                               "AND variant = ? AND source = ? ORDER BY captured_at", (card_id, variant, prices.AVERAGE)).fetchall()
        for day in _days(conn, start, today):
            found = hist.at((card_id, variant), day, backfill=False)
            if found:
                spread = next((r for r in reversed(spreads) if r[0] <= day), None)  # the day's low and high (0.20.1)
                low, high = (spread[1], spread[2]) if spread and found[1] == prices.AVERAGE else (None, None)
                points.append((day, found[0], SOURCE_LABELS.get(found[1], found[1]), low, high))
    return {"points": points, "range": _range(key), "first": first.isoformat() if first else None}


def chase_moves(conn: sqlite3.Connection, collection_id: int, today: date | None = None) -> dict[str, float]:
    """card id -> how its cheapest printing moved over CHASE_DAYS, for the Chases tab."""
    m = movers(conn, collection_id, "1m", of="chases", today=today)
    return {r["card_id"]: r["change"] for r in m["up"] + m["down"]}


STALE_DAYS = 3  # Warn when no TCGplayer price changed this long while nightly runs went on


def freshness(conn: sqlite3.Connection, collection_id: int, today: date | None = None) -> dict:
    """When a TCGplayer price of a card you have last changed, and whether that looks stuck: no change for
    STALE_DAYS or more while price updates kept running (TCGdex's prices went stale for a few days in Oct 2026)."""
    today = today or display.today()
    last = conn.execute("""SELECT MAX(date(captured_at)) FROM price_snapshots WHERE source = 'tcgplayer'
                           AND card_id IN (SELECT card_id FROM inventory WHERE user_id = ?)""", (collection_id,)).fetchone()[0]
    if not last:
        return {"last": None, "stale": False}
    runs = conn.execute("SELECT COUNT(DISTINCT date(ran_at)) FROM price_runs WHERE date(ran_at) > ? "
                        "AND COALESCE(status, 'ok') <> 'failed'", (last,)).fetchone()[0]
    stale = (today - date.fromisoformat(last)).days >= STALE_DAYS and runs >= 2
    return {"last": last, "stale": stale, "runs": runs}


CHEAPER_PERCENT = 10  # Home's "Chases that got cheaper", down this much or more in a week


def cheaper_chases(conn: sqlite3.Connection, collection_id: int, today: date | None = None, limit: int = 8) -> list[dict]:
    """Single chases whose cheapest printing fell CHEAPER_PERCENT% or more in the last 7 days, biggest drop first."""
    m = movers(conn, collection_id, "1w", by="pct", of="chases", today=today)
    return [r for r in m["down"] if r["percent"] is not None and r["percent"] <= -CHEAPER_PERCENT][:limit]


# --- how much price history is kept ---------------------------------
# Every price change is kept for the daily period; after it, each week becomes one row holding the week's
# average daily price, and after the weekly period each month becomes one row with the month's average. Monthly
# rows older than the monthly period are deleted (0 = keep all). Settings are in app_meta, one for the install.

DAILY_CHOICES = {30: "30 days", 60: "60 days", 90: "90 days", 182: "6 months", 365: "12 months"}
WEEKLY_CHOICES = DAILY_CHOICES
MONTHLY_CHOICES = {182: "6 months", 365: "12 months", 730: "2 years", 1826: "5 years", 0: "Keep all"}
KEEP_DEFAULTS = {"daily": 30, "weekly": 365, "monthly": 0}
_CHOICES = {"daily": DAILY_CHOICES, "weekly": WEEKLY_CHOICES, "monthly": MONTHLY_CHOICES}


def keep_settings(conn: sqlite3.Connection) -> dict[str, int]:
    out = dict(KEEP_DEFAULTS)
    for r in conn.execute("SELECT key, value FROM app_meta WHERE key IN ('history_daily', 'history_weekly', 'history_monthly')"):
        part = r["key"].split("_", 1)[1]
        try:
            if int(r["value"]) in _CHOICES[part]:
                out[part] = int(r["value"])
        except ValueError:
            pass
    return out


def _span(keep: dict, part: str) -> float:
    return float("inf") if part == "monthly" and not keep[part] else keep[part]


def shorter(old: dict, new: dict) -> bool:
    """Whether new keeps less of some kind than old, so saving it thins out history now."""
    return any(_span(new, p) < _span(old, p) for p in KEEP_DEFAULTS)


def save_keep(conn: sqlite3.Connection, daily: int, weekly: int, monthly: int) -> dict:
    """Save the settings and thin the history to them. Raises ValueError for a choice not on the list."""
    new = {"daily": daily, "weekly": weekly, "monthly": monthly}
    for part, value in new.items():
        if value not in _CHOICES[part]:
            raise ValueError(part)
    for part, value in new.items():
        conn.execute("INSERT INTO app_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                     (f"history_{part}", str(value)))
    conn.commit()
    rollup(conn)
    return new


def history_size(conn: sqlite3.Connection) -> dict:
    """{'rows', 'bytes', 'oldest'} of the stored price history."""
    rows, oldest = conn.execute("SELECT COUNT(*), MIN(date(captured_at)) FROM price_snapshots").fetchone()
    try:
        size = conn.execute("SELECT TOTAL(pgsize) FROM dbstat WHERE name IN ('price_snapshots', 'prices_by_card')").fetchone()[0]
    except sqlite3.Error:  # SQLite built without the dbstat table: about 70 bytes a row with its index
        size = rows * 70
    return {"rows": rows, "bytes": int(size), "oldest": oldest}


def clear_history(conn: sqlite3.Connection) -> int:
    """Delete all price history; today's prices stay, and history starts again with the next update."""
    n = conn.execute("DELETE FROM price_snapshots").rowcount
    conn.commit()
    forget()
    return n


def _month(d: date) -> tuple[date, date]:
    start = d.replace(day=1)
    following = (start + timedelta(days=32)).replace(day=1)
    return start, following - timedelta(days=1)


def _week(d: date) -> tuple[date, date]:
    """Monday to Sunday, cut at the month's ends so a week never spans two months."""
    start, month = d - timedelta(days=d.weekday()), _month(d)
    return max(start, month[0]), min(start + timedelta(days=6), month[1])


def _thin(rows: list[tuple], daily_cut: date, weekly_cut: date) -> list[tuple]:
    """rows: (captured_at, price, basis), or with (low, low_source, high, high_source, samples, local, local_currency)
    after them, oldest first, a row only when the price changed. A month that ended before weekly_cut becomes one
    row, and a week that ended before daily_cut one row, at the first day it holds: the average of its daily prices
    (each day's latest price, carried over from an earlier row), the lowest low and highest high (with their
    sources), samples = the days it holds, and the average in the install's currency when every day has one in the
    same currency. Where the basis changes inside one (TCGdex to TCGCSV), each part is averaged on its own, so the
    switch isn't blended."""
    days = [date.fromisoformat(r[0][:10]) for r in rows]
    wide = bool(rows) and len(rows[0]) > 3
    out, i = [], 0
    while i < len(rows):
        d = days[i]
        span = _month(d) if _month(d)[1] < weekly_cut else _week(d) if _week(d)[1] < daily_cut else None
        if span is None:
            out.append(rows[i])
            i += 1
            continue
        j = i
        while j < len(rows) and span[0] <= days[j] <= span[1]:
            j += 1
        # each day of the span: the latest row at or before it
        daily, k, prev = [], i, rows[i - 1] if i else None
        day = span[0]
        while day <= span[1]:
            while k < j and days[k] <= day:
                prev, k = rows[k], k + 1
            if prev is not None:
                daily.append((day, prev))
            day += timedelta(days=1)
        parts: list[list] = []
        for day, row in daily:
            if not parts or parts[-1][2] != row[2]:
                parts.append([day, [], row[2]])
            parts[-1][1].append(row)
        for start, held, basis in parts:
            price = round(sum(r[1] for r in held) / len(held), 2)
            at = f"{start.isoformat()}T00:00:00Z"
            if not wide:
                out.append((at, price, basis))
                continue
            low = min(held, key=lambda r: r[3] if r[3] is not None else r[1])
            high = max(held, key=lambda r: r[5] if r[5] is not None else r[1])
            currencies = {r[9] for r in held}
            local = round(sum(r[8] for r in held) / len(held), 2) \
                if len(currencies) == 1 and None not in currencies and all(r[8] is not None for r in held) else None
            out.append((at, price, basis, low[3] if low[3] is not None else low[1], low[4],
                        high[5] if high[5] is not None else high[1], high[6], len(held),
                        local, next(iter(currencies)) if local is not None else None))
        i = j
    return out


SNAPSHOT_COLUMNS = "captured_at, price, basis, low, low_source, high, high_source, samples, local, local_currency"


def rollup(conn: sqlite3.Connection, today: date | None = None) -> int:
    """Thin price history to the settings above, keeping each point's low, high, sample count and amount in the
    install's currency (0.20.1). Returns rows removed. Commits."""
    today = today or display.today()
    keep = keep_settings(conn)
    daily_cut = today - timedelta(days=keep["daily"])
    weekly_cut = daily_cut - timedelta(days=keep["weekly"])
    before = conn.execute("SELECT COUNT(*) FROM price_snapshots").fetchone()[0]
    keys = conn.execute("SELECT DISTINCT card_id, variant, source FROM price_snapshots WHERE captured_at < ?",
                        (daily_cut.isoformat(),)).fetchall()
    for card_id, variant, source in keys:
        rows = [tuple(r) for r in conn.execute(
            f"""SELECT {SNAPSHOT_COLUMNS} FROM price_snapshots WHERE card_id = ? AND variant = ? AND source = ?
                ORDER BY captured_at""", (card_id, variant, source))]
        thin = _thin(rows, daily_cut, weekly_cut)
        if thin != rows:
            conn.execute("DELETE FROM price_snapshots WHERE card_id = ? AND variant = ? AND source = ?", (card_id, variant, source))
            conn.executemany(f"INSERT INTO price_snapshots (card_id, variant, source, {SNAPSHOT_COLUMNS}) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             [(card_id, variant, source, *row) for row in thin])
    if keep["monthly"]:
        conn.execute("DELETE FROM price_snapshots WHERE captured_at < ?",
                     ((weekly_cut - timedelta(days=keep["monthly"])).isoformat(),))
    conn.commit()
    forget()
    return before - conn.execute("SELECT COUNT(*) FROM price_snapshots").fetchone()[0]


# --- drawing ----------------------------------------------------------------------------

def svg_path(points: list[tuple], width: int = 640, height: int = 200, pad: int = 6) -> dict:
    """Polyline points for a chart drawn on the server, plus the min and max for the labels."""
    if not points:
        return {"line": "", "area": "", "lo": None, "hi": None, "dots": [], "band": ""}
    values = [p[1] for p in points]
    bands = [(p[3], p[4]) for p in points if len(p) > 4 and p[3] is not None and p[4] is not None]
    lo, hi = min(values + [b[0] for b in bands]), max(values + [b[1] for b in bands])
    span = (hi - lo) or max(hi * 0.1, 1)
    first, last = date.fromisoformat(points[0][0]), date.fromisoformat(points[-1][0])
    days = max((last - first).days, 1)
    coords = []
    for p in points:
        x = pad + (width - 2 * pad) * ((date.fromisoformat(p[0]) - first).days / days)
        y = pad + (height - 2 * pad) * (1 - (p[1] - lo) / span) if hi != lo else height / 2
        coords.append((round(x, 1), round(y, 1)))
    if len(coords) == 1:
        coords = [(pad, coords[0][1]), (width - pad, coords[0][1])]
    line = " ".join(f"{x},{y}" for x, y in coords)
    area = f"{coords[0][0]},{height} {line} {coords[-1][0]},{height}"
    dots = [{"x": x, "y": y, "day": p[0], "value": p[1], "source": p[2] if len(p) > 2 else None,
             "low": p[3] if len(p) > 4 else None, "high": p[4] if len(p) > 4 else None}
            for (x, y), p in zip(coords if len(points) > 1 else coords[:1], points)]
    band = ""
    if len(bands) == len(points) > 1 and any(b[0] != b[1] for b in bands):  # each day's low to high, behind the line
        def y_of(v):
            return round(pad + (height - 2 * pad) * (1 - (v - lo) / span), 1) if hi != lo else height / 2
        top = [f"{x},{y_of(b[1])}" for (x, _), b in zip(coords, bands)]
        bottom = [f"{x},{y_of(b[0])}" for (x, _), b in zip(coords, bands)]
        band = " ".join(top + bottom[::-1])
    return {"line": line, "area": area, "lo": lo, "hi": hi, "dots": dots, "width": width, "height": height, "band": band}
