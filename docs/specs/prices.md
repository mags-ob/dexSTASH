# Prices

## Purpose

Show what each card is worth, per printing, from free and open sources and the host's own API keys, and keep
how that changes over time for the cards a household has or chases. Every price shown anywhere comes from the
same stored numbers, so a card's tile, its page, a set's value and the stash's worth always agree.

## Behaviour

### Sources

1. A price belongs to one card and one printing (Normal, Holo, Reverse Holo, 1st Edition, Poké Ball, Master
   Ball, ★, ★★ and so on). Each language's card is its own card, so prices never mix languages.
2. The sources, all equal, with none preferred over another:
   - **TCGplayer through TCGCSV** (`DEXSTASH_TCGCSV`, on by default): one prices file per TCGplayer set, English
     and Japanese only. Our sets are matched to TCGplayer's weekly, by printed code and then by name; a set
     that doesn't match is counted on Data sources, never guessed. Cards are matched to products by number.
     The market price is the price; TCGplayer's low and mid are kept with it.
   - **TCGplayer through TCGdex**: only for the printings TCGCSV has no price for (Poké Ball and Master Ball
     printings among them). TCGCSV's own prices are never replaced by TCGdex's.
   - **CardTrader** (the host's token): the median of Near Mint (or Mint) listings in the card's language,
     graded and sealed listings left out. A listing that doesn't say its language is left out and counted. A
     printing with no Near Mint listing gets no CardTrader price. Poké Ball, Master Ball and mainland Chinese
     reverse holo expansions price those printings. It's the only downloaded source for Chinese cards.
   - **PriceCharting** (the host's token, a paid plan): its official API's ungraded price. A card's product
     is matched weekly by English name, set and number, and only when exactly one product fits each printing.
   - **Prices you type**: your own price, and a price you saw on PriceCharting, each for one printing and in
     the currency it was typed in.
3. A source's price for one named printing goes on that printing only. A CardTrader or PriceCharting price
   for the whole card goes on the card's one base printing (Normal or Holo, the one you have or chase, else the
   one the card comes in), and nowhere when there are two. A price tied to no printing is never spread over
   several.
4. When TCGplayer lists exactly one of Normal and Holo, and you have or chase only the other, yours takes
   TCGplayer's price (TCGdex and TCGplayer name some cards' one printing differently).
5. Cardmarket isn't a source. No source is read from a web page: only official APIs and files published for
   reuse (see [data sources](data-sources.md)).

### Working out a printing's price

6. `prices.resolve` is the only place a shown price is worked out. For each printing it takes one price from
   each source that has one and stores, in `prices`:
   - the **price**: the average of the sources counted, used by tiles, sorting, set values, the stash's worth
     and history;
   - the **range**: the lowest and highest of them;
   - the **sources**: each one's price, when it was updated, and what it was converted from.
7. A downloaded price more than 20 times and more than $20 above TCGplayer's for that printing (or above the
   lowest, with no TCGplayer price) is left out of the average and listed as left out. A typed price always
   counts. CardTrader also skips such a listing before it's stored.
8. A typed PriceCharting price wins over the PriceCharting API's for the same printing.
9. A printing you have or chase with no price of its own borrows the closest printing's (for Normal: Reverse
   Holo, then Holo; for Master Ball: Poké Ball, then Reverse Holo, and so on). It's shown marked "≈ another
   printing" and counts in the stash's worth. Printings nobody has or chases don't borrow.
10. Every condition counts at the full price in the stash's worth, except Damaged, which counts at nothing.

### Currency

11. Prices are stored in US dollars and in the install's currency (picked in the first-run wizard and Data
    sources, one of about 30), with the rate used. Pages read the stored amount and never convert while
    drawing.
12. Rates are the European Central Bank's daily reference rates (`DEXSTASH_EURO_RATE`, on by default), fetched
    once a day before the price run and kept by day. Past days' rates are read once from the ECB's history.
13. A price in another currency (CardTrader's euros, a typed price) is converted at the day's rate. One that
    can't be converted yet doesn't count. With no rate for the chosen currency, prices show in dollars.
14. A change of currency saves every stored price again in the new one, and every history point at its own
    day's rate. A point older than every kept rate keeps what it had.

### Which cards keep prices

15. Stored prices are kept for cards someone has, single-card chases, cards of Pokémon you follow, cards
    with a typed price or link, and a scanned photo's matched card while it waits in scan review. Other cards'
    stored prices are removed after each full run.
16. A page never fetches a price. A card nobody keeps shows TCGCSV's price from the last read of its set (its
    own product, in that printing), else TCGdex's stored TCGplayer price, else "Not priced yet", and its set is
    read in the background for the next visit.
17. A grid tile shows the same price as the card's page: the first printing you have with a price, else the
    first priced printing in TCGplayer's order.

### Price runs

18. A full run (Update prices, the nightly run, or `python -m app.sync`) is a job. It fetches the day's
    exchange rates, then each source in turn: TCGCSV per set, TCGdex for the kept cards TCGCSV doesn't fully
    price (and every kept card once a week), CardTrader one request per expansion, then PriceCharting. Then it
    works out every kept card's prices once, removes the prices of cards no longer kept, and records a run
    summary.
19. The run summary records when it ran, `ok`, `partial` (a source had problems) or `failed`, the rates'
    date, and per source what it did and its first errors. The last 30 are kept; Data sources shows the latest.
20. The nightly run is on by default at 04:00 in the install's time zone; Data sources turns it off or moves
    it. After it come the nightly chores: the backup, the picture tidy-up, and thinning history. A night
    missed while the server was off isn't caught up, and a failed run is tried again the next night.
21. A short run prices new cards straight away: after an add, an import, or a scan batch's matches, for just
    those cards' sets, from TCGCSV, CardTrader, PriceCharting and TCGdex. Sets added during a run are queued
    for the next one.
22. Only one price run at a time. Runs give way to page loads (see [architecture](architecture.md)).
23. CardTrader waits a second between requests and stops for the night when asked to slow down twice; an
    expansion it didn't read keeps its last prices.

### History

24. History is kept for cards someone has and single-card chases. Each source's price, each printing's
    average (with its low and high and their sources), and "≈ another printing" each keep their own series.
    A new point is written only when the price changes.
25. History is thinned by the settings under Data sources › Prices › Price history: every change for the
    daily period (30 days by default), one point a week for the weekly period (1 year), then one a month,
    kept for the monthly period (forever by default). A thinned point keeps the average, the lowest low, the
    highest high, how many days it covers, and the amount in the install's currency.
26. Clearing all history is on the same page, after typing CLEAR.

### Value over time and movers

27. The value chart shows the cards you have today at each day's prices, so adding a card isn't a rise.
    Today's point uses the price shown everywhere else. Ranges: 1M, 3M (the default), 1Y and All.
28. A card's price chart shows one printing's average over the range with its daily low and high.
29. Top movers compare each printing you have with its own history from the same source its price comes
    from now. Cards under $1 now are left out; five are shown each way. "≈ another printing" is never a move.
30. Chases show how their cheapest printing moved over 30 days; Home lists chases down 10% or more in a week.
31. Data sources shows how many printings you have or chase each source prices, and how many have no price
    or a stale one (no downloaded price newer than 7 days, a setting). The stash warns when no TCGplayer price
    changed for 3 days while runs kept going.

## Rules and limits

- No paid feed is required. A source that needs a key uses the host's own, entered under Data sources ›
  Prices › API keys; with no key it's never contacted.
- Every source counts equally. No source wins over another, apart from a typed PriceCharting price over the
  API's (8) and TCGCSV over TCGdex for the same TCGplayer printing (2).
- A source is added to `prices.SOURCES` with its fetch and how it ties to a printing; `resolve` and the run
  pick it up from there.
- Prices of one language never stand in for another's.
- Links out (TCGplayer, PriceCharting search, eBay search) are links only; their pages are never read.

## Interfaces

- Code: `app/prices.py` (`SOURCES`, `resolve`, `combine`, history), `app/sync.py` (runs, nightly),
  `app/tcgcsv.py`, `app/cardtrader.py`, `app/pricecharting.py`, `app/tcgdex.py`, `app/fx.py`, `app/extras.py`
  (typed prices), `app/value.py` (charts, movers, thinning), `app/tile_prices.py`, `app/keys.py`,
  `app/web.py` (`_prices_for`).
- Tables: `price_quotes` and `prices` (catalog), `typed_prices`, `price_snapshots`, `price_runs`, `fx_rates`,
  `app_meta`.
- Pages: card pages, grids and tiles, My Stash and set values, Home, Value over time, scan review,
  Account › Data sources › Prices.
- Settings: `DEXSTASH_TCGCSV`, `DEXSTASH_EURO_RATE` (see [deploy](deploy.md)).
- Related specs: [overview](overview.md), [architecture](architecture.md), [data sources](data-sources.md),
  [scanning](scanning.md), [My Stash](my-stash.md), [chases](chases.md).

## Open questions

- eBay sold prices as a bring-your-own-key source is parked until its terms are checked.
- The opening notes of `app/prices.py` and `app/value.py` still describe TCGdex's TCGplayer price as the only
  one; they should be brought in line with this spec when that code is next touched.
