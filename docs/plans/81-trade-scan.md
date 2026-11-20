# 81: Ability to trade scan

**Issue:** [#81](https://github.com/mags-ob/dexSTASH/issues/81). The issue decides; this file explains.

Design agreed 20 Nov 2026 (second draft), waiting on the go.

## Original notes

The issue as first written, kept word for word:

> Setup two queues that are connected
> Name the other person
> Title of scan becomes specified trade instance and with who
> Need to design scan order (person 1 scan first and then scan two
> Maybe two buttons on the queue, scan for person 1 vs person 2
> Button to preview what person 1 v 2 have scanned
> A total
> If no total automatically loaded, need to figure out a way to add it in
> Match up cards in trade with inventory for person 1 (assumed person 1 = library owner)

## Context

- Part of the app: scanning, binders. Spec: [scanning](../specs/scanning.md); binders has no spec yet (#59).
- A scan batch today is one queue: a name that can be changed, photos read one at a time, review, then Add puts
  matched cards in the stash. The batch's From and Binder choice applies to every card added (scanning spec
  items 4 to 7, 16 to 18; `scan_batches`, `scans`; `app/scans.py`, `app/routes/scans.py`).
- "From" already has Trade as a choice for where a copy came from (My Stash spec item 4).
- Prices for matched cards come from stored prices, and a short price run fetches them while a photo waits in
  review (scanning spec item 17). A card with no price shows none; a typed price is the existing way to add one.
- Two linked queues would need a side on each scan (or a link between batches): a new numbered database step.
- Part of the Trading parent issue (#86), with #80 (binder type Trade). #86 says the binders spec comes first.
- Sharing is never for selling (sharing spec); a trade scan is for the household's own use. Arranging trades online
  stays out of scope (product brief).
- #118 (whether scanning stays optional): a trade scan needs photos read. If scanning can be off, each side also
  needs a way to add a card by search, or the Start a trade button is hidden when scanning is off.

## UI/UX (draft 2)

Phone width, 375 px. Made from the real pages with a demo collection; "proposed" screens are the real page with
the changes drawn in. Files in [wireframes/trade-scan](../specs/wireframes/trade-scan/). Updated 20 Nov 2026
after the maintainer's feedback: sides are named "Your cards" and "<name>'s cards", scanning always starts on
Your cards, no Add price sheet, a no-price warning before finishing, and a binder for the other person's cards.

| Now | Proposed |
| --- | --- |
| Scan tab: New batch, Start ([now-scan-tab](../specs/wireframes/trade-scan/now-scan-tab.png)) | Start and **Start a trade** side by side; a trade batch shows "⇄ Trade with Brock" and both totals ([proposed-scan-tab](../specs/wireframes/trade-scan/proposed-scan-tab.png)) |
| none | New trade sheet: only "Trading with". Scanning always starts on Your cards ([proposed-new-trade](../specs/wireframes/trade-scan/proposed-new-trade.png)) |
| Batch: name, From · Binder, grid, Add N to My Stash ([now-batch](../specs/wireframes/trade-scan/now-batch.png)) | Trade batch: two tabs, **Your cards** and **Brock's cards** (named from Trading with), each with its card count and total, the difference under them, then the same grid for that tab ([your cards](../specs/wireframes/trade-scan/proposed-trade-your-cards.png), [Brock's cards](../specs/wireframes/trade-scan/proposed-trade-their-cards.png)) |
| none | Your cards tiles say how many you have ("In stash ×2", or "Not in your stash") |
| Tile shows "No price"; a price is added in the card view | Same as today. The tab says "1 no price" and the total leaves it out |
| Count row on every batch: "N photos · N checked · N needs a match" and Set for all | Not on a trade batch: the tabs already show counts and no-price. Condition and paid are set per card in the card view |
| From · Binder line on the batch | Not on a trade batch. The binders are picked in Finish trade |
| Camera: "Scanning cards" ([now-camera](../specs/wireframes/trade-scan/now-camera.png)) | Camera: a **Your cards / Brock's cards** switch at the top, named from Trading with; photos go to the side picked ([proposed-camera-side](../specs/wireframes/trade-scan/proposed-camera-side.png)) |
| none | Compare both sides (link on its own line, centred, under "Brock's cards are worth $X more/less"): one list per side with prices, stash counts and the difference, with a close button ([proposed-compare](../specs/wireframes/trade-scan/proposed-compare.png)) |
| Add N to My Stash | **Finish trade**: "N card(s) have no price" warning when any do, a binder for each of Brock's cards (or All of them; default None), "Cards leaving your stash: N", then Back or Finish ([proposed-finish](../specs/wireframes/trade-scan/proposed-finish.png)) |

Kept as they are: batch name editing, language tags, the card view (including adding a price), the
camera's library button and shutter, Added and Deleted scans folds, the delete menu, and plain batches.

## Answers so far

From the maintainer, 20 Nov 2026:

- Sides are "Your cards" and "<Trading with>'s cards", on the batch tabs and the camera switch.
- Scanning always starts on Your cards; no choice in the New trade sheet.
- The button says "Finish trade".
- A card with no price: warn before finishing the trade. Prices are added in the card view, as today.
- The other person's cards: no From · Binder line on the batch. Finish trade asks which binder each card goes in
  (with an All of them choice), default None. From is Trade, with their name. (Added 20 Nov 22:01.)
- Finish trade sheet: warning just says "N card(s) have no price"; buttons Back and Finish; "Cards leaving your
  stash: N" with no list. Compare has a close button. The Compare link sits on its own line, centred, under
  "<name>'s cards are worth $X more/less". (Added 20 Nov 22:09.)
- No count row and no Set for all on a trade batch; condition and paid are set per card. (Added 20 Nov 22:23,
  replacing 22:12.)

- Sides can be switched any time, from the tabs or the camera switch.
- Finish trade adds the other person's cards to the stash (From: Trade, with their name, in the binders picked in Finish trade),
  and takes Your cards out of the stash, logged as traded. A card that isn't in the stash is skipped and said so.
- The other person is just a name, typed when the trade starts. No login or shared link.

## Still to ask

- Does the other person's side also show stash counts ("You have 1", "On your chase list")? Not in the notes, so not drawn.
- Is the price shown the main price (average of sources) for the printing picked? Assumed yes.
- Which copy leaves the stash: decided, see Architecture review finding 2.

## What's reused and what's new

A trade is a scan batch with two extra fields, not a separate feature. Regular batches don't change: with no
`trade_with` they look and work exactly as today.

- **Reused as is:** the batch and its name, the camera and upload, photo reading and Enhanced Search, matching and
  the card view (fixing a match, printing, condition, price paid, adding a price), price lookups, language tags,
  Added and Deleted scans, deleting a batch, and the code that adds cards to the stash.
- **Reused with a switch:** the batch page and camera show the side tabs, totals and side switch only when the
  batch is a trade; the Scan tab shows Start a trade beside Start.
- **New:** the two database fields, side totals and the difference, stash counts on Your cards, Compare, and
  Finish trade (binder per card, and taking Your cards out of the stash, logged as traded).

## Architecture review (20 Nov 2026)

Checked against main (`app/scans.py`, `app/routes/scans.py`, `app/inventory.py`, `app/binders.py`,
`app/static/photo-queue.js`, `app/static/js/scan-batch.js`).

| Piece | Reuse | Change |
| --- | --- | --- |
| Start a trade | `scans.create_batch` | Takes `trade_with`; batch name set to "Trade with <name>" through `set_batch_name` |
| Photo upload and side | `scans.add_photo`, `/scans/{id}/photos` | Takes `side`. The phone's offline queue (`photo-queue.js`) must save the side with each photo, or a retried upload lands on the wrong side |
| Reading, matching, card view | All of it, unchanged | None |
| Tile prices and side totals | `tile_prices` / `scan_tile` (the price each tile already shows) | Side total = sum of tile prices × quantity, so totals always agree with the tiles. No second price path; `prices.resolve` stays the only place a price is worked out |
| Adding their cards | The Add route's per-card loop: `scans.add_card`, `spending.set_paid`, `binders.put_some`, `_binder_choice` | **Refactor first:** that logic sits inline in the route (`scan_review_add`). Move it to a plain function in `app/scans.py` that takes the lines and a binder per scan; Add N to My Stash and Finish trade both call it. Per-card binders come from the Finish form, so no new column |
| Taking your cards out | `inventory.set_quantity` (logs every change), then `binders.tidy` | New log source `trade` ("Traded"). Lowering a line takes Not-in-a-binder copies first, then the fullest binder: existing behaviour |
| Stash counts | A read of `inventory` by card | Count every copy of the card; say which printing/condition will leave |
| Count row, Set for all, From · Binder | Templates | Hidden when the batch is a trade; plain batches unchanged |
| One transaction | `db.transaction` | Finish trade adds and removes in one transaction; any failure keeps nothing, as Add does today |

Findings:

1. **Refactor the Add logic out of the route first** (its own commit). It's the biggest reuse, and AGENTS.md wants
   logic in plain modules, not routes. Plain batches must behave exactly as before; existing Add tests cover it.
2. **Your cards need the printing and condition you own.** Removing a copy needs the exact stash line
   (card, printing, condition). Decided 20 Nov 22:58: when you own more than one line of that card (languages,
   printings, conditions), the card view for that scan warns and asks you to pick which copy leaves. With one line,
   it's picked for you. Finish trade can't run until each of Your cards points at a copy you own (or is marked not
   in your stash).
3. **The other person's name.** Decided 20 Nov 22:58: not kept on the stash line or after the trade. The card's
   history keeps it: the same database step adds a short note to each history entry, so the card page reads
   "Added from a trade with Brock" and "Traded to Brock". The batch holds the name only while the trade is open
   (for the tab names).
4. **Offline photo queue** must carry the side (finding above); a test covers a retried upload.
5. **No new background work.** Prices for matches already come from the existing short price run; nothing new
   reaches another site.
6. **Clear finished batches** deletes finished trades as today; the card history keeps who each trade was with.

## Plan (draft, after answers)

Short version goes in the issue's Plan section once the answers are in.

- Database: a new numbered step adds `scan_batches.trade_with` (the other person's name, empty for a plain batch),
  `scans.side` (`mine` or `theirs`), `scans.from_line` (the stash line a Your cards scan takes from) and
  `inventory_log.note` ("Trade with Brock").
- First, move the Add logic from `scan_review_add` into `app/scans.py`, unchanged in behaviour.
- `app/scans.py`: side totals from the tile prices; stash counts for Your cards;
  Finish trade adds the other side's cards and removes Your cards' copies in one transaction, each history entry
  noted with the trade.
- Routes and templates: Start a trade, side tabs named from Trading with, the camera's side switch, Compare, Finish trade with the no-price warning and a binder per card.
  New CSS and JavaScript in the scan files, through `asset()`.
- Tests: one per acceptance criterion, in `tests/` next to the scan tests.
- Docs impact: scanning spec (new Trade section), My Stash spec (traded-away log), concepts.md (Trade scan,
  Your cards, the other person's cards), CHANGELOG.

## Built (20 Nov 2026)

What was built, and where it differs from the plan above:

- Database step 20: `scan_batches.trade_with`, `scans.side`, `scans.from_line`, `inventory_log.note`.
- `scans.add_lines` holds the Add logic that was inline in the route; Add N to My Stash and Finish trade both use
  it. Like Add, each card is saved as it's added; Finish checks everything first (copies picked, their cards
  loadable, binders real), then takes your cards out, then adds theirs.
- The copy picker lists the matched card's printings and conditions you own. A card in another language is a
  different card: change the photo's language tag to match the copy you're trading.
- The binder menus in Finish trade offer None and your binders; there's no "+ New binder" there (make one on
  My Stash › Binders first).
- `tests/test_trade_scan.py` covers each acceptance criterion. `tests/test_round27.py::test_step_19` now checks the
  database ends at the latest step (it pinned 19). `tests/routes.txt` and the architecture diagrams list the two
  new routes.
