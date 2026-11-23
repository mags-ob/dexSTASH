# Sets and search

## Purpose

Find any Pokémon card quickly from a phone, by name in any language, number, set or a description, and open
it on a page that says what it is, what you have of it and what it's worth.

## Behaviour

### Find

1. With no language picked (the default), Find searches English, Japanese and Chinese (Taiwan and Hong Kong
   sets from TCGdex plus the mainland sets stored here) at once and shows one tile per art: the same
   illustration by the same artist in any language (see [chases](chases.md)). A tile's picture is the English
   card when there is one, else the Japanese, else the Chinese; EN, JA and ZH tags on it show the languages the
   art is printed in, filled when you have a card in that language; ✓ when you have any. With Best match, arts
   come newest set first across the languages (each set ranked within its own language). A line says how many
   arts and cards were found, and a tap opens the cover card. The arts are the picture-matched ones (Enhanced
   Search); without them, cards of the same Pokémon by the same artist group (mainland Chinese cards stay
   alone then). A card in no art is a tile of its own. Japanese and Taiwan Chinese are left out until their card
   catalog is here (the page says so); English is searched as one language always was (#95).
   A language is picked only in the Filters Language group (English, Japanese, Chinese or Korean; nothing is
   ticked by default). A picked language shows as a chip under the search box with ✕ (back to every language),
   counts on the Filters badge and sticks between searches. English, Japanese and Chinese still show one tile
   per art, with that language's card as the picture; Korean shows its cards one by one.
2. A search can be a name, a number (`125`), a number with its printed total (`125/197`), or both
   (`charizard 125`). Accents and case don't matter.
3. An English Pokémon name also finds the card in the searched language, from PokéAPI's names list, so
   "Pikachu" finds ピカチュウ.
4. Smart words become filters, each shown as a chip with ✕: printings (holo, reverse holo, 1st edition),
   stamped, shiny, full art, vintage, set names, and others. Unknown words stay in the name search.
5. While typing, close Pokémon names are suggested (by spelling and by sound); a suggestion never changes the
   search by itself.
6. Filters: set, rarity, kind (Pokémon, Trainer, Energy), owned or not, tag, who added it. Sorts: best match
   (the default), Number, Name, Price high to low, Price low to high, Newest set, Oldest set.
7. Results show 24 at a time, up to 400.
8. A search can be saved with a name (up to 30 per login) and runs again from a chip under the Find box. A
   saved search keeps the words, language, set, filters and sort.
9. Once a language's whole card list is in Find's card catalog (fetched in the background the first time
   someone searches in it, and checked nightly for new sets), searches and the rarity and kind filters are
   answered locally. Until then Find asks TCGdex. `DEXSTASH_CARD_INDEX=0` always asks TCGdex.
   Each card's artist, Pokémon and rarity come with its set when TCGdex's GraphQL answers; when it doesn't
   (since Nov 2026 it fails sets with a missing field, and has no Japanese or Taiwan sets), they're fetched one
   card at a time in the background, once, and kept when the set is fetched again. Taiwan cards take their
   Japanese twin's Pokémon, else the one their Chinese name names. Arts are grouped again when that finishes (#95).
10. Prices on results come from stored prices only; a page never fetches one (see [prices](prices.md)).
    An empty Find shows Recently viewed, the cards opened last, as picture and name only, with no price line
    (#66).

### Don't know the name?

11. Describe finds a Pokémon from words like "blue turtle with cannons": the words are matched against its
    English Pokédex entries, genus, colour, shape and types, with colour and type chips to narrow it. Words
    the household adds to a Pokémon are searched too. There's no AI model; the data is PokéAPI's.

### Previews and the card page

12. A result opens a preview of the card before it's stored: picture, printings, price, and buttons to add or
    chase it. A card from a stored set opens its own card page instead. When TCGdex doesn't answer for a
    preview (or a set preview), the page says "Couldn't load this card" (or set) with Try again and Back, and
    the reason goes to the log; it's sent with `X-Dexstash-Page`, so the phone's offline helper doesn't swap it
    for "Can't reach your server" (#77).
13. The card page shows the card's picture (tap for full size), set, number, rarity and printings, its
    prices per printing with where each came from (see [prices](prices.md)), what the household has of it
    (by printing and condition) with add and remove, binders, tags, notes, the Chase box when chased, links
    out to shops and price sites, prices or links typed by hand, and the history of who added and removed
    copies. Under the name, set and rarity (above the price), a chip for each language the card's art is printed
    in (English, Japanese, Chinese), on every card page and preview, followed Pokémon or not: outlined in the
    language's colour, filled when you have a card in that language, the card's own ringed. A tap opens that
    language's card (the one you have, else English, else Taiwan, else mainland). A card in one language shows its
    one chip. Korean and other languages show none (#95).
    A tag chip opens Find with every card tagged with it, except the artist's automatic tag, which searches
    Find for the artist's name, the same as typing it (#67). The artist's tag is the automatic tag whose name
    is on TCGdex's illustrator list kept here; the card page never asks TCGdex for it.
    A name typed in Find counts as an artist when it is a whole artist's name and not a Pokémon, digits
    included ("5ban Graphics").
14. A Japanese or Chinese card shows its English name beside its own, and its set's English name, when one
    is known (TCGCSV's or CardTrader's set lists, or the app's own list). A name is never machine-translated.
    Data sources › Settings picks which names show on Japanese and Chinese cards.
15. A card with no picture of its own borrows one: TCGplayer's product picture, a linked page's, the same art
    in another language, or the household's own photo or scan.
16. A card that no source has (a mainland Chinese promo, say) can be added by hand with its set code, number
    and name, and edited or deleted later.

### Sets

17. Adding a set stores all its cards from TCGdex (for Chinese, the Taiwan and Hong Kong sets first, then
    mainland). A set's preview can be browsed before it's stored.
18. A set page shows every card in number order as tiles, with what you own and ★ on chased cards. Its
    filter picks a rarity, and its sort is Value (the dearest printing first, the default), Number or Name,
    kept per login. A card with no stored price shows the set's TCGplayer list price for the Value sort.
19. Typing numbers on a set page adds cards quickly (see [My Stash](my-stash.md)).
20. A Pokémon's or artist's page is one grid like a set page, with the same Value, Number and Name sorts (see
    [chases](chases.md)). It isn't split into rows by era.

### Searching from Scan and Import

21. When a scanned photo or an import row needs its card picked by hand, Find opens with the photo or row
    carried along, and "Use this" on a result picks it and returns there.

## Rules and limits

- Searches and page loads come before background work.
- Japanese, Chinese and Korean text never goes into a search on another site; links out use the English name
  or the number.
- Smart words, spelling help and Describe use plain text matching only.
- Nothing on these pages is fetched from a site that's switched off.

## Interfaces

- Pages: `/search` (Find) with `/search/more`, `/search/prices`, `/search/suggest`, `/search/saved`,
  `/search/add`; `/describe`; `/preview/{lang}/{card_id}`; `/set-preview/{lang}/{set_id}`; `/sets` and
  `/sets/{set_id}`; `/cards/{card_id}` and its actions; `/cards/new` (a card added by hand); `/find/use`.
- Code: `app/search.py`, `app/card_index.py`, `app/smart.py`, `app/spelling.py`, `app/names.py`,
  `app/describe.py`, `app/saved_searches.py`, `app/english.py`, `app/handmade.py`,
  `app/catalog.py`, `app/tcgdex.py`, `app/display.py` (`set_grid_view`), `app/routes/search.py`,
  `app/routes/sets.py`, `app/routes/cards.py`.
- Tables: `sets`, `cards` (the household's file), `index_sets`, `index_cards`, `index_names` (catalog),
  `user_prefs` (saved searches, set sort).
- Related specs: [My Stash](my-stash.md), [chases](chases.md), [prices](prices.md),
  [data sources](data-sources.md), [scanning](scanning.md).

## Open questions

- Find, My Stash and Chases each have their own search box and filters; making them alike is planned.
