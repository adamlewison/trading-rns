# Design: UK RNS news drift, no-LLM baseline (written 2026-11-12, BEFORE any result was looked at)

Fixed before running; later changes are added at the end with the reason. Testing rules in `CLAUDE.md`.
An LLM labelling pilot only follows if this baseline finds drift before costs worth labelling.

## The idea
UK small companies (AIM and small Main Market) are thinly followed, so prices may under-react to news
released before the 08:00 open and drift for days after. Costs are the obstacle: wide spreads, 0.5%
stamp duty on Main Market buys, thin order books.

## Data
`announcements` (Investegate, pre-open = before 08:00 UK on a weekday), `prices_daily` (Yahoo, pence),
benchmarks ^FTAI (AIM All-Share) for AIM stocks and ^FTSC (FTSE SmallCap) for the rest,
`uk_trading_service` (SETS / SETSqx).

## Events
- Pre-open, regulatory, not admin, not a replacement, universe tickers. All of a company's pre-open
  announcements on one day form one event (category flags, count, total body length).
- Trade at **that day's open** (the 08:00 auction). Needs a clean price on the day and 60 clean days before.
- Tradable: prior close ≥ 5p and 20-day average turnover (close × volume) ≥ £50,000.

## Outcomes (each net of the benchmark over the same days)
O1 open → close day 1; H3 / H5 / H20 open → close of day 3 / 5 / 20; A5 = bracket at +2 / −1 × ATR20
from the open, 5 days (a day touching both counts as the stop; gaps fill at the open).

## Selections
- **S0 by category:** every event of one headline category (results, trading_update, contract_win,
  fundraise, director_dealing, acquisition_disposal, board_change, operational, dividend, takeover,
  other), each reported separately, for each outcome.
- **S1 model:** LightGBM (fixed settings as before) predicting the H5 excess return from category flags,
  number of announcements, body length, AIM or not, SETS or SETSqx, 5/20/60-day returns, 20-day
  volatility, turnover, 52-week high/low distance, announcements in the past 90 days, weekday.
  Buy above last year's top-quintile prediction.

## Costs (UK, Trading 212: no commission, no FX on GBP stocks)
Full spread by 20-day £ turnover (an estimate; UK small-cap spreads are wide and real fills can be
worse): ≥ £5m 0.4%, £1–5m 0.8%, £250k–1m 1.5%, £50k–250k 3.0%; half paid each side. Slippage 0.25%
each side. Stamp duty 0.5% on buys of Main Market (non-AIM) shares.

## Account and years
£120 per position (≈ $150), at most 40 open; best first within a day (S0: largest turnover, S1:
prediction); skipped when full. Fit 2012–2018, choose 2019–2021, final 2022–2026 once, frozen.
Pass = the account beats the FTSE All-Share (^FTAS, as the index fund) after costs in the final test.

## Known risks (⚠️)
- ⚠️ Survivorship is severe in the UK data: 27% of pre-open news belongs to delisted companies without
  prices, skewed to failures. Results are an upper bound.
- ⚠️ Yahoo's UK open is often an auction or mid price; the spread and slippage above are meant to cover
  the real fill, but may not for illiquid names.
- `headline_category` is a rough regex label (the LLM pilot would replace it).

## Choose-years result (2019–2021, run 2026-11-12): fails, no final test run
129,077 announcement days built; 52,912 priced (most others belong to delisted companies), 17,703
tradable 2012–2021. Fit years (seen before the run): +0.1% to +0.7% over 5 days for the best categories
(results, fundraise, dividend, director dealing), −1% to −4% after contract wins and "operational" news.
2019–2021: average round-trip cost of the trades 2.4–2.8% (spread table + slippage + stamp duty) against
gross excess of −8% to +2.5% per event. All 60 variants (11 categories + the model × 5 exits) lost to the
benchmark after costs; none positive in more than one year. Closest: the model's picks held 20 days
(+2.46% gross vs 2.76% cost, −£638 over three years on a £4,800 account). Final years not used.

## Addendum: the labelled test (written 2026-11-13, BEFORE any labelled result was looked at)
The user approved Luna labels for the 55,654 announcements of priced events (`uk-rns-full-v1-20261113T064938`,
£14.11). Same events, costs, account, years and benchmark as above. Labels are aggregated per event
(company-day): any "ahead of expectations", any "below", any "in line"; outlook raised / lowered /
withdrawn; profit improved / worse; news for shareholders (share positive, any negative); max
materiality; mean tone; any funding risk; director buy value and sell value (GBP k); fundraise discount
(max) and amount; contract value; the LLM categories as flags.
- **S1L model:** LightGBM (same fixed settings) on the baseline features **plus** the label features,
  predicting the H5 excess return; buy above last year's top-quintile prediction.
- **S2 "ahead":** every event saying results are ahead of market expectations, or outlook raised, with no
  funding risk (a simple rule, no fitting).
- Exits O1, H3, H5, H20, A5 as before. The baseline model (S1, no labels) is re-run beside them.
**Pass to the final test:** the account beats the benchmark after costs in total over 2019–2021 and in at
least 2 of the 3 years. If several pass, the user chooses one to freeze.
