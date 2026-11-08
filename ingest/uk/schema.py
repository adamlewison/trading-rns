"""UK database schema (data/uk.duckdb)."""

SCHEMA = """
-- LSE instrument list (current snapshot; has TIDMs)
CREATE TABLE IF NOT EXISTS lse_instruments (
    snapshot_date DATE, tidm VARCHAR, issuer_name VARCHAR, instrument_name VARCHAR, isin VARCHAR,
    icb_industry VARCHAR, icb_supersector VARCHAR, start_date DATE, country VARCHAR,
    currency VARCHAR, lse_market VARCHAR, fca_category VARCHAR, segment_code VARCHAR,
    sector_code VARCHAR
);

-- LSE monthly issuer-list snapshots (no TIDMs, but point-in-time market + market cap)
CREATE TABLE IF NOT EXISTS lse_issuer_snapshots (
    snapshot_date DATE, company_name VARCHAR, name_norm VARCHAR, market VARCHAR,
    admission_date DATE, icb_industry VARCHAR, country VARCHAR, mcap_gbp_m DOUBLE,
    source_file VARCHAR
);

-- Research universe: one row per TIDM
CREATE TABLE IF NOT EXISTS universe (
    tidm VARCHAR PRIMARY KEY,
    name VARCHAR,
    name_norm VARCHAR,
    isin VARCHAR,
    currency VARCHAR,
    market VARCHAR,              -- AIM | MAIN
    grp VARCHAR,                 -- aim | smallcap_proxy
    is_current BOOLEAN,          -- in the latest LSE instrument list
    source VARCHAR,              -- lse_instrument_list | announcement_index
    first_seen DATE,
    last_seen DATE,
    yahoo_ticker VARCHAR,
    notes VARCHAR
);

-- Daily prices (split-adjusted OHLC from Yahoo; adj_close also dividend-adjusted)
CREATE TABLE IF NOT EXISTS prices_daily (
    tidm VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
    adj_close DOUBLE, volume DOUBLE,
    is_stale BOOLEAN,            -- zero volume or O=H=L=C (no real trading)
    is_suspect BOOLEAN,          -- likely GBP/GBp unit glitch or impossible bar
    PRIMARY KEY (tidm, date)
);

CREATE TABLE IF NOT EXISTS price_download_log (
    tidm VARCHAR, yahoo_ticker VARCHAR, attempted_at TIMESTAMP, status VARCHAR,
    n_rows INTEGER, first_date DATE, last_date DATE, error VARCHAR
);

-- Every announcement discovered from a source's listing pages (incl. admin notices)
CREATE TABLE IF NOT EXISTS ann_index (
    ann_id VARCHAR PRIMARY KEY,  -- '<source>:<source_id>'
    source VARCHAR, source_id VARCHAR, tidm VARCHAR, company VARCHAR, headline VARCHAR,
    url VARCHAR, supplier VARCHAR, is_regulatory BOOLEAN,
    published_uk TIMESTAMP,      -- Europe/London wall-clock time as shown by the source
    published_utc TIMESTAMP,
    pre_open BOOLEAN,            -- weekday and published_uk < 08:00
    headline_category VARCHAR,   -- rule-based, from headline (source has no RNS code)
    is_admin BOOLEAN, admin_rule VARCHAR,
    is_replacement BOOLEAN,
    discovered_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ann_bodies (
    ann_id VARCHAR PRIMARY KEY, body_text VARCHAR, body_chars INTEGER,
    status VARCHAR,              -- ok | empty | error | not_found
    fetched_at TIMESTAMP
);

-- Resumability for listing scrapes
CREATE TABLE IF NOT EXISTS scrape_progress (
    source VARCHAR, key VARCHAR, status VARCHAR, n_rows INTEGER, updated_at TIMESTAMP,
    PRIMARY KEY (source, key)
);

-- LSE trading service (SETS / SETSqx / ...) per ticker, and registered market makers (ingest/uk/trading_service.py)
CREATE TABLE IF NOT EXISTS lse_service_map (
    version_date DATE, trading_service VARCHAR, segment_code VARCHAR, segment_name VARCHAR,
    sector_code VARCHAR, sector_name VARCHAR
);
CREATE TABLE IF NOT EXISTS uk_trading_service (       -- one row per ticker per instrument-list snapshot
    snapshot_date DATE, tidm VARCHAR, segment_code VARCHAR, sector_code VARCHAR,
    trading_service VARCHAR, map_version DATE,
    PRIMARY KEY (snapshot_date, tidm)
);
CREATE TABLE IF NOT EXISTS uk_market_makers (         -- long format: one row per (security, market maker)
    as_of DATE, tidm VARCHAR, isin VARCHAR, market_maker VARCHAR, source_file VARCHAR
);
"""
