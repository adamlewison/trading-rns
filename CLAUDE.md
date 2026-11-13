# trading-rns: master doc

**Mission: find an algorithmic trading strategy that makes money after costs and beats an index
fund.** Not tied to any market, data source or idea. When a track fails, record why here and switch
tracks. We don't stop until one passes.

## How to use this doc (every agent, Claude or not)
This is the **only** project doc (`AGENTS.md` is a link to it). Read it fully before starting work.
It is the project's memory: what isn't written here is lost between sessions.

**Update it as you work, not at the end.** Whenever you learn or decide something, write it here
in the same commit as the work:
- a **result** of a test → section 6 (what we've tried), one row, with the record file;
- a **lesson** (something that would change how we work next time) → section 4;
- a **new idea or strategy** → section 7, placed in priority order with a one-line reason;
- a **finding about the data or a bias** → section 8;
- **status**, the user's preferences (account size, broker, hold length) and what's waiting → section 1;
- new **commands, files or infrastructure** → section 9.

**Keep it short and useful.** Aim to stay under ~250 lines. When you add something, also tidy:
- summarise older or finished material to a line or two (details stay in git history and the
  test's design file);
- merge duplicates, delete what's no longer true, keep numbers that matter;
- prefer one clear sentence to a paragraph; write for a reader with no memory of past sessions.

Each tested strategy also gets a design file written and committed before it runs (e.g.
`backtest/DESIGN_US_EARNINGS.md`): the proof that rules weren't changed after seeing results.
Summarise its outcome here; the file itself is the record.

**How we work.** Claude brings a broad knowledge of the trading literature, statistics, market
microstructure and what traders have done before, and uses it to choose ideas with a real chance
and to test them properly and fast. Knowledge alone is not an edge: markets are competitive and
published effects shrink once known, so every idea passes the same tests (below). The tests
protect us from our own confidence. No real money until a strategy passes a clean final test,
then paper trading, then small size.

---

## 1. Status (2026-11-12)
- **Data:** UK done (RNS announcements 2012–2026, prices for 874 tickers, SETS/SETSqx). US done
  (all 8-Ks 2012–2026 with text, XBRL facts, prices for 7,627 tickers, split histories).
- **Tried (all failed, section 6):** US earnings drift and insider buying (final tests); no-news reversals,
  the pre-earnings run-up and ETF trend-following (choose period; final years unused).
- **Leveraged trend-following** passed 1994–2021 but **failed its final test** (2022 → now: +7% vs SPY +69%).
- **UK news drift (no LLM)** failed on 2019–2021: costs ~2.5–2.8% a round trip beat every category.
- **Now:** UK labelling with Luna. Pilot passed 2026-11-13 (500/500 parsed, 11% state results vs market
  expectations, £0.12; the batch queue sat idle 8h so it ran direct at 2× price). Full run
  `uk-rns-full-v1-20261113T064938` (55,654 announcements, ~£14, cap £55) running direct on the droplet
  (`uk-llm-full`, `classify.uk_news run NAME --sync`), done ~13:00 UTC. Then: user OK for the labelled UK test.
- **The user's account:** about $6,000, around $150 per trade, Trading 212, holds up to ~20 days.
- Droplet runner (`trading-rns-agent`) installed 2026-11-12: agents reach the droplet themselves (~6s a command).
- If the last ~419 Luna labels arrive (155 small batches still at OpenAI, `luna-run` service waiting): `classify.labels build`, then `features.us_earnings_labels build`.

## 2. Working rules
- **Stop at the end of each phase** (data → labels → features → backtest → live), report, and wait
  for the user's go-ahead.
- **Spending:** never buy anything. No LLM run over £5 without the user's OK
  (`llm.confirm_cost_above_gbp`); no DigitalOcean upgrades.
- **Scraping:** honest User-Agent, respect robots.txt; Investegate 2 req/s, SEC max 8 req/s. On
  403/429 back off, then stop. **Never evade a block**; report it.
- **Never delete scraped data.** Everything goes to Backblaze B2. Only US cached pages may be pruned
  locally after upload (`--prune-raw`).
- Investegate text is personal-use only: never publish or commit it (`data/` is gitignored).
- Secrets live in `.env` or the environment's credential settings, never in chat or commits.
- Run `uv run pytest` before pushing. Branch: `claude/uk-smallcap-rns-drift-i83n19`.

## 3. Testing rules (every strategy)
1. **Write the design file and commit it before running.** Every later change is listed as a change.
2. **Walk-forward:** fit only on the past; choose on 2019–2021; final test on 2022–2026 **once**,
   with the setup frozen. A strategy's final years are spent once used.
3. **Point in time:** features use only data published before the trade (XBRL by `filed` date,
   first-filed values; trade at the next price we could actually get after the news).
4. **Costs:** Trading 212 (primary) and IBKR, details in section 8.
5. **The real account:** $150 per trade, up to 40 open ($6,000), signals skipped when full.
   Compare with an index fund over the same years.
6. A **no-LLM baseline** must be beaten before paying for LLM labels.
7. **Report every variant tried**, not just the best, and flag anything that could flatter results.

## 4. What we've learnt
1. **Costs decide everything at small size.** At $150 a trade, IBKR's $1 minimum each way is 1.3% a
   round trip; Trading 212 is about 0.5–0.6% all-in on liquid US stocks. An edge has to clear that.
2. **Don't estimate spreads from daily high/low for liquid stocks.** Corwin–Schultz gave Apple
   0.42% (real ~0.01%); it measures volatility. Use the liquidity table (section 8).
3. **LLMs are good, cheap readers.** GPT-6 Luna read 150,099 earnings releases for $49, EPS matched
   the later SEC filing 97% of the time, and its labels improved the model's ranking by about half.
   Reading wasn't the bottleneck; the size of the edge was.
4. **Anonymising doesn't hide the company** (named in 90% of tests): use LLMs for facts copied from
   the text; treat their judgements as possibly coloured by hindsight.
5. **The best of many tries always looks good.** After ~75 variants on 2019–2021, the winner failed
   on 2022–2026. Design first, count every try, keep a final test untouched.
6. **Simulate the real account.** Signals come in bursts (earnings season) and 40 slots fill with
   the first ones, not the best. This changed results a lot.
7. **In liquid US stocks the simple published effects are gone after costs:** post-earnings drift after
   the next open (2022–2026), short-term reversal after no-news falls and the pre-earnings run-up
   (2019–2021, both ≈0% before costs). Prefer strategies that trade rarely.
12. **OpenAI's batch queue can sit idle for hours** (UK pilot: 0/500 after 8.5h). For runs under ~£20 the
    direct API (2× price, `--sync`) finishes in hours; keep batches for big runs.
11. **Trend-following lowers risk but lags buy-and-hold in bull markets** (2007–2021: 9.1% vs 10.8%/yr).
    Adding leverage made it beat the index over 1994–2021 (two slow crashes it dodged) and lose badly in
    2022–26 (a choppy bear it didn't): its result rests on a handful of crash episodes, so it's fragile.
8. **Survivorship:** almost no prices for delisted companies (US or UK); every result is flattered
   until that's fixed (paid options in section 10).
9. **Infrastructure:** on the 1GB droplet every DuckDB connection needs `ROW_GROUP_SIZE 8192`
   (`core.db.connect`); OpenAI batches stall or expire after 24h, so runners must resume and survive
   refused calls; run long droplet jobs with `systemd-run ... -p Restart=on-failure`.
10. **Models drift towards beaten-down, volatile stocks** (they had the biggest past rebounds). Such edges
    depend on the market regime (2020 rebound) and are the most flattered by missing failed companies.

## 5. Markets compared
- **US** (SEC 8-Ks, Trading 212 or IBKR): free, official, timestamped filings; no stamp duty;
  tight spreads on liquid stocks; crowded with professionals.
- **UK** (RNS on AIM / small caps): possibly bigger underreaction, but 0.5% stamp duty on
  main-market buys, 2–3% spreads on small AIM stocks, thin fills.

## 6. What we've tried

| Strategy | Result | Record |
|---|---|---|
| US earnings drift: Luna-labelled releases, LightGBM model scores them, 6 exit rules × 3 models, then the $6k account | **Failed.** Labels improved the ranking but the edge was smaller than costs; the best setup (liquid stocks, buy at the release day's close, hold 20 days, $150 × 40) lost to the market by $1,283 over 2022–2026 | `backtest/DESIGN_US_EARNINGS.md` |
| US insider buying: Form 4 purchases by officers/directors at market prices (500k purchases, 2012–2026), 2 universes × 3 selections × 3 exits, $6k account | **Failed.** Buying every insider purchase or clusters lost to costs. The model's picks (mostly insider buys after big falls) beat the average event; the frozen setup (liquid, model, 20 days) won 2019–21 but lost $468 vs the market in 2022–26. Small-stock model picks were positive in 2022–26 but that's where survivorship bias is worst | `backtest/DESIGN_INSIDERS.md` |
| US no-news reversals: liquid stocks falling ≥2.5σ vs the market with no 8-K, buy at the close or next open, hold 1–5 days or ATR bracket | **Failed on 2019–2021** (final years unused): no bounce on average (≈0% vs the market), the model didn't help, every variant lost every year after costs. Also says plain short-term dip-buying isn't what drove the insider picks | `backtest/DESIGN_REVERSALS.md` |
| US pre-earnings run-up: buy 6 days before the expected release (last year's date + 364 days, right within ±3 days 72% of the time), sell before or just after it | **Failed on 2019–2021** (final years unused): ≈0% vs the market before costs, the model didn't help, every variant lost | `backtest/DESIGN_RUNUP.md` |
| ETF trend-following / momentum (Faber GTAA-5, dual momentum, top-3-of-10, S&P timing), monthly, 2007–2021 | **Failed the pass rule** (beat holding the S&P 500): best 9.1%/yr vs 10.8% for SPY; halved the worst falls (−22% vs −55%) but lost through whipsaws in a long bull market | `backtest/DESIGN_ETF_TREND.md` |
| Leveraged trend: 2× (or 1.5×) S&P 500 fund only while the trend is up, else T-bills; modelled with financing, fees, daily reset | **Failed the final test.** Won 1994–2021 (16.3%/yr vs SPY 10.7%) by sidestepping 2001–02 and 2008, but 2022 → now made +7% vs SPY +69% (worst fall −49% vs −25%): the choppy 2022 bear whipsawed the monthly signal and leverage doubled each miss | `backtest/DESIGN_LEVERAGED_TREND.md` |
| UK RNS news drift, no LLM: pre-open announcements by headline category or a model, buy at the 08:00 open, hold 1–20 days or ATR bracket | **Failed on 2019–2021** (final years unused): gross drift −8% to +2.5% per event vs 2.4–2.8% round-trip costs; all 60 variants lost; closest was the model's picks held 20 days (+2.5% gross vs 2.8% cost) | `backtest/DESIGN_UK_NEWS.md` |

Every strategy whose final test ran has spent its final years: further work on them can only be judged live (paper trading)
or with delisted prices. Open lead: the insider model's small-stock picks (worth a paper trial).

## 7. What's next, in priority order

| # | Strategy | Data | LLM cost | Why here |
|---|---|---|---|---|
| 1 | **US news drift** (deals, CEO exits, profit warnings) | have it (421,832 filings) | ~$140 | Only with a reason to expect a bigger effect than earnings |
| 2 | **UK news drift with LLM labels** | labelling now | ~£14 | No-LLM model got within 0.3% of costs at 20 days; labels lifted US ranking ~50%. Survivorship flatters |


Also: **UK director dealings** (RNS) is untested; the US result lowers its priority.
1. **US news drift.** The earnings pipeline on other 8-Ks; only with a reason to expect a bigger effect.
2. **UK news drift with LLM labels.** The no-LLM baseline failed narrowly at 20-day holds; worth a
   pilot only if the user wants to keep the UK track open.

**Exit ideas** (tools applied per strategy, fixed before each test):
- **A. Targets scaled to each stock's normal move (default):** target and stop as multiples of the
  20-day average true range (e.g. 2× / 1×) plus a time limit.
- **B. Trailing stop:** once in profit the stop follows the price (e.g. 2× the daily move below the
  highest close). For longer holds.
- **C. Model predicts how far the stock will run:** not planned; too easy to fit noise.
Exits shape the payoff; they don't create an edge the signal doesn't have.

**More tracks for later:** analyst
revisions if a cheap source exists; index rebalancing; buying delisted prices if a strategy needs
small caps.

## 8. Assumptions and biases (⚠️ = could make results look better than reality)

**Every test**
- **⚠️ Survivorship:** see lesson 8. UK: of 420 delisted companies with announcements, 43 have prices.
- **⚠️ LLM hindsight:** see lesson 4. A judgement label must not predict clearly better before the
  model's training cutoff than after it.
- **⚠️ Many tries:** see lesson 5. **Capacity:** without the account simulation every trade is funded.

**Costs**
- Trading 212: no commission, FX 0.15% each way, slippage 0.15% each side (no closing-auction orders).
  IBKR Pro fixed: $0.005/share, min $1, max 1% of the trade, FX 0.03% each way, slippage 0.10% each
  side. Check current price lists before going live.
- Spread (full, half paid each side) by 20-day dollar volume: ≥$500m 0.05%, $100–500m 0.10%,
  $20–100m 0.20%. An estimate, not measured; Corwin–Schultz is only the pessimistic check.
- UK: 0.5% stamp duty on main-market buys (not AIM).

**US data**
- 8-Ks from EDGAR's quarterly index (2012 on); document = Ex-99.1, else first EX-99.x, else the 8-K.
  An 8-K with earnings (2.02) and a news item counts as earnings.
- Acceptance time from each filing's index page (ET → UTC). Never use the submissions JSON's
  `acceptanceDateTime` (4–5 hours off for many older filings).
- **⚠️ News is often public before the 8-K** (newswire first), so trades start at the next price
  after the filing, never earlier.
- Ticker map: current companies only. Prices: Yahoo daily, split-adjusted, NYSE + Nasdaq + SPY/IWM;
  `is_stale` (no volume or O=H=L=C) and `is_suspect` (impossible bar, ≥30× jump) are flagged, never
  corrected, and excluded from outcomes.
- Splits (`us_splits`): price then = close × later split ratios (Yahoo's fractional "splits" for
  spin-offs are undone the same way, which is correct).
- XBRL facts by `filed` date, first value per period. Market cap: median of the last 3 share counts
  filed before the trade, cross-checked against net income ÷ EPS from the release (>3× off → release
  wins), >30bn shares and caps outside $100k–$6tn dropped. Ratio caps are fixed numbers, never sample
  percentiles.
- Earnings labels (GPT-6 Luna, prompt v4): 150,099 of 150,518, $49.12; EPS vs XBRL 97.0%; 37% of
  releases cut at 25,000 characters. Consensus is mentioned in only 2% of releases, so surprise is
  measured against the company's own history (SUE = (EPS − prior-year EPS) / std of its last 8 seasonal
  changes, clipped ±10). Raw responses are stored, so labels never need regenerating.

**UK data**
- Universe: AIM + a FTSE SmallCap proxy (Main Market commercial companies outside the FTSE 350), from
  LSE's current instrument list. ⚠️ About half of 2012's AIM companies have since delisted; daily-mode
  scraping adds them to announcements but they usually have no prices. Index membership is today's.
- ⚠️ Yahoo's UK open/close are often auction or mid prices (close outside the day's range on ~19% of
  days): always add half-spread + slippage. Since ~2024 some stale days are in pounds not pence (100×
  too low): treat the stale stretch after a suspect row as unusable. TIDMs get reused: drop prices
  outside a company's listing window.
- Announcements (Investegate): UK wall-clock time → UTC; pre-open = before 08:00. `headline_category`
  is a rough regex label. Admin notices kept separately without bodies; "Director/PDMR Shareholding"
  is **not** admin. Replacements and non-regulatory items kept and flagged.
- SETS/SETSqx from LSE's official files, current only (today's service is a proxy for the past).

## 9. Infrastructure, layout, commands

| Piece | What it is | Notes |
|---|---|---|
| **GitHub** | `adamlewison/trading-rns`, branch `claude/uk-smallcap-rns-drift-i83n19` | code and this doc; never data or secrets |
| **DigitalOcean droplet** `trading-rns` | 178.62.96.171, 1 vCPU / 1GB RAM / 25GB disk + 2GB swap, ~$6/month | the only machine that scrapes and pushes backups; the user connects with **Termius**; agents reach it themselves (below) |
| **Backblaze B2** bucket `trading-rns-backup-c0772b` (private) | backups of everything: UK `trading-rns/v1/`, US `trading-rns/us/v1/`, LLM runs and results `trading-rns/llm/v1/` | keys `B2_KEY_ID` / `B2_APP_KEY` in `.env`; also how cloud sessions get the data (`core.backup pull`) |
| **Cloud sessions** (Claude Code on the web) | where agents work; fresh container each time, 4 CPU / 16GB | data must be pulled from B2 first; API keys for api.openai.com and ai-gateway.vercel.sh are injected by the session's network proxy (no key in code) |
| **OpenAI API** | GPT-6 Luna via the Batch API (half price) | prepaid credit topped up by the user; key on the droplet in `/opt/trading-rns-us/.env` (`OPENAI_API_KEY`) |
| **Anthropic API** | Haiku runner (`classify/us_earnings_full.py`), fallback only | key only on the droplet's `.env`, never in cloud sessions |
| **Vercel AI Gateway** | used for model comparisons | free tier blocks some models and OpenAI-format batches |
| **Droplet control** | `deploy/on-droplet.sh "CMD" [DIR]` picks the route: SSH if this machine can, else the B2 job queue | see *Reaching the droplet* below |
| **Keys** | `.env.example` lists every key and where it lives | cloud sessions: environment network secrets (B2, OpenAI, Vercel) injected by the proxy; droplet: `.env` files |
| **Scheduled check-in** | a Claude scheduled task that checks the droplet and LLM runs every ~5 hours | re-arms itself; stop it when nothing is running |

**Reaching the droplet** (it depends where the agent runs; `deploy/on-droplet.sh` tries 1 then 2)
1. **Local machine with SSH:** `ssh root@178.62.96.171`. Check first with
   `ssh -o BatchMode=yes -o ConnectTimeout=6 root@178.62.96.171 true`.
2. **Cloud session** (only HTTPS leaves the container, port 22 is refused): the B2 job queue,
   `uv run python -W ignore -m core.remote run "CMD" [--cwd DIR]`. The droplet's `trading-rns-agent`
   service (installed by `deploy/setup-agent.sh`) polls B2 every 5s when busy (10s idle), runs each job once (jobs >1h
   old are ignored) and stores the output in B2. B2 write access = root on the droplet: guard that key.
3. **If SSH says "Permission denied (publickey)"** on a local machine, this machine's key isn't on
   the droplet yet. Ask the user to add it once:
   - on the local machine, if there's no key yet: `ssh-keygen -t ed25519 -C "trading-rns agent"`;
     then show the **public** key: `cat ~/.ssh/id_ed25519.pub` (safe to share; never the private key);
   - on the droplet (Termius, or the job queue): `echo "PASTE_PUBLIC_KEY" >> ~/.ssh/authorized_keys`.
   If SSH times out or the connection is refused instead, it's the network (e.g. a cloud session):
   use route 2. If both fail, ask the user to check the droplet in Termius.

**Droplet details**
- UK checkout `/opt/trading-rns` (services `trading-rns-scrape`, `trading-rns-backup`); US checkout
  `/opt/trading-rns-us` (services `trading-rns-us`, `trading-rns-us-backup`, plus one-off units such
  as `luna-run`, `us-splits`). Setup: `bash deploy/setup-uk.sh` / `bash deploy/setup-us.sh`. Logs in
  `data/logs/`. Run long one-off jobs with `systemd-run --unit=NAME -p Restart=on-failure ...`.
- Each backup loop pushes every 30 minutes and uploads a status file; read it from anywhere with
  `core.backup status --remote --profile uk|us`.
- **One writer per market:** only the droplet scrapes or pushes backups. Never run a scrape or
  `core.backup push` elsewhere while it runs (two writers corrupt the backup).
- DuckDB repair after a failed checkpoint: `core.dbcheck --profile us --checkpoint`.

**Layout:** `config.yaml` (all settings); `core/` (config, DuckDB, polite HTTP, B2 backup, remote
jobs); `ingest/uk|us/` (data; each has `common.py`, `schema.py`, `backup.py`, `run_all.sh`);
`classify/` (anonymise, prompts, Luna/Haiku runners, labels table); `features/`; `backtest/` (incl.
`DESIGN_*.md` test records); `deploy/` (droplet setup, backup loop, agent runner); `tests/`.
Databases `data/uk.duckdb`, `data/us.duckdb`; raw caches `data/raw/uk|us/`.

**Commands** (setup: `uv sync`, `cp .env.example .env`, `uv run pytest`; every data step is resumable)
```bash
nohup ingest/uk/run_all.sh >> data/logs/uk_run.out 2>&1 &     # UK data (index --mode daily also finds delisted)
nohup ingest/us/run_all.sh >> data/logs/us_run.out 2>&1 &     # US data; `ingest.us.run splits` for splits
uv run python -W ignore -m core.backup status --remote --profile uk|us   # watch the droplet (read-only)
uv run python -m core.backup pull --profile uk|us --force [--skip-raw]   # data from B2 (UK: then announcements.run build)
uv run python -W ignore -m classify.openai_full run --max-gbp 55|status  # Luna batches (resumable)
uv run python -W ignore -m classify.labels build|report
uv run python -W ignore -m features.us_earnings build && uv run python -W ignore -m features.us_earnings_labels build
uv run python -W ignore -m backtest.us_account choose --hold 20          # final runs need --final, once
```
Backups never lose data: local files are deleted only after a verified upload, and a push never
replaces a table snapshot with an empty one. Only the US loop prunes cached SEC pages.

## 10. Open items
- **UK market makers:** the user downloads LSE's "Registered Market Makers by Security" file by hand,
  then `import-mm` (above).
- **Delisted prices (survivorship fix), nothing bought.** Rough prices Nov 2026, check before buying:
  FirstRate Data (~16k US tickers from 2000, ~7k delisted, ~$240 one-time); FinzData (8.5k+ delisted,
  ~$195–670 one-time); Norgate Platinum (~25k delisted, ~$630/year, Windows); Sharadar (subscription);
  Nasdaq WIKI archive (free, frozen at March 2018); CRSP (academic only).
