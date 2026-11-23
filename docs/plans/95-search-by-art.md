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
- **No art sheet on Find.** An art's languages show on the card view instead, as language chips on every card
  page and preview, followed Pokémon or not (`arts.members`, or the rough rule without Enhanced Search). They sit
  in the top box, under the name, set and rarity and above the price; the Languages row further down goes. Each
  chip uses its language's colour (EN blue, JA red, ZH gold), filled when you have a card of that language and
  outlined when you don't, like the EN/JA/ZH tags on Find's tiles; no ✓. A tap opens that language's card. A card
  in only one language still shows its one chip, so every card view looks the same. (Maintainer, 20 Nov 2026.)
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
  `arts.members` for any card. `app/templates/card.html` (and the preview): the chips move into `cv-info`, after
  `cv-kind`; `app/static/css/card.css` centres them.
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
  its one language chip; the chips sit in the top box under the rarity, not further down; a chip is filled in its
  language colour when owned and outlined when not, with no ✓ (7).
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

## Build notes (22 Nov 2026)

- The grouping lives in `app/find_arts.py` (a plain module): `tiles` for Find, `card_languages` for the card view.
- Still loading (AC8): only Japanese and Taiwan Chinese can be left out and named, because only they are in the
  local card catalog. English and mainland Chinese are always searched. With the catalog turned off, every
  language asks TCGdex as before. Not in the plan; asked the maintainer.

## Amended 22 Nov 2026: the card catalog's details

Testing on the test copy: Find grouped nothing. TCGdex's GraphQL now fails a set when any card misses a
non-null field (rarity, category) and returns no Japanese or Taiwan sets, so `card_index` stored the plain
set lists: 0 of 42,000 English, Japanese and Taiwan cards had an artist or Pokémon, and arts (#116) could only
group mainland Chinese cards. The maintainer chose to fix it here (no rough fallback while picture matching
can work).

- `card_index.fill_details(api)`: cards kept without details (`illustrator IS NULL`) are asked of TCGdex one at
  a time (`/cards/<id>`, through the sites queue at its background pace, about 2½ hours once for her catalog).
  No artist is kept as `''` so it isn't asked again. Taiwan cards take the Japanese twin's `dex_ids`.
- `store_set` keeps those details when a set is fetched again without them; GraphQL cards get `''` for no artist.
- Runs after each language's catalog job and the nightly refresh; arts are rebuilt after (only when Enhanced
  Search has illustrations, so a rebuild never empties them).
- After-upgrade task 11 starts it on existing installs. No database step.
- Tests: `tests/test_round15d.py` (GraphQL failing: details card by card, Taiwan borrows, kept on refetch; GraphQL
  cards never asked again).

## 23 Nov 2026: order across languages

Testing: with Best match, every English art came first and the Japanese and Chinese-only arts last, because
each language's results were simply joined. With every language and no sort picked, results are now ordered by
how new their set is within its language (the same rank Newest set uses), before grouping, so an art takes the
place of its newest card. A picked language or another sort is unchanged.

## 23 Nov 2026: tighter crop and Taiwan cards (maintainer chose both)

Checks on the test copy (Ampharos, #181):
- Same art, English and mainland Sword & Shield cards scored 0.86-0.88 with the old crop (bar 0.885). Mainland
  pictures are 286x400 with a wider border; English 245x337. With (0.12, 0.12, 0.88, 0.46) the same pairs
  scored 0.97-0.99, pairs already matched stayed 0.96-0.97 and different arts by the same artist 0.81 or less.
  `pictures.ART_BOX` changes and `ART_VERSION` 2 keeps illustrations in `<lang>.art2.npy`, so every one is made
  again in the background (about 38,000 queued downloads, once); the old file goes on the first save.
- 2,665 of 5,959 Taiwan Pokémon cards had no Pokémon (no Japanese twin on TCGdex), and Taiwan cards borrowed a
  Japanese picture most Japanese cards don't have. `card_index.dex_from_names` reads the Pokémon from the
  Chinese name; Enhanced Search now also fingerprints Taiwan sets' own TCGdex pictures (`pictures.LANGS`), for
  arts only: scans keep reading the Japanese pictures for Taiwan cards (`ARTS_ONLY`).
- After-upgrade task 12 runs both. Bar and rule unchanged.
- Open: cards with no TCGdex picture at all (most Japanese; Taiwan 蒼空烈流) stay alone unless Limitless pictures
  are fingerprinted too (asked the maintainer; Limitless publishes no terms for its pictures).

## 23 Nov 2026: Check for updates in the nightly run (maintainer asked)

New sets only reached Enhanced Search and arts when someone pressed Check for updates. The nightly run now adds
these jobs, in this order (same priority, so the runner keeps the order):
1. Check for updates (`data_sources.check_updates(nightly=True)`): stored TCGdex sets, Chinese sets, the artist
   list, followed Pokémon, sprites. Skipped when the Chinese sets are loading or a check is already going.
   First, so new cards are priced the same night.
2. The price update, which also fetches Find's catalog: new sets, then each card's artist and Pokémon.
3. New sets for Enhanced Search (`web._nightly_pictures`): pictures, illustrations, then arts, after the
   catalog's details so new cards group.
4. The chores (backup, picture tidy and recheck, history roll-up). The picture recheck stays here only.
A failed step is logged; the rest still run. Arts may be grouped twice on a night with both new catalog
details and new pictures; that's a few minutes at night, kept simple.
