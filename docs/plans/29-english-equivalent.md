# 29: Show an English equivalent for Japanese and Chinese cards

**Issue:** [#29](https://github.com/mags-ob/dexSTASH/issues/29). The issue decides; this file explains.

Not planned yet. This file holds the context a plan will need. Nothing here is decided.

## Context

- Part of the app: sets and search, prices. Specs: [sets and search](../specs/sets-and-search.md),
  [prices](../specs/prices.md).
- A Japanese or Chinese card already shows its English name and its set's English name when one is known
  (`app/english.py`: TCGCSV's Japanese set list, CardTrader's expansions, then the shipped
  `set_names_en.json`). A card's English name is `cards.twin_name` from the English card, or `english_name`.
  Nothing is machine-translated.
- When a card is in an art of a followed Pokémon or artist, and the art is printed in more than one language, the
  card page's Languages chips link each language's card (sets and search spec item 13). Arts come from
  picture fingerprints plus hand fixes, so that link is reliable only for followed Pokémon and artists.
- #95 tries grouping Find's results by art across languages (stored arts, else same Pokédex number and
  illustrator), with its known limits.
- Prices never mix languages: each language's card is its own card (prices spec item 1).

## Earlier notes

- From the issue (before this rewrite): needs a design first. An earlier version was removed because its matches
  were often wrong; a match must be reliable or not shown.
- `needs-info` added 16 Nov 2026: waiting on the maintainer's decision on a design that gives reliable matches.

## Open questions

- Is the Languages chip on the card page (arts of followed Pokémon) already enough, or should every card get one?
- What makes a match "reliable" enough to show?
