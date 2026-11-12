# Design: leveraged trend-following on the S&P 500 (written 2026-11-12, BEFORE any result was looked at)

Fixed before running; later changes are added at the end with the reason.

## Why this, now
The ETF trend test (`DESIGN_ETF_TREND.md`) showed S&P 500 timing (hold it above its 10-month average,
else cash) had ~2/3 of the index's volatility and under half its worst fall (−22% vs −55%), but earned
less (9.1% vs 10.8% a year, 2007–2021). Leverage applied only while the trend is up aims to turn that
lower risk into a higher return than the index (Gayed & Bilello 2016, "Leverage for the Long Run").
Leveraged S&P 500 UCITS ETFs (2× daily) are available to UK investors.

## Data
SPY total-return prices from 1993; the 13-week T-bill yield (Yahoo `^IRX`) for cash and financing.

## Modelled leveraged fund (L× daily, rebalanced daily like the real products)
Daily return = L × SPY's daily return − (L − 1) × (T-bill rate + 0.30%) / 252 − 0.75% / 252.
(Swap financing on the borrowed part plus a fund fee typical of 2× UCITS products.) Daily resetting
means losses in choppy markets ("volatility drag") are captured automatically.

## Strategies (fixed)
- **L1:** 2× fund when SPY's month-end price is above its 10-month average, else cash (monthly).
- **L2:** 2× fund when SPY's close is above its 200-day average (checked daily, traded at the next
  day's close), else cash.
- **L3:** as L1 with 1.5× (between the two).
- **Benchmarks:** SPY held; the 2× fund held throughout; 1× timing (V4 from the ETF test).

## Costs
Per switch, each side: 0.10% spread (leveraged ETFs are a little wider) + 0.05% slippage + 0.15%
currency fee (Trading 212). Cash earns the T-bill rate. No tax (ISA).

## Years and pass rule
Choose period **1994-01 to 2021-12** (includes 2000–2002, 2008, 2020); final test **2022-01 to the latest
data, once**, with the chosen strategy frozen. **Pass = a higher total return after costs than SPY
held over the final period.** Also reported: yearly returns, worst fall from a peak, volatility,
Sharpe. A worse worst-fall than SPY's is flagged even if the strategy passes.

## Known risks
A crash that happens faster than the signal can react (1987-style, 2020-style) is twice as painful.
Choppy, trendless years cause repeated switching losses and leveraged drag. Real leveraged funds
can differ from the model (financing, tracking); the fee and spread assumptions are estimates.

## Choose-period result (1994-01 .. 2021-12, run 2026-11-12)
After costs, a year: **L1 2× monthly 16.3%** (worst fall −40%, 34 switches in 28 years); L3 1.5× monthly
13.2% (−31%); L2 2× 200-day 12.9% (−46%, 184 switches); SPY held 10.7% (−55%); 2× held 14.4% (−88%);
1× monthly 10.2% (−21%). All three timed strategies beat holding SPY with a smaller worst fall.
The cost: losing years when SPY rose (whipsaws): L1 −15% in 1994, −10% 2010, −11% 2011, −19% 2015,
−19% 2018; it avoided 2001–2002 and 2008 (+1% in 2008 vs SPY −36%).
Caveats: leveraged ETFs only exist from 2006, so earlier years use the modelled fund; 3 variants were
compared. Next: the user chooses which one to freeze for the one-time final test (2022 → latest).

## FROZEN for the final test (2026-11-12, user chose L1)
**L1:** 2× modelled S&P 500 fund (daily reset, financing at T-bill + 0.30%, 0.75%/yr fee) when SPY's
month-end close is above its 10-month average, else T-bills; switch at the next day's close; 0.30% per
side per switch. Run ONCE on 2022-01 → latest data with `backtest.leveraged_trend final --final`.
Pass = higher total return than SPY held over that period. Not changed or re-run afterwards.

## FINAL TEST RESULT (2022-01 → 2026-11-12, run once): fails
L1 +6.7% total (1.4%/yr), worst fall −49%, vs SPY held +68.9% (11.4%/yr), worst fall −25%. 2022: L1 −40.8%
vs SPY −18.6%: the bear market was a slow, choppy decline with sharp rallies, so the monthly signal got
out late and back in at the wrong times (1× timing also lagged: −22%); leverage doubled each mistake.
L1 then trailed in 2023 (+9% vs +27%) after re-entering late. For information (not a verdict): the 2×
fund held throughout made +93%, the 200-day version +33%.
