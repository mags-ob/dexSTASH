# 18: Back-fill price history

**Issue:** [#18](https://github.com/mags-ob/dexSTASH/issues/18). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- Part of the app: prices. Spec: [prices](../specs/prices.md), items 24 to 28 (history, thinning, charts).
- History is kept for cards someone has and single-card chases, one series per source and per printing's
  average, a point only when the price changes (`price_snapshots`, `app/value.py`).
- Thinning settings (daily, weekly, monthly periods) apply to every point; a back-filled point would follow them.
- TCGCSV (`app/tcgcsv.py`) gives today's TCGplayer prices per set. Its daily archive is the source this issue
  would read.
- Sets are matched to TCGplayer's weekly by code then name; an unmatched set is never guessed, so its cards get
  nothing back-filled.

## Earlier notes

- Blocked: TCGCSV's price archive has been offline since late October 2026.

## Open questions

- Is the archive back? Check before planning.
