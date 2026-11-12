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
