# Chases

## Purpose

Keep track of the cards the household is looking for: single cards, every card of a favourite Pokémon, or
every card by a favourite artist, with what they cost now and how that's moving, and celebrate when one joins
the stash.

## Behaviour

### Chasing a single card

1. Any card can be chased from its card page or a search preview, in any language. Chasing a card from a
   preview stores its set first.
2. A chase is for the whole card, any printing. It records who added it and when.
3. A chased card shows ★ on its set page.
4. A chased card's page has a ★ Chase box: up to 20 links to anywhere (a listing, a video, a shop, labelled
   with the site's name) and up to 12 photos. Its short note is an ordinary card note.
5. Stopping a chase deletes its Chase box (links and photos). The card's notes stay.

### Following a Pokémon or an artist

6. A Pokémon is followed by name or Pokédex number; an artist by the name TCGdex gives them (or as typed),
   with suggestions while typing.
7. Following starts a background job (shown under Running now) that lists the Pokémon's or artist's cards in
   English, Japanese and Taiwan Chinese, plus any loaded mainland Chinese sets, and stores what the chase
   pages need: illustrator, printings, picture and price. For a Pokémon, a card counts when its main Pokémon
   has that Pokédex number.
8. A Pokémon's cards are grouped into arts: the same illustrator and near-identical pictures. A group can be
   fixed by hand (take a card out, add one, undo), and the fixes are kept. ↻ Re-check compares the pictures
   again and keeps the fixes. Check for updates adds new cards.
9. A followed Pokémon's page shows its arts, each with language tags (EN, JA, ZH) and what you own. Sorts:
   Newest first (the default), Oldest first, Price high to low. It can show only owned, missing, chased, full
   art or promo cards, and one language.
10. Cards of followed Pokémon keep stored prices (see [prices](prices.md)), but aren't single chases: they
    have no price history and no Chase box unless they're chased too.
11. Unfollowing removes the follow. When nobody follows it any more, its arts, fingerprints and the cards
    stored only for it go; cards someone has, cards added or taken out by hand, and art fixes stay.

### The Chases page

12. Chases lists every single chase with its price (the cheapest priced printing, "from" when more than one
    has a price) and how it moved over the last 30 days, then the followed Pokémon and artists.
13. Single chases can be searched by name or number and filtered by language and price range. Filters are
    remembered per login.
14. Home shows chases whose cheapest printing fell 10% or more in the last week. Value over time can show
    movers among chases.
15. The chases download is a CSV of single chases: name, English name, set, set code, number, language,
    printing, price with its currency and date, chasing since, who added it, and the Chase box's shop links.
    Prices paid are never in it.

### "You got it"

16. When the first copy of a chased card goes into the stash (by hand, from a scan, an import or Find), the
    person who added it sees a "You got it!" pop-up on their next page. A batch of scans or an import shows
    one pop-up listing them all (the first 20, then "and N more").
17. The pop-up asks whether to stop chasing those cards; Yes stops each one (and deletes its Chase box). Until
    then the chase stays.
18. The waiting pop-ups live in memory: a restart skips them, and nothing else is lost.

## Rules and limits

- Chases belong to the household's one collection; every login sees them.
- Following never stores a card's picture for good: pictures are fingerprinted and dropped.
- A followed Pokémon's cards are listed from TCGdex, and from mainland Chinese sets only when they're loaded.
- Chase box links are only links; nothing is read from them.

## Interfaces

- Pages: `/chases`, `/chases/export.csv`, `/chases/stop`, `/wants/add`, `/wants/{want_id}/remove`,
  `/cards/{card_id}/chase/links` and `/photos`, `/pokemon/follow`, `/pokemon/{dex}` and its art, fix and
  re-check routes, `/pokemon/{dex}/unfollow`, `/artists/follow`, `/artists/suggest`, `/artists/{norm}`.
- Code: `app/wants.py`, `app/chase_box.py`, `app/chase.py`, `app/chase_pages.py`, `app/celebrate.py`,
  `app/export.py` (`chases`), `app/value.py` (`chase_moves`, `cheaper_chases`), `app/routes/wants.py`,
  `app/routes/pokemon.py`.
- Tables: `wants`, `chase_links`, `chase_photos`, `followed_pokemon`, `artists`, `pokemon_cards`, `arts`,
  `art_cards`, `art_fixes`.
- Related specs: [My Stash](my-stash.md), [prices](prices.md), [sets and search](sets-and-search.md),
  [sharing](sharing.md).

## Open questions

- Sharing a chase list with friends is planned and will need a decision on what a visitor may see.
