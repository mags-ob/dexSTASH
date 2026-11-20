# 117 Move Find language buttons into Filters

**Issue:** [#117](https://github.com/mags-ob/dexSTASH/issues/117)

## Context

Find shows English, Japanese and Chinese buttons under the search box
(`lang_chips` in `app/templates/search.html`). The Filters panel already has a
Language group with every language (English, Japanese, Chinese, Korean). Korean
and the other non-button languages already show as a removable chip under the
search box and count on the Filters badge (`BUTTON_LANGS` in
`app/routes/search.py`).

The card page's language chips are a different screen and don't change.

## Design

- Remove the language buttons row from Find.
- English is the default (what "Clear all" goes back to). Any other language
  works the way Korean does today: a chip under the search box ("Japanese ✕")
  and +1 on the Filters badge. Tapping ✕ goes back to English. So you can still
  see at a glance which language you're searching, since the choice sticks.
- Set aside: showing nothing for Japanese or Chinese. The language sticks
  between searches, so with no chip you could search in Japanese without
  knowing it.

## Files

- `app/templates/search.html`: drop the `find-chips` row and the `lang_chips`
  import.
- `app/routes/search.py`: the chip and badge rule becomes "any language but
  English" (replace `BUTTON_LANGS` with the default `"en"`); drop `lang_url`.
- `app/static/css/search.css`: drop the `.find-chips` rules.
- `lang_chips` stays in `_macros.html`; Scan still uses it.

## Tests

In `tests/` next to the Find tests:

- Find has no language buttons row.
- Japanese or Chinese shows a "Japanese ✕" style chip whose link goes back to
  English, and the Filters badge counts it.
- English shows no language chip and doesn't count on the badge.
- The Filters panel still has the Language group with the current language
  ticked.

## Docs impact

- `docs/specs/sets-and-search.md`: Find item 1 says the language is chosen
  under Filters and shows as a chip unless it's English.
- `CHANGELOG.md`: one line under Unreleased.
- README, product.md, concepts.md: none.

## Notes

- #95 (search by art proof) planned a trial chip beside the language buttons.
  If this merges first, that trial choice would go in the Filters Language
  group instead.
