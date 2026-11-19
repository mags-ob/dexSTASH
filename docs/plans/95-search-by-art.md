# 95: Try Find with every language, one tile per art

**Issue:** [#95](https://github.com/mags-ob/dexSTASH/issues/95). The issue decides; this file explains.

## Context

- Find searches one language at a time. The `lang` chip is kept in the session.
- `app/routes/search.py` loops over `tcgdex.expand(lang)` and answers from the local card catalog (`index_cards`) once a language is complete. `index_cards` has the illustrator and Pokédex numbers for every TCGdex language.
- Arts exist only for followed Pokémon and artists (`arts`, `art_cards`), built from picture fingerprints in a background job.
- Mainland Chinese cards are stored in `cards` without an illustrator.
- Spec: `docs/specs/sets-and-search.md`, Find item 1 ("searches one language at a time").

## Design

Proof of concept only. A trial chip, so today's Find stays untouched while the grouping is judged.

- New choice `all` runs the existing per-language loop over `en`, `ja`, `zh-tw`, `zh-cn`, skipping any language whose catalog isn't complete, then hands the results to the grouping. It is not saved as the sticky language.
- `group(conn, results)` gives back art tiles:
  1. Stored arts first: one query on `art_cards` for the result ids. Picture matching and hand fixes win.
  2. Otherwise a key of Pokédex number plus `chase.artist_norm(illustrator)`.
  3. A card with no illustrator or no Pokédex number (most Trainers and Energy, mainland Chinese cards outside a followed art) is its own tile.
- Plain Python over the result list: no new tables, no database step, no pictures compared, no network.
- Tile cover: English when there is one, else Japanese, else Chinese. Tiles reuse `tile` with the language tags from the Pokémon page.

### Known limits (what the trial is meant to show)

- One illustrator who drew the same Pokémon twice (two different pictures) lands in one art. Picture matching would fix that, but it's slow and only runs for followed Pokémon today.
- Mainland Chinese cards have no illustrator, so they only group when they're in a followed art.
- Results cap at 400 per language as today, so a very common name can show more cards than one language did.

### Open questions (maintainer decides)

1. A trial chip next to the language chips, or replace the chips straight away? The plan says trial chip.
2. Is "same Pokémon and same artist" close enough for the proof, knowing it merges two different pictures by one artist?
3. Korean isn't in the chips today; leave it out of All · by art too?

## Files

- `app/routes/search.py`: the `all` choice.
- `app/search.py`, or a small new `app/art_groups.py`: `group(conn, results)`.
- `app/templates/search.html` and `_macros.html`: the All · by art chip, the results line, art tiles, and the sheet of an art's cards.
- `app/static/css/search.css`, `app/static/js/search.js`: styling and opening the sheet.
- `CHANGELOG.md`: "Find can try every language at once, grouped by art" under Unreleased.

## Tests

In `tests/` next to the Find tests, with a fake catalog (no network). Numbers are the issue's acceptance criteria.

- Grouping by stored art beats the Pokémon-and-artist rule (3).
- Same Pokémon and artist in three languages makes one tile with three tags (3, 4).
- A Trainer with no Pokédex number stays alone (3).
- An incomplete language is left out and named (7).
- English, Japanese and Chinese chips give the same results as before (1).
- The sheet lists every card of the art, with links (6).
- The choice isn't kept in the session (8).

## Docs impact

- `docs/specs/sets-and-search.md`: Find item 1 gains the All · by art trial and how it groups.
- `docs/concepts.md`: add "art" if it isn't defined (the same picture across languages; a stored art from picture matching, or same Pokémon and illustrator in Find's trial).
- README, product.md: none.

## UI/UX

Wireframes at 375px, on branch `feature/search-by-art` in `docs/specs/wireframes/search-by-art/`: `now.png`, `proposed.png` (results), `sheet.png` (tap a tile).

## Branch

`feature/search-by-art`, a draft pull request after the go.
