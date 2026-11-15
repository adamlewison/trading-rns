# Design: UK investment trust discounts (written 2026-11-15, BEFORE any data was looked at)

Fixed before running; later changes go under "Changes" with a date. How to run it: `backtest/RUNBOOK.md`.

## The idea
An investment trust is a listed company that owns a portfolio of shares. Its share price often trades
below the value of that portfolio per share (the net asset value, NAV): a discount. Discounts move with
sentiment and tend to return towards each trust's own normal level (Pontiff 1995 for US funds; Cheng,
Copeland and O'Hanlon 1994 for UK trusts). Buying a trust whose discount is unusually wide **for that
trust** means buying an index-like portfolio cheaply, with the narrowing as extra return.

This is the slowest idea on the list (holds of months) and does not match the user's wish for short
trades. It is here because it is the one with the clearest reason to beat an index fund.

## Data (this is most of the work)
- **Universe:** closed-ended investment companies on the LSE Main Market from `lse_instruments`
  (find them by `fca_category` / `sector_code`; the current research universe excludes them). Keep
  trusts that invest in **listed equities** and publish a NAV at least weekly. Exclude VCTs, property,
  infrastructure, renewables, private equity, debt and hedge-fund trusts: their NAVs are quarterly
  estimates, so their "discount" is not comparable. Build the list by AIC sector name where available,
  otherwise by NAV frequency (below).
- **Prices:** Yahoo daily via the existing `ingest/uk/prices.py` path. Same stale and suspect flags.
- **NAV:** RNS announcements headed `Net Asset Value(s)`. Check first whether `ann_index` already holds
  them for trust tickers (they were treated as admin notices, so bodies were not kept). For each trust
  and each month, fetch the body of the **last** NAV announcement of the month (about 300 trusts × 12 ×
  14 years ≈ 50,000 pages, about 7 hours at 2 requests a second). Do not fetch every daily NAV.
- **Parsing:** NAV per share in pence, **cum income**, with debt at fair value where both are given,
  and the date the NAV refers to. Regex first. A Luna run for the remainder needs a cost estimate; over
  £5 needs the user's go-ahead.
- Delisted trusts: `ingest.uk` daily-mode index finds their announcements; most will have no prices.

## Step 0: data checks (no outcomes)
Report: trusts in the universe by sector; trusts with prices; NAV announcements found per year; share of
month-ends with a parsed NAV per trust. Check the parser: discount = price ÷ NAV − 1 on the NAV date
must lie between −60% and +30% for at least 98% of points (a 100× error means pence versus pounds; fix
the parser). Compare 20 current discounts with the AIC website by hand. If fewer than 80 equity trusts
have at least 5 years of monthly discounts, report and stop.

## Rules
- **Monthly, on the first trading day of each month.** Signals use the last NAV of the previous month and
  the closing price on that NAV's date. Trades are made at the first trading day's open.
- **Discount** d = price ÷ NAV − 1 (negative = discount), one point per trust per month.
- **z** = (d − mean of the trust's previous 12 monthly d) ÷ their standard deviation; needs all 12, and a
  standard deviation of at least 1 percentage point. The current month is not in the mean.
- **Tradable:** price ≥ 20p, 20-day average turnover ≥ £100,000, clean price on the trade day.
- **Variants (3, nothing else tried):**
  - **Z:** buy trusts with z ≤ −1.5, most negative first, up to 5 holdings; sell when z ≥ 0, or after 6
    monthly checks, whichever comes first.
  - **Z-catalyst:** as Z, but only trusts whose discount is also wider than −8% (room to narrow).
  - **W (naive baseline):** hold the 5 widest discounts in the universe, rebalanced monthly.
- No model.

## Costs and account
0.5% stamp duty on buys; half the spread each side from the UK cost table; 0.25% slippage each side.
£5,000 account, £1,000 per holding, at most 5 holdings; idle cash sits in the FTSE All-Share fund.
Returns use `adj_close` (dividends reinvested) from entry to exit.

## Benchmark, years and pass rule
Benchmark: a FTSE All-Share fund with dividends (as in `backtest/uk_directors.py`). Also report against a
global tracker (SPY converted to pounds) because most equity trusts hold global shares (lesson 13).
Choose: 2015–2021 (2013–2014 only build the 12-month history). Final: 2022 → latest, once, one variant frozen.
- **Pass to the final test:** variant Z or Z-catalyst beats the FTSE All-Share after costs over 2015–2021
  and in at least 4 of the 7 years, beats variant W (otherwise the z-score adds nothing), and has at
  least 60 completed holdings.
- **Final pass:** the frozen variant beats the FTSE All-Share after costs over 2022 → latest.
- Report the **discount capture** as the diagnostic: for each holding, price return minus the trust's
  own NAV return. If that is not positive on average, any outperformance came from what the trusts
  held, not from the discount, and the verdict is **failed** whatever the account shows.

## Known risks and traps
- ⚠️ Survivorship: trusts that were wound up or merged usually paid out close to NAV, which would help
  this strategy, so missing them probably understates it; a few failed outright, which flatters it.
  Report how many trusts with NAV announcements have no prices.
- ⚠️ 2022–2023 saw discounts widen and stay wide for two years. That is the final period: do not look.
- Trap: using a NAV announced after the trade date. The NAV must be published before the first trading
  day of the month; use the announcement's timestamp, not the NAV's own date.
- Trap: mixing cum-income and ex-income NAVs, or par and fair-value debt, within one trust's history.
  Pick one basis per trust and keep it; if the basis changes, restart that trust's 12-month history.
- Trap: comparing an adjusted price with NAV. The discount uses the unadjusted close in pence.
- Trap: a full-sample mean or standard deviation in z. Only the previous 12 months.
- Trap: trusts with share classes or subscription shares (several tickers per trust). One line per trust:
  the ordinary shares.
