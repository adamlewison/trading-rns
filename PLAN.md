# Plan: find an algorithmic trading strategy that makes money

**The goal:** an automated strategy that makes money after all costs and beats simply holding an
index fund. We are not attached to any market, data source or idea. When something fails, we
record why and move to the next track. We keep going until one passes.

**How we work:**
- **Ideas come from the evidence base.** Claude brings a broad knowledge of the trading
  literature, statistics, market microstructure and what traders have done before, and uses it to
  pick ideas with a real chance and to test them properly and fast.
- **Knowledge alone is not an edge.** Markets are competitive and most published effects shrink once
  known, so every idea has to pass the same tests. The tests protect us from our own confidence.
- **No real money until a strategy passes a clean final test**, then paper trading, then small size.

**The account we design for:** about $6,000, around $150 per trade, Trading 212 (no commission,
0.15% currency fee each way), holds of up to about 20 trading days.

---

## What we've learnt (keep this list growing)

1. **Costs decide everything at small size.** At $150 a trade, IBKR's $1 minimum each way is 1.3% a
   round trip; Trading 212 is about 0.5–0.6% all-in on liquid US stocks. An edge has to clear that.
2. **Don't estimate spreads from daily high/low for liquid stocks.** Corwin–Schultz gave Apple
   0.42% (real: ~0.01%). It measures volatility. Use the liquidity table in the testing rules.
3. **LLMs are good, cheap readers.** GPT-6 Luna read 150,099 earnings releases for $49, with EPS
   matching the later SEC filing 97% of the time, and its labels improved the model's ranking by
   about half. Reading wasn't the bottleneck; the size of the edge was.
4. **Anonymising doesn't hide the company** (the model named it in 90% of tests), so use LLMs for
   facts copied from the text, and treat their judgements as possibly coloured by hindsight.
5. **The best of many tries always looks good.** After ~75 variants on 2019–2021, the winner failed
   on 2022–2026. Write the design down first, count every try, keep a final test untouched.
6. **Simulate the real account.** With 40 slots, signals arrive in bursts (earnings season) and the
   slots fill with the first ones, not the best. This changed results a lot.
7. **Post-earnings drift after the next open is gone in liquid US stocks (2022–2026).**
8. **Survivorship:** we have almost no prices for delisted companies (US or UK). Every result is
   flattered until that's fixed (paid data options in the README).
9. **Infrastructure:** on the 1GB droplet DuckDB needs `ROW_GROUP_SIZE 8192`; OpenAI batches can
   stall or expire after 24h, so runners must resume and survive refused calls.

---

## What we've tried

| Strategy | Result | Record |
|---|---|---|
| US earnings drift: LLM-labelled releases, model scores them, 6 exit rules × 3 models | **Failed.** Edge smaller than costs; the best setup (liquid stocks, buy at the release day's close, hold 20 days, $150 × 40) lost to the market by $1,283 over 2022–2026 | `backtest/DESIGN_US_EARNINGS.md` |

The 2022–2026 years are spent for earnings drift. Any further work on it can only be judged by
live paper trading.

---

## What's next, in priority order

| # | Strategy | Data | LLM cost | Why here |
|---|---|---|---|---|
| 1 | **Insider buying** (US + UK) | SEC insider data sets (free bulk download); UK director dealings already in RNS | none | Strongest published evidence; few trades so costs hardly matter; fits a small pot |
| 2 | **No-news reversals** | have it | none | Cheap to test |
| 3 | **Run-up before scheduled earnings** | have it | none | Cheap to test; small edge, liquid stocks only |
| 4 | **US news drift** (deals, CEO exits, profit warnings) | have it (421,832 filings) | ~$140 | Only if 1–3 find nothing; earnings drift failed, so the bar is high |
| 5 | **UK RNS news drift** | have it | pilot first | High costs (AIM spreads, 0.5% stamp duty on main market) |

**1. Insider buying.** When directors buy their own company's shares with their own money (above
all several within a few weeks), buy and hold weeks to months. US: SEC's quarterly *Insider
Transactions Data Sets* (Forms 3/4/5 as tables, 2006 on). UK: "Director/PDMR Shareholding"
announcements in the RNS data. Open-market purchases (code P) by officers and directors only;
cluster = 2+ insiders in 30 days. Holds of 20 / 60 / 120 days. Risk: the effect is biggest in small
companies, the ones most missing from our prices if they later failed.

**2. No-news reversals.** A stock falls sharply (e.g. worse than −2× its normal daily move, and
worse than the market) on a day with no filing or announcement: buy at the close, hold a few days.
Risk: news we can't see (downgrades, sector news). Liquid stocks only.

**3. Run-up before scheduled earnings.** Buy about 5 trading days before the expected results date
(from the company's past release dates), sell the day before. Small edge, so liquid stocks only.

**4. US news drift.** The earnings pipeline applied to other 8-Ks. Worth it only with a reason to
expect a bigger effect than earnings (less-followed events, smaller firms).

**5. UK RNS news drift.** Data complete; costs are the problem. The LSE market-maker file (manual
download) is still pending.

### Exit ideas (tools applied to each strategy, fixed before each test)
- **A. Targets scaled to each stock's normal move (default).** Target and stop as multiples of the
  20-day average true range, e.g. target 2×, stop 1×, plus a time limit. Simple, hard to overfit.
- **B. Trailing stop.** Once in profit the stop follows the price (e.g. 2× the daily move below the
  highest close). For the longer holds (1, 4).
- **C. Model predicts how far the stock will run.** Not planned: a second prediction on top of a
  weak first one is the easiest way to fit noise.

Exits change the shape of the payoff, not whether the picks go up. A signal with no edge before
costs can't be rescued by an exit rule.

### More tracks to consider when these are done
Momentum and trend-following on ETFs (few trades, low costs); analyst-revision data if a cheap
source exists; index rebalancing; the delisted-price purchase if a strategy depends on small caps.

---

## Testing rules (every strategy)

1. **Write the design down and commit it before running.** Every later change is listed as a change.
2. **Walk-forward:** fit only on the past; choose on 2019–2021; final test on 2022–2026, **once**,
   with the setup frozen. A strategy's final years are spent once used.
3. **Point in time:** a feature may only use data published before the trade (XBRL by `filed`
   date, first-filed values, trade at the next possible price after the news).
4. **Costs:** Trading 212 (primary) and IBKR. Spread by 20-day dollar volume: ≥$500m 0.05%,
   $100–500m 0.10%, $20–100m 0.20% (full spread, half each side); slippage 0.10–0.15% each side.
5. **The real account:** $150 per trade, up to 40 open ($6,000), signals skipped when full. Compare
   with an index fund over the same years.
6. **A no-LLM baseline** must be beaten before paying for LLM labels.
7. **Report every variant tried**, not just the best, and flag anything that could flatter results
   (`ASSUMPTIONS.md`).
