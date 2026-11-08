"""Home's fun fact of the day: one of the English Pokédex entries of the Pokémon
on the Home tile. Facts take turns, one a day, so a login that keeps one Pokémon
sees each of its entries in turn, and "New every day" gets the day's Pokémon's.

The entries come from PokéAPI's flavour text file (pokeapi.py), already
downloaded for Find's "Don't know the name?". Nothing new is downloaded. Reading
the file takes about a second, so it's read once, in the background: until then
Home shows no fact."""

import csv
import os
import re
import threading
from datetime import date
from pathlib import Path

from . import pokeapi

NICE = 15
_facts: dict[int, list[str]] | None = None
_loading = False
_lock = threading.Lock()


def _clean(text: str) -> str:
    return " ".join(text.replace("\xad ", "").replace("\xad", "").replace("\f", " ").split())


def _capitals(text: str) -> int:
    return sum(c.isupper() for c in text)  # POKéMON, EEVEE: the older games' capitals


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold().replace("é", "e"))


def read(folder: Path | None = None) -> dict[int, list[str]]:
    """{species: [entries]}, English only. Entries that differ only in capitals or spacing count once,
    keeping the one written in normal case (Eevee, not EEVEE)."""
    found: dict[int, dict[str, str]] = {}
    with open(pokeapi.path("pokemon_species_flavor_text", folder), encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["language_id"] != "9":
                continue
            text = _clean(r["flavor_text"])
            if not text:
                continue
            seen = found.setdefault(int(r["species_id"]), {})
            k = _key(text)
            if k not in seen or _capitals(text) < _capitals(seen[k]):
                seen[k] = text
    return {dex: list(texts.values()) for dex, texts in found.items()}


def _load(folder: Path | None) -> None:
    global _facts, _loading
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), NICE)  # this thread only
    except (OSError, AttributeError):
        pass
    try:
        _facts = read(folder)
    except Exception:
        _facts = None  # A broken file: try again on a later visit
    finally:
        _loading = False


def for_day(species_id: int, day: date, folder: Path | None = None) -> str | None:
    """The day's fact about a Pokémon, or None when there's none (yet). Never waits for the file."""
    global _loading
    if _facts is None:
        if not pokeapi.have("pokemon_species_flavor_text", folder):
            return None
        with _lock:
            if _facts is None and not _loading:
                _loading = True
                threading.Thread(target=_load, args=(folder,), name="facts", daemon=True).start()
        return None
    texts = _facts.get(species_id)
    return texts[day.toordinal() % len(texts)] if texts else None


def forget() -> None:
    """Read the file again next time (after Data sources fetches PokéAPI's files again, and in tests)."""
    global _facts
    _facts = None
