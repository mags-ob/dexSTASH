# 30: Translate Japanese and Chinese card text

**Issue:** [#30](https://github.com/mags-ob/dexSTASH/issues/30). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- Part of the app: sets and search, data sources. Specs: [sets and search](../specs/sets-and-search.md),
  [data sources](../specs/data-sources.md).
- Today a name is never machine-translated (sets and search spec item 14); English names come only from
  TCGCSV, CardTrader or the shipped list (`app/english.py`).
- A translation service would be a new outside site: it goes through `sites.client()`, gets a switch that is
  off on a new install, a row in `NOTICE.md`, and an entry in the deploy firewall list and the README's "Where
  data comes from" (data sources spec, Rules).
- Japanese, Chinese and Korean text never goes into a search on another site today (sets and search spec,
  Rules). Sending card text to a translation service the host set up would be a new kind of request.

## Earlier notes

- From the issue (before this rewrite): needs a decision on whether and when. No free source of official English
  text exists for these cards.
- `needs-info` added 16 Nov 2026: waiting on the maintainer's decision on whether and when to add machine
  translation.

## Open questions

- Whether to do this at all, and when.
- Where translations are kept (catalog database, so they can be made again?).
