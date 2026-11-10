# trading-rns

Goal: an automated trading system that makes money after costs. Not tied to a market or strategy; whatever wins gets traded. Strategy 1: Claude labels company news released outside market hours, a model scores it, buy at the open with a bracket (+5% / −3% / 3 days). Two candidate markets on equal terms: **US** (SEC 8-Ks, IBKR; low costs, likely the stronger candidate) and **UK** (RNS on AIM/small caps; high costs). README.md has the commands and layout; ASSUMPTIONS.md lists the biases (keep it updated).

## Status
- **Phase 1 (data):**
  - UK: **complete** (2026-11-09): 314,339 announcements + 252,544 admin, 147,980 bodies, prices for 874 tickers, SETS/SETSqx map. 47 new bodies skipped (Investegate 403s on administration notices). Only 43 of 420 delisted companies have prices (see ASSUMPTIONS.md). Final report given; waiting for the Phase 2 go-ahead.
  - US: metadata, facts and prices are done; filing documents downloading on the droplet (2 workers sharing the 8 req/s cap, `us.workers`). Earnings first, then news (~524k filings).
- **Plan revised 2026-11-09 (PLAN.md):** model inputs = Claude labels + numbers from the release + earnings surprise + point-in-time XBRL financials + 8-K item codes + price context; a no-Claude baseline must be beaten; exit rules fixed in advance; walk-forward with 2022–26 touched once.
- **Phase 2:** user approved the US earnings pilot (Haiku 5.5 batch, anonymised, 500 + 50 leak checks, ~£0.24). Built (`classify/`). The Anthropic key can't be stored in cloud sessions, so the paid step runs on the droplet (`run-remote`, key in `/opt/trading-rns-us/.env`); prepare/report run here. Prepared run: `us-earnings-pilot-20261109T224927` (in B2 under `trading-rns/llm/v1/`). Full runs need a separate OK.
- **Droplet memory (2026-11-10):** the US download died twice out of memory (1GB, no swap?). Fixes pushed: DuckDB caps in backup (300MB) and downloader (200MB), flush retry, run_all retries exit 1 twice. Asked the user to add a 2GB swapfile and send the error lines from us_run.out.
- **Phases 3–5:** not started.

## Working rules
- Go phase by phase. **Stop at the end of each phase**, report, and wait for the user's go-ahead.
- No features or backtests until told. LLM calls only as approved (the US earnings pilot is approved; full runs need a separate OK).
- Be conservative. Flag anything that could flatter results.
- Never buy anything. Ask before spending: no LLM call costing over £5 without confirmation (`llm.confirm_cost_above_gbp`), and no DigitalOcean upgrades.
- **Scraping:**
  - Use an honest User-Agent and respect robots.txt.
  - Rate limits: Investegate 2 req/s; SEC max 8 req/s (limit 10).
  - On 403/429, back off, then stop. **Never evade a block**; report it to the user.
- **Never delete scraped data.**
  - Everything goes to Backblaze B2.
  - Only US cached pages may be pruned locally, and only after upload (`--prune-raw`).
- Investegate text is personal-use only. Never publish or commit it; `data/` is gitignored.
- Secrets live in `.env` (B2_KEY_ID/B2_APP_KEY, SEC_USER_AGENT). Never put them in chat or commits.
- Run `uv run pytest` before pushing.
- Develop on branch `claude/uk-smallcap-rns-drift-i83n19`.

## Where things run
- **Droplet** `trading-rns` (DigitalOcean, 178.62.96.171, 1 vCPU / 1GB / 25GB disk):
  - The user connects via Termius. Cloud sessions can't SSH, so give them commands to run.
  - UK checkout `/opt/trading-rns`: services `trading-rns-scrape` and `trading-rns-backup`.
  - US checkout `/opt/trading-rns-us`: services `trading-rns-us` and `trading-rns-us-backup`.
  - Logs are in `data/logs/` (`uk_run.out`, `announcements.log`, `http.log`, `us.log`).
- **One writer per market.** The droplet is the only machine that scrapes or pushes backups. Never run a scrape or `core.backup push` elsewhere while the droplet runs; two writers corrupt the backup.
- **Monitor from anywhere** (read-only):
  `uv run python -W ignore -m core.backup status --remote --profile uk|us`
- **Get the data locally:**
  `core.backup pull --profile uk|us --force` (add `--skip-raw` for tables only), and `pull-bulk --profile us` for the SEC zips. After a UK pull, run `uv run python -m ingest.uk.announcements.run build` (the `announcements` tables are derived, not backed up).

## Pending
1. Pilots done 2026-11-10 (v1 `us-earnings-pilot-20261109T224927`, v2 `us-earnings-pilot-v2-20261109T233820`: EPS 98%, buyback ~95%, guidance objective). **Full run approved by the user 2026-11-10 ("go £71")**: `us-earnings-full-v2-20261110T103550`, 150,518 tradeable releases (out of hours, priced on the date) in 76 chunks of 2,000, estimate £73.06, hard cap £85 (`classify/us_earnings_full.py`). Runs on the droplet: `uv run python -W ignore -m classify.us_earnings_full run NAME --max-gbp 85` (resumable, state.json in B2); check from here with `... us_earnings_full status NAME`. Then: collect the results into a labels table and report quality/cost. (An abandoned first prepare left 2-5 chunk files under `us-earnings-full-v2-20261110T102959` in B2: never sent, ignore.)
2. When `us_run.out` says `US FULL RUN COMPLETE`, pull the US data and give the final US Phase 1 report. Phase 2 needs the user's go-ahead (the earnings filings are enough for the pilot).
3. **UK market makers:** the user downloads LSE's "Registered Market Makers by Security" file by hand (the site blocks scripts). Then run:
   `uv run python -m ingest.uk.trading_service import-mm FILE`
4. Survivorship fix for US prices: options are in the README. Nothing has been bought.
