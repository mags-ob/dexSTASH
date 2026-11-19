# 27: Make the three card searches look and work the same

**Issue:** [#27](https://github.com/mags-ob/dexSTASH/issues/27). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- Part of the app: sets and search, scanning. Specs: [sets and search](../specs/sets-and-search.md),
  [scanning](../specs/scanning.md).
- Find: `/search` (`app/routes/search.py`, `app/search.py`, `search.html`, `_find_page.html`), smart words,
  spelling help, filters and sorts, saved searches, Describe ("Don't know the name?").
- Scan's search: the card view's name, set and number boxes search again as you type, and Find can open with
  the photo carried along (scanning spec item 15; `_scan_details.html`, `scan-details.js`).
- Import's "Find it": a row needing a card opens Find with the row carried along, and "Use this" returns it
  (`/find/use`; sets and search spec item 21).
- The sets and search spec's open question notes that Find, My Stash and Chases also each have their own search
  box and filters; making them alike is planned. That wider set isn't in this issue.
- Related: #62 (scan review built around finding the match).

## Open questions

- Should My Stash's and Chases' search boxes be part of this, or a separate issue?
