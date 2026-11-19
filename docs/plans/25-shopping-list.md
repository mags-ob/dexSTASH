# 25: Add a shopping list of missing cards

**Issue:** [#25](https://github.com/mags-ob/dexSTASH/issues/25). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- Part of the app: collection (My Stash), chases. Specs: [My Stash](../specs/my-stash.md),
  [chases](../specs/chases.md).
- Set pages show every card in number order with what you own (`app/routes/sets.py`, `app/display.py`
  `set_grid_view`, `set.html`). Set completion is in `app/catalog.py` (`set_completion`): cards owned out of the
  printed total, secret rares counted beside it.
- "Master set" (every printing) appears in the issue, but the app has no master-set view or switch today, and
  it isn't defined in [concepts](../concepts.md) or the specs.
- Prices come only from stored prices (`prices.resolve`). A card nobody keeps shows TCGCSV's last price for its
  set, else TCGdex's, else "Not priced yet" (prices spec item 16), so missing cards may have no stored price.
- CSV downloads exist for the stash, one binder and chases (`app/export.py`), with a byte order mark.

## Open questions

- What is "master set on", and where is it switched?
- Where does the list live: on the set page, a page of its own, or both? Same for chases.
