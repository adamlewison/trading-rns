# trading-rns

Research: do UK small-cap/AIM shares drift 1–3 days after pre-08:00 RNS news, after costs? US SEC 8-Ks are collected as a comparison market. README.md has the commands and layout; ASSUMPTIONS.md lists the biases (keep it updated).

## Status
- **Phase 1 (data):**
  - UK announcement-text download is running on the droplet.
  - US: metadata, facts and prices are done; the document download is set up via `deploy/setup-us.sh`.
- **Phases 2–5 (LLM classification, features/labels, backtest, live):** not started.

## Working rules
- Go phase by phase. **Stop at the end of each phase**, report, and wait for the user's go-ahead.
- **Data collection only** until told otherwise: no LLM calls, features or backtests.
- Be conservative. Flag anything that could flatter results.
- Never buy anything. Ask before spending: no LLM call costing over £5 without confirmation (`llm.confirm_cost_above_gbp`), and no DigitalOcean upgrades.
- **Scraping:**
  - Use an honest User-Agent and respect robots.txt.
  - Rate limits: Investegate 2 req/s; SEC ~5 req/s (limit 10).
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
  - Logs are in `data/logs/` (`uk_run.out` or the old `full_run.out`, `announcements.log`, `http.log`, `us.log`).
- **One writer per market.** The droplet is the only machine that scrapes or pushes backups. Never run a scrape or `core.backup push` elsewhere while the droplet runs; two writers corrupt the backup.
- **Monitor from anywhere** (read-only):
  `uv run python -W ignore -m core.backup status --remote --profile uk|us`
- **Get the data locally:**
  `core.backup pull --profile uk|us --force` (add `--skip-raw` for tables only), and `pull-bulk --profile us` for the SEC zips.

## Pending
1. When the UK run finishes (log says `PHASE1 FULL RUN COMPLETE`), the droplet is still on the old layout (`data/rns.duckdb`). The user runs:
   ```
   systemctl stop trading-rns-backup trading-rns-scrape
   cd /opt/trading-rns && git pull && bash deploy/migrate-layout.sh
   ```
   Don't `git pull` there before the run finishes. `migrate-layout.sh` refuses to run while it's running.
2. Then pull the UK data and give the final Phase 1 report: row counts per year/category, 10 samples, delisted coverage. Wait for the Phase 2 go-ahead.
3. **UK market makers:** the user downloads LSE's "Registered Market Makers by Security" file by hand (the site blocks scripts). Then run:
   `uv run python -m ingest.uk.trading_service import-mm FILE`
4. Survivorship fix for US prices: options are in the README. Nothing has been bought.
