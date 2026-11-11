# Assumptions & known biases

Where something was uncertain, I picked the conservative option. Anything that could make
results look better than they really are is flagged with ⚠️.

## UK

### Universe

- **⚠️ Survivorship bias (big).** The LSE instrument list (the only free source with TIDMs) has
  *current* companies only. LSE monthly issuer snapshots show roughly 1,100 AIM companies in 2012
  vs about 600 today, so about half of the 2012 AIM universe has since delisted (failures,
  takeovers, moves). What we do about it:
  - The announcement index (Investegate) covers delisted companies, and `ingest.uk.universe add-delisted`
    adds a TIDM when its company name matches an AIM (or non-FTSE-350 Main Market) company in a
    historic LSE snapshot. This only finds delisted names that show up in the announcement index
    we scraped. Company-mode indexing never finds them; daily mode does.
  - **Yahoo has almost no price history for delisted tickers** (e.g. `IOG.L` returns nothing), so
    even when we have a delisted company's announcements, we usually can't label its trades.
    Delisted companies are skewed toward failures, so the remaining sample is skewed toward
    winners. Expect any backtest edge to be **overstated**. Phase 4 will report results by
    whether the ticker is still listed, so we can see how big this effect is.
- **FTSE SmallCap is a proxy.** Free constituent lists aren't available. "SmallCap proxy" means
  Main Market equity shares with FCA category *commercial companies* or *transition*, excluding
  FTSE 100/250 members (LSE Market Sector Codes `FE10/FS10` = FTSE 100, `F25T/F25F/F25S` = FTSE 250,
  checked: 100 and 250 lines respectively). Closed-ended funds and shell companies are excluded.
  The proxy also includes FTSE Fledgling and unindexed names, so it is broader than the real index.
- Index membership is as of **today**, not point-in-time. A company that was in the FTSE 250 in
  2015 but is a small cap now counts as a small cap throughout. Phase 3 will use the monthly issuer
  snapshots (market and market cap, by name match) to get point-in-time `is_aim` and `market_cap`.
- LSE issuer snapshots for 2025–2026 no longer include market cap. Phase 3 needs another source
  for those months (shares in issue × price, or skip).
- **TIDM reuse / changes.** TIDMs get recycled (e.g. `BVS` was Bovis Homes, now Bravura). Investegate
  company pages appear to be keyed to the current company. Yahoo keys by symbol, though, and 39
  current tickers have Yahoo prices from before the company's admission date (re-admissions or
  reuse). Phase 3 must drop price rows that fall outside a company's listing window and must check
  that announcement and price series belong to the same company.

### Prices (Yahoo via yfinance)

- OHLC are **split-adjusted only** (`auto_adjust=False`). `adj_close` is also dividend-adjusted.
  Volume is split-adjusted, so `close × volume` gives a consistent £ turnover.
- **⚠️ Open/close are often not trade prices.** For LSE small caps, Yahoo's open/close look like
  official auction or mid prices, while high/low are actual trade extremes. Close sits outside
  [low, high] on about 19% of traded days and open does on about 11%. Consequences:
  - Entering at "the open" is optimistic for illiquid names. The real fill is around the offer,
    which is worse than the mid. Phase 3 always adds half-spread + slippage on entry and exit.
  - We do **not** flag these rows as bad data.
- `is_stale` = zero volume or O=H=L=C. Yahoo forward-fills untraded days, and about 10–30% of
  rows are stale depending on the year. Stale days must not count as "barrier hit" days in labels
  (Phase 3).
- `is_suspect` = impossible bar (high < low, price ≤ 0), or a ≥30× jump (Yahoo's GBP vs GBp unit
  glitch). Flagged, never auto-corrected. Suspect rows will be excluded from labels.
  Since about Feb 2024, Yahoo often returns forward-filled zero-volume days for illiquid names
  in pounds instead of pence (100× too low; 213 tickers affected, 80% of suspect rows are stale
  rows). Only the *jump day* is flagged, so Phase 3 has to treat a whole stale stretch after a
  suspect row as unusable, not just the flagged row.
- **⚠️ Delisted prices:** only 13 of the first 53 delisted tickers found had any Yahoo history,
  and some of those 13 may be a different company that reused the TIDM.
  Final count (2026-11-09): 420 delisted companies have announcements, only 43 have any prices.
  Their 40,357 pre-open announcements (27% of all pre-open news) can't be labelled, and the
  SETS/SETSqx map is missing for 397 of them. A UK backtest is survivors-only unless we buy
  delisted UK prices.
- Prices are in the trading currency (mostly GBX = pence; a few USD/EUR AIM lines). The currency
  is stored in `universe.currency`.

### Announcements (Investegate)

- **Source choice and terms.** robots.txt allows the pages we use (`/company/`, `/today-announcements/`,
  `/announcement/`). The Terms of Use allow personal reference by private investors only, with no
  redistribution or commercial use. So this pipeline is for personal research. The scraped text
  stays in the local `data/` folder (gitignored) and must never be published. Rate limit is 2
  requests per second (robots.txt sets no crawl delay; raised from 1 per 2s, then 1/s, on 2026-11-07).
  On 403/429/5xx the scraper backs off and stops after 3 consecutive blocks; it never evades a block, with an honest User-Agent, and all HTML is cached so nothing is fetched twice.
- **Timestamps** come from the listing pages and are UK wall-clock time (e.g. "07:00 AM"). They
  are stored as `published_uk` and converted to UTC with DST handled. `pre_open` = weekday and
  before 08:00 UK. Bank holidays are not handled here; Phase 3 maps every announcement to the next
  actual trading open using the price calendar.
- **No RNS category code.** Investegate shows the headline but not the RNS code. `headline_category`
  is derived from the headline with regex rules and is a rough label only. The Phase 2 LLM `type`
  is the real classification.
- **Admin filter is deliberately narrow.** A headline only goes to `announcements_admin` if the
  *whole* headline is an admin phrase (TVR, holdings, Form 8.x, own-share transactions, meeting
  notices/results, results-date notices, adviser changes, Rule 2.9, block listing, etc.).
  Mixed headlines such as "Director Subscription and Total Voting Rights" or "Result of AGM and
  Trading Update" stay in the main table. **"Director/PDMR Shareholding" is NOT admin**, because
  director dealing is one of the event types we study.
- We don't download bodies for admin notices. The headline is enough and it saves requests.
- The page's "AI summary" block is ignored. The standard RNS footer is cut from the text.
- **Form 8.x disclosures filed by third parties** (e.g. "Man Group PLC : Form 8.3 - Serica")
  show up on the *target's* company page but under the *discloser's* name on day pages. The
  admin filter catches them in both places, including Investegate's odd raw headlines like
  `20250326_SERICA ENERGY PLC_8.5 EPT RI_UK_MLI`.
- **Completeness check (pilot):** on 3 busy days (Mar/Apr 2025), Investegate's daily listing
  contained every announcement that company mode found for the pilot tickers, apart from those
  third-party Form 8s. Day pages hold about 440–540 announcements per weekday.
- A ticker's company page includes announcements made under the company's former name
  (e.g. PHAR shows "Soco International" in 2014). That's the same legal entity, so it's fine.
- Non-regulatory announcements (RNS Reach and similar, `is_regulatory = false`) are kept and
  flagged. Phase 2/3 can decide whether to drop them.
- Replacement/amended announcements are kept and flagged (`is_replacement`). Phase 3 should not
  treat a replacement as new information.

### UK trading service (SETS / SETSqx)

- Trading service = (Market Segment Code, Market Sector Code) from the LSE instrument list, mapped
  through the "Sector Breakdown" sheet of LSE's Business Parameters workbook. Both are official LSE
  files. **History** only starts from our first download, because LSE publishes the instrument
  list as "latest" only. Each later monthly `ingest.uk.universe build` adds a snapshot.
- **⚠️ Today's service applied to the past is an approximation:** stocks move between SETS and
  SETSqx (LSE reviews liquidity periodically). For backtest filtering, a stock's current service is
  a proxy for its historical one.
- LSE's weekly "List of SETS/SETSqx securities" and "Registered Market Makers by Security" files
  are only linked from a JavaScript page whose API refuses scripted access (HTTP 403), so we don't
  scrape them. The market-maker file is downloaded by hand and imported with
  `ingest.uk.trading_service import-mm`.

## Phase 2: LLM classification (both markets)

- **Model:** Claude Haiku 5.5 through Anthropic's Batch API (decided 2026-11-08). Estimated cost:
  ~£40 for the UK announcements, ~£110 for US earnings 8-Ks, ~£170 for US news 8-Ks. The
  500-announcement pilot (~15p) measures real token counts before any full run.
- **⚠️ Look-ahead bias from the model's own knowledge:** the model was trained on news up to
  2025–26, so it may "know" how a company's story ended (e.g. later administration) and let that
  colour its labels. That would flatter a backtest. Mitigation: company names, TIDMs and tickers are
  stripped from the text before classification (`llm.anonymise`). It's imperfect, because distinctive
  details (project names, places) can still identify a company. Label quality should be compared on
  pre- and post-training-cutoff announcements.
- **⚠️ Anonymisation does NOT hide the company (pilot, 2026-11-09).** With names, tickers, URLs,
  phones, IDs and filenames stripped, Haiku 5.5 still named the company in 45 of 50 releases (90%),
  small caps included, from cities, products, executives and figures. So the model's memory of what
  happened next can leak into any *judgement* it makes. Consequences:
  - Use Claude mainly for **objective extraction**: numbers, guidance figures and stated events. Facts
    copied from the text can't be coloured by hindsight, and EPS matched the later XBRL filing in
    98% of cases.
  - Treat subjective labels (tone) as suspect. Any use must pass a leakage test: their predictive
    power must not be clearly higher before the model's training cutoff than after it.
  - The no-Claude baseline is leak-free by construction.
- **Truncation:** releases longer than 40,000 characters (after anonymising) are cut, and this is
  flagged per document. The headline numbers come first in a release, but long tables at the end can be lost.
- **EPS accuracy check:** extracted GAAP diluted EPS is compared with the figure the company later
  filed in XBRL for the same period. That later figure is used only to measure accuracy, never as a
  trading input, because it wasn't public on the trade date.
- **Model drift:** API models get retired, so labels can't be regenerated with the exact same model
  later. The raw responses are stored so labels never have to be regenerated.

## US

### SEC EDGAR filings

- **Acceptance time:** we use the **filing index page** (`<accession>-index.htm`, "Accepted",
  Eastern time). The `acceptanceDateTime` in SEC's submissions JSON is labelled UTC ("…Z"), but for
  many older filings it is actually **Eastern time**: in a 2012–2024 sample, 20–50% per year were
  off by 4–5 hours. It is stored as `sec_filings.acceptance_utc_sub` for reference only and must
  never be used for timing.
- **⚠️ 8-K acceptance ≠ when the market saw the news.** Companies usually put earnings out on a
  newswire first and file the 8-K minutes to hours later. The acceptance time is therefore an
  *upper bound* on when the news was public. A strategy timed off acceptance looks later (more
  conservative) than one timed off the wire. If the 8-K comes well after the release, "pre_market"
  can understate how early the news was out.
- **Session labels** use wall-clock ET only. Exchange holidays and half days are not flagged yet.
- **Documents:** Ex-99.1, else the first EX-99.x, else the primary 8-K document. For news items
  (1.01/2.01/7.01/8.01) without an exhibit, the 8-K body *is* the news. An 8-K carrying both 2.02
  and a news item is filed under "earnings" only.
- **Tickers:** `company_tickers_exchange.json` covers **current** companies only (about 10k rows).
  Delisted CIKs keep their filings but have no ticker, so no prices.
- **⚠️ Survivorship:** yfinance has no delisted US prices (see the README for paid options).
- **Prices:** only NYSE and Nasdaq tickers (OTC skipped), plus SPY and IWM. Same OHLC
  conventions and flags as the UK prices (split-adjusted; `adj_close` also dividend-adjusted).
- **Company facts:** a fixed list of XBRL concepts (config `us.fact_concepts`) is extracted, and
  the whole `companyfacts.zip` is kept so other concepts can be added later. `filed` is the
  point-in-time date to use. Facts come from 10-K/10-Q filings, so they lag 8-K earnings releases.

### US as a trading market (not just a comparison)

- **⚠️ Survivorship is NOT solved by EDGAR.** EDGAR has the filings of dead companies, but our
  prices (yfinance) only cover today's tickers. Companies that failed after bad news drop out of the
  backtest, so results look better than reality. This is the biggest open gap for a US decision, and
  needs a delisted price source (README lists options; nothing bought).
- **⚠️ The news is often public before the 8-K.** Earnings usually go out on a newswire first and
  the 8-K is accepted minutes to hours later. Professionals react to the wire, so the strategy must
  only count drift **after** the price we could actually trade at (the next open), never from the
  previous close.
- **Post-earnings drift has weakened** since it was first documented, especially in liquid stocks.
  What remains tends to be in small, illiquid names, where spreads and slippage are highest.
  Results will be reported by size and liquidity bucket.
- **Spreads:** no free historical quotes, so the spread is estimated from daily high/low. That's
  rough, and results are rerun with 1.5× and 2× spreads.

## Phase 3: US earnings events (`features/us_earnings.py`)

- **Trade timing:** buy at the open of the first trading day after the release (pre-market release:
  that morning if the market opens that day; after-close release: the next trading day). Releases
  during market hours are excluded. The trade-day open is the entry price and is **not** used as a
  feature (a market-on-open order can't see it), so the overnight gap isn't an input.
- **Ticker:** today's SEC ticker map joined to Yahoo price series that cover the trade date; with
  several share classes, the most traded. ⚠️ A ticker reused by a different company could attach the
  wrong history; the coverage-date check limits but doesn't exclude this.
- **Prices are split-adjusted to today** (Yahoo), while XBRL share counts and EPS are as reported at
  the time. Returns and dollar volume are unaffected; **market cap, P/E and P/B are not computed yet**
  (would be wrong for companies that split later). Needs split histories (small Yahoo download).
- **Exit rules:** a day whose range touches both bracket levels counts as the stop (conservative);
  an opening gap through a level fills at the open. Dividends are ignored (short holds).
  Windows touching stale/suspect prices are excluded (3,931 of 150,518); 1,297 releases have no
  price on the trade date.
- **Financials:** only facts filed before the trade date; the first filed value for each period
  (restatements ignored). Q4 EPS is derived as year minus three quarters (approximate).
- **Sanity check:** the average 5-day return net of SPY across all releases is about zero (−0.1%),
  as expected if there's no look-ahead in the pipeline. Extreme values (±300% days) exist and need
  price/liquidity filters before any backtest.
