# trading-rns

Goal: an automated trading system that makes money after costs. Not tied to a market or strategy; whatever wins gets traded. Strategy 1: Claude labels company news released outside market hours, a model scores it, buy at the open with a bracket (+5% / −3% / 3 days). Two candidate markets on equal terms: **US** (SEC 8-Ks, IBKR; low costs, likely the stronger candidate) and **UK** (RNS on AIM/small caps; high costs). README.md has the commands and layout; ASSUMPTIONS.md lists the biases (keep it updated).

## Status
- **Phase 1 (data):**
  - UK: **complete** (2026-11-09): 314,339 announcements + 252,544 admin, 147,980 bodies, prices for 874 tickers, SETS/SETSqx map. 47 new bodies skipped (Investegate 403s on administration notices). Only 43 of 420 delisted companies have prices (see ASSUMPTIONS.md). Final report given; waiting for the Phase 2 go-ahead.
  - US: metadata, facts and prices are done; filing documents downloading on the droplet (2 workers sharing the 8 req/s cap, `us.workers`). Earnings first, then news (~524k filings).
- **Plan revised 2026-11-09 (PLAN.md):** model inputs = Claude labels + numbers from the release + earnings surprise + point-in-time XBRL financials + 8-K item codes + price context; a no-Claude baseline must be beaten; exit rules fixed in advance; walk-forward with 2022–26 touched once.
- **Phase 2:** user approved the US earnings pilot (Haiku 5.5 batch, anonymised, 500 + 50 leak checks, ~£0.24). Built (`classify/`). The Anthropic key can't be stored in cloud sessions, so the paid step runs on the droplet (`run-remote`, key in `/opt/trading-rns-us/.env`); prepare/report run here. Prepared run: `us-earnings-pilot-20261109T224927` (in B2 under `trading-rns/llm/v1/`). Full runs need a separate OK.
- **Droplet DB failures (2026-11-10), root cause found:** crossing a 122,880-row group boundary in `us_filing_docs` makes DuckDB compress the whole group at checkpoint (135s on 16GB; out of scratch on the 1GB droplet). Fix: `core.db.connect` attaches every DB with `ROW_GROUP_SIZE 8192` (must be used by EVERY connection, else a checkpoint merges groups back). Also: memory caps, flush retry, run_all retries, 2GB swapfile added by the user. Repair step: `core.dbcheck --profile us --checkpoint`.
- **Phase 3 (started 2026-11-11 at the user's request, while labels finish):** `features/us_earnings.py build` -> `data/features/us_earnings_events.parquet` (150,518 rows: trade timing, outcomes for the 4 exit rules net of SPY/IWM, price context, point-in-time financials, 8-K item flags, EPS history). To do: join Luna labels (SUE, guidance change vs previous guidance), split histories for valuation ratios (droplet download after the news run). Phases 4–5 not started.

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
1. Phase 2 labels. Prompt v4 (latest quarter; full-year GAAP guidance first, basis recorded; disclaimers stripped). Model test 2026-11-10 on the same 500 (`classify/gateway_test.py`): Haiku 5.5 EPS 98%, buybacks 89%, £0.194/500; GPT-5 nano worse (EPS 90%); Gemini 2.5 Flash-Lite ignored the schema in gateway batches; **GPT-6 Luna (reasoning none) EPS 98% (99% identical to Haiku), buybacks 97%, £0.127/500**. **User chose Luna** (approved full run, "switch to Luna"). Runner: `classify/openai_full.py` (OpenAI Batch API from the cloud session; key injected by the proxy; reuses chunks of `us-earnings-full-v4-20261110T123421`; jobs of 500; state in B2 under `.../openai-gpt-6-luna/`; resumable; cap £55; estimate £39.89). **Started 2026-11-10 18:23 UTC after the user added credit** (runs in the background of the cloud session; check on each hourly check-in and re-run `run` if the process is gone). Commands: `... -m classify.openai_full estimate|run --max-gbp 55|status`. If the cloud container restarts, re-run `run` (it resumes). Then: collect results into a labels table, report quality/cost. The Haiku runner (`classify/us_earnings_full.py`) stays as a fallback.
2. When `us_run.out` says `US FULL RUN COMPLETE`, pull the US data and give the final US Phase 1 report. Phase 2 needs the user's go-ahead (the earnings filings are enough for the pilot).
3. **UK market makers:** the user downloads LSE's "Registered Market Makers by Security" file by hand (the site blocks scripts). Then run:
   `uv run python -m ingest.uk.trading_service import-mm FILE`
4. Survivorship fix for US prices: options are in the README. Nothing has been bought.
