# 16: Add eBay sold prices with your own key

**Issue:** [#16](https://github.com/mags-ob/dexSTASH/issues/16). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- Part of the app: prices, data sources. Specs: [prices](../specs/prices.md), [data sources](../specs/data-sources.md).
- Data sources › Prices › API keys already has a greyed "eBay sold prices" row marked Coming soon
  (`app/keys.py`, `"ebay": {..., "soon": True}`; drawn in `app/templates/data_sources.html`).
- The card page's "Search eBay sold" link is only a link (data sources spec item 14). The eBay site it opens is a
  setting (`/account/data/ebay`).
- A new source is added to `prices.SOURCES` with its fetch and how it ties to a printing; `resolve` and the run
  pick it up (prices spec, Rules). Its quotes go to `price_quotes`, and it keeps its own history.
- A key-based source is contacted only when the host has a key, and is listed in `NOTICE.md` with its terms.
- Both specs list this as an open question: a bring-your-own-key eBay source, terms not checked yet.

## Earlier notes

- From the issue (before this rewrite): the likely provider (PokemonPriceTracker, free tier with your own key)
  resells eBay data, so its terms must allow use in an app other people host. If they don't, look for another
  source or drop this.
- `needs-info` was added 16 Nov 2026: waiting on the maintainer's decision on whether the provider's terms allow it.
- 19 Nov 2026: #110 replaces the eBay row with a PokemonPriceTracker "Coming soon" row, because eBay's own
  sold-price API is limited to select developers. #111 evaluates PokemonPriceTracker's free tier. #110 says to
  check afterwards whether this issue can be closed.

## Open questions

- Close this once #110 and #111 are done, or keep it for an eBay route other than PokemonPriceTracker?
