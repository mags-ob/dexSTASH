"""API keys you give dexSTASH for other sites (round 28): entered on Data sources › Prices, never in .env.

Each key is kept in data/api_keys.json, next to the database but not in it, so backups (copies of the database),
restores and exports never hold one. It is stored encrypted with the app's secret key (data/secret_key or
DEXSTASH_SECRET_KEY): a stream from HMAC-SHA256 in counter mode, XORed with the key, then an HMAC-SHA256 tag over
the result, so a changed or wrong-key file reads as no key rather than garbage. Only the last 4 characters are ever
shown again. A key is never logged or put in an error message, and the scanner container never reads this file.

A CardTrader token still set in .env (DEXSTASH_CARDTRADER_TOKEN, before 0.28) is moved here once at start-up
(upgrades task 9) and keeps working from .env until then.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
from pathlib import Path

from . import clock

log = logging.getLogger("dexstash.keys")

# site -> what Data sources shows. soon: a row shown greyed out, no key taken yet.
SITES = {
    "cardtrader": {"label": "CardTrader", "what": "Near Mint prices from CardTrader's marketplace, any language.",
                   "how": "cardtrader.com › Settings › API (free with an account). The token is a full key to that "
                          "account, so keep it private.", "env": "DEXSTASH_CARDTRADER_TOKEN"},
    "pricecharting": {"label": "PriceCharting", "what": "Ungraded prices from PriceCharting's official API.",
                      "how": "pricecharting.com › Subscriptions (a paid plan with API access), then your API token."},
    "ebay": {"label": "eBay sold prices", "what": "Recent sold prices.", "soon": True},
}
FILE_NAME = "api_keys.json"
_lock = threading.Lock()
folder: Path | None = None  # tests point this elsewhere


def _path() -> Path:
    from . import db
    return (folder or db.DB_PATH.parent) / FILE_NAME


def _secret() -> bytes:
    from .web import SECRET_KEY
    return SECRET_KEY.encode()


def _subkey(purpose: bytes) -> bytes:
    return hmac.new(_secret(), b"dexstash api keys v1 " + purpose, hashlib.sha256).digest()


def _stream(nonce: bytes, length: int) -> bytes:
    key, out, counter = _subkey(b"stream"), b"", 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return out[:length]


def seal(value: str) -> str:
    data = value.encode()
    nonce = secrets.token_bytes(16)
    box = bytes(a ^ b for a, b in zip(data, _stream(nonce, len(data))))
    tag = hmac.new(_subkey(b"tag"), nonce + box, hashlib.sha256).digest()
    return base64.b64encode(nonce + tag + box).decode()


def unseal(sealed: str) -> str | None:
    try:
        raw = base64.b64decode(sealed)
    except ValueError:
        return None
    nonce, tag, box = raw[:16], raw[16:48], raw[48:]
    if len(nonce) < 16 or not hmac.compare_digest(tag, hmac.new(_subkey(b"tag"), nonce + box, hashlib.sha256).digest()):
        return None  # changed, or made with another secret key
    try:
        return bytes(a ^ b for a, b in zip(box, _stream(nonce, len(box)))).decode()
    except UnicodeDecodeError:
        return None


def _read() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    tmp.replace(path)


def get(site: str) -> str | None:
    """The key for a site: the one entered on Data sources, else (CardTrader) the old .env token."""
    entry = _read().get(site) or {}
    value = unseal(entry["sealed"]) if entry.get("sealed") else None
    if value:
        return value
    env = SITES.get(site, {}).get("env")
    return (os.environ.get(env) or "").strip() or None if env else None


def save(site: str, value: str) -> None:
    value = (value or "").strip()
    if site not in SITES or SITES[site].get("soon"):
        raise ValueError("That site doesn't take a key yet.")
    if not value or len(value) > 500 or any(ch.isspace() for ch in value):
        raise ValueError("Paste the key on its own, with no spaces.")
    with _lock:
        data = _read()
        data[site] = {"sealed": seal(value), "last4": value[-4:], "added_at": clock.now()}
        _write(data)


def remove(site: str) -> None:
    with _lock:
        data = _read()
        if data.pop(site, None) is not None:
            _write(data)


def _note(site: str, **fields) -> None:
    with _lock:
        data = _read()
        if site in data:
            data[site].update(fields)
            _write(data)


def tested(site: str, ok: bool, message: str) -> None:
    _note(site, tested_at=clock.now(), test_ok=ok, test_message=message[:200])


def used(site: str) -> None:
    _note(site, used_at=clock.now())


_last_used: dict[str, float] = {}


def used_soon(site: str) -> None:
    """used(), at most once a minute (a price update makes hundreds of calls)."""
    import time
    if time.monotonic() - _last_used.get(site, -999) > 60:
        _last_used[site] = time.monotonic()
        used(site)


def info(site: str) -> dict:
    """What Data sources shows for a site's key: never the key."""
    entry = _read().get(site) or {}
    stored = bool(entry.get("sealed")) and unseal(entry["sealed"]) is not None
    env = SITES[site].get("env")
    from_env = not stored and bool(env and (os.environ.get(env) or "").strip())
    return {"site": site, **{k: v for k, v in SITES[site].items() if k != "env"}, "set": stored or from_env,
            "from_env": from_env, "unreadable": bool(entry.get("sealed")) and not stored,
            "last4": entry.get("last4") if stored else None, "added_at": entry.get("added_at"),
            "tested_at": entry.get("tested_at"), "test_ok": entry.get("test_ok"), "test_message": entry.get("test_message"),
            "used_at": entry.get("used_at")}


def rows() -> list[dict]:
    return [info(site) for site in SITES]


def move_env_tokens() -> list[str]:
    """Keys still in .env, stored here once (upgrades task 9). Returns the sites moved."""
    moved = []
    for site, about in SITES.items():
        env = about.get("env")
        value = (os.environ.get(env) or "").strip() if env else ""
        entry = _read().get(site) or {}
        if value and not (entry.get("sealed") and unseal(entry["sealed"])):
            save(site, value)
            moved.append(site)
    return moved


def test(site: str) -> tuple[bool, str]:
    """One request to the site with its key; (worked, what to show). Never shows the key."""
    key = get(site)
    if not key:
        return False, "No key entered."
    try:
        if site == "cardtrader":
            from . import cardtrader
            ok, message = cardtrader.test()
        elif site == "pricecharting":
            from . import pricecharting
            ok, message = pricecharting.test()
        else:
            return False, "This site can't be tested yet."
    except Exception as e:  # a network problem: never the key, which isn't in any URL we show
        log.info("key test for %s failed: %s", site, type(e).__name__)
        ok, message = False, "Couldn't reach the site. Try again later."
    tested(site, ok, message)
    return ok, message
