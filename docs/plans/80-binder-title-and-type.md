# 80: Update binder title and add type

**Issue:** [#80](https://github.com/mags-ob/dexSTASH/issues/80). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Original notes

The issue as first written, kept word for word:

> Title should be adjustable
> Type should be prefixed in title
> Add type = trade

## Context

- Part of the app: binders (no spec yet; #59 writes `docs/specs/binders.md` from the current code and is listed
  as needed before any binders feature work).
- A binder today has a name, a layout (4, 9 or 12 pockets a page) and a page order (`binders` table in
  `app/schema.sql`; `app/binders.py`, `app/binder_pages.py`, `app/routes/binders.py`). It has no type.
- A binder can already be renamed: `binders.rename` and `POST /collection/binders/{id}/rename`, and the settings
  form (`/collection/binders/{id}/settings`) takes a name. Check with the maintainer what "adjustable" adds
  (for example, editing it from the binder's own page).
- A type would be a new column, so a new numbered database step (`app/db.py`) and a `Host:` line in the pull
  request.
- Shared binders show their name to visitors (`public_binder.html`), so a type prefix would show there too.
- Part of the Trading parent issue (#86), with #81 (trade scan).

## Open questions

- What types besides Trade, and is there a default (none)?
- Does a type change anything besides the title?
- Is today's Rename enough for "title should be adjustable"?
