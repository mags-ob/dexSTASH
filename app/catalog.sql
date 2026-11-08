-- What dexSTASH downloads that's the same for everyone and can be fetched again: TCGdex replies,
-- TCGCSV's sets and prices, CardTrader's expansions, cards and prices, and the colours worked out from the
-- sprites. It's a file of its own next to the database (dexstash-catalog.db), attached as `catalog`, so backups
-- hold only what's yours and Data sources › Rebuild downloaded data can empty it. Tables are named here with
-- catalog. in front; queries use the bare name, which SQLite finds in the catalog.

-- Pokémon colour themes (app/themes.py), worked out once from each sprite with Pillow.
CREATE TABLE IF NOT EXISTS catalog.pokemon_palette (
    species_id   INTEGER NOT NULL,
    shiny        INTEGER NOT NULL DEFAULT 0,
    swatches     TEXT NOT NULL,              -- JSON: up to 5 colours, most used first
    light        TEXT NOT NULL,              -- JSON tokens for light mode: accent, on, ink, soft, second, tint
    dark         TEXT NOT NULL,              -- the same for dark mode
    extracted_at TEXT NOT NULL,
    PRIMARY KEY (species_id, shiny)
);
-- TCGdex card and set replies saved for browsing (previews, Find, set pages), for 7 days.
-- Prices never come from here: the price update and Check for updates always ask TCGdex.
CREATE TABLE IF NOT EXISTS catalog.tcgdex_cache (
    url        TEXT PRIMARY KEY,
    body       TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

-- TCGCSV, TCGplayer's data per set (tcgcsv.py).
CREATE TABLE IF NOT EXISTS catalog.tcgcsv_groups (
    category     INTEGER NOT NULL,   -- 3 English, 85 Japanese
    group_id     INTEGER NOT NULL,
    name         TEXT NOT NULL,
    abbreviation TEXT,
    published_on TEXT,
    PRIMARY KEY (category, group_id)
);
CREATE TABLE IF NOT EXISTS catalog.tcgcsv_sets (
    set_id      TEXT PRIMARY KEY,    -- our set id
    category    INTEGER NOT NULL,
    group_id    INTEGER NOT NULL,
    matched_by  TEXT NOT NULL,       -- 'code' | 'name'
    products_at TEXT,
    prices_at   TEXT
);
-- A card's TCGplayer products: variant '' is the card itself (its printings come from the price file's
-- subtypes), 'pokeball' and 'masterball' the Poké Ball and Master Ball pattern products listed apart.
CREATE TABLE IF NOT EXISTS catalog.tcgcsv_cards (
    card_id     TEXT NOT NULL,
    variant     TEXT NOT NULL DEFAULT '',
    product_id  INTEGER NOT NULL,
    group_id    INTEGER NOT NULL,
    image_url   TEXT,
    url         TEXT,                    -- the product on TCGplayer
    modified_on TEXT,                    -- when TCGplayer last changed the product
    PRIMARY KEY (card_id, variant)
);
-- Every product of a TCGCSV set, for sets nobody keeps cards from (Find's pictures, set sort by value).
CREATE TABLE IF NOT EXISTS catalog.tcgcsv_products (
    category   INTEGER NOT NULL,
    group_id   INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    number     TEXT,                 -- tcgcsv._number form ('1', 'TG5')
    name       TEXT,
    image_url  TEXT,
    market     REAL,                 -- the dearest printing's market price
    PRIMARY KEY (group_id, product_id)
);
-- Each printing's price of a browsed set's products (subtype as TCGplayer names it).
CREATE TABLE IF NOT EXISTS catalog.tcgcsv_product_prices (
    group_id   INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    subtype    TEXT NOT NULL,
    market     REAL,
    low        REAL,
    PRIMARY KEY (group_id, product_id, subtype)
);
CREATE TABLE IF NOT EXISTS catalog.tcgcsv_group_reads (
    group_id    INTEGER PRIMARY KEY,
    category    INTEGER NOT NULL,
    products_at TEXT,
    prices_at   TEXT
);
CREATE TABLE IF NOT EXISTS catalog.tcgcsv_prices (
    card_id    TEXT NOT NULL,
    variant    TEXT NOT NULL,
    market     REAL NOT NULL,
    low        REAL,
    mid        REAL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (card_id, variant)
);

-- CardTrader (app/cardtrader.py). Its Pokémon expansions, refreshed weekly. Which of them is one of
-- our sets is yours (ct_sets, in schema.sql).
CREATE TABLE IF NOT EXISTS catalog.ct_expansions (
    id         INTEGER PRIMARY KEY,
    code       TEXT NOT NULL,
    name       TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
-- An expansion's cards, refreshed weekly. number is the collector number as CardTrader writes it.
CREATE TABLE IF NOT EXISTS catalog.ct_blueprints (
    blueprint_id INTEGER PRIMARY KEY,
    expansion_id INTEGER NOT NULL,
    number       TEXT,
    name         TEXT,
    tcgplayer_id INTEGER,
    fetched_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS catalog.ct_blueprints_expansion ON ct_blueprints(expansion_id);
-- PriceCharting's product for a kept card, per printing ('' = the card itself), round 28 (pricecharting.py).
-- product_id 0: searched, none found (looked for again a week after checked_at).
CREATE TABLE IF NOT EXISTS catalog.pc_matches (
    card_id    TEXT NOT NULL,
    variant    TEXT NOT NULL,
    product_id INTEGER NOT NULL,
    name       TEXT,
    console    TEXT,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (card_id, variant)
);
-- Every downloaded price, one row per card, printing and source; prices.resolve picks the one
-- each card shows (main.prices). tcgplayer: TCGplayer's market price (USD) through TCGCSV (basis 'tcgcsv') or TCGdex.
-- cardtrader: the lowest listing, in the listing's currency, basis 'nm' or 'any' (no Near Mint copy in the card's
-- language was among the cheapest, so the lowest of any condition), ref = the CardTrader card (blueprint). Its
-- variant '' = the card's own printing (not marked reverse holo or 1st edition; those, and Poké Ball / Master Ball,
-- have their own rows).
CREATE TABLE IF NOT EXISTS catalog.price_quotes (
    card_id    TEXT NOT NULL,
    variant    TEXT NOT NULL,
    source     TEXT NOT NULL,
    price      REAL NOT NULL,
    currency   TEXT NOT NULL,
    low        REAL,
    mid        REAL,
    basis      TEXT,
    ref        INTEGER,
    updated_at TEXT NOT NULL,            -- when dexSTASH fetched it
    source_updated_at TEXT,              -- the source's own updated date, when it gives one
    url        TEXT,                     -- the product on that site, when the source gives one
    PRIMARY KEY (card_id, variant, source)
);

-- The local card catalog (app/card_index.py). Every card of every TCGdex set in a language, one
-- GraphQL request per set, for Find, search and close matches. Its full-text index of names (index_names) is made
-- by card_index.make_names when this SQLite has FTS5 trigrams.
CREATE TABLE IF NOT EXISTS catalog.index_sets (
    lang       TEXT NOT NULL,
    set_id     TEXT NOT NULL,                -- TCGdex's set id
    cards      INTEGER NOT NULL DEFAULT 0,   -- cards kept
    listed     INTEGER,                      -- cards TCGdex's set list said it had when it was fetched
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (lang, set_id)
);
CREATE TABLE IF NOT EXISTS catalog.index_cards (
    lang        TEXT NOT NULL,
    id          TEXT NOT NULL,               -- TCGdex's card id
    set_id      TEXT NOT NULL,
    local_id    TEXT NOT NULL,               -- the number as TCGdex writes it ('004', 'TG05')
    name        TEXT NOT NULL,
    folded      TEXT NOT NULL,               -- the name as display.fold compares it
    image       TEXT,
    rarity      TEXT,
    illustrator TEXT,
    kind        TEXT,                        -- 'pokemon' | 'trainer' | 'energy'
    dex_ids     TEXT,                        -- JSON list of Pokédex numbers
    PRIMARY KEY (lang, id)
);
CREATE INDEX IF NOT EXISTS catalog.index_cards_number ON index_cards(lang, local_id);
CREATE INDEX IF NOT EXISTS catalog.index_cards_set ON index_cards(lang, set_id);
