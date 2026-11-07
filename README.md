# trading-rns: do UK small caps drift after pre-open RNS news?

The hypothesis being tested:

> UK small-cap / AIM shares underreact to RNS announcements published before the 08:00 open, and
> some news types keep drifting for 1–3 days. If so, a rule like "buy ~08:05, +5% target, −3% stop,
> exit by day 3" could be profitable **after realistic costs**.

The aim is an honest answer. Every assumption that might flatter results is listed in
[`ASSUMPTIONS.md`](ASSUMPTIONS.md). Read it before trusting any number.

## Status

| Phase | What | Status |
|---|---|---|
| 1 | Data ingest: universe, prices, announcements | built; full announcement scrape awaiting go-ahead |
| 2 | LLM classification | not started |
| 3 | Features & labels | not started |
| 4 | Backtest | not started |
| 5 | Live alerts | only if the backtest says so |

## Setup

```bash
uv sync                      # Python 3.12, deps from pyproject/uv.lock
cp .env.example .env         # secrets (only needed from Phase 2)
uv run pytest                # tests
```

All settings are in `config.yaml`: date range, rate limits, cost assumptions, model name and
thresholds. Data goes in `data/` (gitignored): one DuckDB file `data/rns.duckdb`, plus raw
downloads cached in `data/raw/` so nothing is ever downloaded twice.

## Phase 1: data ingest

Run these in order. Every step is **resumable**: rerun it and it carries on where it stopped.
DuckDB allows one writer process at a time. Jobs use short connections and wait for the lock, so
two jobs can run side by side, just more slowly.

```bash
# 1. Universe: AIM + FTSE SmallCap proxy (LSE instrument list + monthly issuer snapshots 2011→)
uv run python -m ingest.universe build
uv run python -m ingest.universe report

# 2. Daily prices 2012→ for every universe ticker + ^FTAI (AIM All-Share), ^FTSC, ^FTAS
uv run python -m ingest.prices download          # --limit N, --tickers A,B, --refresh
uv run python -m ingest.prices report

# 3. Announcements
#   a) discover (listing pages only, cheap)
uv run python -m ingest.announcements.run index --mode company            # all universe tickers
uv run python -m ingest.announcements.run index --mode company --sample 25 # pilot
uv run python -m ingest.announcements.run index --mode daily --start 2012-01-01 --end 2012-12-31
#   b) full text for non-admin announcements of universe tickers
uv run python -m ingest.announcements.run bodies --limit 500 --random
#   c) add delisted TIDMs found by daily mode, then materialise tables + report
uv run python -m ingest.universe add-delisted
uv run python -m ingest.announcements.run build
uv run python -m ingest.announcements.run report
```

**Full run** (all of the above, in order, resumable):

```bash
nohup ingest/run_full.sh > data/logs/full_run.out 2>&1 &
```

**Backups** (the container is ephemeral): `core/backup.py` pushes incremental snapshots of the
DuckDB tables and the raw-HTML cache to a private Backblaze B2 bucket. `ingest/resume.sh` restores
from that backup on a fresh machine and restarts the scrape and the hourly backup loop.

```bash
uv run python -m core.backup push | pull | status
ingest/resume.sh
```

**Running the long scrape on your own machine / VPS** (cloud sessions suspend when idle):

```bash
git clone <repo> && cd trading-rns && uv sync
cp .env.example .env            # fill in B2_KEY_ID / B2_APP_KEY
uv run python -m core.backup pull          # restore everything scraped so far
nohup ingest/run_full.sh >> data/logs/full_run.out 2>&1 &   # resumes where it stopped
nohup ingest/backup_loop.sh >> data/logs/backup_loop.out 2>&1 &
tail -f data/logs/announcements.log
```

**Index modes:**
- `company`: walks each universe ticker's announcement list, newest first, until it passes
  `dates.start`. Uses few requests (about 4–5 per ticker), but only covers tickers we already know,
  which means current companies.
- `daily`: walks Investegate's per-day listing. It covers every company, including ones that have
  since delisted, so it's the only way to reduce survivorship bias on the announcement side. It
  takes far more requests.

### Tables (DuckDB)

| table | contents |
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

## Layout

```
core/       config, DuckDB schema, polite cached HTTP client, logging
ingest/     universe, prices, announcements (pluggable sources: ingest/announcements/base.py)
classify/   Phase 2      features/  Phase 3      backtest/  Phase 4      live/  Phase 5
notebooks/  marimo notebooks
tests/      pytest
```

Adding another announcement source means implementing the `AnnouncementSource` protocol in
`ingest/announcements/base.py` and registering it in `ingest/announcements/run.py::SOURCES`.
