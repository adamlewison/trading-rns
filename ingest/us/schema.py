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
CREATE TABLE IF NOT EXISTS us_splits (            -- Yahoo split events; ratio 4.0 = 4-for-1
    ticker VARCHAR, date DATE, ratio DOUBLE
);
CREATE TABLE IF NOT EXISTS us_split_log (
    ticker VARCHAR, attempted_at TIMESTAMP, status VARCHAR, n_splits INTEGER, error VARCHAR
);
CREATE TABLE IF NOT EXISTS us_insider_tx (       -- Form 4 non-derivative transactions (SEC data sets)
    accession VARCHAR, quarter VARCHAR, filing_date DATE, doc_type VARCHAR, issuer_cik BIGINT,
    issuer_name VARCHAR, issuer_ticker VARCHAR, owner_ciks VARCHAR, owner_names VARCHAR,
    relationship VARCHAR, titles VARCHAR, is_director INTEGER, is_officer INTEGER, is_ten_pct INTEGER,
    n_owners INTEGER, aff10b5one BOOLEAN, trans_sk BIGINT, security_title VARCHAR, trans_date DATE,
    trans_code VARCHAR, acq_disp VARCHAR, shares DOUBLE, price DOUBLE, shares_after DOUBLE,
    direct_indirect VARCHAR
);
CREATE TABLE IF NOT EXISTS us_etf_daily (          -- whole-history ETF prices (ETF trend test)
    ticker VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, adj_close DOUBLE, volume DOUBLE
);
CREATE TABLE IF NOT EXISTS paper_picks (           -- paper trading: what the frozen rules picked
    variant VARCHAR, scored_on DATE, entry_date DATE, rank INTEGER, ticker VARCHAR, score DOUBLE,
    entry_adj DOUBLE, dvol20 DOUBLE, recorded_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS paper_marks (           -- paper trading: each month's result vs SPY
    variant VARCHAR, entry DATE, until DATE, ret DOUBLE, spy DOUBLE, open BOOLEAN, updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS us_progress (
    key VARCHAR PRIMARY KEY, status VARCHAR, n INTEGER, updated_at TIMESTAMP
);
"""
