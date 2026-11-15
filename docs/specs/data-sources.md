# Data sources and their terms

## Purpose

Which outside sites dexSTASH reads, what it takes from each and how often, and the rules that keep it within
each site's terms. A new source, or a new use of an old one, starts here.

## Behaviour

### How the app reaches other sites

1. Every request to another site, from either container, goes through `sites.client()` in `app/sites.py`,
   with one User-Agent, `dexSTASH/<version> (+<repository>)`.
2. Each source module also checks a request's host before it's sent, and doesn't follow a redirect to
   another host.
3. TCGplayer's and PriceCharting's web pages are never requested: `sites.client()` refuses any address on
   either site, except TCGplayer's product picture hosts and PriceCharting's `/api/` with the host's token.
4. Requests pause between each other where a site asks for it (TCGCSV a quarter second, CardTrader and
   PriceCharting a second), and background work gives way to page loads.
5. Background requests take turns per site (`sites.wait_turn`): at most 5 a second to one site, shared by every
   job and download thread in that container. A 429 or 503 stops that site's queue for its Retry-After, else
   30 seconds, doubling up to 5 minutes while it keeps refusing. Page requests skip the queue (#77).

### Always on

6. These are contacted with no switch, because the app doesn't work without them:

   | Site | What | How often |
   |---|---|---|
   | TCGdex (`api.tcgdex.net`, `assets.tcgdex.net`) | Sets, cards, card pictures, TCGplayer prices for printings TCGCSV doesn't price | Sets when stored and when checked for updates; Find's card catalog nightly for new sets and each set again after 60 days; replies kept a week (`DEXSTASH_TCGDEX_CACHE`) |
   | TCGCSV (`tcgcsv.com`) | TCGplayer's set lists, products and daily prices, English and Japanese | Set lists and products weekly; prices each run for sets with kept cards, and in the background for a set someone browses |
   | TCGplayer picture hosts | Product pictures, at addresses TCGCSV lists | When a card has no picture of its own |
   | PokéAPI's files on GitHub | Pokémon names in every language, the Pokédex, sprites (an admin downloads them) | Names on first start and when checked for updates |
   | European Central Bank | Daily reference exchange rates; past days once from its history file | Once a day before the price run (`DEXSTASH_EURO_RATE`) |
   | Enhanced Search model host | The picture model (CLIP ViT-B/32 as ONNX), from `DEXSTASH_PICTURE_MODEL_URL` | Once, when Enhanced Search is turned on |

### Switched sites

7. Three sites have a switch under Data sources › Price and picture sites, and a new install starts with
   each off:
   - **Limitless TCG**: Japanese card pictures.
   - **Chinese card datasets**: mainland Chinese sets and pictures (PTCG-CHS-Datasets) and their English
     names (PTCG-card-data), from GitHub.
   - **Bulbapedia**: Pokémon descriptions for Describe, through its API.
8. A site that's off gets no request at all, redirects included, and its pictures aren't sent to the phone
   either. Turning one off deletes nothing; what was downloaded is used again once it's back on.
9. The scanner container sees a change of switch within 15 seconds.

### Sites that need the host's own key

10. **CardTrader** (official API v2) and **PriceCharting** (official API, a paid plan) are contacted only
   when the host has entered a key under Data sources › Prices › API keys. Without one, nothing is sent.
11. A key is stored in `data/api_keys.json`, encrypted with the app's secret key and signed, outside the
    database, so backups, restores and exports never hold one. Only its last four characters are shown again.
    It is never logged or put in an error message, and the scanner container never reads it.
12. A CardTrader token still in `.env` from before 0.28 is moved into the key file once at start-up.
13. A key the site refuses is shown as refused on Data sources; the run carries on with the other sources.

### Links out only

14. These are only links that open in the person's browser; nothing is read from them: TCGplayer product
    and search pages, PriceCharting search, CardTrader card and search pages, Cardmarket search, eBay search
    (the eBay site is a setting), PokéDoku, and any link someone pastes on a card.

### The Data sources page

15. Account › Data sources is for the admin who set up the install. Its tabs:
    - **Cards**: Check for updates (new and changed sets on TCGdex and in the Chinese sets, new cards of
      followed Pokémon, new sprites, missing pictures), card sets and languages, Pokémon data (names, sprites,
      descriptions).
    - **Prices**: Update prices, the nightly update, rebuilding downloaded data, sets not matched on TCGCSV or CardTrader (with a way to
      pick the match), sets with no price source, how many printings each source prices, the last runs,
      price history settings, and API keys.
    - **Pictures & scanning**: the site switches, picture counts, and the scanner.
    - **Settings**: time zone, currency, the eBay site for links, when a price counts as stale, which names
      show on Japanese and Chinese cards, and how old a set must be to count as vintage.
    - **Activity log**: the last hours of both containers' logs, with Copy last hour.
16. Running now lists the jobs that are running or waiting, and those a restart stopped.

## Rules and limits

- The [core rules](../../rules/core.md) on data sources apply: official APIs, open datasets and files
  published for reuse only. No page scraping, and no paid feed required.
- Every source's terms are listed in [NOTICE.md](../../NOTICE.md); a new source adds its row there in the
  same pull request.
- Data with personal-use terms (PTCG-CHS-Datasets, Pokémon sprites, Bulbapedia text) is downloaded on the
  host's own server and never committed or put in an image.
- A new site that isn't needed for the app to work gets a switch, off on a new install.
- A new outgoing host is added to DEPLOY.md's firewall list and the README's "Where data comes from".
- Tests never contact a real site.

## Interfaces

- Code: `app/sites.py` (`client`, `SITES`, `blocked`), `app/keys.py`, `app/tcgdex.py`, `app/tcgcsv.py`,
  `app/cardtrader.py`, `app/pricecharting.py`, `app/fx.py`, `app/names.py`, `app/pokeapi.py`,
  `app/chinese.py`, `app/bulbapedia.py`, `app/card_index.py`, `app/card_pictures.py`.
- Pages: Account › Data sources (`app/routes/data_sources.py`, `app/templates/data_sources.html`).
- Settings: `DEXSTASH_TCGCSV`, `DEXSTASH_EURO_RATE`, `DEXSTASH_TCGDEX_CACHE`, `DEXSTASH_CARD_INDEX`,
  `DEXSTASH_PICTURE_CHECK`, `DEXSTASH_PICTURE_MODEL_URL` (see [deploy](deploy.md)).
- Terms: [NOTICE.md](../../NOTICE.md).
- Related specs: [prices](prices.md), [scanning](scanning.md), [sets and search](sets-and-search.md).

## Open questions

- NOTICE.md's price row still names Cardmarket via TCGdex and leaves out PriceCharting's API; it should list
  TCGCSV, TCGdex, CardTrader and PriceCharting.
- eBay sold prices through a third party would be a new key-based source; its terms aren't checked yet.
