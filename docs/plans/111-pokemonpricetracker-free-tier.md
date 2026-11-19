# 111: Evaluate PokemonPriceTracker free tier

**Issue:** [#111](https://github.com/mags-ob/dexSTASH/issues/111). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- This is an evaluation: the result is a write-up and a recommendation, not code. Any build work becomes its own
  issue.
- Specs it would touch later: [prices](../specs/prices.md) (a new entry in `prices.SOURCES`) and
  [data sources](../specs/data-sources.md) (a key under API keys, a row in `NOTICE.md`).
- A trial must not put a key in the repository, a test, a log or an issue (core rules). Tests never contact a
  real site, so the trial is done by hand, outside the test suite.
- "Typical collection" for the credits estimate: use the demo collection from `tools/demo_data.py` as the
  example, not anyone's own.
- What counts as a match: our cards are per language and per printing (prices spec items 1 to 3). A price for
  the whole card can only go on the base printing.
- Shared binder links show each card's price and where it came from (sharing spec item 9), which is why the terms
  question covers shared links.

## Earlier notes

- From their terms (checked 19 Nov 2026): personal, non-commercial use is allowed on the free plan; data can be
  stored as history; no resale or passing on the raw data. Free is 100 credits a day and 3 days of history. Their
  pages don't mention open-source or self-hosted apps.
- Related: #110 (its "Coming soon" row), #16 (eBay sold prices), #19 (trending cards).

## Open questions

- Who asks PokemonPriceTracker about the terms, and where is the answer kept? (Not in the repository if it holds
  anyone's contact details.)
