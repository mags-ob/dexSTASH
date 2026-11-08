"""The app's time zone, set in Data sources and kept in app_meta.

On first start the zone comes from .env (DISPLAY_TZ, else TZ, else UTC). An install that already has accounts
keeps what it showed before: DISPLAY_TZ, else Eastern. After that the setting is what counts."""

import os
from zoneinfo import available_timezones

from . import display

ZONE_KEY = "time_zone"
OLD_DEFAULT = "America/New_York"  # what every install showed before time zone was a setting


def _meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO app_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))
    conn.commit()


def zone_names() -> list[str]:
    """Standard zone names for the list, like Europe/London (no posix/ or right/ copies)."""
    return sorted(n for n in available_timezones()
                  if "/" in n and not n.startswith(("posix/", "right/", "Etc/", "SystemV/", "US/"))) + ["UTC"]


def load(conn) -> str:
    """Use the saved zone, saving the starting one first if there's none yet."""
    name = _meta(conn, ZONE_KEY)
    if name is None:
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            shown = (os.environ.get("DISPLAY_TZ") or "").strip()
            name = shown if display._zone_or_none(shown) else OLD_DEFAULT
        else:
            name = display.env_zone() or "UTC"
        _set_meta(conn, ZONE_KEY, name)
    try:
        display.set_zone(name)
    except ValueError:  # a zone this server doesn't know (tzdata missing): keep the one from .env
        pass
    return name


def save_zone(conn, name: str) -> None:
    """Raises ValueError for a name that isn't a time zone."""
    name = (name or "").strip()
    display.set_zone(name)
    _set_meta(conn, ZONE_KEY, name)


def zone_note() -> str | None:
    """Data sources says when .env names another zone, since .env only counts on the first start."""
    env = display.env_zone()
    current = display.zone().key
    if env and display._zone_or_none(env).key != current:
        return f"Your .env says {env}. That was only used on the first start; this setting is what counts."
    return None
