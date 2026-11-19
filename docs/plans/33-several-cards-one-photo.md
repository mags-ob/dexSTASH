# 33: Read several cards from one photo

**Issue:** [#33](https://github.com/mags-ob/dexSTASH/issues/33). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- Part of the app: scanning. Spec: [scanning](../specs/scanning.md).
- One photo is one scan today (`scans` table, `app/scans.py`). The scanner (`app/scanner.py`,
  `app/scan_worker.py`, `app/scanning.py`) reads the text with RapidOCR on the CPU and looks the card up by set
  code and number, else by name; Enhanced Search compares the photo with picture fingerprints.
- Photos are shrunk to 1600 pixels on the long side before they're saved, which leaves fewer pixels per card
  when several share a photo.
- Memory: reading a photo needs about 500 MB, the picture model another 400 to 600 MB; the separate scanner
  container is capped at 1408 MB with one CPU (scanning spec items 1 and 3).
- The camera shows a guide frame for one card (`placeGuide`, `guideCrop` in `scan-batch.js`).

## Open questions

- How are cards found in the photo (a card-outline detector, a fixed grid, or something else), and does it fit
  the memory cap on arm64 and amd64?
- Does the 1600-pixel limit need to change for several-card photos?
