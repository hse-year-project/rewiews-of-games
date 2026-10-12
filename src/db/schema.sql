CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS clean;
CREATE SCHEMA IF NOT EXISTS meta;

CREATE TABLE IF NOT EXISTS core.games (
    appid             integer PRIMARY KEY,
    name              text,
    type              text,
    is_free           boolean,
    release_date      date,
    developers        text[],
    publishers        text[],
    genres            text[],
    categories        text[],
    group_tag         text,
    fetched_at        timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS core.game_prices (
    appid             integer NOT NULL,
    country_code      text NOT NULL,
    currency          text,
    price_initial     integer,          -- in cents
    price_final       integer,
    discount_percent  integer,
    fetched_at        timestamptz NOT NULL,
    PRIMARY KEY (appid, country_code)
);

CREATE TABLE IF NOT EXISTS core.reviews (
    recommendation_id               bigint PRIMARY KEY,
    appid                           integer NOT NULL,
    language                        text NOT NULL,
    review                          text,
    voted_up                        boolean NOT NULL,
    votes_up                        integer,
    votes_funny                     integer,
    weighted_vote_score             double precision,
    comment_count                   integer,
    steam_purchase                  boolean,
    received_for_free               boolean,
    refunded                        boolean,
    written_during_early_access     boolean,
    primarily_steam_deck            boolean,
    created_at                      timestamptz NOT NULL,
    updated_at                      timestamptz NOT NULL,
    author_steamid                  bigint,
    author_num_games_owned          integer,
    author_num_reviews              integer,
    author_playtime_forever         integer,   -- minutes
    author_playtime_at_review       integer,
    author_playtime_last_two_weeks  integer,
    author_last_played              timestamptz,
    fetched_at                      timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS reviews_app_lang_created ON core.reviews (appid, language, created_at);
CREATE INDEX IF NOT EXISTS reviews_author ON core.reviews (author_steamid);

CREATE TABLE IF NOT EXISTS core.review_summaries (
    appid             integer NOT NULL,
    language          text NOT NULL,
    fetched_at        timestamptz NOT NULL,
    total_reviews     integer,
    total_positive    integer,
    total_negative    integer,
    review_score_desc text,
    PRIMARY KEY (appid, language, fetched_at)
);

CREATE TABLE IF NOT EXISTS core.news (
    gid               text PRIMARY KEY,
    appid             integer NOT NULL,
    title             text,
    url               text,
    author            text,
    contents          text,
    feedlabel         text,
    feedname          text,
    tags              text[],
    is_patch          boolean NOT NULL DEFAULT false,
    published_at      timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS news_app_date ON core.news (appid, published_at);

CREATE TABLE IF NOT EXISTS core.player_counts (
    appid             integer NOT NULL,
    ts                timestamptz NOT NULL,
    players           integer NOT NULL,
    PRIMARY KEY (appid, ts)
);

CREATE TABLE IF NOT EXISTS meta.review_collection (
    appid                 integer NOT NULL,
    language              text NOT NULL,
    status                text NOT NULL,
    last_cursor           text,                   -- next page to fetch
    watermark_updated_at  timestamptz,            -- newest updated_at seen in last finished run
    run_max_updated_at    timestamptz,            -- newest updated_at seen in the current run
    n_items               integer NOT NULL DEFAULT 0,
    started_at            timestamptz NOT NULL,
    finished_at           timestamptz,
    PRIMARY KEY (appid, language)
);

CREATE TABLE IF NOT EXISTS meta.cleaning_report (
    run_id            text NOT NULL,
    rule              text NOT NULL,
    appid             integer NOT NULL,
    language          text NOT NULL,
    n_total           integer NOT NULL,
    n_flagged         integer NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, rule, appid, language)
);

CREATE TABLE IF NOT EXISTS clean.review_flags (
    recommendation_id     bigint PRIMARY KEY,
    is_empty              boolean NOT NULL,
    is_junk               boolean NOT NULL,
    is_duplicate_text     boolean NOT NULL,
    lang_detected         text,
    lang_conf             double precision,
    is_lang_mismatch      boolean NOT NULL,
    text_language         text,                   -- language of the text (`language` = Steam client)
    is_edited             boolean NOT NULL,
    keep                  boolean NOT NULL
);
