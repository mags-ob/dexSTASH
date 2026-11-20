# 81: Ability to trade scan

**Issue:** [#81](https://github.com/mags-ob/dexSTASH/issues/81). The issue decides; this file explains.

Draft design (20 Nov 2026), waiting on the maintainer's answers and go. Nothing here is decided.

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

## UI/UX (draft)

Phone width, 375 px. Made from the real pages with a demo collection; "proposed" screens are the real page with
the changes drawn in. Files in [wireframes/trade-scan](../specs/wireframes/trade-scan/).

| Now | Proposed |
| --- | --- |
| Scan tab: New batch, Start | Scan tab: Start and **Start a trade** side by side; a trade batch shows "⇄ Trade with Brock" and both totals ([proposed-scan-tab](../specs/wireframes/trade-scan/proposed-scan-tab.png)) |
| none | New trade sheet: who it's with, which side to scan first ([proposed-new-trade](../specs/wireframes/trade-scan/proposed-new-trade.png)) |
| Batch: name, From · Binder, grid, Add N to My Stash ([now-batch](../specs/wireframes/trade-scan/now-batch.png)) | Trade batch: two tabs **You give** / **You get**, each with its card count and total, the difference under them, then the same grid for the tab you're on ([you give](../specs/wireframes/trade-scan/proposed-trade-you-give.png), [you get](../specs/wireframes/trade-scan/proposed-trade-you-get.png)) |
| none | You give tiles say how many you have ("In stash ×2", or "Not in your stash" in amber) |
| Tile shows "No price" | You get tile shows **Add price**; the tab says "1 no price" and the total leaves it out ([add price sheet](../specs/wireframes/trade-scan/proposed-add-price.png)) |
| Camera: "Scanning cards" ([now-camera](../specs/wireframes/trade-scan/now-camera.png)) | Camera: a You give / You get switch at the top; photos go to the side picked ([proposed-camera-side](../specs/wireframes/trade-scan/proposed-camera-side.png)) |
| none | Compare both sides: one list per side with prices, stash counts and the difference ([proposed-compare](../specs/wireframes/trade-scan/proposed-compare.png)) |
| Add N to My Stash | **Finish trade…**: says what joins and what leaves the stash before it happens ([proposed-finish](../specs/wireframes/trade-scan/proposed-finish.png)) |

Kept as they are: batch name editing, language tags, the card view, Set for all, the camera's library button and
shutter, Added and Deleted scans folds, the delete menu, and plain batches.

## Proposed answers to the open questions

These are drawn in the wireframes but are the maintainer's to decide.

1. **Scan order:** either side, switch any time (tabs on the batch, switch in the camera). The New trade sheet
   only picks which side the camera starts on.
2. **Missing price:** the side total leaves out cards with no price and says how many ("1 no price"). Add price
   on the tile opens a small sheet to type a value for this trade, with an option to save it as the card's typed
   price. No single typed total per side.
3. **When the trade is done:** Finish trade adds You get cards to the stash (From: Trade, with the person's
   name), and takes You give cards out of the stash, logged as traded. A You give card that isn't in the stash is
   skipped and said so. If more than one copy could go, the one in a Trade binder goes first (needs #80).
4. **The other person:** just a name, typed when the trade starts. No login or shared link.

## Still to ask

- Does the You get side also show stash counts ("You have 1", "On your chase list")? Not in the notes, so not drawn.
- Is the price shown the main price (average of sources) for the printing picked? Assumed yes.
- Does Finish trade wait for #80 (Trade binder type), or does it pick any copy for now?

## Plan (draft, after answers)

Short version goes in the issue's Plan section once the answers are in.

- Database: a new numbered step adds `scan_batches.trade_with` (the other person's name, empty for a plain batch)
  and `scans.side` (`give` or `get`).
- `app/scans.py`: side totals and "no price" counts from `prices.resolve`; stash counts for You give cards;
  Finish trade adds You get cards and removes You give copies in one transaction.
- Routes and templates: Start a trade, side tabs, the camera's side switch, Add price, Compare, Finish trade.
  New CSS and JavaScript in the scan files, through `asset()`.
- Tests: one per acceptance criterion, in `tests/` next to the scan tests.
- Docs impact: scanning spec (new Trade section), My Stash spec (traded-away log), concepts.md (Trade scan,
  You give, You get), CHANGELOG.
