# Design: monthly large-company factor portfolio, US (written 2026-11-13, BEFORE any result was looked at)

Fixed before running; later changes are added at the end with the reason. Testing rules in `CLAUDE.md`.

## The idea and the evidence
Cheap stocks (value), profitable and conservatively financed ones (quality) and recent winners (momentum)
have earned more than the market over long periods, and combining them works better than any one
(Fama & French; Asness, Moskowitz & Pedersen 2013 "Value and Momentum Everywhere"; Novy-Marx 2013).
In large companies the effects are smaller but cheaper to trade, and survivorship bias is small
(big companies rarely vanish by failing; they are usually taken over at a premium). Monthly
rebalancing keeps costs low for a $6,000 account.

## Data (point in time)
`us_prices_daily` (adj_close = total return, 2012 on), `us_facts` (only values filed before the
rebalance date, first-filed per period), `us_splits` (to get the price as it was).

## Universe, each month-end
The **500 largest** companies by market cap (price then × shares, as in `features/us_earnings_labels.py`)
with price ≥ $5 and 20-day dollar volume ≥ $20m. One share class per company (the most traded).

## Scores (percentile ranks within the universe, higher = better; a missing value counts as middle)
- **Value:** mean rank of earnings yield (net income / market cap), book-to-market (equity / market cap),
  sales-to-price (revenue / market cap).
- **Quality:** mean rank of return on equity, operating margin (operating income / revenue), and low
  leverage (−long-term debt / assets).
- **Momentum:** return from 12 months to 1 month before the rebalance.
- **QVM:** mean of the three.

## Portfolio
Top **30** by score, equal weight (≈ $200 each in a $6,000 account), rebalanced on the next trading day's
close after each month-end. Four variants: V, Q, M, QVM. Costs on the traded amount, each side: Trading 212
FX 0.15% + slippage 0.10% + half the spread from the liquidity table. Benchmark: SPY held (total return).

## Years and pass rule
Choose period **2013-01 to 2021-12** (momentum needs 12 months of prices from 2012); final test
**2022-01 → latest, once**, frozen. **Pass to the final test:** beats SPY in total over the choose
period and in at least 5 of the 9 years, and the margin must not depend on 2020 alone (lesson 14: it must
also beat SPY over 2013–2019). **Final pass:** beats SPY in total over 2022 → latest.

## Survivorship check (no purchase)
For each year, the companies whose latest 10-K reported a public float (`EntityPublicFloat`) ≥ $5bn,
and how many of them have no price in our data at that date: the share of the large-company world we
cannot see. Reported beside the results.

## Change 1 (2026-11-13): bug fix after the first run
The first run used each stock's own last trading day of the month as its "month-end", creating 311
rebalance dates in 179 months; on the odd dates the universe was a handful of stocks and the top 30
included junk (e.g. a $15,538 reverse-split penny stock), giving impossible returns (2020: +99% to +178%)
and wrong SPY years. Fixed: only the market's real month-end trading day (SPY calendar) counts. No rule
was changed; the first run's numbers are void.
Change 2 (same day): yearly figures were filed under the year each monthly period ended (so "2019" held
December 2018); now filed under the month held. Totals were unaffected.

## Choose-period result (2013–2021, after the fixes, run 2026-11-13): fails the pass rule, no final test
Per year after costs vs SPY 16.2%: **Quality 17.3%** (total +319% vs +288%; beat SPY in 4 of 9 years; also
ahead over 2013–2019), Value 12.6% (3/9), Momentum 10.6% (2/9), QVM 7.7% (2/9). Quality misses the
"at least 5 of 9 years" rule, so nothing goes to the final test. Survivorship check: of the large filers
(public float ≥ $5bn) 29% had no price in 2013, 17% in 2019, 3% in 2026: the early years miss a big slice
of the large-company world, so even large-cap results here are not clean.

## FROZEN for the final test (2026-11-13): user override of the pass rule
The user chose to run the final test on **Quality** although it met only 4 of the 9-year rule (5 required).
Frozen exactly as above: top 30 of the 500 largest by the quality score (ROE, operating margin, low
leverage), equal weight, monthly, Trading 212 costs. Run ONCE on 2022-01 → latest. Pass = beats SPY (total
return) over the period. The other three variants print for information only.
