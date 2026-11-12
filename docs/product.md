# Product brief

<!-- A living document. Update it as the solution changes, in the same pull
     request as the work. No approval step. -->

> Drafted from the README and [docs/specs/overview.md](specs/overview.md). Correct anything that's off.

## The problem

Collectors who want to track Pokémon cards usually hand their collection, and the data about it, to a cloud
service, or keep it in spreadsheets that don't show prices, sets or what's still missing. They want to see what
they own, what they're chasing and what it's worth on a phone, without giving it away.

## Who it's for

- **Collectors:** see cards, sets, binders and chases on a phone, in any language TCGdex covers, plus mainland
  Chinese cards.
- **A household:** several logins sharing one collection, with who added what.
- **Self-hosters:** run it in Docker on a NAS or small server (amd64 or arm64), alone or on a hosting service
  such as PikaPods.

## Intended outcome

A self-hosted app that keeps a household's collection on its own server and shows, on a phone:

- **R1:** what each person owns (printing, condition, quantity, cost) and how much of each set is complete.
- **R2:** what they're chasing (cards, a Pokémon or an artist) and how its price is moving.
- **R3:** what the collection is worth, from free or host-keyed price sources, updated nightly.
- **R4:** cards found by name or description and added by scanning photos, read on the server's own CPU.
- **R5:** upgrades that back up first and never lose what someone typed, collected or uploaded.

## What success looks like

- A new host has it running from the README quick start without help, and the first-run wizard gets them to a
  first card.
- A collector can add, find and price a card from a phone in a few taps.
- Nothing a person keeps leaves their server unless they choose to share it.

Priority: R1 and R5 Must; R2 and R3 Should; R4 Could (scanning is off by default).

## Out of scope

- Selling, trading or marketplace features.
- Paid price feeds as a requirement (a source that needs a key is optional and uses the host's own key).
- Reading other sites' web pages; the app uses official APIs and open datasets, and otherwise links out.
- Sending the collection or photos to a cloud service.

## Open questions

- Is the first public release (1.0.0) tied to a feature set? (maintainer)
