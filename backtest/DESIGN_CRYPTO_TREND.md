# Design: crypto trend-following on Bitcoin and Ether (written 2026-11-15, BEFORE any data was looked at)

Fixed before running; later changes go under "Changes" with a date. How to run it: `backtest/RUNBOOK.md`.

## The idea
Bitcoin and Ether move in long trends and deep crashes. Holding them only while the trend is up, sized
so that a volatile coin gets less money, has been reported to keep much of the upside and avoid much of
the crashes (time-series momentum: Moskowitz, Ooi and Pedersen 2012; for crypto, Liu and Tsyvinski 2021).
Trading is around the clock, costs are moderate, and signals change every few weeks.

**Prior: low.** Most of the return is simply from owning crypto in a rising market; trend rules lagged
buy-and-hold in equities (lesson 11); the effect has weakened as the market matured. It is tested
because it is cheap to test and matches the user's wish for shorter trades. UK residents can hold spot
crypto on FCA-registered exchanges; crypto derivatives are banned for UK retail, so no shorting or leverage.

## Data
Daily closes (00:00 UTC) for BTC-USD and ETH-USD from `yfinance` (`Close`; BTC from 2014, ETH from late
2017), plus SPY `Adj Close`. **Only these two coins:** picking today's other large coins would be choosing
winners with hindsight. Cross-check 20 random dates per coin against another public source (for example
Kraken's or Coinbase's public candle API); differences above 2% on more than one date: stop and report.

## Step 0: data checks (no outcomes)
Report first and last date per coin, missing days (there should be none: crypto trades every day),
and the cross-check. Do not plot or summarise returns by year yet.

## Rules
- Signals are computed at each daily close and traded **at the next day's close** (one full day later,
  which is conservative and easy to do by hand).
- **Trend signal, per coin (2 variants):** **M100**: in when the close is above its 100-day simple
  average; **R28**: in when the 28-day return is above zero. Otherwise out (cash, earning nothing).
- **Size, per coin (2 variants):** **V**: weight = min(50%, 50% × 40% ÷ realised volatility), where
  realised volatility is the standard deviation of the last 30 daily returns × √365; **F**: a flat 50%
  when in. Weights never sum above 100%: no leverage.
- Four variants (M100-V, M100-F, R28-V, R28-F). Nothing else is tried: no parameter search.
- Rebalance a coin only when its signal flips or its target weight differs from the current weight
  by more than 10 percentage points (keeps trading costs down).
- ETH has no signal until it has 100 days of history; until then its 50% stays in cash.

## Costs
0.45% of the traded amount each way (exchange taker fee of about 0.40% at the smallest size on an
FCA-registered exchange, plus spread). Check the current fee table of the exchange the user would use
and write it in the report; if it is higher, re-run with the real number and report both.
⚠️ Not modelled: UK capital gains tax, due on each sale above the annual allowance.

## Benchmarks, years and pass rule
Two benchmarks: **SPY with dividends** (the project's) and **buy-and-hold 50% BTC / 50% ETH**, rebalanced
monthly (shows whether the timing adds anything beyond owning crypto).
Choose: 2018-06-01 → 2021-12-31 (the first date both coins have 100 days of history plus a margin).
Final: 2022-01-01 → latest, once, one variant frozen.
- **Pass to the final test:** a variant beats SPY in total return, **and** has a higher Sharpe ratio than
  the buy-and-hold crypto benchmark (daily returns, √365), **and** its worst fall from a peak is no
  deeper than 50%, **and** it is not only one year: it beats SPY in at least 2 of the calendar years
  2019, 2020, 2021.
- **Final pass:** the frozen variant beats SPY in total return over 2022 → latest with a worst fall no
  deeper than 50%, and has a higher Sharpe ratio than buy-and-hold crypto.
- Beating SPY in 2018–2021 by holding crypto proves nothing on its own; the comparison with buy-and-hold
  crypto is the real test of the rule.

## Report (besides the runbook's table)
Per variant and per benchmark: total return, yearly returns, Sharpe, worst fall, share of days in the
market, number of trades, costs paid. Then the same with BTC only (ETH's short history is a risk).

## Known risks and traps
- ⚠️ The choose period is one bull market and one crash. Two episodes decide the result (lesson 11).
- ⚠️ Yahoo's crypto close is an average across exchanges, not a price the user could trade.
- ⚠️ Exchange risk (failure, frozen withdrawals) is real and not in any backtest.
- Trap: trading at the same close that produced the signal. The trade is one day later.
- Trap: annualising with 252 days. Crypto has 365.
- Trap: computing volatility or the moving average with the current day's close included in a way that
  also uses it as the trade price (same error as above).
- Trap: trying other averages (50, 150, 200 days) "to check robustness" and then reporting the best.
  Any extra run is a variant, is reported, and counts against the result.
