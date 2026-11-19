# 36: Ship known data fixes with each release

**Issue:** [#36](https://github.com/mags-ob/dexSTASH/issues/36). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- Part of the app: data model, data sources. Spec: none yet; the issue says a data model spec comes first.
  [architecture](../specs/architecture.md) and [data sources](../specs/data-sources.md) are the closest.
- Two mechanisms run once on an existing database today:
  - numbered database steps (`app/db.py`, `_step_N`, `LATEST`) change tables;
  - after-upgrade tasks (`app/upgrades.py`) do work an update needs once, numbered and recorded in `app_meta`
    as `after_upgrade:N`. Quick ones run before the first page, slow ones in the background. A new database
    starts with every task marked done.
- Every upgrade backs the database up first (Rules in [concepts](../concepts.md)).
- Hand fixes the app already keeps and must not overwrite: art fixes (`art_fixes`), set matches picked on Data
  sources, typed prices and links, hidden tags (`card_tag_hidden`).
- Data sources' Activity log shows both containers' recent logs.

## Earlier notes

- From the issue (before this rewrite): needs a decision on how: fixes could ship as numbered upgrade tasks, as a
  data file in the image, or as a file each release downloads.
- `needs-info` added 16 Nov 2026: waiting on the maintainer's decision on how data fixes ship.

## Open questions

- Which of the three ways? After-upgrade tasks already exist and run once after the backup, which may make them
  the smallest change.
- Should the data model spec be written first, as a separate issue?
