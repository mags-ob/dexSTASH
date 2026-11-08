"""PokéAPI's data files (BSD licence), downloaded once and kept next to the
database: the species list (evolution lines, colours, shapes), types and the
English Pokédex entries. The names list (names.py) comes from the same place.
Nothing here updates by itself; Data sources can fetch them again."""

import csv
import io
import threading
from pathlib import Path

from . import db, sites

BASE_URL = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/"
FOLDER = db.DB_PATH.parent / "pokeapi"
FILES = ("pokemon_species", "pokemon_types", "types", "pokemon_colors", "pokemon_shapes", "pokemon_species_flavor_text")
SPECIES = 1025  # #001 to #1025; higher numbers are forms

_lock = threading.Lock()


def path(name: str, folder: Path | None = None) -> Path:
    return (folder or FOLDER) / f"{name}.csv"


def have(name: str, folder: Path | None = None) -> bool:
    return path(name, folder).exists()


def fetch(name: str, folder: Path | None = None, get=None, again: bool = False) -> Path:
    """The file, downloaded first if it isn't here (or again=True). Raises on a network error."""
    target = path(name, folder)
    with _lock:
        if again or not target.exists():
            get = get or (lambda url: sites.get(url, timeout=60, follow_redirects=True))
            resp = get(BASE_URL + f"{name}.csv")
            resp.raise_for_status()
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_suffix(".part")
            part.write_bytes(resp.content)
            part.replace(target)  # never a half-written file
    return target


def rows(name: str, folder: Path | None = None, get=None) -> list[dict]:
    return list(csv.DictReader(io.StringIO(fetch(name, folder, get).read_text(encoding="utf-8"))))
