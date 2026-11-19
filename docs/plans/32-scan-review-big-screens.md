# 32: Fit scan review to big screens

**Issue:** [#32](https://github.com/mags-ob/dexSTASH/issues/32). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- Part of the app: scanning, design system. Spec: [scanning](../specs/scanning.md), items 15 to 19.
- The batch page is `scan_batch.html` with `scan-batch.js`; a photo's card view is `_scan_details.html` /
  `scan_details_page.html` with `scan-details.js` and `scan-details-page.js`. CSS is one file per template in
  `app/static/css`, linked through `asset()`.
- The app is built phone first; there's no front-end framework or build step (AGENTS.md, Stack).
- Related: #62 redesigns scan review around finding the match. Doing #62 first may change what this lays out.

## Open questions

- Do this after #62, or together?
- What width counts as a wide screen?
