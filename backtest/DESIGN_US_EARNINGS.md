# Phase 4 design: US earnings (written 2026-11-12, BEFORE any result was looked at)

Fixed here so that results can't shape the design. Anything changed after a result is seen gets
listed in the results report as a change, with the reason.

## Data
- `data/features/us_earnings_model.parquet` (Phase 3). One row per earnings release.
- **Tradable rows only:** has a price on the trade date, a clean forward window, prior close ≥ $1,
  20-day average dollar volume ≥ $500k (at $500 per trade we would be ≤0.1% of a day's volume).
- **Years:** fit 2012–2018; choose on 2019, 2020, 2021; final test 2022–2026, **run once**, only
  after the user agrees the setup is frozen. The code refuses to look at 2022+ without `--final`.

## Walk-forward
- For each year Y (2016 onwards; 2016–2018 only to set the top-decile cut, see below): train on
  all tradable rows from 2012 to Y−1 whose exit could not reach year Y (trade date before 1 Dec
  Y−1, so 20-day holds end in Y−1), predict year Y.
- No hyperparameter search. LightGBM, fixed settings: 400 trees, learning rate 0.03, 15 leaves,
  min 200 rows per leaf, 80% row and column sampling, L2 objective, seed 7.
- Target: excess return for the exit rule, clipped to [−50%, +50%] (fixed bounds) when fitting only.
  Benchmark: SPY if market cap ≥ $10bn, else IWM (missing market cap: IWM).

## Models (feature groups, nested)
- **B: no LLM.** Price context (5/20-day return, volatility, dollar volume), session (pre/after),
  point-in-time financials and ratios, valuation (log market cap, earnings yield, book/market,
  sales/price), 8-K items on this filing and the past 90 days, EPS history (scale, count, last known).
- **N: B + numbers the LLM read from the release.** SUE (GAAP and adjusted), EPS change / price,
  revenue growth, swing to loss/profit, guidance change vs previous (EPS, revenue), first guidance.
- **L: N + the LLM's judgements.** Tone, guidance raised/kept/cut/withdrawn as stated, beat/miss as
  stated, the 17 event flags, confidence, is-an-earnings-release, fiscal period.
The LLM is worth its cost only if L (or N) beats B on the choose years after costs.

## Exit rules (Phase 3; the only ones tested)
(a) open → close day 1; (b) +5% / −3% / 3 days; (c) hold 5 days; (d) 20 days with −8% stop.

## Which trades (two rules, both reported)
- **S1 "beats its cost":** buy when the predicted excess return > the estimated round-trip cost at
  that position size.
- **S2 "top decile":** buy when the prediction is above the 90th percentile of the PREVIOUS year's
  out-of-sample predictions (so no knowledge of the current year's distribution).

## Costs (config `us_costs`; IBKR Pro fixed)
- Commission: $0.005/share, min $1, max 1% of trade value, each side.
- FX: 0.03% each way. Slippage: 0.10% each side.
- Spread: half the Corwin–Schultz estimate (20 days before the trade, from daily high/low) each
  side, ×1 / ×1.5 / ×2.
- Shown at $30 / $100 / $200 / $500 per position.

## What gets reported (every variant: 3 models × 4 rules × 2 selections)
Per choose year and pooled: trades, mean gross excess return, mean net at each size, hit rate,
t-statistic, and the rank correlation between prediction and outcome on all tradable rows.
Shorts (bottom decile, gross only) separately: they need a margin account and borrow costs we don't model.

## Known limits (flag in every report)
Survivorship (delisted firms mostly missing) flatters results. Capacity: trades are assumed
independent and fully funded, no limit on positions per day.

## Addendum 2026-11-12 (added after the first choose-years run finished, BEFORE its results were opened)
The user agreed to add a post-earnings-drift test. Two more exit rules, same models, selections and costs:
- **(e)** buy at the **close of the first trading day** after the release, hold 20 trading days, sell at the close.
- **(f)** the same, held 60 trading days.
For (e) and (f) the model also sees the first day's reaction, which is known at that close:
the opening gap (open vs previous close), the open-to-close return, and that return minus the
benchmark's. Training rows must have ended before the predicted year (trade before 1 Oct of the
previous year for 60-day holds). A window with a stale or suspect price, or that runs past the
data, gives no outcome. No stop-loss (the drift literature holds through).

## Addendum 2 (2026-11-12, after the choose-years results were seen; reported as a change)
**Why:** in the first run every short-hold rule lost because the picks cost ~1.3–2.1% to trade (wide
spreads on small stocks), while the LLM labels did improve the ranking. One follow-up, agreed with
the user: the **liquid universe**. Only releases whose stock traded ≥ $20m a day on average over the
previous 20 days, for training AND trading. Everything else unchanged (models, rules a–f,
selections, costs, years). Output stage `choose_liquid`. This is the second look at 2019–2021; both
runs are reported. If nothing passes, no more variants are tried on these years without a new reason.

## Finding 2026-11-12: the spread estimate is far too high for liquid stocks (cost-model error)
Corwin–Schultz from daily high/low gives AAPL 0.42%, MSFT 0.34%, median 0.5–0.7% for every liquidity
band, while real quoted spreads for such stocks are a few hundredths of a percent. It measures intraday
volatility, not the spread. So costs were overstated by ~0.5–0.7% per round trip for liquid stocks.
Re-priced (same trades, same selection S2) with a full-spread table by 20-day dollar volume:
≥$500m 0.05%, $100–500m 0.10%, $20–100m 0.20%. This table is an assumption chosen after seeing the
problem, not measured; real quote data would settle it. Rules e/f buy and sell in the closing auction,
where the spread matters least.
Result (liquid, model L, S2, $500): rule e +0.88% per trade net, positive in 2019, 2020, 2021
(+0.67/+1.26/+0.54); its picks beat the average liquid release by 1.5/2.0/1.05% (average drift
0.0–0.3%, so not survivorship drift). Model B (no LLM) is negative in 2021. 72 variants have now been
looked at on these years, so the best one is flattered by selection; only the final test can confirm.

## Addendum 3 (2026-11-12): the user's account, written before running (third look at 2019–2021)
The user wants $150 per trade, at most 10 days held, $6,000 in total. One setup, fixed here:
- **Rule (g):** buy at the close of the first trading day after the release, sell at the close 10
  trading days later. Liquid universe (≥ $20m/day), model L (all LLM inputs + day-1 reaction),
  walk-forward as before, selection S2 (above last year's top-decile prediction).
- **Account:** $150 per position, at most 40 open (= $6,000). Days in date order; within a day the
  highest predictions first; a signal is skipped when 40 are open. Cash not compounded.
- **Costs, two brokers (same trades):** spread from the liquidity table (≥$500m/day 0.05%,
  $100–500m 0.10%, $20–100m 0.20% full spread), half each side.
  - Trading 212: no commission, FX 0.15% each way, slippage 0.15% each side (no closing-auction
    orders, so a market order just before the close).
  - IBKR Pro fixed: $0.005/share, min $1, max 1% per side; FX 0.03% each way; slippage 0.10% each side.
  - Pessimistic check: Corwin–Schultz spread instead of the table.
- **Reported:** per year, trades taken and skipped, $ profit vs the market and actual, as % of $6,000.
If it fails here, it is not tuned further on these years.
**Result (2026-11-12): fails.** Picks vs market before costs +0.61% / +1.03% / −0.48% per trade
(2019/2020/2021). Trading 212: −$64 / +$189 / −$576 vs the market on $6,000; IBKR fixed: −$440 /
−$538 / −$1,038. Not tuned further on these years, as written above.
