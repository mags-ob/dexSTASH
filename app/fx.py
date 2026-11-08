"""Euro to dollar: the European Central Bank's daily reference rate, fetched at most once a
day with the nightly price update and kept in app_meta, and by day in fx_rates (0.20.1, with past days read once
from the ECB's history file), so history can be shown in another currency at each day's rate. Used for
prices in other currencies, CardTrader's and the ones you type.

Every currency in the same file is kept, for the currency setting. Prices are stored in
US dollars and shown in the chosen currency at today's rate: dollars -> X = (X per euro) / (dollars per euro).
With no rate for the chosen currency yet, prices show in dollars."""

import json
import logging
import re
import sqlite3
from datetime import date, timedelta
from urllib.parse import urlparse

from . import clock

log = logging.getLogger("dexstash.fx")
URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
HISTORY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"  # the last 90 days, same format
FULL_HISTORY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"  # every day since 1999 (large)
HOSTS = {"www.ecb.europa.eu"}
EVERY = timedelta(hours=20)


def fetch(url: str = URL) -> str:
    from . import sites
    parts = urlparse(url)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in HOSTS:
        raise ValueError(f"Not the ECB: {url}")
    with sites.client(timeout=20, follow_redirects=False) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text


def parse(xml: str) -> float | None:
    """US dollars per euro from the ECB's daily file."""
    return parse_all(xml).get("USD")


def parse_all(xml: str) -> dict[str, float]:
    """Each currency per euro from the ECB's daily file, like {'USD': 1.08, 'JPY': 162.3}."""
    out = {}
    for code, value in re.findall(r"currency=['\"]([A-Z]{3})['\"]\s+rate=['\"]([0-9.]+)['\"]", xml or ""):
        try:
            if float(value) > 0:
                out[code] = float(value)
        except ValueError:
            pass
    return out


def parse_days(xml: str) -> dict[str, dict[str, float]]:
    """Each day's rates from an ECB file: {'2026-10-28': {'USD': 1.08, ...}}. The daily file has one day."""
    out: dict[str, dict[str, float]] = {}
    for day, body in re.findall(r"<Cube\s+time=['\"](\d{4}-\d{2}-\d{2})['\"]\s*>(.*?)</Cube>\s*(?=<Cube\s+time|</Cube>)",
                                xml or "", re.S):
        rates = parse_all(body)
        if rates:
            out[day] = rates
    return out


def save_days(conn: sqlite3.Connection, days: dict[str, dict[str, float]]) -> int:
    """Keep each day's rates in fx_rates (EUR = 1.0 too); a day already kept is left as it is. Returns days added."""
    added = 0
    for day, rates in days.items():
        if conn.execute("SELECT 1 FROM fx_rates WHERE rate_date = ? LIMIT 1", (day,)).fetchone():
            continue
        conn.executemany("INSERT OR IGNORE INTO fx_rates (rate_date, currency, per_eur) VALUES (?, ?, ?)",
                         [(day, code, value) for code, value in {**rates, "EUR": 1.0}.items()])
        added += 1
    conn.commit()
    return added


def rate_on(conn: sqlite3.Connection, day: str, code: str) -> float | None:
    """code per euro on day, or the newest day before it with a rate. None before the first kept rate."""
    if code == "EUR":
        return 1.0
    row = conn.execute("SELECT per_eur FROM fx_rates WHERE currency = ? AND rate_date <= ? ORDER BY rate_date DESC LIMIT 1",
                       (code, day[:10])).fetchone()
    return row[0] if row else None


def usd_rate_on(conn: sqlite3.Connection, day: str, code: str) -> float | None:
    """code per US dollar on day (the newest kept day on or before it), for re-saving history."""
    theirs, usd = rate_on(conn, day, code), rate_on(conn, day, "USD")
    return round(theirs / usd, 6) if theirs and usd else None


HISTORY_DONE = "fx_history_done"


def backfill(conn: sqlite3.Connection, get=fetch) -> int:
    """Once: past days' rates from the ECB's history file, back to the oldest price history point (the last 90
    days' file, or the whole one when history is older). A failure is tried again at the next update. Returns days
    added."""
    if _meta(conn, HISTORY_DONE):
        return 0
    oldest = conn.execute("SELECT MIN(captured_at) FROM price_snapshots").fetchone()[0]
    url = FULL_HISTORY_URL if oldest and oldest[:10] < clock.since(timedelta(days=85))[:10] else HISTORY_URL
    try:
        days = parse_days(get(url))
    except Exception as e:
        log.warning("past exchange rates not fetched from the ECB: %s", e)
        return 0
    if oldest:  # from a week before the oldest point, so a weekend or holiday there still has a rate
        start = (date.fromisoformat(oldest[:10]) - timedelta(days=7)).isoformat()
        days = {d: r for d, r in days.items() if d >= start}
    added = save_days(conn, days)
    conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)", (HISTORY_DONE, clock.now()))
    conn.commit()
    return added


def _meta(conn, key):
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def refresh(conn: sqlite3.Connection, get=fetch, force: bool = False) -> float | None:
    """Today's rate, fetched once a day (force: now, for a currency just picked). A failure keeps the last rate."""
    at = _meta(conn, "eur_usd_at")
    if not force and at and at > clock.since(EVERY):
        return rate(conn)
    try:
        xml = get(URL)
        rates = parse_all(xml)
    except Exception as e:
        log.warning("euro rate not fetched from the ECB: %s", e)
        return rate(conn)
    value = rates.get("USD")
    if value:
        save_days(conn, parse_days(xml))  # 0.20.1: the day's rates kept by date too
        now = clock.now()
        for key, v in (("eur_usd", str(value)), ("eur_usd_at", now), (RATES_KEY, json.dumps(rates, sort_keys=True))):
            conn.execute("INSERT INTO app_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, v))
        conn.commit()
        load(conn)
    return value or rate(conn)


def rate(conn: sqlite3.Connection) -> float | None:
    value = _meta(conn, "eur_usd")
    try:
        return float(value) if value else None
    except ValueError:
        return None


def usd(conn: sqlite3.Connection, eur: float | None) -> float | None:
    r = rate(conn)
    return round(eur * r, 2) if eur is not None and r else None


# --- The currency prices show in -------------------------------------------------------------
CURRENCY_KEY, RATES_KEY = "currency", "ecb_rates"
# The currencies in the ECB's daily file: name, symbol, decimals.
CURRENCIES = {
    "USD": ("US dollar", "$", 2), "EUR": ("Euro", "€", 2), "GBP": ("Pound sterling", "£", 2),
    "CAD": ("Canadian dollar", "CA$", 2), "AUD": ("Australian dollar", "A$", 2), "NZD": ("New Zealand dollar", "NZ$", 2),
    "JPY": ("Japanese yen", "¥", 0), "CNY": ("Chinese yuan", "CN¥", 2), "HKD": ("Hong Kong dollar", "HK$", 2),
    "SGD": ("Singapore dollar", "S$", 2), "KRW": ("South Korean won", "₩", 0), "INR": ("Indian rupee", "₹", 2),
    "CHF": ("Swiss franc", "CHF ", 2), "SEK": ("Swedish krona", "SEK ", 2), "NOK": ("Norwegian krone", "NOK ", 2),
    "DKK": ("Danish krone", "DKK ", 2), "PLN": ("Polish złoty", "zł ", 2), "CZK": ("Czech koruna", "CZK ", 2),
    "HUF": ("Hungarian forint", "HUF ", 0), "RON": ("Romanian leu", "RON ", 2), "ISK": ("Icelandic króna", "ISK ", 0),
    "TRY": ("Turkish lira", "₺", 2), "BRL": ("Brazilian real", "R$", 2), "MXN": ("Mexican peso", "MX$", 2),
    "ZAR": ("South African rand", "R ", 2), "ILS": ("Israeli new shekel", "₪", 2), "IDR": ("Indonesian rupiah", "Rp ", 0),
    "MYR": ("Malaysian ringgit", "RM ", 2), "PHP": ("Philippine peso", "₱", 2), "THB": ("Thai baht", "฿", 2),
}
_state = {"currency": "USD", "rates": {}}  # replaced whole, so threads never see half a change


def load(conn: sqlite3.Connection) -> None:
    """Read the setting and the last rates (at start, after a refresh and after a change)."""
    try:
        rates = json.loads(_meta(conn, RATES_KEY) or "{}")
    except ValueError:
        rates = {}
    if "USD" not in rates and rate(conn):
        rates["USD"] = rate(conn)  # from older versions: only the dollar was kept
    rates["EUR"] = 1.0
    code = _meta(conn, CURRENCY_KEY) or "USD"
    global _state
    _state = {"currency": code if code in CURRENCIES else "USD", "rates": rates}


def setting() -> str:
    """The chosen currency."""
    return _state["currency"]


def shown() -> str:
    """The currency prices show in: the chosen one, or dollars while there's no rate for it."""
    code = _state["currency"]
    return code if code == "USD" or (code in _state["rates"] and "USD" in _state["rates"]) else "USD"


def save_currency(conn: sqlite3.Connection, code: str) -> None:
    if code not in CURRENCIES:
        raise ValueError("Pick a currency from the list.")
    conn.execute("INSERT INTO app_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                 (CURRENCY_KEY, code))
    conn.commit()
    load(conn)


def rates_at(conn: sqlite3.Connection) -> str | None:
    return _meta(conn, "eur_usd_at")


def convert(amount: float | None, frm: str, to: str) -> float | None:
    """amount in frm as to, at today's rate; None when a rate is missing."""
    if amount is None:
        return None
    if frm == to:
        return amount
    rates = _state["rates"]
    if frm not in rates or to not in rates:
        return None
    return amount / rates[frm] * rates[to]


def local_rate() -> tuple[str, float]:
    """The shown currency and how many of it a dollar buys at the last saved rates (1.0 for dollars)."""
    code = shown()
    return code, round(convert(1.0, "USD", code) or 1.0, 6) if code != "USD" else 1.0


def in_usd(amount: float | None, currency: str | None) -> tuple[float | None, float | None]:
    """An amount in currency as dollars, with the rate used (dollars per unit): (None, None) when there's no rate
    for it, so a price that can't be converted isn't counted."""
    if amount is None:
        return None, None
    code = (currency or "USD").upper()
    if code == "USD":
        return round(amount, 2), 1.0
    each = convert(1.0, code, "USD")
    return (round(amount * each, 2), round(each, 6)) if each else (None, None)


def from_usd(amount: float | None) -> float | None:
    """A stored (dollar) price in the shown currency."""
    out = convert(amount, "USD", shown())
    return amount if out is None else out


def to_usd(amount: float | None, currency: str | None) -> float | None:
    """An amount typed in currency, in dollars (as typed when there's no rate for it)."""
    out = convert(amount, currency or "USD", "USD")
    return amount if out is None else out


def typed(amount: float | None, currency: str | None) -> float | None:
    """An amount typed in currency, in the shown currency (for totals)."""
    out = convert(amount, currency or "USD", shown())
    return amount if out is None else out


def symbol(code: str | None = None) -> str:
    return CURRENCIES.get(code or shown(), ("", (code or "") + " ", 2))[1]


def decimals(code: str | None = None) -> int:
    return CURRENCIES.get(code or shown(), ("", "", 2))[2]


def fmt(amount: float, code: str | None = None) -> str:
    """'$1,234.50', '¥1,235', '-€3.10'."""
    code = code or shown()
    places = decimals(code)
    text = f"{abs(amount):,.{places}f}"
    return ("-" if amount < 0 and round(abs(amount), places) else "") + symbol(code) + text
