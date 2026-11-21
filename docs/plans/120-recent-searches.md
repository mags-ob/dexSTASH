# 120: Show recent searches on Find

**Issue:** [#120](https://github.com/mags-ob/dexSTASH/issues/120). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- Part of the app: sets and search. Spec: [sets and search](../specs/sets-and-search.md) (item 10, Recently
  viewed; item 8, saved searches).
- The original note, word for word: "Similar to recently viewed cards including recently searched sets and
  artists and only matches not incorrect spelling".
- Recently viewed (#66) is the model: `app/recent.py` keeps the last 50 cards opened in `card_views`, shared by
  everyone on the server, and an empty Find shows 9 of them as picture and name (`search.html`,
  `recent.latest` in `app/routes/search.py`).
- Saved searches (`app/saved_searches.py`) are kept per login in `user_prefs` and keep the words, language, set,
  filters and sort (`KEEP`), shown as chips under the Find box. A recent search could reuse `clean_query` so it
  remembers the same things.
- What counts as a set or artist search today: typing a set name lists matching sets above the results
  (`set_matches`); `by=` searches an artist, and a whole artist's name typed counts as one (spec item 13);
  artist names that contain what was typed are offered above the results (`artist_hits`).
- Spelling: when a name search finds fewer than 3 cards, Find offers "Did you mean" (`app/spelling.py`). The
  note says misspelt searches shouldn't be kept; "found at least one card, set or artist" is one way to tell.
- Related: #6 (search and card pages, parent), #27 (the three card searches made alike), #95 (Find with every
  language). #117 (Find language buttons) was folded into #116.

## Notes for planning

- Keeping them in `user_prefs`, like saved searches, needs no new table, so no database step.
- An empty Find would then have saved search chips, Recent searches and Recently viewed. The wireframe should
  show how they sit together at phone width.

## Open questions

See the issue's Questions. Also: is a set or artist page opened from a result a "search" here, or only what's
typed in Find?
