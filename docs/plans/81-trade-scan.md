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
| From · Binder sheet | On Brock's cards: From is Trade · Brock, and you pick the binder they go in ([proposed-their-binder](../specs/wireframes/trade-scan/proposed-their-binder.png)) |
| Camera: "Scanning cards" ([now-camera](../specs/wireframes/trade-scan/now-camera.png)) | Camera: a **Your cards / Brock's cards** switch at the top, named from Trading with; photos go to the side picked ([proposed-camera-side](../specs/wireframes/trade-scan/proposed-camera-side.png)) |
| none | Compare both sides: one list per side with prices, stash counts and the difference ([proposed-compare](../specs/wireframes/trade-scan/proposed-compare.png)) |
| Add N to My Stash | **Finish trade**: says what joins and leaves the stash, and warns first when a card has no price ("Add the price first" or "Finish anyway") ([proposed-finish](../specs/wireframes/trade-scan/proposed-finish.png)) |

Kept as they are: batch name editing, language tags, the card view (including adding a price), Set for all, the
camera's library button and shutter, Added and Deleted scans folds, the delete menu, and plain batches.

## Answers so far

From the maintainer, 20 Nov 2026:

- Sides are "Your cards" and "<Trading with>'s cards", on the batch tabs and the camera switch.
- Scanning always starts on Your cards; no choice in the New trade sheet.
- The button says "Finish trade".
- A card with no price: warn before finishing the trade. Prices are added in the card view, as today.
- The other person's cards: the user picks the binder they go in.

- Sides can be switched any time, from the tabs or the camera switch.
- Finish trade adds the other person's cards to the stash (From: Trade, with their name, in the binder picked),
  and takes Your cards out of the stash, logged as traded. A card that isn't in the stash is skipped and said so.
- The other person is just a name, typed when the trade starts. No login or shared link.

## Still to ask

- Does the other person's side also show stash counts ("You have 1", "On your chase list")? Not in the notes, so not drawn.
- Is the price shown the main price (average of sources) for the printing picked? Assumed yes.
- Which copy leaves the stash when there's more than one: assumed the one in the fewest binders, then the oldest.
  A Trade binder (#80) could go first later.

## Plan (draft, after answers)

Short version goes in the issue's Plan section once the answers are in.

- Database: a new numbered step adds `scan_batches.trade_with` (the other person's name, empty for a plain batch)
  and `scans.side` (`mine` or `theirs`).
- `app/scans.py`: side totals and "no price" counts from `prices.resolve`; stash counts for Your cards;
  Finish trade adds the other side's cards and removes Your cards' copies in one transaction.
- Routes and templates: Start a trade, side tabs named from Trading with, the camera's side switch, the binder for the other side, Compare, Finish trade with the no-price warning.
  New CSS and JavaScript in the scan files, through `asset()`.
- Tests: one per acceptance criterion, in `tests/` next to the scan tests.
- Docs impact: scanning spec (new Trade section), My Stash spec (traded-away log), concepts.md (Trade scan,
  Your cards, the other person's cards), CHANGELOG.
