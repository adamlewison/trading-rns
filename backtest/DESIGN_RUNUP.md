# Design: run-up before scheduled earnings, US (written 2026-11-12, BEFORE any result was looked at)

Fixed before running; later changes are added at the end with the reason. Testing rules and the
account are in `CLAUDE.md` (sections 3 and 8).

## The idea and the evidence
Stocks tend to rise in the days before a scheduled earnings release and earn a premium around it
(Frazzini & Lamont 2007; Barber, De George, Lehavy & Trueman 2013), and firms whose past
announcements earned more tend to do so again (Aboody, Lehavy & Trueman 2008). Few trades, no LLM.

## Expected release date (point in time)
Companies report on a near-fixed calendar. For every earnings release X (8-K item 2.02, timestamp from
`us_filing_docs`), the same quarter next year is expected 364 days later (same weekday). The
expected **reaction day R** = the trading day the market first sees it (the next trading day for
after-close releases, the same day for pre-market ones). Only releases already filed are used.
Reported: how often the actual release fell within ±3 trading days of R.

## Windows (fixed)
- **W1 run-up only:** buy at the close 6 trading days before R, sell at the close the day before R.
- **W2 through the release:** the same buy, sell at the close of R+1.
If the actual release comes early, inside the window, the trade still holds as planned (realistic).

## Universe and selection
- **U1:** prior close ≥ $5 and 20-day dollar volume ≥ $5m. **U2:** dollar volume ≥ $20m.
- **S0** every event. **S1** LightGBM (fixed settings) predicting the W2 excess return from: the
  company's average excess return in its last 4 W1 and W2 windows (only finished ones), how many there
  were, 20/60-day returns and 20-day volatility before entry, dollar volume, market cap, month.
  Buy above last year's top-quintile prediction.

## Costs, account, years
Trading 212 (primary) and IBKR; spread table by dollar volume (as in the insider test). $150 per
position, at most 40 open, best first within a day (S0: the company's past W2 average; S1: the
prediction), skipped when full. Benchmark SPY if market cap ≥ $10bn else IWM. Fit 2013–2018, choose
2019–2021, final 2022–2026 once with the setup frozen.

## Known risks
The edge is small (tenths of a percent), so costs decide it. Survivorship (companies that later
failed have no prices) is milder here (liquid stocks, one-week holds) but still flatters.
