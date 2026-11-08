"""Data sources' "Download Pokémon descriptions": each Pokémon's "Origin" section from
Bulbapedia, which says what it's based on (Lugia: a sea bird, a plesiosaur…). Describe reads looks-like
words from it.

Bulbapedia's text is CC BY-NC-SA: kept for personal use in data/bulbapedia/origin.json, credited on Data
sources, never put in the app's code or a repository. It's fetched politely: one page a second, a clear
User-Agent (sites.USER_AGENT), and it picks up where it stopped. If the site can't be reached, it says so and
Describe keeps working on name roots alone. Off in Data sources: nothing is fetched."""

import json
import re
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from . import sites

API = "https://bulbapedia.bulbagarden.net/w/api.php"
CREDIT = "Pokémon descriptions from Bulbapedia (bulbapedia.bulbagarden.net), CC BY-NC-SA 2.5, for personal use."
PAUSE = 1.0
SAVE_EVERY = 20


def title(name: str) -> str:
    """Bulbapedia's page for a species: 'Farfetch'd (Pokémon)', 'Nidoran♀ (Pokémon)'."""
    return f"{name.replace('’', chr(39))} (Pokémon)"


def _get(url: str) -> dict:
    r = sites.get(url, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.json()


def origin_section(wikitext: str) -> str:
    """The plain text of the ===Origin=== section (not its Name origin part)."""
    m = re.search(r"^===\s*Origin\s*===\s*$(.*?)(?=^==)", wikitext or "", re.S | re.M)
    return plain(m.group(1)) if m else ""


def plain(text: str) -> str:
    """Wiki markup to plain sentences: templates out, links to their words, quotes and tags gone."""
    text = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", text, flags=re.S)
    for _ in range(4):  # nested templates, inside out
        text = re.sub(r"\{\{[^{}]*\}\}", lambda m: _template_words(m.group(0)), text)
    text = re.sub(r"\[\[(?:File|Image):[^\]]*\]\]", "", text)
    text = re.sub(r"\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", text)
    text = re.sub(r"\[https?://\S+ ([^\]]*)\]", r"\1", text)
    text = re.sub(r"'{2,}|<[^>]+>", "", text)
    return " ".join(text.split())


def _template_words(t: str) -> str:
    """{{p|Lugia}} -> Lugia; other templates go."""
    parts = t[2:-2].split("|")
    if parts[0].strip().lower() in ("p", "pkmn", "m", "t", "a", "i", "wp", "tt", "rt", "type"):
        return parts[-1] if len(parts) > 1 else ""
    return ""


class Descriptions:
    def __init__(self, folder: Path, get=None, pause: float | None = None):
        self.path = folder / "origin.json"
        self.get = get or _get
        self.pause = PAUSE if pause is None else pause

    def load(self) -> dict[int, str]:
        try:
            return {int(k): v for k, v in json.loads(self.path.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError):
            return {}

    def _save(self, found: dict[int, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        part = self.path.with_suffix(".part")
        part.write_text(json.dumps({str(k): v for k, v in sorted(found.items())}, ensure_ascii=False), encoding="utf-8")
        part.replace(self.path)

    def download(self, species: dict[int, str], job=None) -> str:
        """Fetch every species not here yet (species: dex -> English name). Returns a summary."""
        if not sites.on("bulbapedia"):
            return "Bulbapedia is off in Data sources, so no descriptions were downloaded."
        progress = job.progress if job is not None else (lambda *a, **k: None)
        found = self.load()
        todo = [(d, n) for d, n in sorted(species.items()) if d not in found]
        failures, unreachable = 0, None
        for i, (dex, name) in enumerate(todo):
            progress(i, len(todo), name)
            try:
                data = self.get(f"{API}?" + urlencode({"action": "parse", "page": title(name), "prop": "wikitext",
                                                       "format": "json", "formatversion": "2", "redirects": "1"}))
                found[dex] = origin_section((data.get("parse") or {}).get("wikitext") or "")
            except httpx.TransportError as e:
                unreachable = e
                break
            except Exception:
                failures += 1
                if failures >= 10 and failures > i // 2:  # mostly failing: stop rather than hammer the site
                    unreachable = "too many pages failed"
                    break
            if (i + 1) % SAVE_EVERY == 0:
                self._save(found)
            time.sleep(self.pause)
        self._save(found)
        have = sum(1 for t in found.values() if t)
        if unreachable is not None:
            return (f"Bulbapedia couldn't be reached ({unreachable}). Kept {have} descriptions; "
                    "press the button again later to carry on. Describe works without them.")
        return f"Pokémon descriptions are ready ({have} of {len(species)} have an Origin section)."
