# Specs

Start with [overview.md](overview.md), which lists every part and its spec, and
[architecture.md](architecture.md).

A spec says what one part of dexSTASH does and why, so a change can be planned and checked against it. The
code shows how it works today; the spec says what it should do.

## Two kinds

- **Capability specs** cover one thing a person does with the app, end to end: for example scanning, prices,
  My Stash, binders, chases, sets and search, sharing, and accounts and first start.
- **Technical specs** cover a layer that many capabilities share: for example the data model and database
  steps, data sources and their terms, the design system, and deploy and configuration.

A capability spec links to the technical specs it relies on instead of repeating them.

## What a spec holds

1. **Purpose:** one or two sentences on what this part is for.
2. **Behaviour:** what a person can do and what they see, as short numbered statements that can each be
   tested.
3. **Rules and limits:** edge cases, defaults, what it never does.
4. **Interfaces:** pages and routes, tables, settings, outside sources, and the other specs it depends on.
5. **Open questions:** anything not decided yet.

Keep each spec to one part. File names are lowercase with hyphens, such as `prices.md` or `data-model.md`.

## How specs are kept current

- A spec is written from the current code the first time an issue touches that part. There's no big up-front
  rewrite.
- A pull request that changes what a part does updates its spec in the same pull request.
- An issue's acceptance criteria point to the statements in the spec they test.
