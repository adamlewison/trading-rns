# Design: no-news reversals, US (written 2026-11-12, BEFORE any result was looked at)

Fixed before running; later changes are added at the end with the reason. Testing rules and the
account are in `CLAUDE.md` (sections 3 and 8).

## The idea and the evidence
Big one-day falls without news tend to partly bounce back over the next days (liquidity-driven
selling, overreaction), while falls on news tend to continue (Chan 2003; Tetlock 2010; Jegadeesh &
Titman 1995 on short-term reversal). It needs no LLM and tests whether the insider model's edge was
just dip-buying.

## Data
`us_prices_daily` (2012 on; split-adjusted), SPY/IWM, `sec_filings` (every 8-K with its filing date).

## Events (all must hold, on day t)
1. **Liquid:** prior close ≥ $5 and 20-day average dollar volume ≥ $20m (T212 costs ~0.5%).
2. **A big fall versus the market:** close-to-close return minus the benchmark's (SPY if market cap
   ≥ $10bn else IWM, missing → IWM) ≤ −2.5 × the stock's 20-day standard deviation of daily returns,
   and the raw return ≤ −4%.
3. **No news:** the company filed no 8-K (any item) dated the previous trading day or day t.
   ⚠️ Filing dates have no time, so an 8-K filed after the close on t also removes the event, though
   we'd only learn of it after buying. That flatters slightly; its share is measured and reported.
4. Clean bars (no stale/suspect day in the 21 days before or the holding window); no event for the
   same stock in the previous 5 trading days.

## Entry
- **E1:** the close of day t (in practice a market order just before the close: slippage 0.15%).
- **E2:** the open of t+1 (if E1 can't be done reliably). Both reported.

## Exits (fixed)
- **H1, H3, H5:** sell at the close 1 / 3 / 5 trading days after entry.
- **A5:** exit idea A: target +1.5 × ATR20, stop −1.5 × ATR20 from the entry price, checked on daily
  high/low (a day touching both counts as the stop; gaps fill at the open), else the close of day 5.

## Selections
- **S0** every event. **S1** LightGBM (fixed settings as before) predicting the H5 excess return from:
  the fall in σ units, raw fall, the market's return that day, 5/20/60-day returns before, 20-day
  volatility, dollar volume, market cap, distance to the 52-week high/low, days since the last
  earnings 8-K, weekday. Buy above last year's top-quintile prediction.

## Costs, account, years
Trading 212 (primary) and IBKR; spread table (≥$500m/day 0.05%, $100–500m 0.10%, $20–100m 0.20%).
$150 per position, at most 40 open, the largest fall (S0) or prediction (S1) first within a day,
skipped when full. Fit 2012–2018, choose 2019–2021, final 2022–2026 once with the setup frozen.
Report per year: events, trades taken, gross excess, net, account $ vs the market and actual.

## Known risks
News we can't see (analyst downgrades, sector news, press releases without an 8-K). Short holds make
results cost-sensitive. Survivorship: stocks that fell and later failed may be missing (smaller
problem for liquid stocks and short holds).

## Change 1 (2026-11-12, before any 2019–2021 result was seen)
54% of 8-Ks with a known time are filed after the 16:00 close, so rule 3 removed many falls whose
8-K came only after we'd have bought (hindsight). Now a day-t 8-K counts as news only if it was filed
before 16:00 ET or its time is unknown (items we didn't download). Previous-day 8-Ks always count.
Seen before the change: on the fit years 2012–2018 the average no-news fall kept falling (−0.3% vs the
market over 5 days, close entry); nothing from 2019 on had been looked at.

## Choose-years result (2019–2021, run 2026-11-12): fails, no final test run
10,199 usable events 2012–2021 (4,267 in 2019–2021). All 16 variants (2 entries × 4 exits × 2
selections) lost to the market in every year with Trading 212 (every-event: −$4,500 to −$6,200 over
three years on $6,000; model picks: −$1,900 to −$2,300). Gross excess per event ≈ 0 (−0.24% to +0.04%);
the model's picks did no better (−0.03% to −0.89%). No bounce to harvest in liquid US stocks after
no-news falls, at 1–5 days. The final years were not used (nothing passed).
