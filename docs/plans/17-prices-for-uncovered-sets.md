# 17: Find prices for sets no source covers

**Issue:** [#17](https://github.com/mags-ob/dexSTASH/issues/17). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- Part of the app: prices, data sources. Specs: [prices](../specs/prices.md), [data sources](../specs/data-sources.md).
- Today CardTrader (the host's token) is the only downloaded source for Chinese cards (prices spec item 2).
  TCGCSV covers English and Japanese only.
- Data sources › Prices lists "sets with no price source", with Search links (data sources spec item 15).
- A new source must be an official API or open data (core rules; data sources spec, Rules), gets a row in
  `NOTICE.md`, and goes through `sites.client()`. A site the app doesn't need starts switched off.
- A new source is added to `prices.SOURCES`; its price counts equally in the average, per printing.

## Earlier notes

- From the issue (before this rewrite): no source is known yet for about 10 Chinese and Taiwanese sets (mostly
  gift boxes, starter decks and promo packs). Start with a check of what each candidate covers and its terms.
- `needs-info` added 16 Nov 2026: waiting on the maintainer's decision on which free source, if any, to use.

## Open questions

- Which candidate sources to check, and is a source that covers only some of the sets worth adding?
