# 95: Find with every language, one tile per art

**Issue:** [#95](https://github.com/mags-ob/dexSTASH/issues/95). The issue decides; this file explains.

**Builds on #116** (Match arts by the illustration), which gives every card an art. #95 owns Find's whole language
change (moved here from #116 on 20 Nov 2026). Built after #116 merges.

## Context

- Find searches one language at a time, picked with the English, Japanese and Chinese buttons under the search
  box, or in the Filters panel's Language group (which also has Korean). The language sticks between searches.
- `app/routes/search.py` loops over `tcgdex.expand(lang)` and answers from the local card catalog (`index_cards`)
  once a language is complete.
- #116 gives every indexed card an illustration fingerprint and builds arts for every Pokémon in
  `catalog.card_arts`. `app/arts.py` has `lookup(conn, card_ids)` (art id per card, followed arts with hand fixes
  first) and `members(conn, card_id)` (every card in that card's art). It also gives a card's rarity group, so a
  rainbow or gold version can be labelled.
- Enhanced Search (Data sources; downloads the picture model) is off on a new install. Without it there are no
  illustration fingerprints, so `card_arts` is empty.
- The card page's Languages chips show today only for a card in an art of a followed Pokémon or artist.

## Design (maintainer, 20 Nov 2026)

- **Default: every language, by art.** With no language filter, a search covers English, Japanese and Chinese
  (`en`, `ja`, `zh-tw`, `zh-cn`) and shows one tile per art. The language buttons under the search box go.
  Korean isn't part of it.
- **Picking a language** happens only in the Filters Language group (English, Japanese, Chinese, Korean; none
  ticked is the default). A picked language shows as a chip under the search box ("Japanese ✕") and counts on the
  Filters badge; ✕ goes back to every language. It still sticks between searches. Results are the arts printed in
  that language, with that language's card as the picture. Korean searches on its own, as today.
- **The grouping.** One `arts.lookup` call for the result ids. Cards of one art make one tile. A card with no art
  yet (most Trainers and Energy) is a tile of its own.
- **Without Enhanced Search** (no picture matching), the same view runs on a rough rule: followed arts first, then
  same Pokédex number and same illustrator (`chase.artist_norm`) from `index_cards`. Mainland Chinese cards have no
  illustrator, so they stay their own tiles there. Maintainer OK'd this for this iteration.
- **Tiles.** Cover: English when there is one, else Japanese, else Chinese (or the picked language). Reuse `tile`
  with EN/JA/ZH tags (filled when you have that one), ✓ when you have
  any card of it, and the cover's price, set code and number. A results line: "6 arts · 13 cards in English,
  Japanese and Chinese". A tap opens the cover card, as a result does today.
- **No art sheet on Find.** An art's other languages show on the card view instead: the Languages chips, looking
  exactly as today, now on any card page or preview whose art is printed in more than one language, followed
  Pokémon or not (`arts.members`, or the rough rule without Enhanced Search). A tap opens that language's card.
- **Unchanged:** the Pokémon box with sprites, Pokémon set › and ★, Tags, "Don't know the name? Describe it",
  saved searches, other filters and sorts, "Not here? Add it by hand", Find from Scan and Import (they carry their
  language as the filter).
- **Speed.** All local: the three catalogs and one art lookup. A language whose catalog isn't complete yet is left
  out and named ("Japanese is still loading"), and its first fetch starts in the background as today. No TCGdex
  calls during a search.

Set aside: the 18 Nov trial chip, the art sheet on Find.

## Files

- `app/routes/search.py`: no language means every language; picked language filters arts; chip and badge for any
  picked language; drop `lang_url`; results line.
- `app/search.py`: `art_tiles(conn, results, lang)` from `arts.lookup`, with the rough rule when there are no
  fingerprints.
- `app/templates/search.html`: drop the language buttons row; art tiles.
- `app/chase_pages.py` (`art_languages`) and `app/routes/cards.py` / preview: Languages chips from
  `arts.members` for any card.
- `app/static/css/search.css`: drop `.find-chips`; tile tags.
- `lang_chips` stays in `_macros.html`; Scan still uses it.
- `CHANGELOG.md` under Unreleased.

## Tests

In `tests/` next to the Find and card page tests, with a fake catalog and fake arts (no network). Numbers are the
issue's acceptance criteria.

- No language buttons on Find; the Filters Language group has none ticked by default (1).
- One search covers English, Japanese and both Chinese; an English name finds Japanese and Chinese cards (2).
- Cards of one art make one tile with their tags and count; a followed art's fix wins; a card with no art is its
  own tile; no card count on the tile (3, 4).
- Without fingerprints, same Pokédex number and illustrator make one tile; a mainland card stays alone (5).
- Picking Japanese shows the chip and badge, only arts printed in Japanese, with the Japanese cover; ✕ goes back to
  every language; Korean searches on its own (6).
- A card page and a preview show Languages for an unfollowed Pokémon's art in two languages; one language shows
  none (7).
- An incomplete language is left out and named (8).

## Docs impact

- `docs/specs/sets-and-search.md`: Find items 1 (every language by art, language in Filters), 13 (Languages on any
  card in an art), 21 (Scan and Import carry their language).
- `docs/concepts.md`: "art" comes from #116; nothing more.
- README, product.md: none.

## UI/UX

Phone width (375px), real pages with demo data and drawn placeholders (no real card art or sprites), on branch
`feature/search-by-art` in `docs/specs/wireframes/search-by-art/`:

- Now: `now-find.png`, `now-filters.png`, `now-card.png`.
- Proposed: `proposed-find.png` (default, every language by art), `proposed-find-japanese.png` (Japanese picked),
  `proposed-filters.png` (Language group, none ticked), `proposed-card.png` (Languages on the card view).

## Branch

`feature/search-by-art`, rebased on #116 once it merges; a draft pull request after the go.
