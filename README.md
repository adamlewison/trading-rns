# trading-rns: do small caps drift after pre-open news?

The hypothesis being tested:

> UK small-cap / AIM shares underreact to RNS announcements published before the 08:00 open, and
> some news types keep drifting for 1–3 days. If so, a rule like "buy ~08:05, +5% target, −3% stop,
> exit by day 3" could be profitable **after realistic costs**.

The UK is the primary market. US companies' SEC 8-K filings (earnings and other material news) are
collected the same way, as a second market to compare against later.

The aim is an honest answer. Every assumption that might flatter results is listed in
[`ASSUMPTIONS.md`](ASSUMPTIONS.md). Read it before trusting any number.

## Status

| Phase | What | UK | US |
|---|---|---|---|
| 1 | Data collection | announcement text downloading | filings metadata, facts and prices done; documents ready to start |
| 2 | LLM classification | not started | not started |
| 3 | Features & labels | not started | not started |
| 4 | Backtest | not started | not started |
| 5 | Live alerts | only if the backtest says so | |

## Layout

```
config.yaml        all settings: shared ones first, then one section per market (uk:, us:)
core/              market-agnostic engine
  config.py          load config.yaml + .env; market("uk"|"us") gives one market's settings
  db.py              DuckDB connections (one writer per file, waits for the lock)
  http.py            polite cached HTTP client: honest UA, robots.txt, rate limit, stops on refusals
  backup.py          incremental Backblaze B2 backup / restore / remote status, per market
  log.py
ingest/            Phase 1 data collection
  shared/prices.py   Yahoo daily prices: tidy + stale/suspect flags (both markets)
  uk/                LSE universe, prices, RNS announcements (Investegate), SETS/SETSqx
  us/                SEC EDGAR 8-K filings, documents, company facts, prices
    each market has the same shape:
      common.py        its config, database session and HTTP client
      schema.py        its tables
      backup.py        what it backs up + its remote status report
      run_all.sh       every step in order (resumable)
classify/ features/ backtest/ live/    Phases 2–5 (not started)
deploy/            droplet setup: setup-uk.sh, setup-us.sh, backup-loop.sh, cloud-init.yaml,
                   migrate-layout.sh (one-time move of older checkouts to this layout)
tests/             pytest, mirroring the code: core/, shared/, uk/, us/
```

Each market has its own database and raw-download cache, so the two never contend for a lock:

| | UK | US |
|---|---|---|
| database | `data/uk.duckdb` | `data/us.duckdb` |
| raw cache | `data/raw/uk/` | `data/raw/us/` |
| backup folder (B2) | `trading-rns/v1/` | `trading-rns/us/v1/` |
| run everything | `ingest/uk/run_all.sh` | `ingest/us/run_all.sh` |
| droplet setup | `deploy/setup-uk.sh` | `deploy/setup-us.sh` |

`data/` is gitignored. Scraped text is for personal research only and is never published.

## Setup

```bash
uv sync                      # Python 3.12, deps from pyproject/uv.lock
cp .env.example .env         # B2 keys, SEC_USER_AGENT="Name email"; LLM keys from Phase 2
uv run pytest                # tests
```

Every step below is **resumable**: rerun it and it carries on where it stopped. Every response is
cached, so nothing is downloaded twice.

## UK: AIM + FTSE SmallCap proxy, RNS announcements

```bash
# 1. Universe: AIM + FTSE SmallCap proxy (LSE instrument list + monthly issuer snapshots 2011→)
uv run python -m ingest.uk.universe build
uv run python -m ingest.uk.universe report

# 2. Daily prices 2012→ for every universe ticker + ^FTAI (AIM All-Share), ^FTSC, ^FTAS
uv run python -m ingest.uk.prices download          # --limit N, --tickers A,B, --refresh
uv run python -m ingest.uk.prices report

# 3. Announcements
#   a) discover (listing pages only, cheap)
uv run python -m ingest.uk.announcements.run index --mode company            # all universe tickers
uv run python -m ingest.uk.announcements.run index --mode company --sample 25 # pilot
uv run python -m ingest.uk.announcements.run index --mode daily --start 2012-01-01 --end 2012-12-31
#   b) full text for pre-open, non-admin announcements of universe tickers
uv run python -m ingest.uk.announcements.run bodies --limit 500 --random
#   c) add delisted TIDMs found by daily mode, then materialise tables + report
uv run python -m ingest.uk.universe add-delisted
uv run python -m ingest.uk.announcements.run build
uv run python -m ingest.uk.announcements.run report

# 4. Trading service (SETS / SETSqx) per ticker, and registered market makers
uv run python -m ingest.uk.trading_service build
uv run python -m ingest.uk.trading_service import-mm FILE.xlsx   # LSE file, downloaded by hand
uv run python -m ingest.uk.trading_service report

# all of the above, in order:
nohup ingest/uk/run_all.sh >> data/logs/uk_run.out 2>&1 &
```

**Index modes:**
- `company`: walks each universe ticker's announcement list, newest first, until it passes
  `dates.start`. Uses few requests (about 4–5 per ticker), but only covers tickers we already know,
  which means current companies.
- `daily`: walks Investegate's per-day listing. It covers every company, including ones that have
  since delisted, so it's the only way to reduce survivorship bias on the announcement side. It
  takes far more requests.

Adding another announcement source means implementing the `AnnouncementSource` protocol in
`ingest/uk/announcements/base.py` and registering it in `ingest/uk/announcements/run.py::SOURCES`.

| table (`data/uk.duckdb`) | contents |
|---|---|
| `lse_instruments` | LSE instrument list snapshot (TIDM, ISIN, market, FTSE sector codes) |
| `lse_issuer_snapshots` | monthly LSE company lists 2011→ (name, market, market cap), no TIDM |
| `universe` | one row per TIDM: `grp` = `aim` / `smallcap_proxy`, `is_current`, `yahoo_ticker` |
| `prices_daily` | split-adjusted daily OHLCV + `is_stale`, `is_suspect` flags |
| `price_download_log` | every download attempt: ok / empty / error |
| `ann_index` | every announcement discovered, including admin ones, with derived fields |
| `ann_bodies` | full text (ok / empty / not_found / error) |
| `announcements` | non-admin announcements of universe tickers (index + body) |
| `announcements_admin` | admin notices kept separately (no body) |
| `scrape_progress` | resumability for listing scrapes |
| `lse_service_map`, `uk_trading_service` | (segment, sector) → SETS / SETSqx, and each ticker's service per snapshot |
| `uk_market_makers` | registered market makers per security (from the hand-downloaded LSE file) |

## US: SEC EDGAR 8-K filings (data collection only)

```bash
# needs SEC_USER_AGENT="Name email" in .env (SEC fair-access policy)
uv run python -m ingest.us.run index          # EDGAR quarterly full-index 2012→: every 8-K / 8-K/A
uv run python -m ingest.us.run tickers        # CIK ↔ ticker ↔ exchange (current companies only)
uv run python -m ingest.us.run submissions    # bulk submissions.zip: items + filing metadata
uv run python -m ingest.us.run estimate       # counts, disk and time estimate
uv run python -m ingest.us.run docs --group earnings --yes   # Item 2.02 → filing index + Ex-99.1
uv run python -m ingest.us.run docs --group news --yes       # 1.01 / 2.01 / 7.01 / 8.01
uv run python -m ingest.us.run facts          # bulk companyfacts.zip → selected XBRL concepts
uv run python -m ingest.us.run prices         # yfinance daily OHLCV, NYSE + Nasdaq, + SPY, IWM
uv run python -m ingest.us.run report

# the document download, in order:
nohup ingest/us/run_all.sh >> data/logs/us_run.out 2>&1 &
```

Code: `filings.py` (index, tickers, submissions), `documents.py`, `facts.py`, `prices.py`,
`report.py`, `parse.py` (pure parsing, tested); `run.py` is the command line.

- **Rate:** about 5 requests per second to sec.gov (SEC's limit is 10), with an honest User-Agent,
  every response cached, resumable, and a stop after 3 consecutive refusals.
- **Timestamps:** the acceptance time comes from each filing's `-index.htm` page, which shows US
  Eastern time, and is converted to UTC with DST handled. `session` is `pre_market` (before 09:30 ET),
  `intraday` (09:30–16:00), `after_close` (16:00 or later) or `non_trading_day` (weekend). Exchange
  holidays are not flagged yet; they'll be flagged from SPY's price calendar later.
- **Documents:** Ex-99.1 (or the first EX-99.x) when present, otherwise the 8-K itself.
- **Disk:** the US download stops cleanly (exit 4) if free disk drops below `us.min_free_gb` (3GB).

| table (`data/us.duckdb`) | contents |
|---|---|
| `sec_form_index` | every 8-K / 8-K/A in EDGAR's quarterly full-index (CIK, name, form, date, accession) |
| `sec_companies`, `sec_filings` | per company and per filing, from bulk submissions (items, primary document) |
| `sec_ticker_map` | CIK ↔ ticker ↔ exchange (current companies only) |
| `us_filing_docs` | acceptance time (ET + UTC), session, items, document text |
| `us_facts` | selected XBRL facts with the `filed` date (point-in-time) |
| `us_prices_daily`, `us_price_download_log` | daily OHLCV with the same flags as the UK prices |
| `us_progress` | resumability |

### US survivorship bias: options to fix it later (nothing bought)

yfinance has **no price history for delisted US stocks**, so a backtest on today's tickers only
sees survivors. EDGAR itself does include delisted companies, so the filings side is fine.
Options for the prices, with rough prices as of Nov 2026 (check current pricing before buying):

| Vendor | What | Rough cost |
|---|---|---|
| Norgate Data (Platinum) | US EOD incl. ~25k delisted, back to 1990; Windows-only, rented | ~$630 / year |
| Sharadar (Nasdaq Data Link) | US EOD + fundamentals, delisted with dates/reasons, from 1997 | subscription; price not found, check |
| FirstRate Data | ~16k US tickers from 2000, ~7k delisted, one-time purchase | ~$240 one-time |
| FinzData | whole-US-market EOD bundles incl. 8.5k+ delisted, one-time | ~$195 (5 yrs) – $670 (30 yrs) |
| Quandl/Nasdaq WIKI archive | free, includes delisted, but **frozen at March 2018** | free |
| CRSP (via WRDS) | the academic gold standard | institutional or academic only |

## Backups (both markets)

`core/backup.py` pushes incremental snapshots of a market's tables and its raw-download cache to a
private Backblaze B2 bucket, and uploads a small status file so a run can be watched remotely.

```bash
uv run python -m core.backup push   --profile uk            # or us; --prune-raw (us only)
uv run python -m core.backup pull   --profile uk            # restore; --skip-raw = tables only
uv run python -m core.backup status --profile uk --remote   # the droplet's latest status file
uv run python -m core.backup push-bulk --profile us         # bulk SEC zips, in 200MB chunks
deploy/backup-loop.sh uk                                    # push every 30 minutes
```

- **Nothing scraped is ever thrown away.** A local file is only deleted after its upload succeeded
  (B2 checks the SHA1), and backed-up pages are never overwritten. A push refuses to run without a
  database, and never replaces a non-empty table snapshot with an empty one.
- **US disk saving:** the US backup loop runs with `--prune-raw`. Every 30 minutes the cached SEC
  pages are uploaded and then deleted locally, so the droplet keeps only the extracted text (about
  4–6GB instead of about 20GB). `pull` brings them back. The UK cache is never pruned.
- **Bulk files** (SEC's submissions.zip and companyfacts.zip) are backed up in 200MB chunks, one
  folder per dated version, and `pull-bulk` checks the SHA-256 when restoring.

## Running on the droplet

Each market runs as two systemd services, the data download and its backup loop:
`trading-rns-scrape` + `trading-rns-backup` (UK, checkout `/opt/trading-rns`) and `trading-rns-us` +
`trading-rns-us-backup` (US, checkout `/opt/trading-rns-us`).

```bash
bash deploy/setup-uk.sh      # asks for the GitHub token and B2 keys the first time only
bash deploy/setup-us.sh      # separate checkout; asks for SEC_USER_AGENT if missing
```

Checkouts from before the UK/US layout (with `data/rns.duckdb`) are moved over once with
`git pull && bash deploy/migrate-layout.sh`, **after** the UK scrape has finished. It refuses to
run while the scrape is running.
