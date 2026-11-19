# 63: Add a photo to the batch as soon as it's taken

**Issue:** [#63](https://github.com/mags-ob/dexSTASH/issues/63). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- Part of the app: scanning. Spec: [scanning](../specs/scanning.md), items 4 to 6.
- Today the camera stays open between shots, and photos stay on the phone until "Add to batch"
  (`scan_batch.html`, the `#cam` dialog; `scan-batch.js`: `snap`, `keep`, `drawShots`, `drop`, `send`). A
  counter shows "N photos taken" with ✕ to delete and retake.
- Sending already copes with a weak connection: a photo sent twice is added once, and when the server is busy the
  phone keeps the photo and tries again (item 5). Photos are shrunk on the phone before sending (`shrink`).
- Removing a photo from a batch moves it to Deleted scans, where it can be put back (item 19).
- Related: #62 (scan review redesign).

## Open questions

- With no confirm step, how does someone remove a bad shot quickly while the camera is open: ✕ on the last
  shot, or only from the batch afterwards?
