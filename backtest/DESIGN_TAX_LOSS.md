# Design: US year-end tax-loss rebound (written 2026-11-15, BEFORE any data was looked at)

Fixed before running; later changes go under "Changes" with a date. How to run it: `backtest/RUNBOOK.md`.

## The idea
US investors sell their losing shares before 31 December to book the loss against tax. The selling is
not about the companies, so the year's worst performers, especially smaller ones, are pushed down in
December and tend to bounce in the first weeks of January (the turn-of-the-year effect: Roll 1983,
Reinganum 1983, and many since). One trade a year, held about three weeks.

Why it might not work now: it has been known for 40 years, the bounce has moved earlier into December,
and the year's worst stocks include ones that are simply failing.

## Data
`us_prices_daily` (split-adjusted; `adj_close` for returns), SPY and IWM from the same source. Nothing new.

## Step 0: data checks (no outcomes)
For each December 2012–2025 report: stocks with a clean price on the first trading day of the year and
on the selection day; how many pass the filters below. Expect a few hundred to two thousand. Fewer than
200 in any year: report and stop.

## Rules
- **Selection day S** = the 6th-last trading day of December. **Entry** at the open of the 5th-last
  trading day. **Exit** at the close of the 10th trading day of January (variant: the 20th).
- **Universe on S:** common stocks with clean prices (not stale or suspect) on S and on the year's first
  trading day; price on S ≥ $2 (actual price, not split-adjusted: multiply by later split ratios);
  20-day average dollar volume on S between $2m and $100m (small and mid-sized, but tradable).
- **Year-to-date return** = `adj_close` on S ÷ `adj_close` on the first trading day of the year − 1.
- **Variants (4, nothing else tried):** selection **W30**: the 30 stocks with the worst year-to-date
  return; or **X80**: the worst 30 after removing any stock that fell more than 80% (the likeliest to be
  failing). Each with the two exits: W30-10, W30-20, X80-10, X80-20.
- Equal stakes of $150, 30 positions ($4,500); the rest of the $6,000 and all money for the rest of the
  year stay in SPY. If a holding has no clean price on the exit day, use its last clean close before it,
  and if it stopped trading altogether, count it as a 100% loss.

## Costs
Trading 212: FX 0.15% each way, slippage 0.15% each side, half the spread each side from the liquidity
table in `CLAUDE.md` section 8; for $2–20m daily dollar volume use a full spread of 0.60%.

## Years and pass rule
Fourteen Decembers exist (2012–2025). Choose: December 2012–2020 (9 trades). Final: December 2021–2025
(5 trades), once, one variant frozen. Each December is **one** observation, not thirty.
- **Pass to the final test:** a variant's basket beats SPY after costs over the same days in at least 7 of
  the 9 years, with a mean excess above 1.5%, and also beats IWM on average (otherwise it is only the
  small-company January effect, which a small-cap fund gives without the costs).
- **Final pass:** the frozen variant beats SPY after costs in at least 4 of the 5 years and in total.
- With 9 and 5 observations no t-statistic means much: report the year-by-year table and nothing fancier.

## Paper trade, whatever the backtest says
Survivorship cannot be fixed in the backtest, but a live trial has none. On the selection day of
**December 2026** (about 23 December; compute it from the trading calendar) record the baskets for all
four variants in the paper-trading tables with their entry prices, and mark them on the exit days in
January 2027. This costs nothing and is the only clean evidence available. Add it to `paper/` beside
the factor portfolios; nothing is traded.

## Known risks and traps
- ⚠️ **Survivorship, strongly flattering.** The year's worst losers are exactly the stocks most likely
  to have been delisted since, and delisted stocks have no prices here. The historical baskets are
  therefore missing their worst members. Report, per year, how many stocks had a price in January of the
  year but none in December (a rough count of what is missing). A backtest pass is weak evidence.
- ⚠️ Lessons 10 and 14: beaten-down small stocks rebound hardest after crashes. Report with the best year removed.
- Trap: choosing the basket with prices from after S, or entering on S itself. Selection uses the close
  of S; entry is the next open.
- Trap: the $2 filter applied to split-adjusted prices (a stock that later reverse-split 1-for-20 looks
  like $40 when it was $2). Use the actual price on S.
- Trap: counting 30 stocks in one December as 30 independent results. They are one bet.
- Trap: comparing with SPY only. The comparison with IWM is what separates this from "small caps went up".

## Changes (2026-11-15, made before any result was seen)
- The price table has no security type, so funds and exchange-traded products are removed by company
  name (`NOT_COMMON` in `backtest/us_tax_loss.py`); realty and bank "trusts" are lost with them.
- "The 20th trading day of January" is counted from January's first trading day and runs into February
  when January has fewer than 20 (January 2021 has 19).

## Choose-years result (Decembers 2012–2020, run 2026-11-15): FAILED the pass rule
Needed: beat SPY after costs in at least 7 of 9 years. Best was 6 of 9. Final years (2021–2025) unused.

| Variant | Years beating SPY | Mean net | Mean vs SPY | Median vs SPY | Mean vs IWM | $ vs SPY (9 trades) | Mean vs SPY without best year |
|---|---|---|---|---|---|---|---|
| W30-10 | 6 of 9 | +3.4% | +2.4% | +3.1% | +2.4% | +$990 | +0.7% (without 2018) |
| W30-20 | 5 of 9 | +5.5% | +3.7% | +1.6% | +4.1% | +$1,503 | −0.4% (without 2020) |
| X80-10 | 6 of 9 | +3.6% | +2.7% | +3.1% | +2.6% | +$1,074 | +0.8% (without 2020) |
| X80-20 | 5 of 9 | +6.0% | +4.2% | +1.6% | +4.6% | +$1,696 | −0.2% (without 2020) |

- By year, W30-10 vs SPY: 2012 +1.8%, 2013 +11.0%, 2014 −5.8%, 2015 −23.1%, 2016 +3.1%, 2017 +6.3%,
  2018 +16.5%, 2019 −3.9%, 2020 +16.2%. The bounce exists on average but one bad January (2016: −23%)
  costs as much as the two best years earn; this is a volatile bet on beaten-down small stocks, not a
  reliable rebound.
- Checks: the whole filtered universe returned about what IWM did each year (benchmark check passes);
  270 trades per variant, none dropped for a missing entry price. Costs 0.8–1.2% a round trip.
- ⚠️ Not one of the 270 picks "stopped trading" before the exit. Among each year's 30 worst stocks that
  is implausible: the failures are missing from the data (survivorship), so even these numbers flatter.
- Averages are carried by 2018 and 2020 (rebounds after sell-offs, lessons 10 and 14).
- The December 2026 paper baskets asked for above are **not yet set up** (open item in `CLAUDE.md`).
