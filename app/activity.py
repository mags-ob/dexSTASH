"""The activity log: both containers also write their log to data/logs/ (app.log,
scanner.log), rotating at 2 MB with 2 older files kept, so it never grows past about 12 MB. Data sources ›
Activity log reads the last hours of both. Passwords, codes, cookies, keys and anything that looks like a
token are blanked before a line is written."""

import logging
import re
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import clock, db

FOLDER = db.DB_PATH.parent / "logs"
MAX_BYTES = 2 * 1024 * 1024
BACKUPS = 2  # app.log, app.log.1, app.log.2
SOURCES = ("app", "scanner")
HOURS = (1, 6, 24)
PROBLEMS = ("WARNING", "ERROR", "CRITICAL")
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_STAMP = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)Z (\w+) (.*)$")

# What is never written: name=value pairs that are secrets, cookies, and long token-like strings.
_SECRET_PAIRS = re.compile(r"(?i)\b(password|passwd|pass|code|token|secret|key|session|cookie|api_key|apikey|authorization)"
                           r"(\s*[=:]\s*|\"\s*:\s*\")([^\s&\"',;]+)")
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+")
_TOKENISH = re.compile(r"\b(?=[A-Za-z0-9_]*\d)(?=[A-Za-z0-9_]*[A-Za-z])[A-Za-z0-9_]{32,}\b")


def scrub(text: str) -> str:
    """Blank anything that looks like a secret."""
    text = _SECRET_PAIRS.sub(lambda m: f"{m.group(1)}{m.group(2)}•••", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} •••", text)
    return _TOKENISH.sub("•••", text)


class _Scrub(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        record.msg, record.args = scrub(message), None
        if record.exc_text:
            record.exc_text = scrub(record.exc_text)
        return True


class _UTC(logging.Formatter):
    def formatTime(self, record, datefmt=None):  # noqa: N802 (logging's name)
        return clock.from_unix(record.created)


def setup(source: str, folder: Path | None = None) -> logging.Handler | None:
    """Write this container's log (the dexstash loggers, and page errors) to data/logs/<source>.log."""
    folder = folder or FOLDER
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(folder / f"{source}.log", maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
    except OSError as e:
        logging.getLogger("dexstash").warning("activity log not written: %s", e)
        return None
    handler.setFormatter(_UTC(_FORMAT))
    handler.addFilter(_Scrub())
    handler.setLevel(logging.INFO)
    for name in ("dexstash", "uvicorn.error"):
        logger = logging.getLogger(name)
        if not any(getattr(h, "baseFilename", None) == handler.baseFilename for h in logger.handlers):
            logger.addHandler(handler)
    logging.getLogger("dexstash").setLevel(logging.INFO)
    return handler


def _files(source: str, folder: Path) -> list[Path]:
    """Oldest first."""
    return [p for p in (folder / f"{source}.log.{i}" for i in range(BACKUPS, 0, -1)) if p.exists()] + \
        ([folder / f"{source}.log"] if (folder / f"{source}.log").exists() else [])


def read(hours: int = 1, problems: bool = False, folder: Path | None = None, now: datetime | None = None) -> list[dict]:
    """The last hours of both containers, newest first: {at, source, level, text}. A traceback stays
    with its line."""
    folder = folder or FOLDER
    since = clock.since(timedelta(hours=hours), now)
    out = []
    for source in SOURCES:
        entry = None
        for path in _files(source, folder):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                m = _STAMP.match(line)
                if m:
                    entry = {"at": m.group(1), "source": source, "level": m.group(2), "text": m.group(3)} \
                        if m.group(1) >= since else None
                    if entry:
                        out.append(entry)
                elif entry is not None and line.strip():
                    entry["text"] += "\n" + line
    if problems:
        out = [e for e in out if e["level"] in PROBLEMS]
    order = {id(e): i for i, e in enumerate(out)}  # same second: the order written
    out.sort(key=lambda e: (e["at"], order[id(e)]), reverse=True)
    return out


def as_text(entries: list[dict]) -> str:
    """Plain text for Copy last hour, oldest first, ready to paste into a chat."""
    return "\n".join(f"{e['at']}Z {e['source']} {e['level']} {e['text']}" for e in reversed(entries))


def problems_count(hours: int = 1, folder: Path | None = None) -> int:
    return len(read(hours, problems=True, folder=folder))
