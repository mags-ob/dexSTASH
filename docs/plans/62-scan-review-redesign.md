# 62: Redesign scan review around finding the match

**Issue:** [#62](https://github.com/mags-ob/dexSTASH/issues/62). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- Part of the app: scanning, design system. Spec: [scanning](../specs/scanning.md), items 15 to 19.
- Today a photo is waiting, being read, ready (a match), no match, or failed (item 6). Up to three guesses come
  back, best first (item 11), plus Enhanced Search matches.
- Tapping a photo opens its card view: the photo beside the match, other guesses and Enhanced Search matches,
  name/set/number boxes that search as you type, Find with the photo carried along, Save and Cancel (item 15).
  Printing, condition, quantity, price paid and links are on the same view (items 16, 17).
- Files: `scan_batch.html`, `_scan_item.html`, `_scan_details.html`, `_scan_found.html`,
  `scan_details_page.html`, with `scan-batch.js`, `scan-details.js`, `scan-details-page.js`;
  `app/routes/scans.py`, `app/scans.py`.
- The plan needs a wireframe at phone width (AGENTS.md, Plans).
- Related: #27 (one search for Find, Scan and Import), #32 (big screens), #63 (add a photo as soon as it's taken).

## Open questions

- What counts as "unsure"? The scanner has a best-first list, not a confidence the page shows today.
