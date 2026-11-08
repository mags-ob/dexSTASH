"""Start over with an empty app: every card, chase, spending entry, note, import,
scan and login is erased. Run it by hand with the app stopped:

    docker compose stop app
    docker compose run --rm app python -m app.reset          # says what it would do
    docker compose run --rm app python -m app.reset --yes    # does it
    docker compose up -d

The database is copied into data/backups first ("Before starting over"), so it
can be restored from Account > Backups. Downloads that don't depend on your
cards are kept, so nothing big is fetched again: Pokémon sprites and their colours,
the downloaded card data and prices (the catalog), the Enhanced Search model and the
English and Japanese fingerprints, and the Pokémon name list.
"""

import shutil
import sqlite3
import sys
from pathlib import Path

from . import backup, db

DATA = db.DB_PATH.parent

# Erased: the database and files that belong to its cards and scans.
ERASE = [
    ("scans", "scan photos"),
    ("card-photos", "pictures of cards added by hand, and saved Chinese card pictures"),
    ("datasets", "Chinese card data files"),
    ("card-images", "pictures kept by older versions"),
    ("index/zh-cn.npy", "Enhanced Search fingerprints of Chinese cards (they include cards added by hand)"),
    ("index/zh-cn.json", None),
]
# Kept: downloads that are the same for everyone.
KEEP = [
    ("backups", "backups, including the one made now"),
    ("sprites", "Pokémon sprites"),
    (db.catalog_path(db.DB_PATH).name, "card data and prices downloaded from TCGdex, TCGCSV and CardTrader, and the Pokémon colours"),
    ("models", "the Enhanced Search model"),
    ("index", "Enhanced Search fingerprints of English and Japanese cards"),
    ("pokemon_species_names.csv", "the Pokémon name list"),
]


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.is_dir() else 0


def plan(data: Path = DATA) -> list[str]:
    lines = [f"Erase the database ({db.DB_PATH.name}): every card, chase, spending entry, note, import, scan, "
             "login and setting."]
    for rel, what in ERASE:
        path = data / rel
        if what and path.exists():
            lines.append(f"Erase {rel} ({what}, {_size(path) / 1048576:.1f} MB).")
    for rel, what in KEEP:
        if (data / rel).exists():
            lines.append(f"Keep {rel}: {what}.")
    return lines


def run(data: Path = DATA, db_path: Path = db.DB_PATH) -> Path | None:
    """Back up the database, then erase it and the files that belong to it. Returns the backup."""
    saved = None
    if db_path.exists():
        conn = sqlite3.connect(db_path)
        try:
            saved = backup.make(conn, data / "backups", "reset")
        finally:
            conn.close()
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    for rel, _ in ERASE:
        path = data / rel
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    return saved


def main(argv: list[str]) -> int:
    print("Starting over erases everything in the app and keeps the big downloads.\n")
    for line in plan():
        print(" -", line)
    if "--yes" not in argv:
        print("\nNothing was changed. Stop the app, then run this again with --yes to do it.")
        return 0
    saved = run()
    print(f"\nDone. The old database is saved as backups/{saved.name}." if saved else "\nDone. There was no database.")
    print("Start the app (docker compose up -d) and open it: it asks you to create the admin login again.\n"
          "Then, as that admin, on Data sources: press Download sprites (quick: it re-reads the files here),\n"
          "load the Chinese sets again if you want them, and then turn Enhanced Search on again\n"
          "(the model and English and Japanese fingerprints are kept; Chinese ones are rebuilt).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
