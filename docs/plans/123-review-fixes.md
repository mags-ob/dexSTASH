# Fix speed, outside-site and safety issues from the app review

- **Issue:** [#123](https://github.com/mags-ob/dexSTASH/issues/123), grouped with
  [#121](https://github.com/mags-ob/dexSTASH/issues/121) (TCG Pocket cards on Pokémon pages) and
  [#58](https://github.com/mags-ob/dexSTASH/issues/58) (outdated notes). Added 25 Nov 2026:
  [#124](https://github.com/mags-ob/dexSTASH/issues/124) (screens, wording, accessibility) joins the same branch;
  its part is planned separately once its mockups have a go.
- **Branch:** `fix/review-fixes`, one pull request.
- **Context:** the whole-app review of 21 Nov 2026. The site queue is from #77 (`app/sites.py`, `jobs.pool`).
  Tile prices on scans are from #81 (trade scanning).

## Design

### Outside sites

- **E1. Find's filtered searches.** `jobs.pool` passes the page's quick-try setting (`tcgdex.page_wait`) to its
  threads, so each lookup gets the page timeout (8 s, no retry) and skips TCGdex for a minute once it has timed
  out. `search.MAX_DETAILS` drops from 400 to 96: at most 96 cards are looked up on TCGdex per search. Cards whose
  details are stored here (or in the local catalog) don't count, since they cost no request.
  When the limit cut some cards off, the end of the results offers to look further. That reruns the search with
  the next 96 (`deeper=2`, `3`…). The cards already looked up come from the saved replies, so each step sends at
  most about 96 new requests. The link is a screen change, so it is in #124's mockups.
- **E2. One way to start a background thread.** A new `jobs.thread(target, name)` starts a thread that is marked
  as background first, so its requests wait for pages and take turns per site. Every thread in `app/` that can
  reach another site uses it: the scan reader and Enhanced Search workers, tile prices, followed-picture checks,
  after-start tasks, TCGdex's set list refresh, the nightly schedule, set values and Pokédex facts. A test reads
  every `threading.Thread(` in `app/` and fails on any outside a short allow-list (the job runner, which marks
  itself; the picture model's unload timer and the scanner process watcher, which send no requests).
- **SC3. Tile prices.** Run on a `jobs.thread`, so they queue. After 3 failed lookups in a row the thread stops and
  no new lookups start for 60 seconds.
- **PR1. Plain errors.** Find, a set from Find, and a card from Find say "Couldn't reach TCGdex. Try again in a
  minute." The detail goes to the Activity log.
- **F2.** A Pokémon page treats any failed TCGdex request (403, 500…) as "couldn't reach TCGdex", not as "no
  cards".

### Scan prices

- **P8.** `TilePrices` drops entries older than `FRESH` whenever it keeps a new one.
- **T1.** `TilePrices.forget(card)` runs whenever a price is typed, a PriceCharting price is typed, or a card goes
  back to what TCGdex has. The tile and the batch or trade total use the new price at the next page load.
- **T2.** `prices.resolve` places a price for the whole card (CardTrader's) on the printing a waiting scan of that
  card shows, when nobody has or chases a printing of it. A scan with no printing picked uses the card's first
  base printing, as its tile does.

### Safety

- **M1.** "Clear the collection" and an import with "Remove … cards that aren't in the file" first make a backup
  (`…-clear.db`, `…-import.db`, shown on Account as "Before clearing" and "Before an import"). Both confirmations
  say a backup is made first. Only admins see and can use "Remove".
- **P4.** A wrong current password on Account counts towards the same lockout as a wrong sign-in, for that user and
  address.
- **P5.** More than 50 photos in one upload is refused with a plain message.
- **M2.** The card extras redirect escapes the card id.
- **SC1.** "Add to a Pokémon set" saves once at the end: the helpers it calls take `commit=False`.

### Speed and tidy-up

- **P7.** `deploy/entrypoint.sh` only changes the data folder's owner when its owner isn't PUID:PGID already.
- **SH2.** A shared binder page builds the binder's rows once per request, and works out every card's pictures
  from one query for the cards and one for their extras.
- **SH5.** A shared tile offers the sprite only when the sprite file is there.
- **P1.** Unused helpers are deleted, with tests that only covered them.
- **P2.** The Dockerfile no longer repeats the version in its label (the release tools label the image). The test
  that compared the two now checks the Dockerfile has no version of its own.

### #121 TCG Pocket cards

- `chase.list_briefs` leaves Pocket cards out, as the name search beside it already does.
- Upgrade task 13 runs the existing Pocket clean-up again (`catalog.drop_pocket`): Pocket cards already saved in
  Pokémon sets are removed and their arts grouped again. A Pocket card someone owns is kept and logged, as task 1
  did. This answers the issue's question the same way 0.29 did, so nothing new is decided.

### #58 Outdated notes

The five notes listed on the issue are brought in line with the specs.

## Tests

| Criterion | Test |
| --- | --- |
| 1. Page timeout, about 96 lookups | `jobs.pool` threads see `in_page()`; a filtered search with 300 unstored cards asks for 96; `deeper=2` asks for the next 96 |
| 2. Every thread queues | the thread allow-list test; `jobs.thread` marks its thread |
| 3. Backup first, admins remove | clear and import each leave a backup; a member's "remove missing" is refused |
| 4. T1 | a typed price shows on a scan tile straight after it's saved |
| 5. T2 | a Chinese card with one CardTrader price for two printings gets it on the scan's printing |
| 6. The rest | P4 lockout, P5 cap, M2 escaping, SC1 rollback, SC3 stop after failures, P8 pruning, F2, PR1, SH2 query count, SH5, #121 filter and upgrade task |

## Notes

- `tools/` is a process file, so P2 doesn't change `tools/release_image.py`.
