"""Find a Pokémon by describing it ("blue turtle with cannons") when you don't
know its name: Find's "Don't know the name?".

Words are matched against each Pokémon's English Pokédex entries, its genus
("Mouse Pokémon"), colour, shape and types, with SQLite's full-text search
(FTS5, porter stemming) — no AI model, nothing paid. Colour and type chips
filter. The data is PokéAPI's (pokeapi.py), downloaded once; the search index
lives in its own file next to the names list, so backups stay small and
starting over doesn't erase it."""

import csv
import re
import sqlite3
import threading
from pathlib import Path

from . import looks_like, pokeapi
from .display import fold as _fold  # Accents don't matter

COLOURS = ["black", "blue", "brown", "gray", "green", "pink", "purple", "red", "white", "yellow"]
TYPES = ["normal", "fire", "water", "grass", "electric", "ice", "fighting", "poison", "ground", "flying", "psychic",
         "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy"]
STATUSES = ["legendary", "mythical", "baby"]
# Body shapes in plain words: 'wings' also finds 'bird'.
SHAPE_WORDS = {"wings": "wings bird winged", "fish": "fish", "squiggle": "squiggle snake serpent",
               "quadruped": "quadruped four legs", "bug wings": "bug wings insect", "tentacles": "tentacles squid octopus",
               "humanoid": "humanoid person", "upright": "upright", "legs": "legs", "arms": "arms", "ball": "ball round",
               "blob": "blob", "heads": "heads", "armor": "armor"}
LIMIT = 24
INDEX_VERSION = 2  # 2: looks-like words, Bulbapedia Origin, legendary/mythical/baby, your own words
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"a", "an", "the", "with", "and", "on", "its", "it", "of", "in", "has", "is", "that", "like", "looks", "kind", "some"}


class Describe:
    def __init__(self, index_path: Path, names_csv: Path, folder: Path | None = None, get=None,
                 origins=lambda: {}, own=lambda: ({}, {})):
        """origins: dex -> Bulbapedia Origin text; own: () -> ({dex: [your words]}, {dex: [hidden words]})."""
        self.index_path, self.names_csv, self.folder, self.get = index_path, names_csv, folder, get
        self.origins, self.own = origins, own
        self._lock = threading.Lock()

    def ready(self) -> bool:
        """Built, and by this version of the app (an older index is rebuilt from the files already here)."""
        if not self.index_path.exists():
            return False
        conn = sqlite3.connect(self.index_path)
        try:
            return conn.execute("PRAGMA user_version").fetchone()[0] == INDEX_VERSION
        finally:
            conn.close()

    def build(self, again: bool = False, rebuild: bool = False) -> int:
        """Download what's missing and build the index. Returns how many Pokémon it holds.
        again downloads PokéAPI's files again; rebuild only remakes the index (new descriptions)."""
        with self._lock:
            if self.ready() and not again and not rebuild:
                return self._count()
            if again:
                for name in pokeapi.FILES:
                    pokeapi.fetch(name, self.folder, self.get, again=True)
            species = {int(r["id"]): r for r in pokeapi.rows("pokemon_species", self.folder, self.get)
                       if int(r["id"]) <= pokeapi.SPECIES}
            colours = {r["id"]: r["identifier"] for r in pokeapi.rows("pokemon_colors", self.folder, self.get)}
            shapes = {r["id"]: r["identifier"].replace("-", " ") for r in pokeapi.rows("pokemon_shapes", self.folder, self.get)}
            type_names = {r["id"]: r["identifier"] for r in pokeapi.rows("types", self.folder, self.get)}
            types: dict[int, list[str]] = {}
            for r in sorted(pokeapi.rows("pokemon_types", self.folder, self.get), key=lambda r: int(r["slot"])):
                if int(r["pokemon_id"]) in species:  # ids up to 1025 are each species' usual form
                    types.setdefault(int(r["pokemon_id"]), []).append(type_names.get(r["type_id"], ""))
            english, genus, zh, ja = {}, {}, {}, {}
            with open(self._names_file(), encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    dex, lang = int(r["pokemon_species_id"]), r["local_language_id"]
                    if dex not in species:
                        continue
                    if lang == "9":
                        english[dex] = r["name"]
                        genus[dex] = r.get("genus") or ""
                    elif lang == "12":
                        zh[dex] = r["name"]
                    elif lang in ("1", "11"):
                        ja.setdefault(dex, []).append(r["name"])
            texts: dict[int, list[str]] = {}
            for r in pokeapi.rows("pokemon_species_flavor_text", self.folder, self.get):
                if r["language_id"] != "9" or int(r["species_id"]) not in species:
                    continue
                text = " ".join(r["flavor_text"].replace("\f", " ").split())
                seen = texts.setdefault(int(r["species_id"]), [])
                if text not in seen:
                    seen.append(text)
            origins = self.origins() or {}
            own, hidden = self.own() or ({}, {})
            part = self.index_path.with_suffix(".part")
            part.unlink(missing_ok=True)
            conn = sqlite3.connect(part)
            try:
                conn.executescript(f"""
                    CREATE VIRTUAL TABLE describe USING fts5(name, genus, tags, looks, text, origin, dex UNINDEXED,
                                                             tokenize='porter');
                    CREATE TABLE info (dex INTEGER PRIMARY KEY, name TEXT, colour TEXT, types TEXT, shape TEXT,
                                       genus TEXT, status TEXT, roots TEXT, origin_words TEXT);
                    PRAGMA user_version = {INDEX_VERSION};""")
                for dex, r in species.items():
                    colour = colours.get(r["color_id"], "")
                    shape = shapes.get(r["shape_id"], "")
                    status = " ".join(s for s, flag in (("legendary", "is_legendary"), ("mythical", "is_mythical"),
                                                         ("baby", "is_baby")) if r.get(flag) == "1")
                    name = english.get(dex) or r["identifier"].title()
                    roots = looks_like.words_for(zh.get(dex, ""), tuple(ja.get(dex, [])))
                    origin = origins.get(dex) or ""
                    conn.execute("INSERT INTO info VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                 (dex, name, colour, ",".join(types.get(dex, [])), shape, genus.get(dex, ""), status,
                                  ",".join(roots), ",".join(looks_like.from_text(origin))))
                    conn.execute("INSERT INTO describe (name, genus, tags, looks, text, origin, dex) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                 (name, genus.get(dex, ""), " ".join([colour, SHAPE_WORDS.get(shape, shape), status,
                                                                      *types.get(dex, [])]),
                                  "", " ".join(texts.get(dex, [])), origin, dex))
                for dex in species:
                    self._set_looks(conn, dex, own.get(dex, []), hidden.get(dex, []))
                conn.commit()
            finally:
                conn.close()
            part.replace(self.index_path)
            return len(species)

    @staticmethod
    def _set_looks(conn, dex: int, own: list[str], hidden: list[str]) -> None:
        row = conn.execute("SELECT roots, origin_words FROM info WHERE dex = ?", (dex,)).fetchone()
        if row is None:
            return
        hide = {_fold(h) for h in hidden}
        words = [w for w in (row[0] or "").split(",") + (row[1] or "").split(",") if w and _fold(w) not in hide]
        conn.execute("UPDATE describe SET looks = ? WHERE dex = ?", (" ".join(dict.fromkeys(words + list(own))), dex))
        # Words you removed stop finding this Pokémon, whichever column they're in (colour, type, genus…).
        conn.execute("CREATE TABLE IF NOT EXISTS hidden_words (dex INTEGER, word TEXT COLLATE NOCASE)")
        conn.execute("DELETE FROM hidden_words WHERE dex = ?", (dex,))
        conn.executemany("INSERT INTO hidden_words (dex, word) VALUES (?, ?)",
                         [(dex, w) for h in hide for w in dict.fromkeys(h.split()) if w])

    def species(self) -> list[dict]:
        """[{dex, name}] for every Pokémon in the index."""
        conn = sqlite3.connect(self.index_path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute("SELECT dex, name FROM info ORDER BY dex")]
        finally:
            conn.close()

    def update_words(self, dex: int, own: list[str], hidden: list[str]) -> None:
        """Your words or hides on one Pokémon changed: Describe searches them from now on."""
        if not self.ready():
            return
        with self._lock:
            conn = sqlite3.connect(self.index_path)
            try:
                self._set_looks(conn, dex, own, hidden)
                conn.commit()
            finally:
                conn.close()

    def tags_for(self, dex: int) -> list[tuple[str, str]]:
        """A Pokémon's automatic tags as (tag, source): colour, types, shape, genus, legendary…, then the
        looks-like words from its names and Bulbapedia. Your words and hides are added by the caller."""
        if not self.ready():
            return []
        conn = sqlite3.connect(self.index_path)
        try:
            row = conn.execute("SELECT colour, types, shape, genus, status, roots, origin_words FROM info WHERE dex = ?",
                               (dex,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return []
        colour, types, shape, genus, status, roots, origin_words = row
        out = [(colour, "colour")] + [(t, "type") for t in (types or "").split(",")]
        out += [(SHAPE_WORDS.get(shape, shape).split()[-1] if shape in ("wings", "squiggle", "bug wings", "tentacles")
                 else shape, "shape")]
        out += [(g, "genus") for g in [re.sub(r"\s*Pokémon$", "", genus or "")]]
        out += [(s, "status") for s in (status or "").split()]
        out += [(w, "root") for w in (roots or "").split(",")] + [(w, "bulbapedia") for w in (origin_words or "").split(",")]
        seen, tags = set(), []
        for t, source in out:
            if t and _fold(t) not in seen:
                seen.add(_fold(t))
                tags.append((t, source))
        return tags

    def _names_file(self) -> Path:
        if not self.names_csv.exists():
            pokeapi.fetch("pokemon_species_names", self.names_csv.parent, self.get)
            fetched = pokeapi.path("pokemon_species_names", self.names_csv.parent)
            if fetched != self.names_csv:
                fetched.replace(self.names_csv)
        return self.names_csv

    def _count(self) -> int:
        conn = sqlite3.connect(self.index_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM info").fetchone()[0]
        finally:
            conn.close()

    @staticmethod
    def read(words: str = "", colour: str = "", kind: str | list[str] = "") -> dict:
        """Colour, type and legendary/mythical/baby words typed in the box narrow the list like the chips do
       ; the first colour typed narrows, any more are ordinary words. Returns the words left to rank
        and the filters, with the typed ones as chips: [(label, the words without it)]."""
        raw = (words or "").split()
        tokens = [t for t in _WORD.findall((words or "").lower()) if t not in _STOP]
        colour = colour if colour in COLOURS else ""
        kinds = [k for k in dict.fromkeys([kind] if isinstance(kind, str) else kind) if k in TYPES][:2]
        statuses, rest, chips = [], [], []
        for t in tokens:
            if t in COLOURS and not colour:
                colour = t
            elif t in TYPES and t not in kinds:
                kinds.append(t)
            elif t in STATUSES and t not in statuses:
                statuses.append(t)
            else:
                rest.append(t)
                continue
            chips.append((t.capitalize(), " ".join(w for w in raw if "".join(_WORD.findall(w.lower())) != t)))
        return {"tokens": rest, "colour": colour, "kinds": kinds, "statuses": statuses, "chips": chips}

    def search(self, words: str = "", colour: str = "", kind: str | list[str] = "", limit: int = LIMIT) -> list[dict]:
        """[{dex, name}] best first. Chips and typed colour/type/status words narrow; the other words rank,
        Pokémon matching more of them first, then by relevance (not every word has to match).
        Nothing to go on gives nothing."""
        r = self.read(words, colour, kind)
        tokens = r["tokens"]
        if not tokens and not r["colour"] and not r["kinds"] and not r["statuses"]:
            return []
        where, args = [], []
        if r["colour"]:
            where.append("i.colour = ?")
            args.append(r["colour"])
        for k in r["kinds"]:
            where.append("(',' || i.types || ',') LIKE ?")
            args.append(f"%,{k},%")
        for st in r["statuses"]:
            where.append("(' ' || i.status || ' ') LIKE ?")
            args.append(f"% {st} %")
        conn = sqlite3.connect(self.index_path)
        conn.row_factory = sqlite3.Row
        try:
            hidden: dict[int, set[str]] = {}
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'hidden_words'").fetchone():
                for d, w in conn.execute("SELECT dex, word FROM hidden_words"):
                    hidden.setdefault(d, set()).add(_fold(w))
            chips = [_fold(c) for c in [r["colour"], *r["kinds"], *r["statuses"]] if c]
            rows = []
            if tokens:
                match = " OR ".join(f'"{t}"' for t in tokens)
                sql = ("SELECT i.dex, i.name FROM describe d JOIN info i ON i.dex = d.dex WHERE describe MATCH ?"
                       + "".join(f" AND {w}" for w in where) + " ORDER BY bm25(describe, 0, 5, 3, 4, 1, 0.5)")
                rows = conn.execute(sql, [match, *args]).fetchall()
                if rows and (len(tokens) > 1 or hidden):
                    hits = {t: {x[0] for x in conn.execute("SELECT dex FROM describe WHERE describe MATCH ?", (f'"{t}"',))}
                            for t in dict.fromkeys(tokens)}
                    count = {row["dex"]: sum(row["dex"] in h and _fold(t) not in hidden.get(row["dex"], ())
                                             for t, h in hits.items()) for row in rows}
                    rank = {row["dex"]: i for i, row in enumerate(rows)}
                    rows = sorted((row for row in rows if count[row["dex"]]), key=lambda row: (-count[row["dex"]], rank[row["dex"]]))
                rows = [row for row in rows if not any(c in hidden.get(row["dex"], ()) for c in chips)][:limit]
            if not rows and where:  # filters only, or words that found nothing among them: the narrowed list
                rows = conn.execute("SELECT i.dex, i.name FROM info i WHERE " + " AND ".join(where) + " ORDER BY i.dex",
                                    args).fetchall()
                rows = [row for row in rows if not any(c in hidden.get(row["dex"], ()) for c in chips)][:limit]
            return [dict(row) for row in rows]
        finally:
            conn.close()
