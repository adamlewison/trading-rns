# trading-rns: master doc

**Mission: find an algorithmic trading strategy that makes money after costs and beats an index
fund.** Not tied to any market, data source or idea. When a track fails, record why here and switch
tracks. We don't stop until one passes.

## How to use this doc (every agent, Claude or not)
This is the project's master doc (`AGENTS.md` is a link to it). Read it fully before starting work; before
running any test also read `backtest/RUNBOOK.md` (how to test) and the strategy's `backtest/DESIGN_*.md`.
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
- **UK labels done 2026-11-13:** 55,654/55,654 announcements, £14.11, 0 errors
  (`data/labels/uk-rns-full-v1-20261113T064938.parquet`; re-create with `classify.uk_news report NAME`).
  6,388 compare results with market expectations (1,826 ahead, 3,870 in line, 446 below); 1,785 flag funding risk.
- **UK news drift with Luna labels**: passed 2019–2021 narrowly, **failed its final test** (2022 → now: +£383
  vs £2,241 for a FTSE All-Share fund; it did beat the small-cap index by £1,486).
- **UK director buying** passed 2019–2021 (+£780) but **failed its final test** (2022 → now: −£1,888 vs a FTSE fund).
- **Large-company factor portfolio** failed: quality (run to the final test by user override) made +42% vs
  SPY +64% in 2022 → now; the variants that lagged in 2013–21 beat SPY in 2022 → now. No consistent edge.
- **Paper trading (live, nothing traded) started 2026-11-13** at the user's request: the four factor portfolios
  (V, Q, M, QVM; frozen rules in `backtest/DESIGN_FACTORS.md`), droplet timer `trading-rns-paper` weekdays
  22:30 UTC, first rebalance **2026-12-01**. Results: `uv run python -W ignore -m paper.factors status`, or
  B2 `trading-rns/paper/status.md`. Judge after ≥ 12 months; no picking a winner early.
  **Also on a Trading 212 practice account** (built 2026-11-22, `paper/t212.py`): one account, four virtual books of $1,500 (practice cash is £5,000),
  net orders after the close fill at the next open; status B2 `trading-rns/paper/t212.md`. Needs the practice key
  (`T212_API_KEY`/`T212_API_SECRET`) in the droplet's `/opt/trading-rns-us/.env`; runs only when it's there.
- **The user's account:** about $6,000, around $150 per trade, Trading 212 (IBKR opened 2026-11-15), holds up to ~20 days.
- **The user's preference (2026-11-15):** short trades (hours to days) whose gains are reinvested (lesson 17).
- **The user's preference (2026-11-18):** not interested in one-off gains of a few hundred pounds (bookmaker sign-up offers);
  wants something that keeps paying (Betfair exchange test run 2026-11-18: failed). Matched-betting tool kept for reference; a 2026-11-18 scan of 312 events found no true
  bookmaker-vs-exchange arbitrage once 3-way hockey prices were excluded.
- **State 2026-11-16** (plans in section 7, procedure in `backtest/RUNBOOK.md`): tax-loss rebound and crypto trend
  have failed (section 6). **UK retail offers:** both periods run, too small to judge (section 6); a **live trial started
  2026-11-16** (the user applies by hand for the minimum in every offer open to new investors and reports it; an agent
  adds it to `paper/retail_offers_log.csv`; rules in the design, "Live trial"; judge after 20 applications or six months). **Trust discounts:** failed its final test (section 6). **Prediction markets:** failed (section 6).
  **Odd-lot tenders:** public sources say a UK holder has 15% of gross proceeds withheld unless the broker accepts a
  Section 302 certificate; IBKR's AI assistant (2026-11-16) did not say it passes one on, so assume withholding applies: the track is
  shelved unless IBKR's corporate-actions desk confirms otherwise in writing.
- **Collecting data that isn't kept as history (started 2026-11-22, user's choice):** daily option chains (S&P 500,
  nearest 4 expiries, ±25% strikes), Yahoo analyst forecasts/revisions/earnings dates (S&P 1500), IBKR borrow fees
  (US + UK, 4× a day), Nasdaq listings + S&P 500/400/600 holdings + prices of every listed US stock (survivorship-free
  from now). `collect/run.py`, droplet timers `trading-rns-collect-*`; worth testing on after ~2 years. Status B2
  `trading-rns/collect/status.md`. Not yet: UK quotes/spreads (wait for IBKR live data), exchange order books.
- Droplet runner (`trading-rns-agent`) installed 2026-11-12: agents reach the droplet themselves (~6s a command).
- US Luna leftovers: 150,457 of 150,518 labelled; 13 small batches still at OpenAI (`luna-run` on the droplet). When done: `classify.labels build`, then `features.us_earnings_labels build`.

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
- Run `uv run pytest` before pushing. Branch: `main` (the default; cloud sessions work on a `claude/...` branch and merge into it).

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
13. **Beating a weak benchmark isn't beating the index fund.** UK labelled picks beat the small-cap index
    in 2022–26 but earned far less than the FTSE All-Share. Judge against what the user would otherwise hold.
12. **OpenAI's batch queue can sit idle for hours** (UK pilot: 0/500 after 8.5h). For runs under ~£20 the
    direct API (2× price, `--sync`) finishes in hours; keep batches for big runs.
11. **Trend-following lowers risk but lags buy-and-hold in bull markets** (2007–2021: 9.1% vs 10.8%/yr; crypto
    2022–26: it beat holding the coins but made +22% vs SPY +69%).
    Adding leverage made it beat the index over 1994–2021 (two slow crashes it dodged) and lose badly in
    2022–26 (a choppy bear it didn't): its result rests on a handful of crash episodes, so it's fragile.
8. **Survivorship:** almost no prices for delisted companies (US or UK); every result is flattered
   until that's fixed (paid options in section 10).
9. **Infrastructure:** on the 1GB droplet every DuckDB connection needs `ROW_GROUP_SIZE 8192`
   (`core.db.connect`); OpenAI batches stall or expire after 24h, so runners must resume and survive
   refused calls; run long droplet jobs with `systemd-run ... -p Restart=on-failure`.
15. **A variant that wins in one period and loses in the next is noise.** Factor variants swapped places
    between 2013–21 and 2022–26; only consistency across both counts.
14. **2020 flatters everything that buys small or beaten-down stocks.** Four strategies looked good on
    2019–21 mainly through the 2020 rebound and failed on 2022–26. Distrust a choose-period win driven by 2020.
16. **Our final test can't tell a small edge from luck, and we fished where professionals fish.** A 30–40
    stock portfolio strays ~10% a year from the index, so over 2022 → now a ±20-point gap is one standard
    deviation of chance (quality's −22 points is that). Prefer trades with a large, structural edge per trade
    (someone forced to sell, or a prize too small for funds), judged per trade against the index over the same days.
21. **Cheap judgement is not an edge against a price.** Decision models without web access forecast far worse than
    a market price (they lack recent news); told the price, they copy it and add noise. Use them to read and sort
    documents, not to second-guess markets.
20. **Fourteen tests, no pass: predicting prices does not pay at this size; being paid for a service might.** The
    only positive numbers came from taking stock others want placed (retail offers). Look for trades where someone
    pays for liquidity, hassle or small size, and judge them live, since such edges leave no usable history.
19. **A real but small mispricing is swamped by the exposure that comes with it.** Trust discounts did narrow
    (+2.5–3.2% per holding in both periods) but long-only you also own the trust's assets, which decided the result.
    Without a hedge, an edge has to be large against the asset's own swings, not just positive.
18. **A quoted price is not a tradable price.** On Kalshi a 97c ask often sat over an empty book; signals must
    use both sides of the quote (or the gap), and the test must fill at the side you would actually pay.
17. **Fast compounding needs near-certain trades, not bigger targets.** 1% a day is 12× a year (the best fund
    ever made ~66%); growth comes from how reliable the edge is and how often capital turns over, and only a
    reliable edge can be sized large. Fixed $150 slots (2.5% of the account) cannot compound whatever the edge.
    Exits were never the problem: entries had ≈0% edge before costs, and no exit rule fixes that.
10. **Models drift towards beaten-down, volatile stocks** (they had the biggest past rebounds). Such edges
    depend on the market regime (2020 rebound) and are the most flattered by missing failed companies.

## 5. Markets compared
- **Prediction markets:** Kalshi and Polymarket are closed to UK residents (no Gambling Commission licence;
  checked 2026-11-15). UK-legal venues are Betfair, Smarkets and Matchbook. Kalshi's public data API needs no
  account but answers 429 above ~1–2 requests a second; Betfair's free price files gave 403 here; Smarkets'
  public API lists no settled markets.
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
| UK director buying: director/PDMR purchases (from the Luna labels, pre-open only), all or ≥ £20k, held 20/60 days or trailing stop, vs a FTSE All-Share fund | **Failed the final test.** ≥ £20k held 60 days won 2019–21 (+£780, mostly 2020) but lost −£1,888 in 2022 → now; before costs the picks trailed the index (−0.4% a trade). Every variant lost | `backtest/DESIGN_UK_DIRECTORS.md` |
| US large-company factors (value, quality, momentum, combined), top 30 of the 500 largest, monthly, $6k | **Failed.** 2013–21: quality 17.3%/yr vs SPY 16.2% (4 of 9 years; 5 required), others lagged. Final test (user override) on quality: +42% vs SPY +64% in 2022 → now, while the three that lagged earlier beat SPY there: each variant won one period and lost the other. Also measured survivorship without buying data: 29% of large filers in 2013 have no price (3% in 2026) | `backtest/DESIGN_FACTORS.md` |
| UK news drift **with Luna labels** (55,654 announcements labelled, £14.11): model on baseline + label features, 20-day hold | **Failed the final test.** 2019–21 just passed (+£193; without labels −£638). 2022 → now: account +£383 vs FTSE All-Share fund +£2,241; it beat the AIM/SmallCap index by +£1,486 (3 of 5 years), but small caps lagged so badly that beating them wasn't enough | `backtest/DESIGN_UK_NEWS.md` |

| US year-end tax-loss rebound: the 30 worst small/mid caps of the year, bought 5 trading days before year-end, sold on January's 10th or 20th trading day | **Failed on Decembers 2012–2020** (final years unused): beat SPY in 6 of 9 years (7 required); mean +2.4% to +4.2% vs SPY but ≈0% without the best year, and January 2016 lost 23%. No pick ever stopped trading: the failures are missing from the data | `backtest/DESIGN_TAX_LOSS.md` |

| Crypto trend on BTC and ETH: hold each coin only while above its 100-day average (or 28-day return > 0), sized by volatility or flat, 0.45% costs | **Failed the final test.** Passed 2018-06 → 2021 (M100-V +446%, Sharpe 1.60 vs 1.12 for holding the coins; SPY +85%), but 2022 → now made +22% vs SPY +69%: it halved the falls and beat holding the coins (−13%), but crypto went nowhere and trading cost $1,887 | `backtest/DESIGN_CRYPTO_TREND.md` |

| UK retail offers (PrimaryBid, RetailBook, WRAP, REX): apply at the placing price, sell at the open on admission; £500 an offer; Luna read 1,134 announcements (£0.38) | **Too small to judge in both periods** (50 usable offers to end-2024, 18 in 2025–26; 100 required; half of all launches are for existing holders only). Filled in full: +4.7% and +6.2% over the index per offer in ~11 days (t 2.9 and 1.1), resting on a few big winners; with popular offers scaled back to a quarter: +0.7% and −0.3%. Allocation decides it and only a live record can measure it. Final period spent | `backtest/DESIGN_UK_RETAIL_OFFERS.md` |

| Prediction-market near-certainties (Kalshi history, 21m settled markets listed, 16,950 sampled with hourly bid/ask): buy the side whose ask is 90–98c within 2 or 7 days of the scheduled end, hold to settlement | **Failed on markets ending up to 2025** (2026 unused; not tradable from the UK anyway): −16% to −19% per bet, because an ask at 97c in a thin market is not a 97% market (a third of bets crossed a gap above 20c and won 62%). Where the gap was a cent the favourite won as often as its price (96.3% at 96.8c): nothing after fees | `backtest/DESIGN_PM_FAVOURITES.md` |

| UK investment trust discounts: buy trusts whose discount is ≥ 1.5 standard deviations wider than their own last 12 months and wider than −8%, up to 5, sell when back to normal or after 6 months; NAVs read by Luna from 32,530 announcements (£3.35) | **Failed the final test.** Passed 2015–2021 (£8,764 vs £7,361 for the FTSE All-Share, 5 of 7 years, placebo 0 of 30) but 2022 → now made £5,825 vs £8,019. Discounts still narrowed (+2.5% per holding, 3.2% before), but the trusts' own assets lagged the index and ~2% of costs ate the rest: the discount edge is real, small, and swamped by what the trust holds | `backtest/DESIGN_UK_TRUST_DISCOUNTS.md` |

| Decision-model forecasts vs market prices: 19 decision models (Gemini Decision 1, GPT-6 Luna Decisions, Microsoft, Liquid, Baseten, Nace, HeyGen and others, Vercel gateway) asked the probability of Kalshi events 24 hours before the end, blind or told the price | **Failed.** Choose half: all 38 combinations worse than the market (blind 20–37% worse by Brier; told the price, the best copied it). Confirm half, Liquid d1.2-Ultra told the price: 6.1% worse (interval −11.7% to −1.6%), betting rule −51% per bet. $0.28 for ~20,000 answers. Web-search models not tested | `backtest/DESIGN_DECISION_FORECASTS.md` |

| Betfair Exchange racing, Aus & NZ (Betfair's free files, 2020 → Oct 2026): back favourites or lay longshots at BSP; back or lay the place market using a Harville model from win prices at the off | **Failed on 2020–2023** (final years unused): favourites −2.8% per £ after 5% commission; laying longshots +0.27% per £ of liability (t 1.7, 4 of 4 years: a real but tiny favourite–longshot bias); place-market bets −1.9% to −9.6%. Place BSPs were almost perfectly calibrated and better than the model. Tick-level ideas untested (need paid data or a live recording) | `backtest/DESIGN_BETFAIR_RACING.md` |

Every strategy whose final test ran has spent its final years: further work on them can only be judged live (paper trading)
or with delisted prices. Open lead: the insider model's small-stock picks (worth a paper trial).

## 7. What's next, in priority order

Each has a full plan written 2026-11-15 **before any data or result was seen**. Whoever runs one follows
`backtest/RUNBOOK.md` (procedure, look-ahead checklist, checks before believing a result, allowed verdicts).

| # | Strategy | Plan | Needs | Why here |
|---|---|---|---|---|
| 1 | **Odd-lot tender offers** (US): buy ≤ 99 shares below a company's own tender price and tender them | `DESIGN_ODD_LOT.md` | EDGAR SC TO-I pull; Luna < $5. **Shelved:** IBKR gave no sign it passes on a Section 302 certificate, so assume 15% of proceeds is withheld for a UK holder (detail in the design) | A prize too small for funds; capped, so it adds income but does not compound |
| 2 | **Passive orders in wide-spread AIM shares** (IBKR API): rest a bid at the best bid, and once filled an offer at the best ask; no orders on days with company news (our RNS feed) | `DESIGN_AIM_PASSIVE.md` | **the user's answers to phase 0** (IBKR's fee for live LSE quotes, that limit orders rest on the book, willingness to keep IBKR's gateway logged in); then 4 weeks of recording quotes, trading nothing | Universe: 64 AIM shares on the SETS order book with £20k–£1m daily turnover. Be the one who is paid the 2–3% spread that sank our UK tests instead of paying it; AIM has no stamp duty; a small account is an advantage in a thin book. Risk: being filled just before bad news and left holding the shares |
| 3 | **Betting-exchange offers and liquidity** (UK-legal, winnings untaxed): hedged bookmaker offers ("matched betting"), later resting orders in thin exchange markets | none yet | **built 2026-11-18:** `matched/` (hedge calculator, oddsmatcher on The Odds API, private ledger in `data/matched_ledger.csv`); needs only the user's bookmaker and exchange accounts | Near-certain, short trades, which is what the user asked for; limited by the number of offers and by bookmakers restricting accounts, so it does not compound; not securities trading |
| 7 | **US news drift** (deals, CEO exits, profit warnings) | none | Luna ~$140 | Only with a reason to expect a bigger effect than earnings |

Ideas 2 and 3 were added 2026-11-16 after every backtest failed (lesson 20); neither can be backtested with data we have.
**Nothing on the 2026-11-15 list is left to backtest.** Running live: the retail-offers trial and the paper factor portfolios.
Not planned: **UK possible offers** (Rule 2.4, hold to the 28-day deadline): acquired targets have no
prices, so it needs delisted data first. Sparse strategies assume idle cash sits in the index fund.

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
- **⚠️ Survivorship:** see lesson 8. UK: of 420 delisted companies with announcements, 43 have prices. US large
  companies (public float ≥ $5bn in filings): 29% have no price in 2013, 17% in 2019, 3% in 2026 (`backtest/factors.py`).
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
- ⚠️ Yahoo's UK `close` is adjusted for later share consolidations (common on AIM, 10–700 to 1): a price stated in
  an announcement cannot be compared with it without the consolidation history or the close stated in the same text.
- SETS/SETSqx from LSE's official files, current only (today's service is a proxy for the past).

## 9. Infrastructure, layout, commands

| Piece | What it is | Notes |
|---|---|---|
| **GitHub** | `adamlewison/trading-rns`, branch `main` | code and this doc; never data or secrets |
| **DigitalOcean droplet** `trading-rns` | 178.62.96.171, 1 vCPU / 1GB RAM / 25GB disk + 2GB swap, ~$6/month | the only machine that scrapes and pushes backups; the user connects with **Termius**; agents reach it themselves (below) |
| **Backblaze B2** bucket `trading-rns-backup-c0772b` (private) | backups of everything: UK `trading-rns/v1/`, US `trading-rns/us/v1/`, LLM runs and results `trading-rns/llm/v1/` | keys `B2_KEY_ID` / `B2_APP_KEY` in `.env`; also how cloud sessions get the data (`core.backup pull`) |
| **Cloud sessions** (Claude Code on the web) | where agents work; fresh container each time, 4 CPU / 16GB | data must be pulled from B2 first; API keys for api.openai.com and ai-gateway.vercel.sh are injected by the session's network proxy (no key in code) |
| **OpenAI API** | GPT-6 Luna via the Batch API (half price) | prepaid credit topped up by the user; key on the droplet in `/opt/trading-rns-us/.env` (`OPENAI_API_KEY`) |
| **Anthropic API** | Haiku runner (`classify/us_earnings_full.py`), fallback only | key only on the droplet's `.env`, never in cloud sessions |
| **Vercel AI Gateway** | used for model comparisons | free tier blocks some models and OpenAI-format batches |
| **Droplet control** | `deploy/on-droplet.sh "CMD" [DIR]` picks the route: SSH if this machine can, else the B2 job queue | see *Reaching the droplet* below |
| **Keys** | `.env.example` lists every key and where it lives | cloud sessions: environment network secrets (B2, OpenAI, Vercel) injected by the proxy; droplet: `.env` files |
| **Paper trading** | `paper/factors.py`, droplet timer `trading-rns-paper` (weekdays 22:30 UTC; acts on the first trading day of each month) | tables `paper_picks`, `paper_marks` (backed up); summary in B2 `trading-rns/paper/status.md`; setup `deploy/setup-paper.sh` |
| **T212 practice orders** | `paper/t212.py`, called by `paper.factors daily` when `T212_API_KEY` is set | practice host only; `check` tests the key and ticker coverage; tables `t212_*` (backed up) |
| **Data collection** | `collect/run.py`, droplet timers `trading-rns-collect-<job>` (UTC weekdays: borrow 08:05/13:05/17:05/20:35, options 19:40, prices 21:40, analysts 23:10, listings 23:55 Mon–Sat), from `/opt/trading-rns-us` | files `data/collect/<job>/<date>.*`, each uploaded to B2 `trading-rns/collect/v1/<job>/`; never deleted; setup `deploy/setup-collect.sh`; sources: Yahoo (options, analysts, prices), IBKR `ftp2` (borrow; `ftp3` unreachable), nasdaqtrader.com, ssga.com |
| **Retail-offers trial** | `paper/retail_offers.py`, droplet timer `trading-rns-retail` (weekdays, from `/opt/trading-rns-pm`) | scans new announcements (07:30, 17:30, 18:45 UK), reads retail offers with Luna, sends a phone alert through ntfy for each new offer open to new investors (topic name in the droplet's `/opt/trading-rns-pm/.env` as `NTFY_TOPIC`, never in git), status in B2 `trading-rns/paper/retail_offers.md`; setup `deploy/setup-retail.sh`; applications are made by hand (no broker API) |
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
  as `luna-run`, `us-splits`). `/opt/trading-rns-pm` (this branch, uses the US checkout's venv and `.env`): units `kalshi-list`, `trust-data` (its `data/uk.duckdb` and `data/raw/uk` are links to the UK checkout). Setup: `bash deploy/setup-uk.sh` / `bash deploy/setup-us.sh`. Logs in
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
uv run python -W ignore -m ingest.uk.announcements.run bodies --headline-re 'retail offer|primarybid|retailbook'  # droplet only
uv run python -W ignore -m classify.uk_retail_offers run --max-gbp 3|report
uv run python -W ignore -m classify.uk_nav run --max-gbp 5|report         # trust NAVs (done: data/labels/uk_nav.jsonl, B2)
uv run python -W ignore -m features.uk_trusts prices|build|report         # trust prices (splits undone), monthly discounts, step 0
uv run python -W ignore -m backtest.uk_trust_discounts choose           # final needs --final, once
uv run python -W ignore -m matched.scan [--book williamhill] [--stake 10 --free 10]   # matched betting: best back/lay pairs (Odds API key = network secret, 500 credits/month)
uv run python -W ignore -m matched.ledger add ...|summary                # private record of offers done
uv run python -W ignore -m matched.terms terms.txt                      # screen an offer's terms (decision model): GOOD / CAUTION / AVOID
uv run python -W ignore -m backtest.betfair_racing choose                # Aus & NZ racing at BSP (data in data/betfair/, free from Betfair's data hub)
uv run python -W ignore -m paper.t212 check|status                     # Trading 212 practice account (factor portfolios)
uv run python -W ignore -m collect.run listings|prices|borrow|options|analysts|status   # daily snapshots (droplet timers)
uv run python -W ignore -m paper.retail_offers status                    # live trial (droplet runs `daily`)
uv run python -W ignore -m backtest.uk_retail_offers choose              # final needs --final, once
uv run python -W ignore -m backtest.crypto_trend data|choose             # final needs --final, once
uv run python -W ignore -m backtest.us_tax_loss choose                  # needs data/us_min/*.parquet (docstring)
uv run python -W ignore -m ingest.pm.kalshi series|markets|candles|status|upload|download  # Kalshi history (resumable, ~1–2 req/s)
uv run python -W ignore -m backtest.pm_favourites check|choose            # final needs --final, once
```
Backups never lose data: local files are deleted only after a verified upload, and a push never
replaces a table snapshot with an empty one. Only the US loop prunes cached SEC pages.

## 10. Open items
- **Tax-loss paper baskets:** the design asks for the four baskets to be recorded about 23 December 2026
  (survivorship-free check) even though the backtest failed; not set up yet.
- **UK market makers:** the user downloads LSE's "Registered Market Makers by Security" file by hand,
  then `import-mm` (above).
- **Delisted prices (survivorship fix), nothing bought.** Rough prices Nov 2026, check before buying:
  FirstRate Data (~16k US tickers from 2000, ~7k delisted, ~$240 one-time); FinzData (8.5k+ delisted,
  ~$195–670 one-time); Norgate Platinum (~25k delisted, ~$630/year, Windows); Sharadar (subscription);
  Nasdaq WIKI archive (free, frozen at March 2018); CRSP (academic only).
