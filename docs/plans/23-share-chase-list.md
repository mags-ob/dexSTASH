# 23: Share your chase list by link

**Issue:** [#23](https://github.com/mags-ob/dexSTASH/issues/23). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- Part of the app: chases, sharing. Specs: [chases](../specs/chases.md), [sharing](../specs/sharing.md).
- Binder sharing today: a Share switch, a random link `/b/<token>`, up to 50 named codes per binder kept only as
  salted scrypt hashes, a signed 30-day pass cookie per link, every request re-checks the code and link, and
  wrong codes are limited to 10 in 10 minutes per address (in memory). Code: `app/shared.py`,
  `app/routes/shared.py`, `app/auth.py` (`Lockout`). Tables: `binders` (switch and token), `binder_codes`,
  `binder_code_log`.
- Visitor pages are sent with `noindex, nofollow`, no referrer and no caching.
- The Chases page lists single chases (`wants`) with price and 30-day move, then followed Pokémon and artists
  (`app/wants.py`, `app/routes/wants.py`, `chases.html`). Chase boxes hold links and photos (`chase_links`,
  `chase_photos`).
- Both specs list this as an open question: what a visitor may see isn't decided.
- Shared pages may show pictures from the Chinese datasets (shown to friends, not republished).

## Open questions

- Should followed Pokémon and artists be on the shared list, or only single chases?
- Prices: the criteria say name, picture, set and number "and nothing else". Confirm no current price either.
- Do Chase box links and photos stay hidden?
- A table for the chase list's link and codes would be a new database step.
