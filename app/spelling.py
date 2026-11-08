"""Spelling help for Find: close Pokémon names (and set names) for what was typed.
Plain text matching, no AI: how alike the letters are, plus a sound-alike key
(ph→f, c→k, doubled letters once…). It only suggests; it never changes a search."""

import difflib
import re

_KEY_RULES = [("ph", "f"), ("ck", "k"), ("qu", "kw"), ("x", "ks"), ("c", "k"), ("z", "s"), ("y", "i"),
              ("ee", "i"), ("ea", "i"), ("oo", "u"), ("ou", "u"), ("ie", "i"), ("ai", "a"), ("ah", "a"), ("gh", "g")]


def _plain(text: str) -> str:
    from .display import fold  # é becomes e instead of being dropped
    return re.sub(r"[^a-z0-9]", "", fold(text).replace("♀", "f").replace("♂", "m"))


def sound_key(text: str) -> str:
    """'Pikachoo' and 'Pikachu' share a key: 'pikakhu'."""
    s = _plain(text)
    for a, b in _KEY_RULES:
        s = s.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", s)


def _score(typed: str, name: str) -> float:
    a, b = _plain(typed), _plain(name)
    letters = difflib.SequenceMatcher(None, a, b).ratio()
    sounds = difflib.SequenceMatcher(None, sound_key(typed), sound_key(name)).ratio()
    return max(letters, sounds)


def suggest(typed: str, names: list[str], n: int = 5, cutoff: float = 0.7) -> list[str]:
    """The closest names, best first. Names starting with what was typed come first
    (typing 'pika' offers Pikachu); an exact name gives nothing (it's spelt right)."""
    t = _plain(typed)
    if len(t) < 3:
        return []
    if any(_plain(x) == t for x in names):
        return []
    starts = [x for x in names if _plain(x).startswith(t)]
    scored = sorted(((_score(typed, x), x) for x in names if x not in starts), reverse=True)
    close = [x for s, x in scored if s >= cutoff]
    return (sorted(starts, key=len) + close)[:n]


def best(typed: str, names: list[str], cutoff: float = 0.75) -> str | None:
    """One name for "Did you mean …?", or None."""
    t = _plain(typed)
    if len(t) < 3 or any(_plain(x) == t for x in names):
        return None
    scored = max(((_score(typed, x), x) for x in names), default=(0, None))
    return scored[1] if scored[0] >= cutoff else None
