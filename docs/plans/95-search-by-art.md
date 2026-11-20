# 95: Find with every language, one tile per art

**Issue:** [#95](https://github.com/mags-ob/dexSTASH/issues/95). The issue decides; this file explains.

**Builds on #116** (Match arts by the illustration). Updated 20 Nov 2026 to line up with it: the arts come from
#116, and the choice sits in Find's Filters Language group, which #116 makes the only place to pick a language.
#95 is built after #116 merges.

## Context

- Find searches one language at a time. After #116, the language is picked only in the Filters panel's Language
  group; any language but English shows as a chip under the search box ("Japanese ✕") and counts on the Filters
  badge.
- `app/routes/search.py` loops over `tcgdex.expand(lang)` and answers from the local card catalog (`index_cards`)
  once a language is complete.
- #116 gives every indexed card an illustration fingerprint and builds arts for every Pokémon in a rebuildable
  catalog table, `catalog.card_arts (lang, card_id, dex_id, art_id)`, with one rule: same Pokémon, same artist,
  illustration ≥ 0.93 (≥ 0.95 when an artist is unknown, never across different artists, ≥ 0.85 for a rainbow or
  gold version). Followed Pokémon and artists keep their own `arts`/`art_cards` with hand fixes, which win.
  `app/arts.py` looks up a card's art (followed arts first). Taiwan Chinese cards share their Japanese twin's
  fingerprints; mainland Chinese cards have no artist, so they need 0.95.
- Without Enhanced Search there are no illustration fingerprints, so `card_arts` is empty.
- Spec: `docs/specs/sets-and-search.md`, Find item 1.

## Design

- **The choice.** The Filters Language group gets a last option, **All languages · by art**. Picked, it works like
  any non-English language in #116: a chip under the search box ("All languages · by art ✕"), +1 on the Filters
  badge, ✕ goes back to English, and it sticks between searches like the other languages. Every other filter and
  sort works the same.
- **The search.** The existing per-language loop over `en`, `ja`, `zh-tw`, `zh-cn`, skipping any language whose
  catalog isn't complete (named in the results line). No TCGdex calls during the search.
- **The grouping.** One `arts.lookup` call for the result ids (#116), no grouping rule of #95's own. Cards of one
  art become one tile. A card with no art yet (its Pokémon's arts aren't built, most Trainers and Energy) is a
  tile of its own. A rainbow or gold card in an art counts as its own card inside the tile and carries its rarity
  label in the sheet, as in #116's Edit art sheet.
- **Tiles.** Cover: English when there is one, else Japanese, else Chinese. Reuse `tile` with the EN/JA/ZH tags
  from the Pokémon page (filled when you have one), the card count, ✓ when you have any card of the art, and the
  cover's price, set code and number.
- **The sheet.** Tapping a tile lists the art's cards: language, set and number, printings with ✓, price; a tap
  opens the card as a search result does today. A line says how the art was grouped (your art, for a followed
  Pokémon with fixes, or matched by picture).
- **Without Enhanced Search** the option isn't offered (open question 1).

Set aside: #95's own Pokédex-and-artist guess (replaced by #116's picture matching), a trial chip beside the
language buttons (#116 removes them).

### Open questions (maintainer decides)

1. Without Enhanced Search: hide the option (planned), or fall back to a rough "same Pokémon, same artist" guess?
2. Korean isn't in the grouping (#116's arts cover English, Japanese and Chinese). Leave it out of All languages?

## Files

- `app/routes/search.py`: the `all` language choice, its chip and badge (same rule as #116), the results line.
- `app/search.py`: `art_tiles(conn, results)` from `arts.lookup` (#116).
- `app/templates/search.html`: the Language option, art tiles and the sheet of an art's cards.
- `app/static/css/search.css`, `app/static/js/search.js`: styling and opening the sheet.
- `CHANGELOG.md`: "Find can search every language at once, one tile per art" under Unreleased.

## Tests

In `tests/` next to the Find tests, with a fake catalog and fake `card_arts` (no network). Numbers are the
issue's acceptance criteria.

- The Language group offers All languages · by art; picked, it shows the chip and counts on the badge, and ✕ goes
  back to English (1).
- One search covers English, Japanese and both Chinese; an English name finds Japanese and Chinese cards (2).
- Cards with one art id make one tile with their language tags; a followed art's fix wins; a card with no art is
  its own tile (3, 4).
- A rainbow card inside an art is counted on its own and labelled in the sheet (4, 6).
- An incomplete language is left out and named (7).
- The sheet lists every card of the art, with links (6).
- English, Japanese and Chinese results are unchanged (1).
- Without Enhanced Search the option isn't offered (8).

## Docs impact

- `docs/specs/sets-and-search.md`: Find item 1 gains All languages · by art.
- `docs/concepts.md`: "art" is defined by #116; nothing more.
- README, product.md: none.

## UI/UX

Wireframes at 375px, on branch `feature/search-by-art` in `docs/specs/wireframes/search-by-art/`:
`filters.png` (the Language group), `proposed.png` (results), `sheet.png` (tap a tile). #116's
`now-find-language.png` and `proposed-find-language.png` show Find before and after its language change.

## Branch

`feature/search-by-art`, rebased on #116 once it merges; a draft pull request after the go.
