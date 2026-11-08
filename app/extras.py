"""What you add to a card yourself: links to the card's page on any site, a price typed in by hand, the
price you saw on PriceCharting, your own photo.

Links are your notes. dexSTASH saves and opens them and never reads the pages. Prices come from
open sources (TCGCSV, TCGdex) and CardTrader's own API; a price you type is for one printing and counts
in that printing's average with the others (prices.resolve).

Pictures, in order: the card's own, TCGplayer's product picture (from TCGCSV's product list), pictures
saved from links before 0.15.1, the English or Japanese version (labelled), your photo.
"""

import json
import logging
import math
import re
import sqlite3
from urllib.parse import urlparse

from . import clock

log = logging.getLogger("dexstash.extras")

_TCGPLAYER_ID = re.compile(r"/product/(\d+)")


def get(conn: sqlite3.Connection, card_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM card_extras WHERE card_id = ?", (card_id,)).fetchone()
    return dict(row) if row else None


def all_extras(conn: sqlite3.Connection) -> dict[str, dict]:
    return {r["card_id"]: dict(r) for r in conn.execute("SELECT * FROM card_extras")}


def _now() -> str:
    return clock.now()


def clean_link(url: str) -> tuple[str, str] | None:
    """('tcgplayer' | 'pricecharting', url) for a product link from one of the two sites, or ('other', url) for
    a page on any other site (kept, but no price is read from it)."""
    url = (url or "").strip()
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https"):
        raise ValueError("Paste a link that starts with https://.")
    if host == "tcgplayer.com" or host.endswith(".tcgplayer.com"):
        m = _TCGPLAYER_ID.search(parts.path)
        if not m:
            raise ValueError("That TCGplayer link isn't a product page. Open the card on TCGplayer and copy its address.")
        return "tcgplayer", f"https://www.tcgplayer.com/product/{m.group(1)}"
    if host == "pricecharting.com" or host.endswith(".pricecharting.com"):
        if not parts.path.startswith("/game/"):
            raise ValueError("That PriceCharting link isn't a card page. Open the card on PriceCharting and copy its address.")
        return "pricecharting", f"https://www.pricecharting.com{parts.path}"
    if "." not in host or " " in url:
        raise ValueError("That isn't a link. Copy the card page's address from your browser.")
    return "other", url


LINK_SITES = ("tcgplayer", "pricecharting", "other")  # card_extras.<site>_url


def other_site(url: str | None) -> str:
    """The name shown for a link to another site: its address without www."""
    host = (urlparse(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def add_link(conn: sqlite3.Connection, card_id: str, by: int | None, url: str) -> tuple[str, str | None]:
    """The one paste box. The site is worked out from the address and the link is saved
    as your note; No page is read. Returns (site, None). Raises ValueError for a link it can't use."""
    if not (url or "").strip():
        raise ValueError("Paste the card's link first.")  # an empty box never removes a link you have
    site, _ = clean_link(url)
    save(conn, card_id, by, **{f"{site}_url": url})
    return site, None


def tcgplayer_id(url: str | None) -> int | None:
    m = _TCGPLAYER_ID.search(url or "")
    return int(m.group(1)) if m else None


def tcgplayer_picture(product_id: int | None) -> str | None:
    return f"https://tcgplayer-cdn.tcgplayer.com/product/{product_id}_in_400x400.jpg" if product_id else None


KINDS = ("manual", "pricecharting")  # A typed price: your own, or the one you saw on PriceCharting


def save(conn: sqlite3.Connection, card_id: str, by: int | None, *, tcgplayer_url: str | None = None,
         pricecharting_url: str | None = None, other_url: str | None = None, manual_price: float | None | str = "keep",
         photo: str | None = None, pc_price: float | None | str = "keep", variant: str | None = None) -> None:
    """Change what you added to a card. Links are checked with clean_link; ''
    removes one. manual_price and pc_price (the price you saw on PriceCharting) are for one printing,
    variant (default_variant when not given): 'keep' leaves it as it is, None clears it. Removing the
    PriceCharting link also clears its prices."""
    current = get(conn, card_id) or {}
    values = {k: current.get(k) for k in ("tcgplayer_url", "pricecharting_url", "other_url", "photo")}
    for key, url in (("tcgplayer_url", tcgplayer_url), ("pricecharting_url", pricecharting_url), ("other_url", other_url)):
        if url is None:
            continue
        found = clean_link(url)
        if found and found[0] != key.split("_")[0]:
            raise ValueError("That link is for another site.")
        values[key] = found[1] if found else None
    if photo is not None:
        values["photo"] = photo or None
    conn.execute(
        """INSERT INTO card_extras (card_id, tcgplayer_url, pricecharting_url, other_url, photo, updated_by, updated_at)
           VALUES (:card_id, :tcgplayer_url, :pricecharting_url, :other_url, :photo, :by, :at)
           ON CONFLICT(card_id) DO UPDATE SET tcgplayer_url = excluded.tcgplayer_url,
             pricecharting_url = excluded.pricecharting_url, other_url = excluded.other_url, photo = excluded.photo,
             updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
        {**values, "card_id": card_id, "by": by, "at": _now()})
    if manual_price != "keep" or pc_price != "keep":
        variant = variant or default_variant(conn, card_id)
    if manual_price != "keep":
        set_typed(conn, card_id, variant, "manual", manual_price, by)
    if pricecharting_url == "" and current.get("pricecharting_url"):
        conn.execute("DELETE FROM typed_prices WHERE card_id = ? AND kind = 'pricecharting'", (card_id,))
    elif pc_price != "keep":
        set_typed(conn, card_id, variant, "pricecharting", pc_price, by)
    apply_price(conn, card_id)
    conn.commit()


def set_typed(conn: sqlite3.Connection, card_id: str, variant: str, kind: str, price, by: int | None) -> None:
    """Keep a typed price for one printing, in the currency it was typed in; None or '' removes it.
    Doesn't commit or work the price out (save does both)."""
    from . import fx, inventory
    if kind not in KINDS or variant not in inventory.VARIANTS:
        raise ValueError("Pick a printing.")
    new = None if price in (None, "") else round(float(price), 2)
    if new is not None and not math.isfinite(new):
        raise ValueError("Type a price.")
    if new is not None and new <= 0:
        raise ValueError("Type a price above 0.")
    if new is None:
        conn.execute("DELETE FROM typed_prices WHERE card_id = ? AND variant = ? AND kind = ?", (card_id, variant, kind))
        return
    conn.execute("""INSERT INTO typed_prices (card_id, variant, kind, price, currency, typed_at, typed_by, was_read)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    ON CONFLICT(card_id, variant, kind) DO UPDATE SET price = excluded.price, currency = excluded.currency,
                      typed_at = excluded.typed_at, typed_by = excluded.typed_by, was_read = 0""",
                 (card_id, variant, kind, new, fx.setting(), _now(), by))


def typed_rows(conn: sqlite3.Connection, card_id: str) -> list[dict]:
    """The prices typed for a card, one per printing and kind, in the usual printing order:
    {variant, kind, price, currency, typed_at, typed_by, was_read}."""
    from . import inventory
    order = {v: i for i, v in enumerate(inventory.VARIANTS)}
    rows = [dict(r) for r in conn.execute("SELECT * FROM typed_prices WHERE card_id = ?", (card_id,))]
    return sorted(rows, key=lambda r: (order.get(r["variant"], 99), KINDS.index(r["kind"]) if r["kind"] in KINDS else 9))


def typed_for(conn: sqlite3.Connection, card_id: str, kind: str) -> dict[str, dict]:
    """One kind of typed price on a card, by printing, for the card page."""
    return {r["variant"]: r for r in typed_rows(conn, card_id) if r["kind"] == kind}


def default_variant(conn: sqlite3.Connection, card_id: str) -> str:
    """The printing a typed price is for when none is picked: the one you own, else the first you own in
    the usual order, else the card's first printing."""
    from . import inventory
    owned = {r[0] for r in conn.execute("SELECT DISTINCT variant FROM inventory WHERE card_id = ?", (card_id,))}
    mine = [v for v in inventory.VARIANTS if v in owned]
    if mine:
        return mine[0]
    card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    quoted = {r[0] for r in conn.execute("SELECT variant FROM catalog.price_quotes WHERE card_id = ? AND variant <> ''",
                                         (card_id,))}
    return inventory.offered_variants(card, quoted)[0]


def reset(conn: sqlite3.Connection, card_id: str) -> None:
    """Remove everything you added to a card, typed prices included. Commits."""
    conn.execute("DELETE FROM card_extras WHERE card_id = ?", (card_id,))
    conn.execute("DELETE FROM typed_prices WHERE card_id = ?", (card_id,))
    apply_price(conn, card_id)
    conn.commit()


def apply_price(conn: sqlite3.Connection, card_id: str) -> None:
    """Give the printings TCGdex has no price for the price from what you
    added (prices.resolve), so they count in the collection's value."""
    from . import prices
    prices.resolve(conn, card_id)


def apply_all(conn: sqlite3.Connection) -> None:
    from . import prices
    prices.apply_all(conn)


# --- pictures -----------------------------------------------------------------------

def _product_id(pricing_json) -> int | None:
    try:
        pricing = json.loads(pricing_json) if isinstance(pricing_json, str) else (pricing_json or {})
    except ValueError:
        return None
    for entry in ((pricing or {}).get("tcgplayer") or {}).values():
        if isinstance(entry, dict) and entry.get("productId"):
            return int(entry["productId"])
    return None


def pictures(card, extra: dict | None, version: tuple[str, str] | None = None) -> list[tuple[str, str]]:
    """The card page's pictures, best first, as (address, label): full size straight from where they
    are (card_pictures.candidates), then the saved copy (it may be your scan). version is a picture of
    the card in another language looked up live, when the card has no twin saved yet."""
    from . import card_pictures
    out = card_pictures.candidates(card, extra)
    if version:  # where a saved twin would go: before your photo
        photo = next((i for i, (_, label) in enumerate(out) if label == "Your photo"), len(out))
        out.insert(photo, version)
    saved = card_pictures.saved_url(card)
    if saved:
        out.append((saved, card_pictures._get(card, "saved_from") or ""))
    return card_pictures._unique(out)
