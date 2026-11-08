"""Settings from the environment (.env through docker-compose.yml): DEXSTASH_<NAME>."""

import logging
import os
import re
import sys

log = logging.getLogger("dexstash")

APP_NAME_DEFAULT = "dexSTASH"
# Every DEXSTASH_ setting the app or docker-compose.yml reads, for the check below.
# (tests/test_round26.py checks the code and docker-compose.yml against it.)
KNOWN = ("ADMIN_PASSWORD", "ADMIN_USER", "APP_MEMORY", "CARD_INDEX", "CARDTRADER_TOKEN", "DB", "DOMAIN", "EURO_RATE",
         "HTTPS_ONLY", "IMAGE", "NETWORK", "NETWORK_EXTERNAL", "PICTURE_CHECK", "PICTURE_MODEL_URL", "PROXY_IPS",
         "PULL_POLICY", "SCANNER", "SECRET_KEY", "TCGCSV", "TCGDEX_CACHE")
_OFF = {"0", "false", "no", "off"}
_OTHER_PREFIX = re.compile(r"^([A-Z][A-Z0-9]*)_(" + "|".join(sorted(KNOWN, key=len, reverse=True)) + r")$")


def get(name: str, default: str | None = None) -> str | None:
    """DEXSTASH_<name>, else default."""
    value = os.environ.get(f"DEXSTASH_{name}")
    return default if value is None else value


def on(name: str, default: bool = True) -> bool:
    """An on/off switch: 0, false, no or off (any case, spaces trimmed) is off; empty is the default; anything
    else (1, true, yes, on) is on."""
    value = (get(name) or "").strip().casefold()
    if not value:
        return default
    return value not in _OFF


def app_name() -> str:
    """The name shown everywhere (APP_NAME in .env); a restart picks up a change, no rebuild."""
    return (os.environ.get("APP_NAME") or "").strip()[:40] or APP_NAME_DEFAULT


def misnamed(environ=None) -> list[tuple[str, str]]:
    """Settings in .env with another prefix (an older name, or a typo) that the app doesn't read,
    with the name they need."""
    environ = os.environ if environ is None else environ
    out = []
    for key in sorted(environ):
        m = _OTHER_PREFIX.match(key)
        if m and m.group(1) != "DEXSTASH":
            out.append((key, f"DEXSTASH_{m.group(2)}"))
    return out


def check_names(environ=None) -> list[tuple[str, str]]:
    """Says clearly, in the log and in docker logs, which settings are being ignored and what to rename them."""
    environ = os.environ if environ is None else environ
    wrong = misnamed(environ)
    for key, right in wrong:
        if right in environ:
            message = f"{key} in .env is not used ({right} is what the app reads). You can delete the {key} line."
        else:
            message = f"{key} in .env is not used: dexSTASH settings start with DEXSTASH_. Rename it to {right}."
        log.error(message)
        print(f"  ERROR: {message}", file=sys.stderr, flush=True)
    return wrong


# Where photos are read. off: nowhere (one small container; the light picture work runs in the app),
# inside: a low-priority child process in the app's own container, separate: the dexstash-scanner container.
# 'threads' is the tests' mode: everything in the app's own threads, as in older versions.
SCANNER_MODES = ("off", "inside", "separate")
_OLD_SCANNER = {"external": "separate", "inline": "inside"}


def scanner_mode(env=None) -> str:
    raw = ((env if env is not None else os.environ).get("DEXSTASH_SCANNER") or "off").strip().lower()
    if raw == "threads" or raw in SCANNER_MODES:
        return raw
    if raw in _OLD_SCANNER:
        log.warning("DEXSTASH_SCANNER=%s is now called %s; both work.", raw, _OLD_SCANNER[raw])
        return _OLD_SCANNER[raw]
    log.warning("DEXSTASH_SCANNER=%s isn't off, inside or separate: scanning is off.", raw)
    return "off"
