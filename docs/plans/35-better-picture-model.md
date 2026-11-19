# 35: Improve the picture model for matching

**Issue:** [#35](https://github.com/mags-ob/dexSTASH/issues/35). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- Part of the app: scanning. Spec: [scanning](../specs/scanning.md), items 12 to 14.
- Enhanced Search uses CLIP ViT-B/32 as ONNX (about 350 MB), downloaded from `DEXSTASH_PICTURE_MODEL_URL` into
  `data/models/` when it's turned on. Fingerprints are kept per language in `data/index/`; pictures are dropped.
- The model needs 400 to 600 MB while loaded and unloads after 5 idle minutes. The separate scanner container is
  capped at 1408 MB.
- Confirmed scans already teach Enhanced Search: a scan's fingerprint (never the photo), at most 5 per card, kept
  in `picture_learned` (512-number vectors). A new model would make those fingerprints stale too.
- Models and downloaded data are never committed or put in the image (Constraints in
  [concepts](../concepts.md)). A new model's licence goes in `NOTICE.md`.
- Rebuilding fingerprints once after a change would be an after-upgrade task (`app/upgrades.py`), slow, in the
  background.

## Earlier notes

- From the issue (before this rewrite): needs a decision on which model and its licence before building.
- `needs-info` added 16 Nov 2026: waiting on the maintainer's decision on which picture model, and its licence.

## Open questions

- Which model, and does its licence allow use here?
- What fixed test set of photos to measure against (it can't hold anyone's personal photos in the repository).
