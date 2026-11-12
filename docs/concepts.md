# Concepts

<!-- A living record of the concepts for this product: terms, rules,
     constraints and assumptions. Update it whenever the solution changes, in
     the same pull request as the work. Name the issue where a concept came
     from. No approval step. -->

Drafted from [docs/specs/](specs/README.md) and [rules/core.md](../rules/core.md).

## Terms

Use these names, exactly as written, in requirements, designs, screens, data and code, so everyone means the same thing.

**Collection**
Everything one install holds. One install holds one collection, shared by every login. ([my-stash.md](specs/my-stash.md))

**My Stash**
The cards the household owns. ([my-stash.md](specs/my-stash.md))

**Copy** (a line in the stash)
One card, one printing and one condition, with a quantity of one or more. The same card in two printings or
conditions is two lines. ([my-stash.md](specs/my-stash.md))

**Printing**
A way a card was printed, such as Normal, Holo or Reverse Holo. Offered only when the card data says the card
comes in it. ([my-stash.md](specs/my-stash.md))

**Condition**
TCGplayer's scale: Near Mint, Lightly Played, Moderately Played, Heavily Played, Damaged. ([my-stash.md](specs/my-stash.md))

**Chase**
A card, or all cards of a followed Pokémon or artist, the household is looking for. A chase is for the whole
card, any printing. ([chases.md](specs/chases.md))

**Binder**
A named group of copies a person can share. ([sharing.md](specs/sharing.md))

**Host**
The person who runs dexSTASH on their own server. The first account is the admin.

**Price source / price quote**
A source writes quotes to `price_quotes`; the shown price comes only from `prices.resolve`. ([prices.md](specs/prices.md))

**Database step**
A numbered `_step_N` in `app/db.py` that changes the database and raises `LATEST`. Older versions won't start on
a database a newer one has changed.

**Catalog database**
`dexstash-catalog.db`: data that can be downloaded again (card lists, price quotes, the card catalog). Never backed up.

## Rules

Behaviour the product must enforce, and the basis for acceptance criteria and tests.

- **Your data stays yours:** a database change never deletes or rewrites what someone typed, collected or uploaded
  without a backup first; shared links show only what the owner chose to share.
- **Backup before upgrade:** every upgrade backs the database up first.
- **One price path:** a shown price comes only from `prices.resolve`.
- **One outside path:** every request to another site goes through `app/sites.py`, which sends the honest
  User-Agent and honours the site switches. A new optional site starts switched off.
- **Speed first:** searches and page loads come before background work.

## Constraints

Limits on what the solution may include or claim. Do not design or build outside them.

- **The core rules** in [rules/core.md](../rules/core.md): nothing personal, no secrets, respect data sources,
  licence and credit, people's data stays theirs.
- **Free to run:** no feature may require a paid service; a paid source is always optional.
- **No scraping:** pages on TCGplayer and PriceCharting are refused in `app/sites.py`.
- **Downloaded data is never committed:** card data, pictures, sprites, text, prices and models.
- **Stack:** Python, FastAPI, Jinja with htmx, SQLite, Docker; no front-end framework and no JavaScript build step.
- **Licence:** AGPL-3.0.

## Assumptions

Guesses that aren't stated in the issues. Check with the maintainer, then move the answer into Terms, Rules or Constraints and remove it here.

- **Binder:** defined here from the sharing spec's wording; check it against the app.
