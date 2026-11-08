# My Stash

## Purpose

Keep track of every card the household owns: which printing, what condition, how many, where it came from and
what it cost, and find any of them again quickly on a phone. Prices are in the [prices spec](prices.md);
binders and sharing have their own specs.

## Behaviour

### Copies

1. A line in the stash is one card, one printing and one condition, with a quantity of one or more. The same
   card in two printings or two conditions is two lines.
2. Printings: Normal, Holo, Reverse Holo, 1st Edition, ★ and ★★ (mainland Chinese), Poké Ball Reverse, Master
   Ball Reverse, and Other. A printing is offered only when the card data says the card comes in it; the ball
   patterns and Other are always allowed.
3. Conditions are TCGplayer's scale: Near Mint, Lightly Played, Moderately Played, Heavily Played and
   Damaged. A copy is Near Mint unless someone says otherwise.
4. A line can also hold where it came from (Pack, Single, Trade, Gift, Other), the price paid for each copy
   (in the currency it was typed in), a binder, and a short note such as a grade.
5. One install holds one collection: every login sees and changes the same stash. Each addition and removal
   is logged with who made it and how (by hand, imported or scanned), and the card page shows that history.

### Adding and removing

6. A card is added from its card page (picking printing and condition; the printing starts on one you own or
   the first one with a price), from scan review, from an import, or as a pull on the Spending page.
7. On a set page, typing numbers adds cards quickly: `45` finds `045`, `045 x3` adds three, `TG05` and `177a`
   work, and `-123` removes one. A quantity is 1 to 99.
8. Removing the last copy of a line removes the line. A card whose copies are all removed leaves the stash
   but keeps its notes and tags.
9. When the first copy of a chased card is added, the app says "You got it!" and asks whether to stop
   chasing it (see [chases](chases.md)).
10. A new or imported card gets its prices from a short price run in the background.

### Finding cards

11. My Stash lists every line across sets as tiles, with search by name (in any language, accents ignored,
    and the English name beside a Japanese or Chinese one) or number.
12. Filters: language, set, rarity, tag, binder (or Not in a binder), how it was added, who added it, where
    it came from, when it was added (today, this week, this month), and price range (under $5, $5 to 9.99,
    $10 to 24.99, $25 to 49.99, $50 and up, no price, estimated).
13. Sorts: Value (the default), Recently added, Set and number, Name, Cheapest.
14. Filters are remembered per login.

### Bulk actions

15. Several lines can be picked, or "Select all" for every line the filters match. Then: set the price paid
    (each, or one total split evenly across every copy), add a note, add them to a purchase on Spending, move
    them to a binder, take them out of a binder, or remove them.

### Tags and notes

16. A card shows three kinds of tag together: automatic ones worked out from the card (rarity group,
    printings, energy type, series, Promo, Vintage), automatic ones from the card's details (kind, stage,
    mechanic, illustrator, stamps), and the household's own. ✕ hides an automatic tag for that card or
    deletes one of your own. Tags belong to the card, so every login shares them.
17. Vintage means a set older than a number of years set under Data sources › Settings.
18. A note belongs to the whole card, not one printing, shows who wrote it, and is at most 500 characters.

### Spending

19. Spending lists what was spent: single cards (the price paid per copy on a line) and purchases such as
    packs or a box, whose cost is shared across the cards pulled from them, unless a pull has its own cost.
    One "Spent before we started tracking" line holds older spending.
20. Amounts stay in the currency they were typed in; totals are converted at today's rate and shown in the
    install's currency.

### Sets

21. My Stash › Sets lists the sets the household has cards in, with how complete each is (cards owned out of
    the set's printed total; secret rares count beside it, not in it) and the household's value in it.
    Sorts: Most complete (the default), Newest, Name, Your value. It can show only complete or only
    incomplete sets.

### Import and download

22. A Collectr CSV export can be imported. The file is read, each row is matched to a set and card (loading
    sets from TCGdex as needed), and rows that don't match can be fixed by hand: a card, a whole set, or
    ignored.
23. Before it's applied, the import shows how the file's quantities compare with the stash. Applying sets
    each line's quantity to the file's, records who imported, and puts purchase prices into one Spending
    entry. Lowering quantities and removing lines missing from the file are choices.
24. Every match is remembered, so importing the same file again lines up without fixing rows again.
25. The stash, or one binder, downloads as a CSV in dexSTASH's own columns or in Collectr's (so it can go back
    into Collectr or be imported here again), with a byte order mark so spreadsheet apps read Japanese and
    Chinese names.

## Rules and limits

- One collection per install. A login never has a stash of its own.
- A line's quantity is always at least one; a line with none is deleted.
- An import only touches the stash when it's applied.
- Damaged copies count at nothing in the stash's worth (see [prices](prices.md)).

## Interfaces

- Pages: `/collection` (My Stash), `/collection/sets`, `/collection/bulk`, `/collection/export.csv`,
  `/spending`, `/imports` and `/imports/{batch_id}`, card pages (`/cards/{card_id}`, tags and notes).
- Code: `app/inventory.py`, `app/collection.py`, `app/tags.py`, `app/notes.py`, `app/spending.py`,
  `app/importer.py`, `app/collectr.py`, `app/export.py`, `app/catalog.py` (`set_completion`),
  `app/routes/collection.py`, `app/routes/imports.py`.
- Tables: `inventory`, `inventory_log`, `notes`, `card_tags`, `card_tag_hidden`, `purchases`, `pulls`,
  `import_batches`, `import_rows`, `import_map`.
- Related specs: [prices](prices.md), [chases](chases.md), [sharing](sharing.md),
  [sets and search](sets-and-search.md), [scanning](scanning.md).

## Open questions

- Binders are part of My Stash today but have their own spec (`binders.md`), not written yet.
