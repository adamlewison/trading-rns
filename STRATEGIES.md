# Strategies: what we have, what we know, what's next (updated 2026-11-12)

The account we design for: **about $6,000, around $150 per trade, Trading 212** (no commission,
0.15% currency fee each way). The bar to beat is an index fund, after costs.

## Priority order

| # | Strategy | Status | Data | LLM cost | Why this position |
|---|---|---|---|---|---|
| 1 | Insider buying (US + UK) | not started | SEC data sets (free download); UK already in RNS | none | Strongest published evidence, few trades so costs are tiny, fits a small pot |
| 2 | No-news reversals | not started | have it | none | Cheap to test with data we hold |
| 3 | Run-up before scheduled earnings | not started | have it | none | Cheap to test; small edge, so only liquid stocks |
| 4 | US news drift (non-earnings 8-Ks) | not started | have it (421,832 filings) | ~$140 | Only after 1–3: earnings drift failed, so the bar is high |
| 5 | UK RNS news drift | data ready | have it | ~£? (pilot first) | High costs (spreads, 0.5% stamp duty on main market) |
| – | US earnings drift | **tested, failed** | – | spent $49 | See below |

Exit ideas (A–C) are tools applied to whichever signal is being tested, not separate strategies.

---

## Tested

### US earnings drift: failed (2026-11-12)
- Luna (an OpenAI model) read 150,099 earnings releases; a model scored them; 6 exit rules × 3 models
  were tested on 2019–2021, and one frozen setup was tested once on 2022–2026.
- Luna's labels improved the ranking, but the edge was smaller than trading costs. The best setup
  (liquid stocks, buy at the release day's close, hold 20 days) lost to the market by $1,283 over
  2022–2026 on a $6,000 account.
- Details: `backtest/DESIGN_US_EARNINGS.md`. The 2022–2026 years are spent for this strategy, so no
  more tuning it on them. Exit ideas A–B could be tried on 2012–2021 only, but there's no clean
  final test left, so new live paper trading would have to be the judge. Low priority.

---

## To test, in order

### 1. Insider buying
- **Idea:** when a company's directors buy its shares with their own money (above all several
  directors within a few weeks), buy too and hold weeks to a few months.
- **Data:** US: SEC's quarterly *Insider Transactions Data Sets* (Forms 3/4/5 as tables, 2006 on;
  a bulk download, no scraping of individual filings). UK: "Director/PDMR Shareholding"
  announcements are already in the RNS data (amounts need reading from the text; regex first, LLM
  only if needed).
- **Signal:** open-market purchases only (code P), by officers/directors; cluster = 2+ insiders in
  30 days; size vs their salary or holding where available. Ignore sales, option exercises, plans.
- **Costs:** about 0.5% per round trip at Trading 212 on liquid stocks, paid a few times a year
  per position, so they hardly matter.
- **Risk:** small companies (where the effect is biggest) are the ones missing from our price data
  if they later failed (survivorship). Results need the delisted-price caveat.
- **Test:** hold 20 / 60 / 120 days; the 2022–2026 years are still clean for this signal.

### 2. No-news reversals
- **Idea:** a stock falls sharply (e.g. −2× its normal daily move, worse than the market) on a
  day with no 8-K or RNS: buy at the close, hold a few days for the bounce.
- **Data:** all in hand (prices + every filing date).
- **Risk:** news we can't see (analyst downgrade, sector news, a filing we didn't download). Short
  holds make costs matter, so liquid stocks only.

### 3. Run-up before scheduled earnings
- **Idea:** stocks drift up slightly in the days before results. Buy ~5 trading days before the
  expected date (from the company's past 8-K 2.02 dates), sell the day before.
- **Data:** in hand. **Risk:** the edge is small (tenths of a percent), so liquid stocks only and
  cost-sensitive.

### 4. US news drift (deals, CEO exits, profit warnings, ...)
- Same pipeline as earnings: Luna labels (~$140 at the measured rate), model, walk-forward,
  one-time final test. Do this only if 1–3 show nothing better, or with a clear reason to expect a
  bigger effect than earnings (less-followed events, smaller firms).

### 5. UK RNS news drift
- Data complete. Costs are the problem: wide spreads on AIM, 0.5% stamp duty on main-market buys,
  fills on small stocks. The LSE market-maker file (manual download) is still pending.
  The UK director dealings part is folded into #1.

---

## Exit ideas (applied per strategy, fixed before each test)

**A. Targets scaled to each stock's normal move (recommended).** Target and stop as multiples of
the stock's typical daily move (20-day average true range), e.g. target 2×, stop 1×, plus a time
limit. A quiet stock and a wild one get comparable odds. Simple and hard to overfit. Use as the
default bracket.

**B. Trailing stop to let winners run.** Once in profit, the stop follows the price, e.g. always
2× the daily move below the highest close. Test alongside A for the longer-hold strategies (1, 4).

**C. Model predicts how far the stock will run, target set from that.** Not planned: it adds a
second prediction on top of a weak first one, and is the easiest way to fit noise.

Note from the earnings test: exits change the shape of the payoff, not whether the picks go up.
If a signal has no edge before costs, no exit rule rescues it.

---

## Rules for every test (from the earnings work)
- Write the design down and commit it **before** running; every later change is listed as a change.
- Walk-forward: fit on the past only; choose on 2019–2021; final test on 2022–2026 **once**.
- Costs: Trading 212 and IBKR; spreads from the liquidity table (≥$500m/day 0.05%, $100–500m 0.10%,
  $20–100m 0.20%), not from daily high/low, which overstates them badly for liquid stocks.
- The account: $150 per trade, up to 40 open ($6,000), signals skipped when full. Compare with an
  index fund over the same years.
- Report every variant tried, not just the best.
