# trading-rns

**Mission: find an algorithmic trading strategy that makes money after costs and beats an index
fund. When a track fails, record why and switch tracks. Don't stop until one passes.**

- `PLAN.md`: the goal, what we've learnt, what we've tried, what's next (in priority order), the
  testing rules. **Keep it current**: add each lesson and each result there.
- `ASSUMPTIONS.md`: data details and every bias that could flatter results. Keep it current.
- `README.md`: layout and commands. Each tested strategy has a design file
  (e.g. `backtest/DESIGN_US_EARNINGS.md`) written before running, with every change and the result.

## Status (2026-11-12)
- **Data:** UK done (RNS 2012–2026, prices for 874 tickers, SETS/SETSqx). US done (8-Ks 2012–2026,
  XBRL facts, prices for 7,627 tickers, split histories).
- **Tried:** US earnings drift (Luna labels, 6 exit rules × 3 models, $6k account): **failed** the
  final test on 2022–2026. Its final years are spent.
- **Next:** insider buying (US Form 4 data sets + UK director dealings), then no-news reversals and
  the pre-earnings run-up (`PLAN.md`). Waiting for the user's go-ahead.
- **The user's account:** ~$6,000, ~$150 per trade, Trading 212, holds up to ~20 days.
- Rebuild labels + model table if the last ~419 Luna labels arrive (`classify.labels build`, then
  `features.us_earnings_labels build`).

## Working rules
- **Stop at the end of each phase** (data, labels, features, backtest, live), report, and wait for
  the user's go-ahead.
- **Every test:** design file committed before running; walk-forward; choose on 2019–2021; final test
  on 2022–2026 once, setup frozen; report every variant; flag anything that could flatter results.
- Be honest about results. Never risk real money before a clean final test, paper trading first.
- **Spending:** never buy anything. No LLM run over £5 without the user's OK
  (`llm.confirm_cost_above_gbp`); no DigitalOcean upgrades.
- **Scraping:** honest User-Agent, respect robots.txt. Investegate 2 req/s; SEC max 8 req/s.
  On 403/429 back off, then stop. **Never evade a block**; report it.
- **Never delete scraped data.** Everything goes to Backblaze B2. Only US cached pages may be pruned
  locally after upload (`--prune-raw`).
- Investegate text is personal-use only: never publish or commit it (`data/` is gitignored).
- Secrets live in `.env` or the environment's credential settings, never in chat or commits.
- Run `uv run pytest` before pushing. Branch: `claude/uk-smallcap-rns-drift-i83n19`.

## Where things run
- **Droplet** `trading-rns` (DigitalOcean, 178.62.96.171, 1 vCPU / 1GB / 25GB, 2GB swap). The user
  connects with Termius; cloud sessions can't SSH, so give the user commands to run.
  - UK checkout `/opt/trading-rns` (services `trading-rns-scrape`, `trading-rns-backup`).
  - US checkout `/opt/trading-rns-us` (services `trading-rns-us`, `trading-rns-us-backup`); API keys in
    its `.env`. Long one-off jobs: `systemd-run --unit=NAME -p Restart=on-failure ...` so they survive.
  - Logs in `data/logs/`.
- **One writer per market:** only the droplet scrapes or pushes backups. Never run a scrape or
  `core.backup push` elsewhere while it runs.
- **Monitor (read-only):** `uv run python -W ignore -m core.backup status --remote --profile uk|us`
- **Get data locally:** `core.backup pull --profile uk|us --force` (`--skip-raw` for tables only);
  `pull-bulk --profile us` for the SEC zips. After a UK pull run
  `uv run python -m ingest.uk.announcements.run build`.
- **DuckDB on the droplet:** every connection goes through `core.db.connect` (`ROW_GROUP_SIZE 8192`),
  or a checkpoint can run the 1GB machine out of memory. Repair: `core.dbcheck --profile us --checkpoint`.

## Open items
- UK market makers: the user downloads LSE's "Registered Market Makers by Security" file by hand;
  then `uv run python -m ingest.uk.trading_service import-mm FILE`.
- Survivorship fix (delisted prices): options in the README. Nothing bought.
