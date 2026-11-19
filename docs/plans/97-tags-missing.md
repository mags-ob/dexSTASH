# 97: Tags missing on some card pages

**Issue:** [#97](https://github.com/mags-ob/dexSTASH/issues/97). The issue decides; this file explains.

Not planned yet. First compare one example card on the test copy and the live copy.

## Context

- Spec: [My Stash](../specs/my-stash.md) item 16 (three kinds of tag), [sets and search](../specs/sets-and-search.md)
  item 13 (the card page).
- Tags: `app/tags.py`, `_card_tags.html`; tables `card_tags`, `card_tag_hidden`.
- The test copy takes the live data when it switches pull requests, so a difference between the two comes from
  the code, not the data.

## Possible causes

From reading the code, not yet checked against a real card:

1. A card from a set that isn't stored (a preview) has no Tags section at all.
2. Tags from a card's details are saved only when its set is stored or refreshed (`app/catalog.py`,
   `app/chinese.py`, through `tags.save_auto`), so a set stored before then, or never refreshed, shows only the
   tags worked out from the stored card.
3. Cards with little detail on TCGdex (some Japanese and Chinese cards, hand-added cards) get few or no tags.
4. Tags removed with ✕ stay hidden until Data sources › Reset tags.

## Open questions

- Maintainer: which card (name and set) shows no tags, and does the live copy show tags for it?
