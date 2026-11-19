# 81: Ability to trade scan

**Issue:** [#81](https://github.com/mags-ob/dexSTASH/issues/81). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

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
- Two linked queues would need a link between batches (or a side on each scan): a new numbered database step.
- Part of the Trading parent issue (#86), with #80 (binder type Trade). #86 says the binders spec comes first.
- Sharing is never for selling (sharing spec); a trade scan is for the household's own use.

## Open questions

- Scan order: one side then the other, or switch any time with two buttons?
- How a missing total is entered: a typed price per card, or one total for the side?
- What happens when the trade is done: do your side's cards leave the stash and the other side's join it?
- Is "the other person" just a name, or tied to a shared link or login?
