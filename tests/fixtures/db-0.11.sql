CREATE TABLE app_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE art_cards (
    art_id  INTEGER NOT NULL REFERENCES arts(id) ON DELETE CASCADE,
    card_id TEXT NOT NULL,
    how     TEXT NOT NULL DEFAULT 'auto', -- 'auto' | 'cover' | 'manual' (your ＋)
    score   REAL,                         -- picture likeness to the cover, 0-1
    PRIMARY KEY (art_id, card_id)
);
CREATE TABLE art_fixes (
    dex_id       INTEGER NOT NULL,
    card_id      TEXT NOT NULL,
    join_card_id TEXT,
    left_card_id TEXT,
    by           INTEGER,
    at           TEXT NOT NULL,
    PRIMARY KEY (dex_id, card_id)
);
CREATE TABLE artists (
    id    INTEGER PRIMARY KEY,
    norm  TEXT NOT NULL UNIQUE,              -- the name folded (no accents, lower case)
    name  TEXT NOT NULL                      -- as TCGdex writes it
);
CREATE TABLE arts (
    id            INTEGER PRIMARY KEY,
    dex_id        INTEGER NOT NULL,
    cover_card_id TEXT NOT NULL
);
CREATE TABLE binder_code_log (
    id        INTEGER PRIMARY KEY,
    binder_id INTEGER NOT NULL REFERENCES binders(id) ON DELETE CASCADE,
    name      TEXT NOT NULL,                 -- who the code was for
    action    TEXT NOT NULL,                 -- 'added' or 'revoked'
    by_user   INTEGER,
    at        TEXT NOT NULL
);
CREATE TABLE binder_codes (
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
CREATE TABLE binder_copies (
    inventory_id  INTEGER NOT NULL,
    binder_id     INTEGER NOT NULL,
    qty           INTEGER NOT NULL,
    PRIMARY KEY (inventory_id, binder_id)
);
CREATE TABLE binders (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL,
    name          TEXT NOT NULL,
    created_by    INTEGER,
    created_at    TEXT
, share_token TEXT, share_on INTEGER NOT NULL DEFAULT 0, shared_at TEXT);
CREATE TABLE card_extras (
    card_id           TEXT PRIMARY KEY,
    tcgplayer_url     TEXT,
    pricecharting_url TEXT,
    link_price        REAL,              -- read from the link's page, USD
    link_source       TEXT,              -- 'tcgplayer' | 'pricecharting'
    link_checked_at   TEXT,
    link_error        TEXT,              -- why the page couldn't be read, last time
    link_image        TEXT,              -- the picture on the link's page
    pc_image          TEXT,              -- the picture on the PriceCharting link's page (round 7)
    manual_price      REAL,              -- typed in by hand, USD
    photo             TEXT,              -- /card-photos/... you uploaded
    updated_by        INTEGER,
    updated_at        TEXT
, pc_price REAL, pc_checked_at TEXT);
CREATE TABLE card_tag_hidden (
    card_id   TEXT NOT NULL,
    tag       TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (card_id, tag)
);
CREATE TABLE card_tags (
    card_id   TEXT NOT NULL,
    tag       TEXT NOT NULL COLLATE NOCASE,
    kind      TEXT NOT NULL,                 -- 'auto' | 'mine'
    added_by  INTEGER,                       -- mine: who added it
    added_at  TEXT,
    PRIMARY KEY (card_id, tag)
);
CREATE TABLE card_views (
    card_id   TEXT PRIMARY KEY,
    name      TEXT NOT NULL DEFAULT '',
    number    TEXT NOT NULL DEFAULT '',
    image_url TEXT,
    href      TEXT NOT NULL,
    viewed_at TEXT NOT NULL
);
CREATE TABLE cards (
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
, tcgplayer_id INTEGER, saved_picture TEXT, saved_from TEXT, saved_src TEXT, picture_checked_at TEXT, twin_picture TEXT, twin_label TEXT, found_image TEXT, found_image_guess INTEGER, english_name TEXT, cardmarket_eur REAL, cardmarket_at TEXT);
CREATE TABLE chase_links (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL,
    card_id       TEXT NOT NULL,
    url           TEXT NOT NULL,
    label         TEXT,
    added_by      INTEGER,
    added_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE chase_photos (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL,
    card_id       TEXT NOT NULL,
    photo         TEXT NOT NULL,             -- /card-photos/... file
    added_by      INTEGER,
    added_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE daily_pokemon (  -- the shared daily pick, one row per day
    day        TEXT PRIMARY KEY,
    species_id INTEGER NOT NULL,
    shiny      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE datasets (
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
CREATE TABLE english_misses (
    card_id    TEXT PRIMARY KEY,
    english_id TEXT NOT NULL,
    checked_at TEXT NOT NULL
);
CREATE TABLE english_prices (
    card_id    TEXT NOT NULL,                -- our (non-English) card
    variant    TEXT NOT NULL,
    price      REAL NOT NULL,
    english_id TEXT NOT NULL,                -- the English card's TCGdex id
    updated_at TEXT NOT NULL,
    PRIMARY KEY (card_id, variant)
);
CREATE TABLE followed_pictures (
    card_id       TEXT PRIMARY KEY,
    saved_picture TEXT,
    saved_from    TEXT,
    saved_src     TEXT,
    checked_at    TEXT NOT NULL
);
CREATE TABLE followed_pokemon (
    collection_id INTEGER NOT NULL,
    dex_id        INTEGER NOT NULL,
    name          TEXT NOT NULL,          -- English
    added_by      INTEGER,
    added_at      TEXT NOT NULL,
    checked_at    TEXT,                   -- the follow job, or Check for updates, last finished
    PRIMARY KEY (collection_id, dex_id)
);
CREATE TABLE found_links (
    card_id    TEXT NOT NULL,
    site       TEXT NOT NULL,
    status     TEXT NOT NULL,
    url        TEXT,
    product_id INTEGER,
    price      REAL,
    image      TEXT,
    name       TEXT,
    found_at   TEXT NOT NULL,
    read_at    TEXT,
    asked_at   TEXT, visited_at TEXT,
    PRIMARY KEY (card_id, site)
);
CREATE TABLE import_batches (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL,
    created_by    INTEGER,
    filename      TEXT,
    source        TEXT NOT NULL DEFAULT 'collectr',
    lang          TEXT NOT NULL DEFAULT 'en',
    file_text     TEXT,                      -- the file as uploaded, so it can be imported again
    status        TEXT NOT NULL,             -- 'matching' | 'ready' | 'applied' | 'failed'
    message       TEXT,
    purchase_id   INTEGER,                   -- the spending entry made from the file's purchase prices
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    applied_at    TEXT
);
CREATE TABLE import_map (
    collection_id INTEGER NOT NULL,
    source        TEXT NOT NULL,
    key           TEXT NOT NULL,
    card_id       TEXT,
    set_id        TEXT,                      -- TCGdex set id, to reload the set if it was removed
    lang          TEXT,
    variant       TEXT,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (collection_id, source, key)
);
CREATE TABLE import_rows (
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
CREATE TABLE inventory (
    id               INTEGER PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id),
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
CREATE TABLE inventory_log (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL,
    card_id       TEXT NOT NULL,
    variant       TEXT NOT NULL,
    condition     TEXT NOT NULL,
    delta         INTEGER NOT NULL,
    user_id       INTEGER,
    source        TEXT NOT NULL,             -- how: 'manual' | 'pull' | 'import' | 'scan'
    origin        TEXT,                      -- where you got it, when given: pack | single | trade | gift | other
    at            TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE notes (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL,
    card_id       TEXT NOT NULL,
    variant       TEXT,
    condition     TEXT,
    author_id     INTEGER,
    text          TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE pc_consoles (
    set_id     TEXT PRIMARY KEY,
    console    TEXT NOT NULL,
    ok         INTEGER NOT NULL,
    checked_at TEXT NOT NULL
);
CREATE TABLE picture_learned (
    key     TEXT PRIMARY KEY,                -- 'scan:<scan id>'
    card_id TEXT NOT NULL,
    set_id  TEXT NOT NULL,
    lang    TEXT NOT NULL,
    vector  BLOB NOT NULL,                   -- 512 float32
    at      TEXT NOT NULL
);
CREATE TABLE picture_lookups (
    card_id   TEXT PRIMARY KEY,              -- our id
    url       TEXT,
    source    TEXT,
    looked_at TEXT,
    found     INTEGER NOT NULL DEFAULT 0,
    asked_at  TEXT NOT NULL
);
CREATE TABLE picture_scores (
    scan_id INTEGER PRIMARY KEY,
    rank    INTEGER NOT NULL,                -- 1 = first; 0 = not in the list
    at      TEXT NOT NULL
);
CREATE TABLE pokemon_cards (
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
    prices_json  TEXT,                    -- JSON {printing: USD}, TCGplayer through TCGdex
    how          TEXT NOT NULL DEFAULT 'auto',  -- 'auto' | 'manual' (added by you) | 'removed' (taken out by you)
    added_by     INTEGER,
    updated_at   TEXT,
    PRIMARY KEY (dex_id, card_id)
);
CREATE TABLE pokemon_palette (
    species_id   INTEGER NOT NULL,
    shiny        INTEGER NOT NULL DEFAULT 0,
    swatches     TEXT NOT NULL,              -- JSON: up to 5 colours, most used first
    light        TEXT NOT NULL,              -- JSON tokens for light mode: accent, on, ink, soft, second, tint
    dark         TEXT NOT NULL,              -- the same for dark mode
    extracted_at TEXT NOT NULL,
    PRIMARY KEY (species_id, shiny)
);
CREATE TABLE pokemon_tag_hidden (
    dex       INTEGER NOT NULL,
    tag       TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (dex, tag)
);
CREATE TABLE pokemon_tags (
    dex       INTEGER NOT NULL,
    tag       TEXT NOT NULL COLLATE NOCASE,
    source    TEXT NOT NULL DEFAULT 'mine',
    added_by  INTEGER,
    added_at  TEXT,
    PRIMARY KEY (dex, tag)
);
CREATE TABLE price_runs (
    ran_at TEXT PRIMARY KEY
);
CREATE TABLE price_snapshots (
    card_id     TEXT NOT NULL REFERENCES cards(id),
    variant     TEXT NOT NULL,
    source      TEXT NOT NULL,               -- 'tcgplayer' | 'cardmarket'
    price       REAL NOT NULL,               -- market price, in source currency
    captured_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE prices (
    card_id    TEXT NOT NULL REFERENCES cards(id),
    variant    TEXT NOT NULL,
    source     TEXT NOT NULL,                -- 'tcgplayer' (USD market) | 'cardmarket' (EUR trend)
    price      REAL NOT NULL,
    currency   TEXT NOT NULL,                -- 'USD'; marked when not TCGplayer's own price for this printing:
                                             -- 'USD-extra' your link or typed in, 'USD-en' ≈ English, 'USD-print' ≈ another printing
    updated_at TEXT NOT NULL,
    basis      TEXT,                         -- USD-en: the English card's TCGdex id; USD-print: the printing it came from;
                                             -- 'tcgcsv' when the price came from TCGCSV (round 11)
    low        REAL,                         -- round 11: TCGplayer's low and mid for the printing (the range)
    mid        REAL,
    PRIMARY KEY (card_id, variant, source)
);
CREATE TABLE pulls (
    id          INTEGER PRIMARY KEY,
    purchase_id INTEGER NOT NULL REFERENCES purchases(id),
    card_id     TEXT NOT NULL REFERENCES cards(id),
    variant     TEXT NOT NULL,
    condition   TEXT NOT NULL,
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    cost_each   REAL,
    UNIQUE (purchase_id, card_id, variant, condition)
);
CREATE TABLE purchases (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    label        TEXT NOT NULL,              -- e.g. '3 Obsidian Flames packs'
    set_id       TEXT REFERENCES sets(set_id),
    packs        INTEGER NOT NULL DEFAULT 1,
    cost         REAL NOT NULL,              -- total paid, USD
    purchased_on TEXT,
    kind         TEXT NOT NULL DEFAULT 'packs', -- packs | box | single | other | before ("Spent before we started tracking")
    created_by   INTEGER,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE scan_batches (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL,
    created_by    INTEGER,
    origin        TEXT,                      -- default 'where you got it' for the batch's cards
    language      TEXT NOT NULL DEFAULT 'en', -- the cards' language, which decides the sets they're matched against
    name          TEXT,                      -- round 10: your name for it; the date when empty
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
, came TEXT, purchase_id INTEGER, new_label TEXT, new_cost REAL, binder TEXT, new_binder TEXT);
CREATE TABLE scans (
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
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    done_at       TEXT,
    rev           INTEGER NOT NULL DEFAULT 0, -- bumped on every change, so the batch page fetches only changed rows
    prev_status   TEXT                        -- a deleted scan's status before, for Put back (round 7)
, checked INTEGER NOT NULL DEFAULT 0, in_binder INTEGER NOT NULL DEFAULT 1, undo_json TEXT, picture_vector BLOB, client_id TEXT);
CREATE TABLE sets (
    set_id        TEXT PRIMARY KEY,          -- TCGdex id, e.g. 'sv03'; 'fr:sv03' for other languages
    language      TEXT NOT NULL DEFAULT 'en',
    name          TEXT NOT NULL,
    series        TEXT,
    release_date  TEXT,
    printed_total INTEGER NOT NULL,          -- number printed on the card ("/197")
    total         INTEGER NOT NULL,          -- including secret rares and subsets
    logo_url      TEXT,
    symbol_url    TEXT,
    cached_at     TEXT
);
CREATE TABLE tcgcsv_cards (
    card_id    TEXT PRIMARY KEY,
    product_id INTEGER NOT NULL,
    group_id   INTEGER NOT NULL,
    image_url  TEXT
);
CREATE TABLE tcgcsv_groups (
    category     INTEGER NOT NULL,   -- 3 English, 85 Japanese
    group_id     INTEGER NOT NULL,
    name         TEXT NOT NULL,
    abbreviation TEXT,
    published_on TEXT,
    PRIMARY KEY (category, group_id)
);
CREATE TABLE tcgcsv_prices (
    card_id    TEXT NOT NULL,
    variant    TEXT NOT NULL,
    market     REAL NOT NULL,
    low        REAL,
    mid        REAL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (card_id, variant)
);
CREATE TABLE tcgcsv_sets (
    set_id      TEXT PRIMARY KEY,    -- our set id
    category    INTEGER NOT NULL,
    group_id    INTEGER NOT NULL,
    matched_by  TEXT NOT NULL,       -- 'code' | 'name'
    products_at TEXT,
    prices_at   TEXT
);
CREATE TABLE tcgdex_cache (
    url        TEXT PRIMARY KEY,
    body       TEXT NOT NULL,
    fetched_at REAL NOT NULL
);
CREATE TABLE theme_settings ( -- one row (id 1) of settings shared by every login
    id              INTEGER PRIMARY KEY,
    only_owned      INTEGER NOT NULL DEFAULT 0,
    shiny_sometimes INTEGER NOT NULL DEFAULT 0,
    updated_by      INTEGER,
    updated_at      TEXT
);
CREATE TABLE user_prefs (
    user_id INTEGER NOT NULL,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE user_theme (    -- no row: always Bulbasaur
    user_id    INTEGER PRIMARY KEY,
    mode       TEXT NOT NULL DEFAULT 'fixed', -- 'fixed' | 'daily'
    species_id INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    collection_id INTEGER,                   -- whose collection they share; their own id for the first account
    session_key   TEXT,                      -- changes with the password, which signs out other devices
    mode          TEXT NOT NULL DEFAULT 'dark', -- round 10: 'dark' or 'light', picked under Account › Theme
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE wants (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    card_id    TEXT NOT NULL REFERENCES cards(id),
    variant    TEXT NOT NULL DEFAULT 'any',
    added_by   INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, card_id, variant)
);
CREATE TABLE worker_jobs (
    id          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL,               -- one of each key at a time, like the app's own jobs
    label       TEXT NOT NULL,
    kind        TEXT NOT NULL,               -- pictures-index, pictures-on, recheck
    arg         TEXT,
    status      TEXT NOT NULL DEFAULT 'waiting',   -- waiting, running, done, failed
    done        INTEGER NOT NULL DEFAULT 0,
    total       INTEGER NOT NULL DEFAULT 0,
    step        TEXT,
    result      TEXT,
    error       TEXT,
    created_at  REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX arts_by_dex ON arts(dex_id);
CREATE INDEX binder_codes_tag ON binder_codes(binder_id, code_tag);
CREATE UNIQUE INDEX binders_share_token ON binders(share_token);
CREATE INDEX card_tags_by_tag ON card_tags(tag);
CREATE INDEX cards_by_set ON cards(set_id, sort_number);
CREATE INDEX chase_links_by_card ON chase_links(collection_id, card_id);
CREATE INDEX chase_photos_by_card ON chase_photos(collection_id, card_id);
CREATE INDEX import_rows_by_batch ON import_rows(batch_id);
CREATE INDEX inventory_log_by_card ON inventory_log(collection_id, card_id);
CREATE INDEX notes_by_card ON notes(collection_id, card_id);
CREATE INDEX picture_learned_card ON picture_learned(card_id, at);
CREATE INDEX prices_by_card ON price_snapshots(card_id, variant, captured_at);
CREATE INDEX scans_by_batch ON scans(batch_id);
CREATE INDEX scans_by_status ON scans(status);
CREATE UNIQUE INDEX scans_client_id ON scans(batch_id, client_id) WHERE client_id IS NOT NULL;
CREATE TRIGGER scans_rev_insert AFTER INSERT ON scans BEGIN
            UPDATE scans SET rev = (SELECT MAX(rev) FROM scans) + 1 WHERE id = NEW.id;
        END;
CREATE TRIGGER scans_rev_update AFTER UPDATE ON scans WHEN NEW.rev = OLD.rev BEGIN
            UPDATE scans SET rev = (SELECT MAX(rev) FROM scans) + 1 WHERE id = NEW.id;
        END;
CREATE INDEX worker_jobs_status ON worker_jobs(status, id);
