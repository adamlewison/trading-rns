"""US database schema (data/us.duckdb)."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS sec_form_index (       -- EDGAR quarterly full-index, 8-K & 8-K/A only
    accession VARCHAR PRIMARY KEY, cik BIGINT, company VARCHAR, form VARCHAR, date_filed DATE,
    filename VARCHAR, year INTEGER, qtr INTEGER
);
CREATE TABLE IF NOT EXISTS sec_companies (        -- from submissions.zip (current metadata)
    cik BIGINT PRIMARY KEY, name VARCHAR, tickers VARCHAR, exchanges VARCHAR, sic VARCHAR,
    sic_description VARCHAR, state_of_inc VARCHAR, category VARCHAR, entity_type VARCHAR,
    fiscal_year_end VARCHAR, former_names VARCHAR
);
CREATE TABLE IF NOT EXISTS sec_filings (          -- 8-K/8-K/A rows from submissions.zip (has items)
    accession VARCHAR PRIMARY KEY, cik BIGINT, form VARCHAR, filing_date DATE, report_date DATE,
    acceptance_utc_sub TIMESTAMP,                  -- as published in submissions JSON ("...Z")
    items VARCHAR, primary_doc VARCHAR, is_xbrl BOOLEAN
);
CREATE TABLE IF NOT EXISTS sec_ticker_map (       -- company_tickers_exchange.json (CURRENT only)
    snapshot_date DATE, cik BIGINT, name VARCHAR, ticker VARCHAR, exchange VARCHAR
);
CREATE TABLE IF NOT EXISTS us_filing_docs (       -- one row per fetched filing
    accession VARCHAR PRIMARY KEY, cik BIGINT, item_group VARCHAR,   -- earnings | news
    form VARCHAR, items_header VARCHAR,
    acceptance_et TIMESTAMP, acceptance_utc TIMESTAMP,
    session VARCHAR,                               -- pre_market | intraday | after_close | non_trading_day
    doc_type VARCHAR, doc_name VARCHAR, doc_url VARCHAR,
    text VARCHAR, chars INTEGER,
    status VARCHAR,                                -- ok | no_document | empty | not_found | error
    fetched_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS us_facts (             -- selected XBRL facts from companyfacts.zip
    cik BIGINT, taxonomy VARCHAR, concept VARCHAR, unit VARCHAR, val DOUBLE,
    start_date DATE, end_date DATE, fy INTEGER, fp VARCHAR, form VARCHAR, filed DATE,
    accn VARCHAR, frame VARCHAR
);
CREATE TABLE IF NOT EXISTS us_prices_daily (
    ticker VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
    adj_close DOUBLE, volume DOUBLE, is_stale BOOLEAN, is_suspect BOOLEAN,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS us_price_download_log (
    ticker VARCHAR, yahoo_ticker VARCHAR, attempted_at TIMESTAMP, status VARCHAR,
    n_rows INTEGER, first_date DATE, last_date DATE, error VARCHAR
);
CREATE TABLE IF NOT EXISTS us_progress (
    key VARCHAR PRIMARY KEY, status VARCHAR, n INTEGER, updated_at TIMESTAMP
);
"""
