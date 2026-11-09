# Plan (revised 2026-11-09)

**Goal:** an automated system that makes money after costs. Two candidate markets on equal terms,
judged by the same tests. Whatever wins is traded.

**Why the revision:** the first design came from the UK data. In that design, Claude labels the news,
a model scores it, and we buy at the open with a fixed +5% / −3% / 3-day bracket. The US data has more:
point-in-time company financials, exact 8-K item codes, the reported numbers in each earnings
release, and a liquid market where shorting is possible. The plan keeps its phases but uses these
inputs. No trading code had been written, so nothing is thrown away.

## Inputs the model gets (US; the UK gets the subset it has)

| Group | What | Source | UK? |
|---|---|---|---|
| **News labels** (Claude) | tone −2..+2, guidance raised/kept/cut/started/withdrawn, beat/miss *if the text says so*, events (CEO exit, restatement, impairment, buyback, equity raise, M&A, going-concern doubt ...), confidence | 8-K text, anonymised | yes (RNS) |
| **Reported numbers** (Claude) | revenue, net income, GAAP diluted EPS, adjusted EPS, the same figures for a year earlier, guidance ranges, period end | earnings release text | partly |
| **Earnings surprise** | growth versus the same quarter last year (seasonal random walk), scaled by the history of past changes (SUE) | release numbers + XBRL history | no |
| **Financials** | market value (shares × price), price/earnings, price/book, debt/equity, cash versus annual loss (cash runway), profitable or not, revenue growth | XBRL `us_facts`, only figures **filed** before the trade date, first-filed values (no restatements) | no |
| **Filing type** | 8-K item codes: 2.02 earnings, 5.02 exec change, 4.02 non-reliance, 1.01/2.01 deals, 1.03 bankruptcy, 7.01/8.01 other | `sec_filings.items` (all 8-Ks, free) | headline category |
| **Price context** | opening gap versus the previous close, return over the previous 5/20 days, volatility, average dollar volume, market move (SPY/IWM) | daily prices | yes |

To add before Phase 3: cash flow from operations and gross profit (`us.fact_concepts`). The full
`companyfacts.zip` is kept, so this is a local re-extraction with no new download.

## Phases (stop and report at the end of each one)

1. **Data:** UK done. US earnings done; US news finishes about Wednesday.
2. **Claude labels**
   - **Pilot:** 500 US earnings releases plus a 50-release leak check. Measures real cost,
     extraction accuracy (EPS checked against the later XBRL filing) and how often anonymised text
     still names the company.
   - **Then, with the user's OK:** the full US earnings run (cost measured by the pilot), US news,
     and the UK.
3. **Features and labels:** build the inputs above, strictly as of the trade time. Outcome labels
   for each exit rule below, in excess of the market (SPY for large caps, IWM for small).
4. **Backtest**
   - **Baseline first, with no Claude:** earnings surprise, filing type and price context only.
     Claude's labels must beat this baseline, after their cost, to be used.
   - **Exit rules:** fixed in advance, and the only ones tested:
     - (a) open → close on the first day;
     - (b) +5% / −3% / 3 days (the original bracket);
     - (c) hold 5 days;
     - (d) hold 20 days with a −8% stop.
   - **Walk-forward:** fit on 2012–2018, choose on 2019–2021, and report 2022–2026 **once** as
     the final test.
   - **Costs:** IBKR commission, spread estimate ×1/×1.5/×2, slippage. Results are shown at
     $30 / $100 / $200 / $500 per position.
   - **Longs first.** Shorts are reported separately because they need a margin account (minimum
     about $2,000).
5. **Live:** paper trading first, then small size, then sized up only if live results match the
   backtest.

## Guards against fooling ourselves (details in ASSUMPTIONS.md)

- **Point in time:** a feature may only use data published before the trade.
  - XBRL uses `filed`, not period end.
  - The trade is at the next open after the 8-K's acceptance time.
- **Restatements:** use the first-filed value for each period, never later corrections.
- **Look-ahead through the model:** text is anonymised and the leak rate is measured. Label quality
  is compared before and after the model's training cutoff.
- **Survivorship:** there are no delisted prices in either market. Results are an upper bound
  until bought data fills the gap.
- **Overfitting:**
  - A short pre-written list of exit rules and feature groups.
  - The test years are touched once.
  - Every variant tried is reported, not just the best.
