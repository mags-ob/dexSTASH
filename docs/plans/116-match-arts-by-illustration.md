# 116: Match arts by the illustration, for every card

**Issue:** [#116](https://github.com/mags-ob/dexSTASH/issues/116). The issue decides; this file explains.

## Context

- Arts today (`app/chase.py`, `group()`): two cards of a followed Pokémon or artist are one art when they have the
  same illustrator and their whole-card fingerprints (CLIP ViT-B/32, `app/pictures.py`) score at least 0.92. Hand
  fixes (`art_fixes`) win. Arts exist only for followed Pokémon and artists.
- The whole-card fingerprint takes in the frame, text box and language, so the same art in English and Japanese
  scores below the bar. Mainland Chinese cards have no illustrator, so they never group.
- Enhanced Search keeps one whole-card fingerprint per card for every set in English, Japanese and mainland Chinese
  (`data/index/<lang>.npy` + `.json`). Pictures aren't kept. Arts reuse those fingerprints; the rest (Taiwan
  Chinese) go in a per-Pokémon index (`data/index/pokemon/<dex>`).
- Taiwan Chinese cards are the same TCGdex cards as the Japanese ones (same set id and number,
  `pictures.BATCH_INDEXES`).
- The app already sorts rarities into groups (`display.rarity_groups`): "secret" covers the gold rarities above the
  set number (Hyper, Secret, Rainbow Rare…).

## The test (19 Nov 2026)

A throwaway script fetched Larvitar (39 cards) and Tyranitar (55) from TCGdex in English, Japanese and Taiwan
Chinese, fingerprinted each picture whole and cropped to the illustration (left 8%, top 10%, right 92%, bottom 50%),
and compared every pair. The maintainer judged the pairs the two ways disagreed on.

| | Larvitar: cards grouped with another | Tyranitar |
| --- | --- | --- |
| Today (whole card, same artist, 0.92) | 6 | 13 |
| Illustration, 0.88 | 22 | 23 |

- Same art across languages: whole card 0.72-0.91, illustration 0.93-0.99.
- Judged wrong (8): all 4 links only today's rule made (whole 0.91-0.94, illustration 0.66-0.84); 2 pairs with
  different artists (illustration 0.89-0.93); 2 same-artist pairs at 0.92-0.923.
- Judged same art, rainbow version (2): illustration 0.881 and 0.928. The maintainer: combine them in the art, but
  they're not the same collect.
- So: the illustration alone, the same artist, and a 0.93 bar keep none of the wrong pairs and all the
  cross-language ones. The whole-card score is not used for arts.

## Design

1. **One store, two fingerprints.** Each language index gets a second array, `<lang>.art.npy`, row for row with
   `<lang>.npy`. A row whose illustration isn't made yet holds zeros (score 0, never matches). `Index.put` takes
   both; `Index.art_vectors` is read under the same lock. Photo rows (`hand:`, `scan:`) never get one.
2. **Making them.** `index_sets` crops and fingerprints the illustration from the same opened picture as the whole
   card (one download, two `embed` calls). A new background step, "Fingerprinting illustrations", fills the rows
   made before this change: one picture at a time through `sites.client()`, newest sets first, after
   `index_sets` and before regrouping. It's part of Check for updates and runs once after upgrading; it stops for a
   restart and picks up where it left off (rows still at zero).
3. **Taiwan Chinese** cards take their Japanese twin's fingerprints (same TCGdex set and number). A Taiwan card with
   no twin keeps today's per-Pokémon fetch.
4. **The rule** (`chase.group`): same artist and illustration ≥ 0.93 (`ART_THRESHOLD`). Artist unknown on either
   card: ≥ 0.95. Different artists: never. Rainbow and gold: when either card is in the "secret" rarity group and
   the artists match, ≥ 0.85 (`RECOLOUR_THRESHOLD`). Hand fixes win, as now. Thresholds are constants, tuned on
   the test above.
5. **Arts when a Pokémon is followed.** Every indexed card already has both fingerprints, so following a Pokémon or
   artist builds its arts straight away from the store, with no picture downloads (Taiwan cards with no Japanese
   twin are the only fetches). Followed Pokémon and artists keep their own `arts`/`art_cards` (with your fixes).
   Updated 20 Nov 2026: no arts table for unfollowed Pokémon, since nothing shows them now that the card page's
   Languages chips go (item 6). #95 and #29 can add one later from the same fingerprints.
6. **Card page Languages removed** (maintainer, 20 Nov 2026). The Languages section goes from the card page and the
   Find preview (`chase_pages.art_languages`, `card.html`, `routes/cards.py`, `routes/search.py`). The same art in
   other languages stays together on the Pokémon's page. A card with no picture still borrows the same art's
   picture in another language (sets and search item 15).
7. **Rainbow label.** In the Edit art sheet, a card in the "secret" rarity group shows its rarity label (Rainbow
   Rare, Hyper Rare…) in gold. It's its own card with its own printings and ticks, as every card is today.
8. **Enhanced Search** keeps reading only the whole-card fingerprint (#115 may use both later).
9. **Without Enhanced Search** (model not downloaded), nothing changes from today: followed Pokémon use their own
   fingerprints and today's rule.

Set aside: a perceptual hash (not needed after the test), a bigger model (heavy for a home server), release date as
a rule (reprints reuse old art years later; it stays only as the cover tie-break), suggested merges in Edit arts
(separate issue if wanted).

## Files

- `app/pictures.py`: `crop_art`, second array in `Index` (load, put, keep_only, save, drop), illustrations in
  `index_sets`, new `fill_illustrations(conn, encoder, job, fetch)`, counts for Data sources.
- `app/chase.py`: thresholds, `group()` rule (illustration vectors, secret-rarity bar), `fingerprints()` returns
  illustration vectors, Taiwan twins, `regroup` unchanged otherwise.
- `app/chase_pages.py`: `art_languages` removed; sheet rows carry the rarity label.
- `app/routes/cards.py`, `app/routes/search.py`, `app/templates/card.html`: Languages section removed.
- `app/jobs.py` / updates: run `fill_illustrations` after `index_sets`, then regroup followed Pokémon.
- `app/templates/pokemon_set.html` (sheet), `data_sources.html` (illustrations row), CSS for the gold label.
- `app/upgrades.py`: regroup followed Pokémon once illustrations exist.
- Database: no change expected (fingerprints are files). If a numbered step turns out to be needed, the PR says so
  at the top.

## Tests

- AC1: `Index` keeps both arrays row for row through put, keep_only, save and load; zeros for missing.
- AC2: a Taiwan card gets its Japanese twin's vectors; one with no twin falls back.
- AC3/AC4: `group()` with fixed vectors: 0.93 same artist joins; 0.92 doesn't; different artists at 0.99 don't;
  unknown artist needs 0.95; a secret-rarity card at 0.86 joins, a plain one doesn't.
- AC5: a fix still wins over the new rule.
- AC6: following a Pokémon whose cards are all in the store fetches no pictures; the fill runs through the queue
  (fake fetch counts one request at a time); the card page and Find preview have no Languages section.
- AC7: the 8 wrong and 2 rainbow pairs from the test, as fixed score fixtures, give the judged answers.

## Docs impact

- `docs/specs/chases.md` item 8 (the art rule), `docs/specs/sets-and-search.md` item 13 (Languages removed),
  `docs/specs/scanning.md` (the illustration fingerprint, Enhanced Search unchanged), Data sources text.
- `docs/concepts.md`: "art" = the same illustration, by the same artist; a rainbow or gold version is the same art
  and its own card.
- README, product.md: none.
