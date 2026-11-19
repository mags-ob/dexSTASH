# 110: Show PokemonPriceTracker and PriceCharting keys as coming soon

**Issue:** [#110](https://github.com/mags-ob/dexSTASH/issues/110). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- Specs: [data sources](../specs/data-sources.md) items 10 to 13 and 15, [prices](../specs/prices.md) items 2, 8
  and 18.
- The API keys rows come from `keys.SITES` in `app/keys.py`: `cardtrader`, `pricecharting`, and
  `ebay` with `"soon": True` (a greyed row, no key taken). `data_sources.html` shows the "Coming soon" badge
  for `soon` rows.
- Keys are stored in `data/api_keys.json`, sealed with the app's secret key, outside the database
  (`keys.save`, `keys.remove`, `keys.get`). Removing a saved PriceCharting key on upgrade would be a one-off
  after-upgrade task (`app/upgrades.py`) calling `keys.remove("pricecharting")`.
- PriceCharting is a source in `prices.SOURCES` (`_fetch_pricecharting`, `app/pricecharting.py`): matched weekly,
  priced each run, a second between requests. With no key it's never contacted, so removing the key stops the
  requests; the source can stay in the code for when it's tested.
- Typed prices ("Prices you type", including a price seen on PriceCharting) are a separate source (`manual`) and
  aren't touched. The PriceCharting search link on card pages is only a link.
- The "Search eBay sold" link and its eBay site setting (`/account/data/ebay`) stay.
- `NOTICE.md` and the specs name the sources; update what they say about PriceCharting and eBay.
- Related: #16 (eBay sold prices; check whether it can close), #111 (PokemonPriceTracker free tier), and the
  CardTrader key fixes still on branch `fix/cardtrader-key` (#64, #65), which touch the same page.

## Open questions

- Stored PriceCharting quotes and their history: keep them, or remove them with the key?
- UI/UX: no wireframe, the maintainer's call (19 Nov 2026).
