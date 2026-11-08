"""Times as the app stores them: one format everywhere, UTC with a Z, to the second ('2026-10-28T19:56:42Z').
Text in this format sorts and compares in time order, and SQLite's date() and datetime() read it. Shown times
follow the time zone setting (display.py). tests/test_round19.py fails if code writes another format."""

from datetime import datetime, timedelta, timezone

FORMAT = "%Y-%m-%dT%H:%M:%SZ"
NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"  # the same, worked out by SQLite


def stamp(when: datetime) -> str:
    """A moment as stored text; a time without a zone is taken as UTC."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime(FORMAT)


def now() -> str:
    return stamp(datetime.now(timezone.utc))


def since(delta: timedelta, start: datetime | None = None) -> str:
    """The moment delta before start (default now), to compare stored times with: 'older than a week'."""
    return stamp((start or datetime.now(timezone.utc)) - delta)


def from_unix(seconds: float) -> str:
    return stamp(datetime.fromtimestamp(seconds, timezone.utc))


def parse(text: str | None) -> datetime | None:
    """A stored time (this format, or an older one) as an aware datetime; None for empty or unreadable."""
    if not text:
        return None
    try:
        when = datetime.fromisoformat(str(text).strip().replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.fromtimestamp(float(text), timezone.utc)  # seconds since 1970, from before 0.19
        except ValueError:
            return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def to_unix(text: str | None) -> float | None:
    when = parse(text)
    return when.timestamp() if when else None
