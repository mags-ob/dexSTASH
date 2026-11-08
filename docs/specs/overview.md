# Overview

## Purpose

dexSTASH is a Pokémon card collection app that a person runs on their own server and uses from a phone. It
tracks what you own and what you're chasing, finds and scans cards, and keeps what your collection is worth up
to date, without sending your collection anywhere.

## Who it's for

- **Collectors** who want to see their cards, sets, binders and chases on a phone, in any language TCGdex
  covers, plus mainland Chinese cards.
- **A household** sharing one collection: several logins, one set of cards, with who added what.
- **Self-hosters** who run it in Docker on a NAS or small server (amd64 or arm64), alone or on a hosting
  service such as PikaPods.

## Behaviour

1. Everything a person keeps (cards, copies, binders, chases, purchases, notes, photos) stays on their own
   server, in `data/`. Backing up `data/` and `.env` backs up everything.
2. One install holds one collection. The first account is the admin; everyone the admin adds shares that
   collection.
3. The app works from a phone browser and as a Home Screen app. Every page also works on a computer.
4. A new install starts with a first-run wizard (password, time zone and currency, languages, sites, downloads),
   then a Getting started list on Home.
5. Card data comes from open sources, downloaded and kept on the server. Prices come from the sources in
   [the prices spec](prices.md) and are updated nightly or when you press Update prices.
6. Scanning is off by default. When it's on, photos are read on the server's CPU; nothing is sent to a cloud
   service.
7. Searches and page loads always come before background work.
8. Every upgrade backs the database up first. An older version refuses to start on a database a newer one has
   changed.

## Rules and limits

- No paid price feeds are required. A source that needs a key (CardTrader, PriceCharting) uses the host's own
  key, entered in the app.
- The app never reads another site's web pages. It uses official APIs, open datasets and files a source
  publishes for reuse, and otherwise links out.
- The [core rules](../../rules/core.md) (privacy, data-source terms, secrets, licence) apply to every part.
- Licence: AGPL-3.0.

## The parts and their specs

A spec is written from the code the first time an issue touches that part, so some of these don't exist yet.

| Part | Kind | Spec |
|---|---|---|
| How the app is built | technical | [architecture.md](architecture.md) |
| Scanning | capability | [scanning.md](scanning.md) |
| Prices | capability | [prices.md](prices.md) |
| My Stash | capability | [my-stash.md](my-stash.md) |
| Binders | capability | `binders.md` (not written yet) |
| Chases | capability | [chases.md](chases.md) |
| Sets and search | capability | [sets-and-search.md](sets-and-search.md) |
| Sharing | capability | [sharing.md](sharing.md) |
| Accounts and first start | capability | `accounts-and-setup.md` (not written yet) |
| Data model and database steps | technical | `data-model.md` (not written yet) |
| Data sources and their terms | technical | [data-sources.md](data-sources.md) |
| Design system | technical | `design-system.md` (not written yet) |
| Deploy and configuration | technical | [deploy.md](deploy.md) |

## Interfaces

- People use it through the web pages (`app/routes/`); `tests/routes.txt` lists every route.
- Hosts configure it through `.env` (see `.env.example`) and Account › Data sources.
- [README.md](../../README.md) is the user guide, [DEPLOY.md](../../DEPLOY.md) the install guide and
  [docs/architecture.md](../architecture.md) the code tour.

## Open questions

- None yet.
