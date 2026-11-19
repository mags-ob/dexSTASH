# 19: Show trending cards on Home

**Issue:** [#19](https://github.com/mags-ob/dexSTASH/issues/19). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- Part of the app: prices. Spec: [prices](../specs/prices.md), items 29 and 30.
- Home already shows top movers among the cards you have (each printing against its own history from the same
  source) and chases down 10% or more in a week (`app/home.py`, `app/value.py`).
- Prices are kept only for cards someone has or chases, so the app has no market-wide price moves of its own.
- Any new source must be free (Constraints in [concepts](../concepts.md)) and official or open data.

## Earlier notes

- Blocked: no free source of market-wide price moves is known. Home's movers already cover cards you keep.

## Open questions

- Is there now a free source of market-wide moves? (#111 checks what PokemonPriceTracker's free tier gives.)
