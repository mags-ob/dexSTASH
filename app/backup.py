"""Copies of the database in data/backups/.

- Before an upgrade: when the app starts with different code than last time,
  the database is copied before its tables are updated.
- By hand, from the Account page.
- Before a restore, so a restore can be undone too.
- Every night after the price update, keeping the last 7.

Copies are made with SQLite's backup, so they're consistent while the app runs.
Scan photos aren't included: they're only kept until the card is added.
"""

import hashlib
import os
import re
import sqlite3
import tempfile
import contextlib
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import clock

KEEP = {"upgrade": 5, "manual": 10, "restore": 3, "reset": 3, "rename": 1, "nightly": 7}
LABELS = {"upgrade": "Before an upgrade", "manual": "Made by hand", "restore": "Before a restore", "reset": "Before starting over",
          "rename": "Before the move to dexSTASH", "nightly": "Nightly"}
_NAME = re.compile(r"^dexstash-(\d{8}-\d{6})(?:-(\d{1,3}))?-(upgrade|manual|restore|reset|rename|nightly)\.db$")
# Copies from before the app's rename carry the old name; they're renamed once so they still show.
_OLD_NAME = re.compile(r"^(?!dexstash-)[a-z]+(-\d{8}-\d{6}(?:-\d{1,3})?-(?:upgrade|manual|restore|reset|rename|nightly)\.db)$")


def adopt_old_names(folder: Path) -> int:
    """Gives copies made under the app's old name the dexstash- name (never over an existing copy)."""
    moved = 0
    if not folder.is_dir():
        return 0
    for path in sorted(folder.iterdir()):
        m = _OLD_NAME.match(path.name)
        if m and not (target := folder / f"dexstash{m.group(1)}").exists():
            path.rename(target)
            moved += 1
    return moved


def code_version() -> str:
    """A fingerprint of the code that touches the data: schema and Python files."""
    h = hashlib.sha256()
    here = Path(__file__).parent
    for path in sorted([here / "schema.sql", *here.glob("*.py")]):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def _stored_version(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute("SELECT value FROM app_meta WHERE key = 'code_version'").fetchone()
    except sqlite3.OperationalError:  # databases from before this table
        return None
    return row[0] if row else None


def _has_data(conn: sqlite3.Connection) -> bool:
    try:
        return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
    except sqlite3.OperationalError:
        return False


def before_upgrade(conn: sqlite3.Connection, folder: Path) -> Path | None:
    """Copy the database if the code changed since it last ran. Call before db.init."""
    if not _has_data(conn) or _stored_version(conn) == code_version():
        return None
    return make(conn, folder, "upgrade")


def mark_version(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO app_meta (key, value) VALUES ('code_version', ?) "
                 "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (code_version(),))
    conn.commit()


def make(conn: sqlite3.Connection, folder: Path, kind: str = "manual") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path, n = folder / f"dexstash-{stamp}-{kind}.db", 1
    while path.exists():  # more than one in the same second
        n += 1
        path = folder / f"dexstash-{stamp}-{n}-{kind}.db"
    tmp = path.with_suffix(".part")
    dest = sqlite3.connect(tmp)
    try:
        conn.commit()
        conn.backup(dest)
    finally:
        dest.close()
    tmp.replace(path)
    _prune(folder, kind)
    return path


def _prune(folder: Path, kind: str) -> None:
    mine = [b for b in list_backups(folder) if b["kind"] == kind]
    for old in mine[KEEP[kind]:]:
        (folder / old["name"]).unlink(missing_ok=True)


def list_backups(folder: Path) -> list[dict]:
    """Newest first."""
    out = []
    if folder.is_dir():
        for path in folder.glob("*.db"):
            m = _NAME.match(path.name)
            if not m:
                continue
            when = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
            out.append({"name": path.name, "kind": m.group(3), "label": LABELS[m.group(3)],
                        "at": clock.stamp(when), "size": path.stat().st_size,
                        "_order": (m.group(1), int(m.group(2) or 1))})
    return sorted(out, key=lambda b: b["_order"], reverse=True)


def find(folder: Path, name: str) -> Path | None:
    """A backup by file name; anything that isn't one of ours is refused."""
    if not _NAME.match(name):
        return None
    path = folder / name
    return path if path.is_file() else None


def check(path: Path) -> None:
    """Raise ValueError unless the file is an intact dexSTASH database."""
    try:
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("That backup is damaged.")
            src.execute("SELECT 1 FROM users LIMIT 1")
            from . import db
            if src.execute("PRAGMA user_version").fetchone()[0] > db.LATEST:  # Numbered database steps
                raise ValueError("That backup is from a newer version of the app. Install that version to restore it.")
        finally:
            src.close()
    except sqlite3.DatabaseError as e:
        raise ValueError(f"That isn't a database dexSTASH can read ({e}).") from None


def restore(conn: sqlite3.Connection, folder: Path, name: str) -> Path:
    """Replace the live database with a backup, after copying the current one.
    Returns the copy made first. Run db.init afterwards: older backups need
    their tables brought up to date."""
    path = find(folder, name)
    if path is None:
        raise ValueError("That backup isn't there any more.")
    check(path)
    safety = make(conn, folder, "restore")
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        conn.commit()
        # The web app's shared connection has a lock; a plain connection (tools, tests) needs none.
        with getattr(conn, "lock", None) or contextlib.nullcontext():
            src.backup(getattr(conn, "raw", conn))
    finally:
        src.close()
    return safety


def delete(folder: Path, name: str) -> None:
    path = find(folder, name)
    if path is not None:
        path.unlink()


def photos_zip(folder: Path) -> Path | None:
    """Every photo in the folder (card and chase photos) in one zip, in a temporary file; None if there are none.
    The photos are JPEGs already, so they're stored as they are."""
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")) \
        if folder.is_dir() else []
    if not files:
        return None
    handle, name = tempfile.mkstemp(prefix="dexstash-photos-", suffix=".zip")
    os.close(handle)
    with zipfile.ZipFile(name, "w", zipfile.ZIP_STORED) as z:
        for p in files:
            z.write(p, p.name)
    return Path(name)
