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

## Changes and state (2026-11-16, before any NAV or outcome was looked at)
- **Universe:** the 288 tickers LSE lists under "Closed-ended investment funds" (current list only, so
  trusts that have since wound up are missing). The split into equity and other trusts is made at step 0
  by NAV frequency: keep trusts with at least 40 NAV announcements a year (weekly or daily).
- **Prices:** Yahoo's UK `close` is adjusted for later share splits and consolidations (found in the
  retail-offers test). The discount needs the actual price on the NAV date: fetch prices with their
  split history (`yfinance` actions) and undo later splits, as for US stocks. Add a unit test with a split.
- **Data pull running on the droplet** (unit `trust-data`, log `/opt/trading-rns-pm/data/logs/trust_data.out`,
  restartable): first the announcement index of the 288 trusts (`index --mode company`), then the text of
  the last "Net Asset Value" announcement of each trust-month (`bodies --headline-re "^net asset value"
  --month-last`). Expect roughly 4 + 7 hours at Investegate's 2 requests a second. Before this, the index
  held NAV announcements for only about 50 tickers.
- Next, in a cloud session once the unit has finished and the UK backup has reached B2: pull the UK data,
  parse the NAVs (regex first), fetch trust prices with splits, then step 0 as written above.

## Step 0 result (2026-11-16): data is usable; no strategy result has been looked at
- **Pull:** the droplet indexed the 288 listed trusts (plus those already held: 330 tickers with NAV
  announcements, 503,173 of them) and fetched the last NAV announcement of each trust-month: 32,530 texts.
- **Change to the plan: Luna reads the NAVs, not regex.** The texts come in dozens of layouts (tables,
  several trusts in one announcement, four bases in one sentence), so regex was dropped before it was
  written. `classify/uk_nav.py`: £3.35 for all 32,530 (estimate £3.77, cap £5). The model returned a NAV
  for the named trust in 84% of texts; units: pence 22,232, pounds 993, other currencies 3,810, not stated 5,495.
- **One basis per trust** (`features/uk_trusts.py`): the first of cum-income/fair, cum-income/par,
  cum-income, unstated, then ex-income bases that appears in at least 80% of that trust's announcements.
  Used: unstated 103 trusts, cum income (debt not mentioned) 92, cum income fair 43, cum income par 10, ex income 10.
- **Prices:** Yahoo, with later splits undone, for 277 of 330 tickers. ⚠️ 53 have none: mostly trusts
  that merged or wound up (survivorship; direction discussed above).
- **Counts:** 160 trusts publish at least 40 NAVs a year; 139 of them have prices: 16,176 trust-months.
  Monthly discounts per year rise from 666 (2012) to about 1,400 (2024–2026).
  **113 trusts have at least 60 monthly discounts** (80 required).
- **Parser check:** 98.05% of discounts lie between −60% and +30% (98% required). The rest are unit
  errors concentrated in five trusts (LTI quotes its NAV in pounds per share against a price in pence;
  JEMA, CGI, SST, MRC): points outside the range are dropped, not corrected.
- **Sanity:** the latest discounts match what is publicly known for well-known trusts (Hansa about −40%,
  City of London and New City High Yield at small premiums). The comparison with 20 AIC figures by hand was not done.
- ⚠️ The report also printed the median discount per year, including 2022–2026 (between −6.5% and −9.6%).
  That is the level of discounts in the final period, not a strategy outcome, but it was seen.
- ⚠️ The universe is filtered by NAV frequency only; no sector data, so a few frequent-NAV debt funds remain.
- **Next:** write `backtest/uk_trust_discounts.py` to the Rules above (monthly, z-score on the previous
  12 months, variants Z, Z-catalyst, W), unit tests, then the choose period 2015–2021 only.

## Choose-period result (2015–2021, run 2026-11-16): Z-catalyst PASSES to the final test
£5,000 account, £1,000 a holding, idle money in the FTSE All-Share (with dividends), all costs. 99 trusts
had a z-score in the period; 12% of trust-months had z ≤ −1.5. Final period (2022 →) not run.

| Variant | Account at end 2021 | Index | Years beating the index | Completed holdings | Per holding vs index (t) | Discount capture | Verdict |
|---|---|---|---|---|---|---|---|
| Z | £8,158 | £7,361 | 3 of 7 | 84 | +0.8% (0.9) | +3.3% | fails (4 years needed) |
| **Z-catalyst** | **£8,764** | £7,361 | **5 of 7** | 66 | +1.7% (1.4) | +3.2% | **passes** |
| W (widest five, baseline) | £7,346 | £7,361 | 4 of 7 | 182 | 0.0% (0.1) | +2.6% | baseline |

Z-catalyst by year, account minus index: 2015 −5.6, 2016 +6.2, 2017 +1.2, 2018 +1.6, 2019 +3.2,
2020 +12.6, 2021 −1.1 points. It meets every part of the pass rule: beats the index over the period
and in 5 of 7 years, beats W, 66 completed holdings (60 required), and the discount capture is positive.

**Checks**
- Placebo (the same z-scores given to random trusts each month, 30 runs): Z-catalyst ends at £7,110 on
  average (5–95%: £5,653–£8,313); none of the 30 reaches the real £8,764. Discount capture in the placebo
  is +0.9% against +3.2% for the real signal. Same picture for Z (0 of 30). The signal does pick trusts
  whose discounts then narrow.
- The mechanism is visible: average discount −13.9% at entry, −11.3% at exit, about four months held.
- Without 2020: strategy +66%, index +59% over the other six years. Still ahead, by less (lesson 14).
- Too-good rule: not triggered. Ten best and worst holdings printed and read; dates and discounts consistent.

**What weakens it**
- ⚠️ The per-holding edge is not statistically firm: +1.7% over the index, t 1.4, and −0.2% without the
  five best holdings. The account result rests on a minority of large narrowings.
- ⚠️ Only 66 holdings in 38 trusts, uneven by year (4 in 2017, 2 in 2021).
- ⚠️ Costs are estimates (stamp duty 0.5%, spread by turnover, 0.25% slippage each side): about 2% a
  round trip against a 3.2% capture, so the margin is thin.
- ⚠️ Universe is today's list of trusts, 53 without prices; frequent-NAV debt funds are not removed.
- 2022–2023 saw discounts widen for two years; a rule that buys widening discounts was exposed to that.

**Proposed freeze:** Z-catalyst exactly as written (z ≤ −1.5 on the previous 12 months, discount wider
than −8%, up to 5 holdings, sell at z ≥ 0 or after 6 monthly checks). Final test: 2022-01 → latest,
once, after the user agrees: `backtest.uk_trust_discounts final --final`.

## FROZEN for the final test (2026-11-16, user agreed)
**Z-catalyst:** on the first trading day of each month, buy trusts whose last discount of the previous
month is at least 1.5 standard deviations wider than their previous 12 monthly discounts and wider than
−8%, most extreme first, up to 5 holdings of £1,000; sell when z ≥ 0 or after 6 monthly checks; idle
money in the FTSE All-Share with dividends; costs as above. Run ONCE on 2022-01 → latest with
`backtest.uk_trust_discounts final --final`. **Pass:** the account ends above the all-index account,
and the discount capture per holding is positive. Z and W are printed for information only.

## FINAL TEST RESULT (2022-01 → 2026-11, run once, 2026-11-16): FAILS
| | Account (£5,000 start) | 2022 | 2023 | 2024 | 2025 | 2026 | Completed holdings | Per holding vs index (t) | Discount capture |
|---|---|---|---|---|---|---|---|---|---|
| **Z-catalyst (frozen)** | **£5,825** | −14.2% | −9.5% | −2.6% | +21.9% | +26.4% | 50 | −2.4% (−1.3) | +2.5% |
| FTSE All-Share | £8,019 | −0.1% | +2.3% | +14.7% | +19.3% | +14.7% | | | |
| Z (information) | £6,108 | −4.5% | −10.4% | +4.5% | +18.0% | +15.9% | 60 | −1.8% (−1.4) | +2.0% |
| W (information) | £6,892 | −20.3% | +11.0% | +12.2% | +30.2% | +6.7% | 119 | −0.7% (−0.5) | +2.9% |

The account made +16.5% against +60% for the index and beat it in 2 of 5 years. The discount signal
still worked: discounts narrowed by 2.5% per holding on average (−14.2% at entry, −12.2% at exit), close
to the choose period's 3.2%. What failed was what the trusts hold: their assets (smaller companies,
growth, emerging markets, alternatives) lagged the FTSE All-Share badly in 2022–2024, and a 2.5%
narrowing less about 2% of costs cannot offset that. Only 38% of holdings beat the index.

So the edge from the discount is real but small, and choosing trusts by their discount means taking
whatever asset exposure comes with them, which swamps it. Capturing the narrowing alone would need
hedging each trust against its own holdings, which a small long-only account cannot do. The final
years are now spent.
