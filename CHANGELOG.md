# Changelog

Versions follow `MAJOR.MINOR.PATCH`. Until 1.0 each round raises the minor version, and a round shipped in parts raises the patch version for each later part. On start, a new version backs up the database before changing it; an older version won't start on a database a newer one has changed (see "Updating" in the README).

## Unreleased

### Changed
- Release images are also pushed to the container registry, so `docker compose pull` updates (#1).
- **Pillow 13.0.0** (from 12.3.0) and **certifi 2026.10.18**, which fix known vulnerabilities pip-audit reported. Photo reading, pictures and thumbnails were checked with the new Pillow.
- **Images built on GitHub.** Each release carries the image ready-built (amd64 and arm64) to load with `docker load`, and every pull request gets a test image (`pr-<number>`) a host can try before it's merged. `DEXSTASH_IMAGE` in `.env` runs one instead of building on the server. See DEPLOY.md › Using a release image.
- **New checks on every pull request** (the Checks workflow): lint, the core rules, secret scanning and known-vulnerable packages. Contributors see what each one wants in AGENTS.md and the rules folder.

## 0.28.0

Round 28: prices for cards nobody keeps, sets matched to TCGplayer correctly, more card names in English, and API keys entered in the app. No database step (upgrade tasks 8 and 9).

**After updating:** if your `.env` has `DEXSTASH_CARDTRADER_TOKEN`, it is moved into Data sources › Prices › API keys at the first start. Check it's listed there, then remove the line from `.env`. Every set's TCGplayer products are read again at the next price update.

### Fixed
- **Cards you don't keep show a price** when TCGplayer has one. A card's price is looked up by the TCGplayer product TCGdex names for it first, in whichever TCGplayer set it was read, and by its number only when there's none. A card page uses TCGdex's own TCGplayer price when TCGCSV has none.
- **Sets matched to the wrong TCGplayer set** (Battle Styles was matched by the code BST to another set): a set code with zeros (SWSH05) matches swsh5, the name wins over a printed code that points elsewhere, and each set's match is checked against its cards' product ids when its products are read, so a wrong one is fixed by itself. Kept cards in a set matched wrongly are priced again.
- **Two TCGplayer products with one number** (POP Series 1's Holo and Non-Holo Tyranitar ex): the one TCGdex names is the card's.
- **A new set is matched to TCGplayer when it's stored**, not at the next weekly set-list read.
- **A TCGplayer price with only a lowest listing** (no sales, no mid) is used, labelled "TCGplayer · lowest listing".

### Added
- **API keys** on Data sources › Prices: CardTrader and PriceCharting, each with Save, Test and Remove. A key is stored encrypted with the install's secret key in `data/api_keys.json`, outside the database (backups, restores and exports never hold one), and only its last 4 characters are shown again. eBay sold prices is listed, greyed out, as coming soon. Only the admin sees the section.
- **PriceCharting prices**, through PriceCharting's official API with your own token (a paid plan): each kept card's product is found once a week (number, set and language must match; a bracketed printing like [Reverse Holo] is that printing) and its ungraded price read once a day. A price you type for PriceCharting wins over the API's. Without a token PriceCharting stays a link, and its pages are still never read.
- **Sets nobody matches**: Data sources lists your sets that matched no TCGplayer set, with a picker, and your sets with no price source at all (neither TCGplayer nor CardTrader), with Search links. Unmatched sets are tried weekly against TCGplayer sets with a similar name, by their cards' product ids.
- **More card names in English** from the Pokémon: TAG TEAMs (Pikachu & Zekrom GX), Radiant, XY-era M…EX, Mega X and Y, V-UNION, BREAK, LV.X, ☆, δ, and forms like Teal Mask Ogerpon and Single Strike Urshifu.

### Changed
- `.env.example` no longer lists `DEXSTASH_CARDTRADER_TOKEN` (a token already there keeps working until it's moved in).

## 0.27.0

Round 27: prices in scan review before you add a card, smarter Search links, English names for Japanese and Chinese sets, chase hearts on tiles, and Find's prices matching the card page. Database step 19.

**After updating:** if you use the Home Screen app in Light mode, remove its icon and add it again, so iOS uses the light status bar. iOS reads that only when the app is added, so do the same after switching between Light and Dark.

### Added
- **Prices in scan review before Add.** Once a batch's photos have their matches, the matched cards are priced in the background: TCGCSV and CardTrader for their sets, as after you add a card. Scan details lists each source's price with a link to where it came from ("Checking CardTrader…" while it runs; "No price found yet" when no source has one). Matched cards keep their prices while their photos wait; a skipped or deleted photo's card goes at the next clean-up. Data sources' Activity log lists each batch's job.
- **CardTrader in the short run after you add cards**: an added card gets its CardTrader price straight away (with a token).
- **Search CardTrader** on the card page, scan review and shared binders; the CardTrader card page itself when a price came from there.
- **English names beside Japanese and Chinese set names**, e.g. "Triplet Beat · 強化拡張パック トリプレットビート". They come from TCGplayer's Japanese set list (TCGCSV) and CardTrader's expansion list, both already downloaded; a set with neither shows its own name only. Data sources › Settings › "Names on Japanese and Chinese cards": English and original (default), English only, or original only. Find finds these sets by their English names too. Card text (attacks, Trainer wording) stays as printed.
- **Chased cards have a rose heart and ring** on tiles in Find and on set pages (it was a star); a card you own and chase keeps the green ring.

### Changed
- **One price rule for tiles.** Find's and set pages' tiles show the card page's main price: the printing you have, else Normal, Holo, Reverse Holo, 1st Edition in that order. A card nobody keeps uses TCGplayer's price for its own product, so Neo Discovery Tyranitar shows its Unlimited price, not 1st Edition's. Sorting a set by value uses the same price.
- **Find fills in missing prices.** Sets in the results with no prices yet are read in the background, one at a time behind your own pages; their tiles say "Checking price…" and fill in without a reload.
- **Search links use each site's own words**: TCGplayer gets name, number and set; PriceCharting name, set, the number without leading zeros and the language; Japanese and Chinese cards use the English set name when there is one.

### Fixed (from testing round 26)
- **Light mode's top strip** in the Home Screen app: an older rule still painted it dark.
- **A tag search shows only its cards**, not Recently viewed under them.
- **Describe a Pokémon keeps one size** while its results change, so the chips don't move.
- The eBay site picker is in Data sources › Settings (the notes said Prices).

## 0.26.0

Round 26: fixes and small features from the November audits. Database step 18.

**After updating:** glance at your shared binders once. Older versions could leave a deleted card's slot in a binder, where a new card could take it over; step 18 clears those, so a shared binder may show one card fewer than before. The app container now has a 2 GB memory cap (`DEXSTASH_APP_MEMORY` in `.env` changes it); `docker compose up -d --build` applies it.

### Fixed
- **A deleted card leaves its binder.** Its binder slot goes with it, so a card added later can't appear in someone else's binder in its place.
- **Price boxes refuse "inf" and "nan"**, and a Collectr file with such a cost, or a quantity that isn't a number, skips that row and says why.
- **Shared binders on one busy Wi-Fi**: a visitor who already entered the code isn't locked out because someone else on the same network typed it wrong. Only wrong codes count towards the lockout.
- **Scan language**: a card with only a little Japanese or Chinese text (a name, a number) is no longer read as that language, and an unsure photo also tries the batch's usual language and English. Sending the same photo twice is ignored quietly.
- **Scan photos no longer show cut off** while the row updates on an iPhone.
- **Light mode's top strip** on an iPhone Home Screen app is light, not dark.
- **Back goes back a page**, not through each filter chip you tapped.
- **Red text is readable in Dark mode** (a lighter red).

### Added
- **Bigger card pictures**: the card page's big view and scan review's lightbox use the large picture and fill the screen.
- **Compare a scan match**: the magnifier on each match shows your photo next to the card, with "Use this card".
- **Search eBay sold** on the card page, shared binders and scan review: a search of sold, ungraded, near-mint listings. Pick the eBay site in Account › Data sources › Settings (ebay.com unless you change it).
- **Did you mean** in My Stash when a search finds nothing.
- **Tags open Find** for every card with that tag.
- **Describe a Pokémon by two types**; a third tap drops the oldest.

### Changed
- Scanning help lists the languages it works out on its own (English, Japanese, Chinese) and how to mark a Korean card.
- The scanning switch says why On isn't available when the container has under 1.5 GB of memory.
- The welcome's "where to start" step offers Collectr or Not now.
- Home's value and movers boxes are the same height on a computer.
- The "Preview only" line on a card you don't have is gone.
- Tests run on every push and before a release image is built; they also run on Windows.
- Tidying: settings read the same way everywhere, unused code removed, smaller labels raised to 11-12px.

## 0.25.1

Round 25, second part: fixes from testing the design polish.

### Fixed
- **My Stash's tabs fit a phone screen**: the five tabs share the width, so Spending is no longer off the edge and the bar doesn't scroll sideways.
- **My Stash's tabs sit in the same place on every tab** on a wide screen (centred, as wide as the labels), on Cards, Binders, Sets, Chases and Spending alike.
- **More room at the bottom of pages** on a wide screen.

### Changed
- **Add and Chase are under the price on a computer too**, as on a phone; they left the top bar, which is less crowded.
- **Card page on a computer**: your copies and the chase stay in the centred column. The chase is its own section under your copies, not beside them, and its heading's tint is lighter, like the copies' headings.

## 0.25.0

Round 25: a design polish so the app looks less generic. No feature, page layout or database change.

### Changed
- **Theme colours stay on the Pokémon tile.** The menu bar, the value card, segmented controls and chips are neutral surfaces with a thin line. The theme gives one accent: the main buttons, Update prices, the open tab and the selected chip (a light tint). A selected segment is a raised neutral piece. Chases use one rose colour.
- **Four corner sizes** (4, 8, 12 and 16) instead of pills; only real circles stay round.
- **Lighter type**: weights 400, 500 and 600 only. The uppercase, letter-spaced labels are now sentence case. Prices use even-width numbers.
- **The phone menu bar is docked** to the bottom edge with a hairline, not floating. My Stash's tab has a line icon like the others; the box logo stays the brand and app icon.
- **Add and Chase on the card page** are buttons in the row under the price instead of floating round buttons.
- **A missing price is quiet**: "No price yet" in muted text, and Cards to review's reasons are plain text.
- **Collector numbers in IBM Plex Mono** (shipped with the app, SIL Open Font License) in grids, lists, Home, the card page, scan review and shared binders. The no-picture card is flat with its number in mono.
- **Smaller clean-ups**: neutral shadows (none in dark mode), see-through lines, no header blur, no gradients, 4px spacing steps, a slight shrink when pressed, 120 ms animations with one easing, a small spinner instead of the fake page-loading bar, an accent-tint filter count.
- **Home's Update prices** is a plain round button with the refresh icon. "Prices updated never" reads "No price update yet", and the value lost its stray "›".
- "🎉 You got Charizard ex!" reads "You got Charizard ex" (the confetti stays).

## 0.24.0

Round 24: Home's price area (option B from the mockups).

### Changed
- **Your Pokémon of the day stays at the top of Home**, with its big sprite, number, name and colours. It now also shows a **fun fact of the day** about that Pokémon and a **PokéDoku of the day** link. Facts come from the English Pokédex entries already downloaded for Describe a Pokémon (nothing new is downloaded) and take turns, one a day. With "New every day" the fact goes with that day's Pokémon. The PokéDoku link only opens pokedoku.com; nothing is read from the site.
- **The green card**: the total value, its change with the date it's measured from ("since Oct 1"), the number of cards on the line under it, the Update prices button top right, and the most valuable card. **Spent left Home**; it's still under My Stash › Spending.
- **Cards to review** comes straight after the price area, before Recently added.
- **Your Pokémon (the Pokémon you follow) left Home**; they're still on Chases.

### Added
- **Today's movers** on Home, beside the green card on a wide screen and under it on a phone: the 3 cards that went up most and the 3 that went down most since the day before, by amount, with Up and Down tabs. Cards under the usual minimum price are left out, as on Value over time. See all opens Value over time's Top movers since the day before (`/value?moves=1d`).

## 0.23.0

Round 23: one job system. Background work runs one job at a time, pages come first, and a new install waits for its basic downloads.

### Changed
- **One job system** (`app/jobs.py`). Every long piece of work is a job in one table: price updates, the nightly chores, saving card pictures, looking pictures up, Find's card catalog, import matching, and the jobs Data sources starts. Each process (the app, the scanner) runs one job at a time: the ones you asked for first, then scheduled ones, oldest first. "Running now" lists them all, with what's waiting its turn.
- **Pages come first.** While a page is being answered, background jobs wait between their steps and before each download, a few seconds at most each time. Status checks (Running now, picture and price polls) don't count. Scanning works as before.
- **A restart stops jobs.** Scans pick up where they were. Every other job that was running or waiting shows "Stopped by a restart", and you start it again yourself. An import that was matching gets a Start matching again button. A night missed while the server was off isn't caught up: prices update the next night, or when you press Update prices.
- **One scheduler** adds the nightly update and then its chores (backup, picture tidy and weekly recheck, history roll-up, Value over time) as jobs.
- **The nightly update asks TCGdex less.** It asks only about cards TCGCSV doesn't price and cards with no picture, and about every card once a week.
- **Each card's prices are worked out once per run**, at the end, not again after each source.
- **The picture saver wakes only when your stash, chases or followed Pokémon change**, and picks the cards that are due in the database.
- **Today's prices in your currency are rewritten only when the rate changed.**
- **The scanner looks at the database only when something changed** (SQLite's change counter), not every 2 seconds.

### Added
- **Getting your stash ready**, after the wizard's Finish. It downloads the basics one at a time: exchange rates and TCGplayer's set list, Pokémon names and sprites, Find's card catalog (English first, Japanese last), then mainland Chinese sets if picked. Continue opens when they're done. The first price update, Bulbapedia descriptions and Enhanced Search then run in the background.
- **Database step 17**: the scanner's `worker_jobs` become rows of `jobs`.

## 0.22.0

Round 22: startup clean-up. Nothing changes on screen unless an upgrade fails or takes a while.

### Changed
- **The app starts in three phases** (`app/startup.py`), from its start-up hook, never just by importing it. The activity log starts first, so upgrade messages show under Data sources › Activity log. Each phase logs how long it took.
- **One list of after-upgrade tasks** (`app/upgrades.py`), each run once and recorded, next to the numbered database steps. It replaces the price re-work flag, 0.20.2's TCGplayer re-read flag and the one-offs that ran at every start.
- **Slow upgrade work runs in the background.** The app opens at once, and pages say "Finishing the upgrade" until it's done.
- **Every-start tidies run at night instead:** forgetting prices of cards nobody keeps, and Chinese pictures nobody needs.
- **Database step 1 is one transaction.** A failure part way leaves an old database as it was.

### Added
- **"The upgrade didn't finish" page.** If the upgrade at start fails, every address shows the error and which backup to put back, instead of the app stopping with only a log line.
- **A test that an upgraded database matches a new one**, from the tables 0.11 made.

## 0.21.0

Round 21: speed fixes. Nothing changes on screen.

### Changed
- **Asking for pictures from Find doesn't hold up other pages.** Its database writes run on a worker thread.
- **Set, binder and My Stash grids ask the database less.** Each tile uses the extras the page already loaded.
- **Scrolling My Stash doesn't build the whole list again.** The next tiles come from the list the page showed (kept for 10 minutes); loading the page builds it afresh.
- **A followed Pokémon's card stored in a set has one copy of its prices,** in the prices table, and isn't fetched a second time by Update prices.
- **Query results are read as they're used** instead of copied into memory first.

### Database
- **Step 16:** an index on the stash by card and printing, and the second copy of followed cards' prices removed. 0.20.x won't start on the database afterwards.

## 0.20.3

### Changed
- **The offline page speaks to any device:** "this device just can't reach it right now. Check your connection, if the server is on, and try again."

## 0.20.2

### Fixed
- **Japanese cards listed with other patterns get TCGplayer's price.** Sets such as MEGA Dream ex list each card beside its "(Quick Ball Pattern)", "(Love Ball Pattern)" or "(Energy Symbol Pattern)" copies, and the card wasn't matched at all. Patterns other than Poké Ball and Master Ball are now left out, and every set's products are read again at the next price update.
- **"Some problems" says which.** A problem with Find's card catalog or the cards of Pokémon you follow is now listed under Last price updates, with its first errors.

## 0.20.1

Rounds 20b, 20c and 20d together: reliable runs, history and currency, flags.

### Changed
- **CardTrader's price is the median of its Near Mint listings** in the card's language (of the cheapest 25 or so its API sends), not the single cheapest. A printing with no Near Mint listing gets no CardTrader price; a listing that doesn't say its language is left out and counted. The "far too high" check compares dollars with dollars.
- **A CardTrader slowdown no longer wipes prices.** Only the printings whose listings were read are replaced; the rest keep last night's.
- **Pages never look prices up.** A card page, preview or Find result shows the stored price, else TCGCSV's from the last read of its set, else no price yet, and the set is read in the background. Adding a card doesn't wait on TCGdex: the short run prices it.
- **Price history keeps each day's low and high** (with their sources) and how many days a point covers. Thinning old history into weeks and months keeps them, and keeps the amount saved in your currency (it was lost before). The card page chart shades the low-to-high band behind the average.
- **Changing currency redraws all of history** at each day's own exchange rate. Points older than every kept rate keep what they had.
- **Cards to review** lists printings you have or chase with no price, a stale price (newest downloaded price older than 7 days, a setting in Data sources › Settings), and PriceCharting prices over 90 days old, with a filter for each.

### Added
- **A summary of each price update** on Data sources › Prices: when, whether it went fine, had problems or stopped, and per source what it did and what went wrong. The last update for cards just added has its own line.
- **Every day's exchange rates are kept** (`fx_rates`). The first update after installing reads the ECB's history file once for past days. Running `python -m app.sync` by hand loads the saved rates first.
- **A coverage line** on Data sources › Prices: how many of your printings each source prices, and how many have no price or a stale one.

## 0.20.0

Round 20a: correct prices. Each printing's price comes only from the sources that price that printing.

### Changed
- **No more card-level prices spread over every printing.** A printing's price is the average of the sources that priced it: TCGplayer (from TCGCSV, else TCGdex), CardTrader and what you typed for it. CardTrader's listing for the whole card counts only for the card's one Normal or Holo printing. A printing with none of its own uses ≈ its closest printing's, as before; a printing you chase as a specific printing does too now.
- **Ex cards saved as Normal** get TCGplayer's Holofoil price when TCGplayer lists only that one printing (and the other way round).
- **A typed price is for one printing.** Your price and the PriceCharting price (also in "Found it on PriceCharting?") ask which printing, defaulting to the one you own. Each is listed under its printing with Remove. On the first start each typed price moves to the printing you own, or the card's first printing; Data sources › Prices lists the cards it had to guess for.
- **TCGdex's TCGplayer price only fills printings TCGCSV has no price for**, and never replaces TCGCSV's.
- **TCGCSV:** a good read replaces a set's prices, so a printing TCGplayer stopped listing loses its price; a failed or empty read keeps the last ones. Poké Ball and Master Ball pattern products price those printings. Each price keeps its TCGplayer link and TCGplayer's own updated date.
- **New and imported cards are priced straight away.** After an add or an import a short update reads only those cards' sets (matched to TCGplayer, products and prices, then TCGdex for gaps); adds close together share one. CardTrader for them waits for the next full update. The first-run wizard starts one full update once its downloads finish.
- **Cardmarket's price code is gone** (the 0.19.2 switch, the movers fallback and the card page's Cardmarket line). Search Cardmarket stays.
- One list of price sources (`prices.SOURCES`) drives the update, the price math and the history keep-list.

### Database
- Step 15: source prices keep the source's updated date and link; a daily exchange rate table (`fx_rates`, seeded with today's); history and price runs get columns used from 20b and 20c; TCGCSV's card table keys a card by printing (rebuilt from downloads); `typed_prices` holds typed prices per printing. An older version won't start on the database afterwards.
- The first start works every price out again and keeps the price history (only a database from before 0.19.2 loses its Cardmarket history).

## 0.19.2

### Changed
- **Cardmarket's prices are off for now.** Its trend price isn't counted in a card's range or average, isn't shown, and isn't kept in price history; cards it was the only price for show no price until another source has one. The Search Cardmarket link stays.
- On the first start, every price is worked out again without Cardmarket, and the saved Cardmarket history and the averages it was counted in are cleared. Charts and movers carry on from the TCGplayer history until new averages build up.

## 0.19.1

### Changed
- **CardTrader's link** shows on the card page whenever the price update found a CardTrader price for the card, for any of its printings.
- **Cardmarket's link follows TCGplayer's and PriceCharting's rules.** Search Cardmarket always shows; after a search it asks for the card's link, and once you save one, the button opens that page instead. Nothing is read from Cardmarket.

## 0.19.0

Round 19: tabs, downloads, Home Screen and clean-up.

### Changed
- **Prices are converted when they're fetched, not when a page draws.** At each price update every source (Cardmarket's euros, CardTrader, a price you typed in another currency) is converted at that day's ECB rate and saved in US dollars and in your currency, with the rate used. A source with no rate yet isn't counted. Pages only show what's saved, in your currency: no euro amounts anywhere. The nightly update fetches the rates first and saves every price again at the new rate; changing the currency does the same in the background. Price history keeps each day's amount in your currency.
- **Data sources is in five tabs:** Cards (Update everything, Card sets, Pokémon data), Prices (Prices, Price and picture sites), Pictures & scanning, Settings and Activity log. The page opens on the tab you last used, and a link to a section opens its tab.
- **Toggles are pills.** Light/Dark, Card/Notes, Scanning Off/On, Pages/Grid, the scan review tabs and the Data sources tabs: the chosen side sits inside the pill in the theme's highlight colour.
- **Card page on a wide screen** keeps the phone layout in a centred column, with your copies and the chase card in a grid beside each other.
- **Chase or Add on a Find preview** replaces the preview in the back history, so Back doesn't land on it again.

### Added
- **Remove on the Select bar** in My Stash and binders. It asks first ("Remove 3 cards (5 copies) from your stash?"); in a binder it asks whether to take them out of your stash or only out of this binder. Chases, notes, tags and history stay.
- **Download as CSV:** your stash (Account), a binder (its ⚙ menu) and your chases (Chases page), in dexSTASH's columns or Collectr's. The file opens in Excel and Numbers with accents intact.
- **Home Screen app:** launch screens for every iPhone and iPad size, more icon sizes (maskable and monochrome too), shortcuts, the phone's status bar over the header, and a "Can't reach dexSTASH" page that tries again by itself when the server is off. A tap that can't reach the server says so in a bar instead of doing nothing. Account has the Add to Home Screen steps.

### Security
- **A strict Content-Security-Policy** on every page: only the app's own scripts run. The inline scripts and `onclick`-style handlers moved into the app's script files.

### Database
- Step 13 saves each price in your currency with the rate used. Step 14 rewrites every stored time in one format, `2026-10-28T19:56:42Z`. The first start works every card's price out again.

### Internal
- Code comments say what the code does; version history is only here.

## 0.18.2

### Fixed
- **Find no longer gives up with "Couldn't search TCGdex" while TCGdex is slow.** A set TCGdex wouldn't send kept Find's card catalog from being used at all, so every search went to TCGdex and waited on it. Now a set that failed in the last two days doesn't hold the rest back (it's tried again at each fetch), and a search TCGdex doesn't answer is answered from the catalog's cards so far.

## 0.18.1

### Changed
- **A shorter `.env.example`.** One line for each setting, in groups, with a short note beside it. Every setting is listed, commented out at its default, except the time zone and the data folder's owner. Nothing changes for an existing `.env`.

## 0.18.0

Round 18: a price from every source.

### Changed
- **Every source counts.** TCGplayer's market price, CardTrader's lowest listing, Cardmarket's trend price (converted from euros), your typed price and the PriceCharting price you typed all count toward a printing's price. Before, the first one found was used. A downloaded price far above the others (20 times and $20 over TCGplayer's) is left out. A price you typed always counts.
- **Card page:** the big number is the range from the lowest source to the highest, with "Average · N sources" under it. **Every price** lists each source with its date, your typed price included.
- **Everywhere else** (Find, Pokémon pages, My Stash, binders, sorting, your stash's worth, set values, Home) uses the average.
- **Price history** keeps each printing's average from now on, and the charts, top movers and Home's 30-day change read it. Before the update they carry on from the TCGplayer price history you already have.

### Added
- **Search Cardmarket** link on the card page when Cardmarket has a price (unless you saved a Cardmarket link of your own).

### Database
- Step 12 adds the range and sources to each price. The first start works every card's price out again.

## 0.17.2

### Changed
- **Cards you don't have show any price we know** instead of "No price": on the card page, TCGplayer's price from TCGCSV's last run comes first (for sets you keep cards from), then TCGdex's, then Cardmarket's converted from euros. Pokémon pages and Find use Cardmarket's too when TCGplayer has none.

## 0.17.1

### Changed
- **Cardmarket's price counts when there's no other.** A card with no TCGplayer, CardTrader or typed price (Galarian Gallery cards before the first price update, for example) now shows Cardmarket's trend price, converted from euros into your currency, as its price, with "Cardmarket · today" under it. It counts in your stash's worth. Any other price still comes first, and no dollar price is made up before the euro rate is known.

## 0.17.0

Round 17: the card page, binder pages, Pokémon eras, My Stash, and turning scanning off.

### Added
- **Binder pages.** A binder opens as pages of 4, 9 or 12 pockets, one copy in each, with empty sleeves after the last card. Swipe or tap Turn page; the page is kept in the address, so Back comes back to it. On iPad and PC two pages show side by side. The header says how many cards, their worth and the order, with Share and a gear (name, pockets a page, order, Delete binder). A **Pages / Grid** toggle switches to the usual grid with search, Filter and bulk select; each login's last choice is remembered. In Pages view the search icon finds a card by name or number and opens its page. Not in a binder and the shared link get pages too, in the owner's layout and order (database step 11).
- **Scanning switch.** With `DEXSTASH_SCANNER` not set in `.env` (the new default), Account › Data sources › Scanning turns scanning on or off with no restart. On reads photos inside the app's container at the lowest priority (about 2 GB of memory). When `.env` sets it, that wins and the switch is greyed out.
- **"You got it!"** When the first copy of a card you're chasing goes into your stash (by hand, a scan, an import or Find), a pop-up with a short confetti burst asks whether to stop chasing it. Several at once (a batch or an import) share one pop-up. Phones set to reduce motion get no confetti.
- **Search found nothing:** a number on its own lists that number in other sets, newest first; when a filter leaves nothing there's a heading, Search without the filter and close matches worked out without it. "Add it by hand" shows with a set filter too.

### Changed
- **Card page.** One price with a chip saying where it came from and how old it is ("TCGplayer · 2 days ago", "Your price · 12 Oct", "≈ Holofoil price"). Link buttons under the top, then + Link. A "Things to check" box when a card you have has no price for any printing. Card and Notes & history tabs (`#notes` opens Notes). A Printings row (✓ the ones you have). One card per printing and condition you have, with an Edit sheet for all or one copy: printing, condition, binder, where you got it, paid and a copy note. A chase is its own card with Where to buy and Photos (♥ instead of ★). On iPad and PC the page is two columns, with Add and Chase in the top bar.
- **Pokémon and artist pages** show arts in rows by era, newest first, each with View all. The language moved into Filters, and Show has Owned, Missing, ♥ Chased, Full art and Promo. Newest first is the default order.
- **"My cards" is "My Stash"** everywhere, and the tab has its name under the box.
- **Scanning off** hides the Scan tab and links, so there are three tabs.
- Every form that comes back to the page it was sent from replaces that page in history, so Back doesn't land on it twice.
- Searching My Stash and binders by a Pokémon's English name also finds its Japanese, Chinese and Korean cards.
- Recently viewed shows 9 cards.
- Going back to 0.16.0 after this means restoring the backup made before the upgrade.

## 0.16.0

Round 16: a first-run wizard.

### Added
- **First-run wizard.** After their first sign-in, the first admin goes through `/welcome` before any other page: change your password (always first, however the account was made; an account from `.env` is also told to take `DEXSTASH_ADMIN_PASSWORD` out), time zone and currency, the languages you collect with the extra sites and the nightly price update (and whether a CardTrader token is set in `.env`), the downloads, your first cards, and an optional login for someone you collect with. There's no Skip. Anyone else who signs in before it's finished sees that the admin is still setting up.
- **Downloads step.** Each download shows its estimated size and time, with a running total of what's ticked: Find's card catalog for each language picked, Pokémon data (the Pokédex and sprites, about 13 MB), Bulbapedia descriptions (with that site on), the mainland Chinese sets (with Chinese (mainland) and the Chinese datasets ticked) and Enhanced Search (only when scanning is on; off unless ticked). They start in the background when you press Finish.
- **Getting started** on Home after the wizard: basics, card catalog, Pokémon data, 10 cards and a first binder, each ticking itself, with a hint to pick your theme once the sprites are in. Hide removes it for good.

### Changed
- An install already in use (cards, chases or scans, or more than one login) is marked as set up (database step 10), so it never sees the wizard. Going back to 0.15.3 after this means restoring the backup made before the upgrade.
- Data sources › Pokémon data › Download missing only fetches Bulbapedia descriptions while that site is on.

## 0.15.3

The last part of round 15: one price table, and a card catalog on your own server for Find.

### Added
- **Card catalog for Find.** Every card of every TCGdex set in a language is kept in `dexstash-catalog.db` (one request per set, with a full-text index of names and artists). Once a language has every set, Find, the search behind the scanner's close matches and number searches answer from it instead of asking TCGdex, and Find's rarity and kind filters no longer fetch cards one by one. A language is fetched in the background the first time someone searches in it; the price update fetches new and grown sets each night. Data sources › Prices shows what's kept. `DEXSTASH_CARD_INDEX=0` turns it off.
- Find prices cards nobody keeps from TCGCSV's stored product lists before asking TCGdex.

### Changed
- **One price table.** Every downloaded price (TCGplayer's through TCGCSV or TCGdex, CardTrader's lowest listing) is kept as a quote, and one rule picks the price each printing shows: TCGplayer's, then CardTrader's, then your price or the PriceCharting price you typed, then ≈ another printing you have. Each shown price says where it came from (database step 9; every price you saw before is kept).
- CardTrader matches more cards: Classic Collection cards by their first printing's number ("NVI 101"), promos numbered with their set code ("MEP 101") by name, and cards added by hand when CardTrader's name agrees.
- `.env.example` lists every setting, and the README, DEPLOY.md, CONTRIBUTING.md and the architecture notes and diagrams describe the price rule and the card catalog. Going back to an older version is now described as it works: put the pre-upgrade backup back by hand, then install the older version.
- Going back to 0.15.2 after this means restoring the backup made before the upgrade.

## 0.15.2

Ready for one-container hosts like PikaPods, and fixes from the architecture review.

### Fixed
- A new scan could show an older scan's photo: deleting a batch frees its scan numbers, and the phone kept the photo for an hour under the same address. Each photo now has its own address.

### Added
- **Scanning modes** (`DEXSTASH_SCANNER`): `off` (the default for new installs: one small container, about 0.5 GB), `inside` (one container; the photo reader runs as a lowest-priority child of the app, started again if it stops, and not at all under 1.5 GB of memory) and `separate` (the scanner container, now started with the `scanner` Compose profile). `external` and `inline` still work.
- The setup code is also written to `setup-code.txt` in the data folder until the first account exists, for hosts where you can't read the log.
- **Rebuild downloaded data** under Data sources › Prices › ⋯.
- A **CardTrader** link on a card's page when the price update matched the card there. It only opens the page.
- Images are also tagged with the minor version (`0.15`), alongside `0.15.2` and `latest`. The README has memory guidance and update notes.

### Changed
- Card data and prices downloaded from TCGdex, TCGCSV and CardTrader, and the sprite colours, move to `dexstash-catalog.db` next to the database (database step 8), so backups hold only your own data and are much smaller. The database is compacted once afterwards.
- The app reads the visitor's address and https from its reverse proxy itself (from `DEXSTASH_PROXY_IPS` only), so it works behind a host's proxy on another domain, and the sign-in cookie is marked secure there.
- Tables and columns left by page reading and the old English price are removed (database step 7). An English name found for a Japanese card is kept on the card.
- Going back to 0.15.1 after this means restoring the backup made before the upgrade.

## 0.15.1

dexSTASH stops reading other sites' pages, and adds CardTrader's official API.

### Removed
- Reading TCGplayer's and PriceCharting's pages, in every form: the link finder (and TCGplayer's search service it used), reading prices and pictures from pasted links, the nightly PriceCharting queue, "Links to confirm" and "Needs an English name". Requests to either site are now refused in `app/sites.py`; only TCGplayer's product pictures, listed by TCGCSV, are fetched.
- The TCGplayer and PriceCharting switches under Price and picture sites (there's nothing left to switch).
- Pictures saved from PriceCharting's pages before no longer show.

### Added
- **CardTrader prices**, with a token in `.env` (`DEXSTASH_CARDTRADER_TOKEN`). The lowest Near Mint listing in the card's language, else the lowest of any condition (marked). It shows beside TCGplayer's price on English and Japanese cards, prices printings TCGplayer has none for, and covers mainland Chinese sets. Reverse holo, 1st edition, Poké Ball and Master Ball printings are priced only from listings of that printing. Sets are matched by code or name; Data sources lists any that didn't match, to pick. A refused or expired token is named on Data sources, and the other prices carry on.
- **Price on PriceCharting**: when you paste a PriceCharting link you can type the price you see there. It shows as "PriceCharting · you, 26 Oct", joins the list of prices, and is the card's price only when TCGplayer and CardTrader have none. Cards to review lists cards whose PriceCharting price is over 90 days old.

### Changed
- Prices read from PriceCharting before are kept as that card's PriceCharting price, marked "last read" with their date. Pictures found on PriceCharting are looked up again from TCGdex or TCGCSV. Nothing else in the database changes (database step 6).
- Search TCGplayer and Search PriceCharting always show; links you paste are saved for you to open and never read.
- "Update prices and links" is "Update prices".
- A printing's price that moves from one source to another (say PriceCharting to CardTrader) isn't counted as a price change in top movers.
- On an iPhone home-screen app, the tab bar, Add button and camera button stay at the bottom of the screen on short pages (a scan batch with one photo) instead of floating up.
- CardTrader skips a price over 20x TCGplayer's and $20 more for the same printing (a parked listing), and more sets match: mainland Chinese codes as CardTrader writes them (CS2.5C is cs25, CSVE1C1 is csve1), the EX sets ('EX Delta Species'), Trainer Gallery, Galarian Gallery, Shiny Vault and Classic Collection cards (in the main set there, matched by number and name), and a few named differently (Pokémon GO, SV promos, McDonald's 2021 and 2024, Japanese M-P promos and Leaders' Stadium). A set with no match is looked for again at each update.
- Going back to 0.15.0 after this means restoring the backup made before the upgrade.

## 0.15.0

Fixes, a new logo, and exact TCGplayer links from open data.

- Scan review: Change match, tapping a photo or card picture for full screen, the price following the printing and condition, and the tabs work again (broken since 0.13). A test now parses every inline script with Node.
- Scan review on a phone: the card view's header starts below the status bar.
- New logo: an open box of cards. It's the home-screen icon, the favicon and the header logo, and it replaces the words "My cards" in both menus (screen readers still hear "My cards"). Icon addresses now change when the icon does; iOS may keep the old home-screen icon until the app is added again.
- A set's sticky header (count, sort, price note) has room below it before the cards start.
- The TCGplayer button opens the card's own TCGplayer page whenever TCGCSV's product list has it, instead of a search. Nothing is requested from TCGplayer.

## 0.14.0

The first numbered version, getting the code ready to share.

### Added
- **Price and picture sites** under Account › Data sources: a switch each for TCGplayer, PriceCharting, Limitless TCG, the Chinese card datasets and Bulbapedia. A switched-off site gets no request at all. A new install starts with them off; an install that already had accounts keeps them on. Turning one off deletes nothing.
- One **"Paste a price link for this card"** box works out the site from the address. Saved links are listed by site name, each with Remove. A link to a site dexSTASH can't read is kept as your own note.
- Account › About shows the version, a link to the source code, and where the app's data comes from.
- A new logo and app icons (two stacked pixel cards).
- GitHub Actions: tests on every pull request, and a multi-arch image built from a version tag.
- `tools/demo_data.py` for a demo collection, and `docs/architecture.md`.

### Changed
- Every outside request uses one honest User-Agent, `dexSTASH/<version> (+<repository>)`.
- Settings in `.env` are read only with the `DEXSTASH_` prefix. A setting with another prefix is named in the log at start, with the name to use.
- Backups from before the rename are renamed on start and can still be restored.
- The Docker network alias for the earlier name is gone: point your reverse proxy at `dexstash:8000`.
- The web routes moved from one large file into `app/web.py` and `app/routes/`. No address or page changed.
- README, DEPLOY.md and the architecture diagrams were rewritten for people setting it up themselves.

## Before 0.14

dexSTASH was built in numbered rounds from September to October 2026, before it had version numbers. In short:

- Accounts shared by several people, set completion, typed entry, Find a card in any language, cards added by hand, My cards, chases, spending and pack pulls, notes, binders and shared binders, and Collectr CSV import.
- Card scanning in batches with RapidOCR in a separate low-priority container, and Enhanced Search by picture (CLIP).
- Prices from TCGdex, TCGCSV and saved links, a nightly update, value over time, top movers and price history settings.
- Pokémon colour themes, light and dark mode, the Nunito font and htmx shipped with the app.
- Automatic backups before upgrades and every night, an activity log, numbered database changes, an automatic secret key and a setup code for the first account, time zone and currency settings, and the AGPL-3.0 licence.
