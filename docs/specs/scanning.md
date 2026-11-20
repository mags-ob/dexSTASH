# Scanning

## Purpose

Add a pile of cards by photographing them: the server reads each photo, works out which card it is, and waits
for a person to check and add them, all on the household's own server with no cloud service.

## Behaviour

### Running the scanner

1. Scanning is off on a new install. It runs in one of three ways, set by `DEXSTASH_SCANNER` or, when that's
   unset, by the Scanning switch in Data sources (Off or On, which is `inside`):
   - `off`: photos aren't read and Enhanced Search doesn't run. Picture lookups for followed Pokémon's cards
     (without comparing artwork) run on one low-priority thread of the app.
   - `inside`: the app starts the scanner as a child process at the lowest priority. If memory runs short the
     child is stopped before the app, and started again later (waiting longer after each failure). It isn't
     started at all when the app's memory cap is under about 1.5 GB.
   - `separate`: the `dexstash-scanner` container (Compose profile `scanner`), at low priority with one CPU
     and a 1408 MB cap.
2. The scanner does the heavy work: reading photos, Enhanced Search, fingerprinting card pictures, grouping
   a followed Pokémon's cards by artwork, and looking up missing pictures. The app only queues it; both share
   the database. A heartbeat tells Data sources whether the scanner is running.
3. Reading a photo needs about 500 MB while photos are queued; the picture model another 400 to 600 MB while
   loaded, and it unloads after 5 idle minutes. The two never run at the same time.

### Batches and photos

4. New batch starts a batch and opens the camera. Photos are taken with the live camera or uploaded, one or
   many at a time, up to 20 MB each, and shrunk to 1600 pixels on the long side before they're saved.
5. A photo sent twice (a retry from a weak connection) is added once. When the server is busy, the phone keeps
   the photo and tries again.
6. Each photo is read in the background, one at a time, and the batch screen updates as they finish. A photo
   is waiting, being read, ready (a match), no match, or failed.
7. A batch has a name that can be changed, and can be deleted.

### Reading a photo

8. The photo's text is read on the CPU (RapidOCR): the name near the top and the bottom line with the set
   code, language and number, like `POR EN 037/088` or `MEP EN 054`. Older cards have only `037/088`.
9. Each photo's language is worked out from that text: a printed `EN`, kana for Japanese, Hangul for Korean,
   and for Chinese the set code or simplified against traditional characters. The language can be changed by
   hand, and the card is looked up again in it.
10. Japanese and Chinese cards print no language after the set code (`M1S 014/063`, `CBB4C 0602/07`), so the
    code is picked out by comparing with that language's set codes, and the name can be in kana or Chinese
    characters.
11. The card is looked up by set code and number when there's a code, else among sets with that many cards,
    else by name across every set. Up to three guesses come back, best first.

### Enhanced Search

12. When the text can't settle the card, Enhanced Search compares the photo with fingerprints of every set's
    card pictures in English, Japanese and mainland Chinese. It's turned on from Data sources, which
    downloads the picture model (CLIP ViT-B/32 as ONNX, about 350 MB) into `data/models/`.
13. Pictures are downloaded one at a time, fingerprinted and dropped: only the fingerprints are kept, one
    index per language in `data/index/`.
14. A photo can be sent to Picture search by hand, for a closer look by picture alone.

### Review and Add

15. Tapping a photo opens its card view: the photo beside the match, the other guesses and Enhanced Search
    matches to pick from, and boxes for name, set and number that search again as you type. Find can also be
    opened with the photo carried along. Save marks the card checked; Cancel throws away changes since it
    opened.
16. Each card has a printing (starting on one with a price), condition, quantity and price paid. "Set for
    all" sets the condition or price paid on every matched card not yet added. The batch's From and Binder
    choice applies to every card added from it.
17. While a photo waits in review its matched card's prices are fetched (a short price run for those sets),
    so the price shows before Add. Links to a TCGplayer or PriceCharting search are offered; a link found
    there can be saved on the card.
18. Add puts every matched photo not yet added into the stash, logged as scanned. Photos still being read or
    with no match stay in the batch.
19. Adding a card deletes its photo and the text read from it. Removing a photo moves it to Deleted scans,
    where it can be put back. Clear finished deletes the batches with nothing left to read or check; the cards
    added stay.

### Trade scans

21. Start a trade (beside Start on the Scan tab) asks only who the trade is with. The batch is named "Trade with
    <name>" and has two sides: **Your cards** and **<name>'s cards**. Scanning starts on Your cards; the batch's
    tabs and a switch at the top of the camera (both named that way) move between sides at any time. A photo goes
    to the side it was taken for, even when the phone sends it later.
22. Each side's tab shows its cards and total (the prices on its tiles times their quantity), and how many have
    no price; under the tabs, how much more or less the other person's cards are worth, and Compare both sides,
    a sheet listing each side's cards and prices. A price is added in the card view, as for any scan.
23. Each of Your cards shows how many copies of it are in the stash, or "Not in your stash". Its card view says
    which copy leaves: the only one, or, when you have the card more than one way (printing, condition), a
    warning and a choice. Finish trade waits until each of those is picked.
24. A trade batch has no From · Binder line, count line or Set for all. Finish trade shows "N cards have no
    price" when any do, a binder for each of the other person's cards (with All of them; None unless picked),
    and how many cards leave your stash, then Back or Finish.
25. Finish takes Your cards out of the stash (history: in a trade; copies leave Not in a binder first, then
    the fullest binder) and adds the other person's cards (From: Trade) to the binders picked. A card of yours
    that isn't in the stash is skipped. History entries say "Traded to <name>" and "Added from a trade with
    <name>"; the name isn't kept on the copies. Photos still being read or with no match stay in the batch.

### After a restart

26. A restart carries on with scans: photos that were being read are read again, and photos waiting in a
    batch stay until someone adds, removes or deletes them.

## Rules and limits

- Nothing is sent to a cloud service; photos are read on the server's CPU.
- Searches and page loads always come before reading photos.
- A photo is kept only until its card is added or the photo is deleted; scan photos aren't in backups.
- The scanner never gets the CardTrader token or the admin password from the environment.
- Live camera support on desktop Safari isn't a goal; upload works there.

## Interfaces

- Pages: `/scans`, `/scans/trade`, `/scans/{batch_id}` and its `photos`, `changes`, `name`, `from`, `set-all`, `add`,
  `finish` and `delete` routes; `/scans/item/{scan_id}` and its card view, language, use, match, save, cancel, skip,
  put-back, retry, picture and link routes; `/scans-finished/clear`; `/account/data/scanning`.
- Code: `app/scans.py`, `app/scanner.py`, `app/scan_worker.py`, `app/scanning.py`, `app/pictures.py`,
  `app/settings.py` (`scanner_mode`), `app/routes/scans.py`.
- Tables: `scan_batches`, `scans`, `scan_matches`, `jobs`, `picture_lookups`, `app_meta` (heartbeat).
- Settings: `DEXSTASH_SCANNER`, `COMPOSE_PROFILES`, `DEXSTASH_APP_MEMORY`, `DEXSTASH_PICTURE_MODEL_URL` (see
  [deploy](deploy.md)).
- Related specs: [architecture](architecture.md), [My Stash](my-stash.md), [prices](prices.md),
  [sets and search](sets-and-search.md), [data sources](data-sources.md).

## Open questions

- None yet.
