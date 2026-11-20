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

## Second test: mainland Chinese (20 Nov 2026)

On the test copy, mainland Chinese cards (no artist) rarely joined. A second script scored the 18 mainland Chinese
Ampharos and Metang cards against every English, Japanese and Taiwan card (Japanese and Taiwan pictures from
Limitless when TCGdex has none). Their best match with the same art: 0.947-0.985, usually the Japanese or Taiwan
printing (same layout); 0.928 once, against English only. Different arts by different artists reach 0.925, and a
mainland card's best wrong match 0.912. So the no-artist bar is 0.94: it adds the 0.947 pair and keeps every
wrong one out.

## Design

1. **One store, two fingerprints.** Each language index gets a second array, `<lang>.art.npy`, row for row with
   `<lang>.npy`. A row whose illustration isn't made yet holds zeros (score 0, never matches). `Index.put` takes
   both; `Index.art_vectors` is read under the same lock. Photo rows (`hand:`, `scan:`) never get one.
2. **Making them.** `index_sets` crops and fingerprints the illustration from the same opened picture as the whole
   card (one download, two `embed` calls). A new background step, "Fingerprinting illustrations", fills the rows
   made before this change: one picture at a time through `sites.client()`, after `index_sets` and before
   regrouping. It's part of Check for updates and runs once after upgrading (after-upgrade task 10, in the
   background, only with Enhanced Search on); it stops for a restart and picks up where it left off (rows still at
   zero).
3. **Taiwan Chinese** cards take their Japanese twin's fingerprints (same TCGdex set and number). A Taiwan card with
   no twin keeps today's per-Pokémon fetch.
4. **The rule** (`chase.group`): same artist and illustration ≥ 0.93 (`ART_THRESHOLD`), or ≥ 0.885 between
   languages (`OTHER_LANGUAGE_THRESHOLD`, from the sample test below). No group holds two artists, even through a
   card with no artist. Mainland Chinese cards read their artist from the datasets. Artist unknown on either
   card: ≥ 0.94 (was 0.95; measured 20 Nov 2026, see below). Different artists: never. Rainbow and gold: when either card is in the "secret" rarity group and
   the artists match, ≥ 0.85 (`RECOLOUR_THRESHOLD`). Hand fixes win, as now. Thresholds are constants, tuned on
   the test above.
5. **Arts when a Pokémon is followed.** Every indexed card already has both fingerprints, so following a Pokémon or
   artist builds its arts straight away from the store, with no picture downloads (Taiwan cards with no Japanese
   twin are the only fetches). A Japanese or Taiwan card TCGdex has no picture for is fingerprinted from the
   Limitless TCG picture its tile shows (added 20 Nov 2026, after testing: many Japanese cards stayed on their own).
   Followed Pokémon and artists keep their own `arts`/`art_cards` (with your fixes).
   Arts for every other Pokémon (Updated 20 Nov 2026, for #95 coming along): a rebuildable catalog table,
   `catalog.card_arts (lang, card_id, dex_id, art_id)`, built per Pokémon in the background after its
   illustrations exist, with the same rule (only cards of one Pokémon are compared, so each rebuild is small).
   Illustrator, rarity and Pokédex numbers from `catalog.index_cards`; mainland Chinese from `cards`. A followed
   Pokémon's arts (with your fixes) win over it. Find grouped by art (#95) reads it, with no TCGdex calls at search
   time.
   For #95: `arts.lookup(conn, card_ids)` gives back, in one local query, each card's art id (followed arts first,
   then `card_arts`) and its rarity group ("secret" for the rainbow label). A card with no art is left out.
   `arts.members(conn, card_id)` gives back every card id in that card's art (followed art first, else `card_arts`),
   so #95 can show the card page's Languages chips for any card.
6. **Card page Languages** stays exactly as it is (maintainer, 20 Nov 2026): chips for an art of a followed Pokémon
   or artist, now grouped by the new rule.
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
- `app/chase_pages.py`: sheet rows carry the rarity label.
- `app/catalog.sql`: `card_arts`. `app/arts.py` (new): build `card_arts` per Pokémon, look up a card's art (followed
  arts first).
- `app/jobs.py` / updates: run `fill_illustrations` after `index_sets`, then regroup followed Pokémon.
- `app/templates/pokemon_set.html` (sheet), `data_sources.html` (illustrations row), CSS for the gold label.
- `app/upgrades.py`: regroup followed Pokémon once illustrations exist.
- Database: no change expected (fingerprints are files). If a numbered step turns out to be needed, the PR says so
  at the top.

## Tests

In `tests/test_match_arts.py`.

- AC1: `Index` keeps both arrays row for row through put, keep_only, save and load; zeros for missing.
- AC2: a Taiwan card gets its Japanese twin's vectors; one with no twin falls back.
- AC3/AC4: `group()` with fixed vectors: 0.93 same artist joins; 0.92 doesn't; different artists at 0.99 don't;
  unknown artist needs 0.94; a secret-rarity card at 0.86 joins, a plain one doesn't.
- AC5: a fix still wins over the new rule.
- AC6: following a Pokémon whose cards are all in the store fetches no pictures; the fill runs through the queue
  (fake fetch counts one request at a time).
- AC7: the 8 wrong and 2 rainbow pairs from the test, as fixed score fixtures, give the judged answers.

## Docs impact

- `docs/specs/chases.md` item 8 (the art rule),
  `docs/specs/scanning.md` (the illustration fingerprint, Enhanced Search unchanged), Data sources text.
- `docs/concepts.md`: "art" = the same illustration, by the same artist; a rainbow or gold version is the same art
  and its own card.
- README, product.md: none.

Updated 20 Nov 2026: the Find language change briefly added here (from #117) moved to #95, which changes Find's
language default to all languages grouped by art.

## Sample test: 60 random cards (20 Nov 2026)

Instead of fixing Pokémon one by one, 12 random Pokémon (seed 20261120) were fingerprinted, 569 cards, and 60
cards picked at random were judged by hand with each card's art and its three closest cards not joined.

- Result with the rule then: 42 of 58 right (72%), 14 missing a card, 2 with a wrong card.
- Mainland Chinese cards do have an artist: PTCG-CHS-Datasets `illustratorName`, on 97% of its cards and all 161
  in the test. The app saved it only as a tag, so these cards used the stricter no-artist bar (8 misses).
- The same art in another language by the same artist scored 0.886-0.925 and was missed. Different arts by the
  same artist scored up to 0.882 in the test, but up to 0.923 within one language in the first test (Tyranitar).
  So the bar is 0.885 between languages and stays 0.93 within one.
- Vikavolt GX (SM1p 021) joined Vikavolt V (Darkness Ablaze 60) through a mainland Chinese card with no
  artist. Now a group never holds two artists, and the closest pairs join first.
- Replaying the answers with these changes: about 53 of 58. Still missed: Bronzong XY 61 and Pocket A2a 59
  (same language, 0.915), and two pairs at 0.83-0.84. A fresh sample checks the changes on cards they weren't
  tuned on.
