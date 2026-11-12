# Design: ETF trend-following and momentum (written 2026-11-12, BEFORE any result was looked at)

Fixed before running; later changes are added at the end with the reason.

## The idea and the evidence
Assets that have risen over the past 6–12 months tend to keep rising, and assets in a downtrend
tend to keep falling (time-series momentum: Moskowitz, Ooi & Pedersen 2012; Hurst, Ooi & Pedersen's
century of trend-following; Faber 2007's 10-month moving average; Antonacci's dual momentum).
Rebalanced monthly, so costs are tiny even for a $6,000 account. The usual benefit is missing the worst
of crashes; whether it beats simply holding the index is what this tests.

## Practical note: the user is in the UK
UK retail investors can't buy US-domiciled ETFs (no key information document). Trading 212 UK offers
London-listed UCITS ETFs tracking the same indexes (e.g. S&P 500: CSPX/VUSA; treasuries, gold, global
stocks). The US ETFs below are used as **stand-ins for their UCITS equivalents** because their price
histories are far longer; total returns (dividends reinvested, `adj_close`) are used. In an ISA there is
no tax, so none is modelled.

## Data
Yahoo daily prices from each fund's start: SPY, QQQ, IWM, EFA, EEM, VNQ, GLD, DBC, TLT, IEF, AGG, SHY
(SHY = cash). Common history from 2006-02 (DBC); with a 12-month look-back, trading starts 2007-03.

## Strategies (fixed; no parameters fitted)
- **V1 Faber GTAA-5:** SPY, EFA, IEF, VNQ, DBC at 20% each; each held only if its month-end price is
  above its 10-month average, otherwise that 20% sits in SHY.
- **V2 Dual momentum (Antonacci GEM):** if SPY's 12-month return beats SHY's, hold whichever of SPY /
  EFA had the higher 12-month return; otherwise hold AGG.
- **V3 Top 3 of 10 with a trend filter:** from SPY, QQQ, IWM, EFA, EEM, VNQ, GLD, DBC, TLT, IEF, hold
  the 3 with the best average of 1/3/6/12-month returns, a third each, each only if above its
  10-month average (else SHY).
- **V4 S&P 500 timing:** SPY when above its 10-month average, else SHY.
- **Benchmarks:** SPY bought and held; 60/40 SPY/IEF rebalanced monthly.

## Mechanics
Signals on the last trading day's close of each month; trades at the **next trading day's close**
(one day late, conservative). Costs on the traded amount, each side: 0.05% spread + 0.05% slippage +
0.15% currency fee (Trading 212, UCITS lines in USD bought from GBP). Whole $6,000 portfolio, no
leverage, no shorting.

## Years and pass rule
Monthly strategies have few decisions a year, so the choose period is long: **2007-03 to 2021-12**
(includes 2008, 2020). **Final test 2022-01 to the latest data, once**, with the chosen strategy
frozen. **Pass = a higher total return after costs than SPY bought and held over the final period.**
Also reported: yearly returns, worst fall from a peak (max drawdown), volatility, Sharpe ratio.
A strategy that only lowers risk but earns less fails the user's goal and is reported as such.

## Known risks
Few independent decisions (trend changes a handful of times a year) make results noisy. US ETF
histories stand in for UCITS ones (small tracking and fee differences). Momentum suffered sharp
reversals in some years (e.g. 2009 rebound, 2020 V-shape).
