-- dexSTASH schema. Applied on startup; every statement is idempotent.

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    collection_id INTEGER,                   -- whose collection they share; their own id for the first account
    session_key   TEXT,                      -- changes with the password, which signs out other devices
    mode          TEXT NOT NULL DEFAULT 'dark', -- 'dark' or 'light', picked under Account › Theme
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS sets (
    set_id        TEXT PRIMARY KEY,          -- TCGdex id, e.g. 'sv03'; 'fr:sv03' for other languages
    language      TEXT NOT NULL DEFAULT 'en',
    name          TEXT NOT NULL,
    series        TEXT,
    release_date  TEXT,
    printed_total INTEGER NOT NULL,          -- number printed on the card ("/197")
    total         INTEGER NOT NULL,          -- including secret rares and subsets
    logo_url      TEXT,
    symbol_url    TEXT,
    cached_at     TEXT,
    english_name  TEXT                       -- round 27: a Japanese or Chinese set's English name (english.py)
);

CREATE TABLE IF NOT EXISTS cards (
    id            TEXT PRIMARY KEY,          -- TCGdex id, e.g. 'sv03-125'; 'fr:sv03-125' for other languages
    set_id        TEXT NOT NULL REFERENCES sets(set_id),
    number        TEXT NOT NULL,             -- as printed: '125', 'TG05', '177a'
    sort_number   INTEGER NOT NULL,          -- numeric part, for ordering
    is_main_set   INTEGER NOT NULL,          -- 1 if plain number <= printed_total
    name          TEXT NOT NULL,
    rarity        TEXT,
    image_url     TEXT,
    -- printings that exist for this card, from TCGdex 'variants'
    has_normal    INTEGER NOT NULL DEFAULT 0,
    has_reverse   INTEGER NOT NULL DEFAULT 0,
    has_holo      INTEGER NOT NULL DEFAULT 0,
    has_first_ed  INTEGER NOT NULL DEFAULT 0,
    pricing_json  TEXT,                      -- raw TCGdex 'pricing', for checking
    hand_added_by INTEGER,                   -- who added it by hand, for cards TCGdex doesn't have; NULL for TCGdex cards
    has_star      INTEGER NOT NULL DEFAULT 0, -- mainland Chinese ★ and ★★ prints
    has_star2     INTEGER NOT NULL DEFAULT 0,
    source        TEXT,                      -- NULL: TCGdex; 'chs': PTCG-CHS-Datasets (chinese.py)
    twin_code     TEXT,                      -- the English version of a Chinese card: set code, number, name
    twin_number   TEXT,
    twin_name     TEXT,
    types         TEXT                       -- card-game types, e.g. 'Fire' or 'Water,Fighting'
);
CREATE INDEX IF NOT EXISTS cards_by_set ON cards(set_id, sort_number);

-- One row per (collection, card, variant, condition). Quantity lives here.
-- user_id here and in wants and purchases is the collection's id (users.collection_id),
-- so everyone sharing a collection sees the same cards.
-- variant: normal | holo | reverse | first_ed | pokeball | masterball | other
-- condition: NM | LP | MP | HP | DMG
CREATE TABLE IF NOT EXISTS inventory (
    id               INTEGER PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id),  -- the collection's id (users.collection_id), not the person
    card_id          TEXT NOT NULL REFERENCES cards(id),
    variant          TEXT NOT NULL DEFAULT 'normal',
    condition        TEXT NOT NULL DEFAULT 'NM',
    quantity         INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    acquired_date    TEXT,
    acquired_price   REAL,
    storage_location TEXT,
    note             TEXT,                -- e.g. 'PSA 9', 'staff stamp'
    origin           TEXT,                -- where you got it: pack | single | trade | gift | other
    UNIQUE (user_id, card_id, variant, condition)
);
CREATE INDEX IF NOT EXISTS inventory_by_card ON inventory(card_id, variant);  -- prices and stash look cards up this way

-- The price each card and printing shows, one row each, worked out by prices.resolve from the
-- downloaded quotes (catalog.price_quotes) and what you typed (card_extras). Rebuilt whenever either changes.
CREATE TABLE IF NOT EXISTS prices (
    card_id    TEXT NOT NULL REFERENCES cards(id),
    variant    TEXT NOT NULL,
    price      REAL NOT NULL,                -- in dollars
    origin     TEXT NOT NULL,                -- 'tcgplayer' | 'cardtrader' | 'manual' (your price) |
                                             -- 'pricecharting' (the PriceCharting price you typed) | 'cardmarket' (euros converted, when no other) | 'printing' (≈ another)
    basis      TEXT,                         -- tcgplayer: 'tcgcsv' when TCGCSV's; cardtrader: 'nm' or 'any';
                                             -- printing: the printing it came from
    low        REAL,                         -- TCGplayer's low and mid for the printing (the range)
    mid        REAL,
    updated_at TEXT NOT NULL,                -- when the price it shows was fetched (or typed)
    range_low  REAL,                         -- Price is the average of every source; these the lowest
    range_high REAL,                         --   and highest of them,
    sources    TEXT,                         --   and this each source, JSON [{origin, price, local, basis, updated_at,
                                             --   paid?: {amount, currency, rate}, left_out?}]
    local_currency TEXT,                     -- the install's currency when this row was worked out, and the same
    local_rate     REAL,                     --   prices in it (dollars x local_rate), saved at the price update
    price_local    REAL,                     --   and refreshed each night and when the currency changes
    range_low_local  REAL,
    range_high_local REAL,
    PRIMARY KEY (card_id, variant)
);

-- When each price update ran (UTC). Home's "change since about 30 days ago" picks its date from these,
-- because price history only gets a row when a price changes.
CREATE TABLE IF NOT EXISTS price_runs (
    ran_at       TEXT PRIMARY KEY,
    finished_at  TEXT,
    status       TEXT,                       -- 'ok' | 'partial' | 'failed'
    fx_date      TEXT,                       -- the date of the exchange rates the run used
    summary_json TEXT                        -- per source: counts, failures, names it couldn't match
);

-- Each day's European Central Bank rates (currency per euro), kept for good, so history can be shown in any
-- currency at its own day's rate. In this file, not the catalog, so it's backed up with the history.
CREATE TABLE IF NOT EXISTS fx_rates (
    rate_date TEXT NOT NULL,                 -- 'YYYY-MM-DD', the ECB file's date
    currency  TEXT NOT NULL,
    per_eur   REAL NOT NULL,
    PRIMARY KEY (rate_date, currency)
);

-- Prices you typed, each for one printing: your own price (kind 'manual') and the price you saw on
-- PriceCharting (kind 'pricecharting'), in the currency typed.
CREATE TABLE IF NOT EXISTS typed_prices (
    card_id  TEXT NOT NULL,
    variant  TEXT NOT NULL,
    kind     TEXT NOT NULL,
    price    REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    typed_at TEXT NOT NULL,
    typed_by INTEGER,
    was_read INTEGER NOT NULL DEFAULT 0,     -- a PriceCharting price read from your link before 0.15.1 ("last read")
    PRIMARY KEY (card_id, variant, kind)
);

-- Price history: a row when a card's price changes (and its first price).
CREATE TABLE IF NOT EXISTS price_snapshots (
    card_id     TEXT NOT NULL REFERENCES cards(id),
    variant     TEXT NOT NULL,
    source      TEXT NOT NULL,               -- 'tcgplayer' | 'cardmarket'
    price       REAL NOT NULL,               -- market price, in source currency
    captured_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    basis       TEXT,                        -- 'tcgcsv' for TCGCSV's market price, else TCGdex's
    local       REAL,                        -- the price in local_currency on its day (dollar prices only), so
    local_currency TEXT,                     --   a past price doesn't move when the exchange rate does
    low         REAL,                        -- an average's lowest and highest source, and which they were
    low_source  TEXT,
    high        REAL,
    high_source TEXT,
    samples     INTEGER NOT NULL DEFAULT 1   -- the days a thinned (weekly or monthly) point stands for
);
CREATE INDEX IF NOT EXISTS prices_by_card ON price_snapshots(card_id, variant, captured_at);

-- Cards you're looking for. variant 'any' means any printing will do.
CREATE TABLE IF NOT EXISTS wants (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    card_id    TEXT NOT NULL REFERENCES cards(id),
    variant    TEXT NOT NULL DEFAULT 'any',
    added_by   INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (user_id, card_id, variant)
);

-- Packs, boxes and other sealed product you bought and opened.
CREATE TABLE IF NOT EXISTS purchases (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id),  -- the collection's id (users.collection_id), not the person
    label        TEXT NOT NULL,              -- e.g. '3 Obsidian Flames packs'
    set_id       TEXT REFERENCES sets(set_id),
    packs        INTEGER NOT NULL DEFAULT 1,
    cost         REAL NOT NULL,              -- total paid, USD
    purchased_on TEXT,
    kind         TEXT NOT NULL DEFAULT 'packs', -- packs | box | single | other | before ("Spent before we started tracking")
    created_by   INTEGER,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Cards pulled from a purchase. Their cost is an even share of the purchase,
-- unless cost_each is set by hand (the rest is then shared among the others).
CREATE TABLE IF NOT EXISTS pulls (
    id          INTEGER PRIMARY KEY,
    purchase_id INTEGER NOT NULL REFERENCES purchases(id),
    card_id     TEXT NOT NULL REFERENCES cards(id),
    variant     TEXT NOT NULL,
    condition   TEXT NOT NULL,
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    cost_each   REAL,
    UNIQUE (purchase_id, card_id, variant, condition)
);

-- Who added or removed what, and how. user_id is the person (not the collection);
-- no foreign keys so the history outlives removed users and sets.
CREATE TABLE IF NOT EXISTS inventory_log (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL,
    card_id       TEXT NOT NULL,
    variant       TEXT NOT NULL,
    condition     TEXT NOT NULL,
    delta         INTEGER NOT NULL,
    user_id       INTEGER,
    source        TEXT NOT NULL,             -- how: 'manual' | 'pull' | 'import' | 'scan'
    origin        TEXT,                      -- where you got it, when given: pack | single | trade | gift | other
    at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS inventory_log_by_card ON inventory_log(collection_id, card_id);

-- Notes on a card, each with its author. variant/condition are set when the
-- note is about one printing (e.g. 'PSA 9').
CREATE TABLE IF NOT EXISTS notes (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL,
    card_id       TEXT NOT NULL,
    variant       TEXT,
    condition     TEXT,
    author_id     INTEGER,
    text          TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS notes_by_card ON notes(collection_id, card_id);

-- CSV imports (Collectr exports). Rows are matched to cards in the background,
-- reviewed, then applied.
CREATE TABLE IF NOT EXISTS import_batches (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL,
    created_by    INTEGER,
    filename      TEXT,
    source        TEXT NOT NULL DEFAULT 'collectr',
    lang          TEXT NOT NULL DEFAULT 'en',
    file_text     TEXT,                      -- the file as uploaded, so it can be imported again
    status        TEXT NOT NULL,             -- 'matching' | 'stopped' (by a restart) | 'ready' | 'applied' | 'failed'
    message       TEXT,
    purchase_id   INTEGER,                   -- the spending entry made from the file's purchase prices
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    applied_at    TEXT
);

CREATE TABLE IF NOT EXISTS import_rows (
    id         INTEGER PRIMARY KEY,
    batch_id   INTEGER NOT NULL REFERENCES import_batches(id),
    line       INTEGER NOT NULL,
    key        TEXT NOT NULL,                -- what identifies this row's card in the file
    set_name   TEXT NOT NULL DEFAULT '',     -- the file's set name
    raw_json   TEXT NOT NULL,                -- the row as read, for showing and fixing
    card_id    TEXT,
    variant    TEXT NOT NULL,
    condition  TEXT NOT NULL,
    quantity   INTEGER NOT NULL,
    cost       REAL,                         -- purchase price per card, if the file has one
    note       TEXT,
    status     TEXT NOT NULL,                -- 'matched' | 'unmatched' | 'ignored'
    matched_by TEXT,                         -- 'saved' | 'auto' | 'you'
    reason     TEXT                          -- why it isn't matched, or why it's ignored
);
CREATE INDEX IF NOT EXISTS import_rows_by_batch ON import_rows(batch_id);

-- Remembered matches, so the next import lines up. A row key ('set|number|name|variance')
-- maps to our card, or to nothing (card_id NULL) for 'ignore this row'. A key 'set:<set name>'
-- maps a whole set of the file to a TCGdex set (set_id, lang), for sets matched by hand.
CREATE TABLE IF NOT EXISTS import_map (
    collection_id INTEGER NOT NULL,
    source        TEXT NOT NULL,
    key           TEXT NOT NULL,
    card_id       TEXT,
    set_id        TEXT,                      -- TCGdex set id, to reload the set if it was removed
    lang          TEXT,
    variant       TEXT,
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (collection_id, source, key)
);

-- Card photos waiting to be read and confirmed. A batch is one go at taking
-- photos; it can be left and picked up later. Once a scan is added or skipped
-- its photo and the text read from it are deleted, and only the outcome stays.
CREATE TABLE IF NOT EXISTS scan_batches (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL,
    created_by    INTEGER,
    origin        TEXT,                      -- default 'where you got it' for the batch's cards
    language      TEXT NOT NULL DEFAULT 'en', -- the cards' language, which decides the sets they're matched against
    name          TEXT,                      -- Your name for it; the date when empty
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS scans (
    id            INTEGER PRIMARY KEY,
    batch_id      INTEGER NOT NULL REFERENCES scan_batches(id),
    collection_id INTEGER NOT NULL,
    created_by    INTEGER,
    photo         TEXT,                      -- file name in the scans folder; NULL once deleted
    status        TEXT NOT NULL,             -- 'waiting' | 'reading' | 'ready' | 'nomatch' | 'failed' | 'added' | 'skipped' | 'expired'
                                             -- or in the Picture search queue: 'picture_waiting' | 'picture_searching' | 'picture_ready'
    read_json     TEXT,                      -- what the reader found (name, number, set code, all text)
    guesses_json  TEXT,                      -- up to 5 likely cards, best first
    picture_json  TEXT,                      -- picture search's closest cards
    card_id       TEXT,                      -- the card added
    variant       TEXT,
    condition     TEXT,
    quantity      INTEGER,
    paid          REAL,                      -- price paid per copy, set on Review your batch
    added_by      INTEGER,
    message       TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    done_at       TEXT,
    rev           INTEGER NOT NULL DEFAULT 0, -- bumped on every change, so the batch page fetches only changed rows
    prev_status   TEXT                        -- a deleted scan's status before, for Put back
);
CREATE INDEX IF NOT EXISTS scans_by_batch ON scans(batch_id);
CREATE INDEX IF NOT EXISTS scans_by_status ON scans(status);

-- Data downloaded for searching, matching and prices (the Data sources page).
CREATE TABLE IF NOT EXISTS datasets (
    id         TEXT PRIMARY KEY,             -- 'chs:CSV7C', 'twins:CSV7C'
    kind       TEXT NOT NULL,                -- 'chs' | 'twins'
    name       TEXT NOT NULL,
    source     TEXT NOT NULL,                -- where it came from
    terms      TEXT,
    rows       INTEGER NOT NULL DEFAULT 0,
    bytes      INTEGER NOT NULL DEFAULT 0,
    version    TEXT,                         -- the source's ETag or md5 when loaded, to check for updates
    loaded_at  TEXT,
    checked_at TEXT
);

-- Small facts about the database itself, e.g. which code last ran on it (backup.py).
CREATE TABLE IF NOT EXISTS app_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- What you added to a card yourself: links, a price typed in, your photo (extras.py).
CREATE TABLE IF NOT EXISTS card_extras (
    card_id           TEXT PRIMARY KEY,
    tcgplayer_url     TEXT,
    pricecharting_url TEXT,
    manual_price      REAL,              -- typed in by hand, USD
    photo             TEXT,              -- /card-photos/... you uploaded
    updated_by        INTEGER,
    updated_at        TEXT
);

-- Pokémon colour themes (app/themes.py). Sprites live in data/sprites/, never in a repo; their colours are in
-- catalog.sql.
CREATE TABLE IF NOT EXISTS user_theme (    -- no row: always Bulbasaur
    user_id    INTEGER PRIMARY KEY,
    mode       TEXT NOT NULL DEFAULT 'fixed', -- 'fixed' | 'daily'
    species_id INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS daily_pokemon (  -- the shared daily pick, one row per day
    day        TEXT PRIMARY KEY,
    species_id INTEGER NOT NULL,
    shiny      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS theme_settings ( -- one row (id 1) of settings shared by every login
    id              INTEGER PRIMARY KEY,
    only_owned      INTEGER NOT NULL DEFAULT 0,
    shiny_sometimes INTEGER NOT NULL DEFAULT 0,
    updated_by      INTEGER,
    updated_at      TEXT
);

-- Per-login settings kept on the server, e.g. My cards' filters and sort.
CREATE TABLE IF NOT EXISTS user_prefs (
    user_id INTEGER NOT NULL,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);

-- Pokémon master chase (app/chase.py). Only followed Pokémon are processed.
CREATE TABLE IF NOT EXISTS followed_pokemon (
    collection_id INTEGER NOT NULL,
    dex_id        INTEGER NOT NULL,
    name          TEXT NOT NULL,          -- English
    added_by      INTEGER,
    added_at      TEXT NOT NULL,
    checked_at    TEXT,                   -- the follow job, or Check for updates, last finished
    PRIMARY KEY (collection_id, dex_id)
);

-- Artists you can follow. A followed artist sits in followed_pokemon (and its cards in
-- pokemon_cards, its arts in arts) under dex_id = -artists.id, so updates, prices and art grouping cover it.
CREATE TABLE IF NOT EXISTS artists (
    id    INTEGER PRIMARY KEY,
    norm  TEXT NOT NULL UNIQUE,              -- the name folded (no accents, lower case)
    name  TEXT NOT NULL                      -- as TCGdex writes it
);

-- Every card of a followed Pokémon, in every language. Kept apart from cards
-- (which hold whole sets) so a set page never shows a part-loaded set.
CREATE TABLE IF NOT EXISTS pokemon_cards (
    dex_id       INTEGER NOT NULL,
    card_id      TEXT NOT NULL,           -- our id: 'ja:SV2a-035', 'sv03.5-035', 'zh-cn:CSV7C-141'
    lang         TEXT NOT NULL,
    set_id       TEXT NOT NULL,           -- our set id
    set_name     TEXT,
    set_code     TEXT,                    -- printed code when known, else the TCGdex set id
    release_date TEXT,
    number       TEXT,
    name         TEXT,
    illustrator  TEXT,
    rarity       TEXT,
    image_url    TEXT,
    variants     TEXT,                    -- JSON list of our printing keys
    prices_json  TEXT,                    -- JSON {printing: USD}, TCGplayer through TCGdex; only for cards not in cards
                                          -- (a stored card's prices are in prices)
    how          TEXT NOT NULL DEFAULT 'auto',  -- 'auto' | 'manual' (added by you) | 'removed' (taken out by you)
    added_by     INTEGER,
    updated_at   TEXT,
    PRIMARY KEY (dex_id, card_id)
);

CREATE TABLE IF NOT EXISTS arts (
    id            INTEGER PRIMARY KEY,
    dex_id        INTEGER NOT NULL,
    cover_card_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS arts_by_dex ON arts(dex_id);

CREATE TABLE IF NOT EXISTS art_cards (
    art_id  INTEGER NOT NULL REFERENCES arts(id) ON DELETE CASCADE,
    card_id TEXT NOT NULL,
    how     TEXT NOT NULL DEFAULT 'auto', -- 'auto' | 'cover' | 'manual' (your ＋)
    score   REAL,                         -- picture likeness to the cover, 0-1
    PRIMARY KEY (art_id, card_id)
);

-- Your fixes to the arts, kept through every re-check: join_card_id puts the
-- card in that card's art; NULL takes it out on its own (left_card_id is the
-- cover of the art it left, for "Not this art" and Undo).
CREATE TABLE IF NOT EXISTS art_fixes (
    dex_id       INTEGER NOT NULL,
    card_id      TEXT NOT NULL,
    join_card_id TEXT,
    left_card_id TEXT,
    by           INTEGER,
    at           TEXT NOT NULL,
    PRIMARY KEY (dex_id, card_id)
);

-- The ★ Chase box on a chased card: links to anywhere (a listing, a video) and photos.
-- The short note is an ordinary card note (notes table), so who wrote it is kept.
CREATE TABLE IF NOT EXISTS chase_links (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL,
    card_id       TEXT NOT NULL,
    url           TEXT NOT NULL,
    label         TEXT,
    added_by      INTEGER,
    added_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS chase_links_by_card ON chase_links(collection_id, card_id);

CREATE TABLE IF NOT EXISTS chase_photos (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL,
    card_id       TEXT NOT NULL,
    photo         TEXT NOT NULL,             -- /card-photos/... file
    added_by      INTEGER,
    added_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS chase_photos_by_card ON chase_photos(collection_id, card_id);

-- Card tags: 'auto' ones saved from a card's details when its set is stored, 'mine' typed in.
-- Keyed by card, so both logins share them. Tags worked out from the card row (rarity group, printings,
-- type, series, Promo, Vintage) aren't stored. Hiding an automatic tag on one card: card_tag_hidden.
CREATE TABLE IF NOT EXISTS card_tags (
    card_id   TEXT NOT NULL,
    tag       TEXT NOT NULL COLLATE NOCASE,
    kind      TEXT NOT NULL,                 -- 'auto' | 'mine'
    added_by  INTEGER,                       -- mine: who added it
    added_at  TEXT,
    PRIMARY KEY (card_id, tag)
);
CREATE INDEX IF NOT EXISTS card_tags_by_tag ON card_tags(tag);
CREATE TABLE IF NOT EXISTS card_tag_hidden (
    card_id   TEXT NOT NULL,
    tag       TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (card_id, tag)
);

-- Binders: named places shared by the collection. binder_copies says how many copies of an
-- inventory line are in each binder; the rest of the line is "Not in a binder" (binders.py).
CREATE TABLE IF NOT EXISTS binders (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL,
    name          TEXT NOT NULL,
    created_by    INTEGER,
    created_at    TEXT,
    layout        INTEGER NOT NULL DEFAULT 9,     -- Pockets a page (4, 9 or 12)
    sort          TEXT NOT NULL DEFAULT 'set'     -- The pages' order (binder_pages.SORTS)
);
CREATE TABLE IF NOT EXISTS binder_copies (
    inventory_id  INTEGER NOT NULL,
    binder_id     INTEGER NOT NULL,
    qty           INTEGER NOT NULL,
    PRIMARY KEY (inventory_id, binder_id)
);

-- A Pokémon's own words: added with "+ word" on its tags, searched by Describe, shared by the
-- collection. Hiding a wrong automatic word: pokemon_tag_hidden.
CREATE TABLE IF NOT EXISTS pokemon_tags (
    dex       INTEGER NOT NULL,
    tag       TEXT NOT NULL COLLATE NOCASE,
    source    TEXT NOT NULL DEFAULT 'mine',
    added_by  INTEGER,
    added_at  TEXT,
    PRIMARY KEY (dex, tag)
);
CREATE TABLE IF NOT EXISTS pokemon_tag_hidden (
    dex       INTEGER NOT NULL,
    tag       TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (dex, tag)
);

-- Share my binder with access codes (shared.py). Only a scrypt hash of each code is kept.
CREATE TABLE IF NOT EXISTS binder_codes (
    id           INTEGER PRIMARY KEY,
    binder_id    INTEGER NOT NULL REFERENCES binders(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    code_hash    TEXT NOT NULL,
    code_tag     TEXT,                       -- keyed lookup tag (shared.lookup_tag), not the code
    created_by   INTEGER,
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT
);

-- The access code history on the Share sheet (added / revoked, by whom, when). Clear empties it.
CREATE TABLE IF NOT EXISTS binder_code_log (
    id        INTEGER PRIMARY KEY,
    binder_id INTEGER NOT NULL REFERENCES binders(id) ON DELETE CASCADE,
    name      TEXT NOT NULL,                 -- who the code was for
    action    TEXT NOT NULL,                 -- 'added' or 'revoked'
    by_user   INTEGER,
    at        TEXT NOT NULL
);

-- Enhanced Search's score (where the card you added was in its list) and what it learned from your
-- confirmed scans (a scan's fingerprint, never the photo), at most 5 per card.
CREATE TABLE IF NOT EXISTS picture_scores (
    scan_id INTEGER PRIMARY KEY,
    rank    INTEGER NOT NULL,                -- 1 = first; 0 = not in the list
    at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS picture_learned (
    key     TEXT PRIMARY KEY,                -- 'scan:<scan id>'
    card_id TEXT NOT NULL,
    set_id  TEXT NOT NULL,
    lang    TEXT NOT NULL,
    vector  BLOB NOT NULL,                   -- 512 float32
    at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS picture_learned_card ON picture_learned(card_id, at);

-- A picture looked up once for a card that has none (scanner: the other language of a JA/Taiwan
-- pair, then TCGplayer, then PriceCharting). looked_at NULL = waiting; a miss is tried again after 14 days.
CREATE TABLE IF NOT EXISTS picture_lookups (
    card_id   TEXT PRIMARY KEY,              -- our id
    url       TEXT,
    source    TEXT,
    looked_at TEXT,
    found     INTEGER NOT NULL DEFAULT 0,
    asked_at  TEXT NOT NULL
);

-- Background work, one job at a time per process (jobs.py, round 23): the "Running now" box reads it. runs_in says
-- which process runs it: the web app, or the scanner container (dexstash-scanner) for work that needs the picture model.
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL,               -- one job per key at a time
    label       TEXT NOT NULL,
    kind        TEXT NOT NULL,               -- prices, nightly, picture-saver, pictures-index, recheck, ...
    arg         TEXT,
    runs_in     TEXT NOT NULL DEFAULT 'app', -- app, scanner
    priority    INTEGER NOT NULL DEFAULT 0,  -- 0 you asked for it, 1 scheduled or automatic
    status      TEXT NOT NULL DEFAULT 'waiting',   -- waiting, running, done, failed, stopped (by a restart)
    done        INTEGER NOT NULL DEFAULT 0,
    total       INTEGER NOT NULL DEFAULT 0,
    step        TEXT,
    result      TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS jobs_next ON jobs(runs_in, status, priority, id);
CREATE INDEX IF NOT EXISTS jobs_key ON jobs(key, status);

-- The last cards opened (card page or preview), shared by everyone, for an empty Find.
CREATE TABLE IF NOT EXISTS card_views (
    card_id   TEXT PRIMARY KEY,
    name      TEXT NOT NULL DEFAULT '',
    number    TEXT NOT NULL DEFAULT '',
    image_url TEXT,
    href      TEXT NOT NULL,
    viewed_at TEXT NOT NULL
);

-- Saved picture copies of followed cards that aren't stored in cards (Pokémon and artist pages).
CREATE TABLE IF NOT EXISTS followed_pictures (
    card_id       TEXT PRIMARY KEY,
    saved_picture TEXT,
    saved_from    TEXT,
    saved_src     TEXT,
    checked_at    TEXT NOT NULL
);

-- CardTrader prices through its own API, with the host's token (app/cardtrader.py); what it sends is in
-- catalog.sql. Which CardTrader expansion is one of our sets: matched by code or name, or picked on Data sources.
-- expansion_id NULL = no match found (listed on Data sources to pick).
CREATE TABLE IF NOT EXISTS ct_sets (
    set_id       TEXT PRIMARY KEY,
    expansion_id INTEGER,
    picked       INTEGER NOT NULL DEFAULT 0,
    checked_at   TEXT NOT NULL
);

-- Round 23: a change to what's kept (your stash, chases, followed Pokémon) counts up kept_rev in app_meta, so the
-- picture saver wakes only when it changed (card_pictures.poke_if_changed), not after every form.
CREATE TRIGGER IF NOT EXISTS inventory_kept_rev_add AFTER INSERT ON inventory BEGIN
    INSERT OR REPLACE INTO app_meta (key, value) VALUES ('kept_rev', COALESCE((SELECT value FROM app_meta WHERE key = 'kept_rev'), 0) + 1); END;
CREATE TRIGGER IF NOT EXISTS inventory_kept_rev_change AFTER UPDATE OF card_id ON inventory BEGIN
    INSERT OR REPLACE INTO app_meta (key, value) VALUES ('kept_rev', COALESCE((SELECT value FROM app_meta WHERE key = 'kept_rev'), 0) + 1); END;
CREATE TRIGGER IF NOT EXISTS inventory_kept_rev_drop AFTER DELETE ON inventory BEGIN
    INSERT OR REPLACE INTO app_meta (key, value) VALUES ('kept_rev', COALESCE((SELECT value FROM app_meta WHERE key = 'kept_rev'), 0) + 1); END;
CREATE TRIGGER IF NOT EXISTS wants_kept_rev_add AFTER INSERT ON wants BEGIN
    INSERT OR REPLACE INTO app_meta (key, value) VALUES ('kept_rev', COALESCE((SELECT value FROM app_meta WHERE key = 'kept_rev'), 0) + 1); END;
CREATE TRIGGER IF NOT EXISTS wants_kept_rev_change AFTER UPDATE OF card_id ON wants BEGIN
    INSERT OR REPLACE INTO app_meta (key, value) VALUES ('kept_rev', COALESCE((SELECT value FROM app_meta WHERE key = 'kept_rev'), 0) + 1); END;
CREATE TRIGGER IF NOT EXISTS wants_kept_rev_drop AFTER DELETE ON wants BEGIN
    INSERT OR REPLACE INTO app_meta (key, value) VALUES ('kept_rev', COALESCE((SELECT value FROM app_meta WHERE key = 'kept_rev'), 0) + 1); END;
CREATE TRIGGER IF NOT EXISTS followed_kept_rev_add AFTER INSERT ON followed_pokemon BEGIN
    INSERT OR REPLACE INTO app_meta (key, value) VALUES ('kept_rev', COALESCE((SELECT value FROM app_meta WHERE key = 'kept_rev'), 0) + 1); END;
CREATE TRIGGER IF NOT EXISTS followed_kept_rev_drop AFTER DELETE ON followed_pokemon BEGIN
    INSERT OR REPLACE INTO app_meta (key, value) VALUES ('kept_rev', COALESCE((SELECT value FROM app_meta WHERE key = 'kept_rev'), 0) + 1); END;

-- Round 26: a deleted copy leaves its binders. SQLite can give a deleted copy's id to the next copy added, so a
-- binder slot left behind would pass to that new card.
CREATE TRIGGER IF NOT EXISTS inventory_gone AFTER DELETE ON inventory BEGIN
    DELETE FROM binder_copies WHERE inventory_id = OLD.id; END;

-- Round 27: a matched scan's card, kept (priced) while the photo waits to be added (prices.KEPT_SQL).
CREATE TABLE IF NOT EXISTS scan_matches (
    scan_id  INTEGER NOT NULL,
    card_id  TEXT NOT NULL,
    PRIMARY KEY (scan_id, card_id)
);
