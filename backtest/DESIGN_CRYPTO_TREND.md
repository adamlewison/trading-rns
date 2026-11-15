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

## Step 0 result (2026-11-15)
BTC-USD 2014-09-17 → 2026-11-15 and ETH-USD 2017-11-09 → 2026-11-15 from yfinance, no missing days.
Cross-check against Coinbase's daily candles on 20 random dates per coin: largest difference 1.7% (BTC),
0.6% (ETH); none above 2%.

## Choose-period result (2018-06-01 → 2021-12-31, run 2026-11-15): three variants PASS to the final test
$6,000 start, costs 0.45% each way. Final period (2022 →) not looked at.

| Variant | Total return | Sharpe | Worst fall | 2018 (from June) | 2019 | 2020 | 2021 | Avg invested | Trading days | Costs | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| M100-V | +446% | 1.60 | −26% | −7% | +59% | +133% | +58% | 35% | 107 | $1,971 | pass |
| M100-F | +906% | 1.40 | −47% | −10% | +81% | +167% | +131% | 59% | 81 | $5,297 | pass |
| R28-V | +357% | 1.48 | −37% | −24% | +21% | +185% | +73% | 34% | 219 | $3,008 | pass |
| R28-F | +911% | 1.43 | −55% | −31% | +4% | +394% | +178% | 58% | 198 | $8,498 | fail (worst fall beyond −50%) |
| Hold 50/50 BTC/ETH | +650% | 1.12 | −76% | −65% | +35% | +397% | +206% | 100% | | $252 | benchmark |
| SPY | +85% | 0.92 | −34% | −7% | +31% | +18% | +29% | 100% | | | benchmark |

BTC only (ETH's half in cash): M100-V +140%, Sharpe 1.44, worst fall −12%; holding 50% BTC +215%,
Sharpe 1.06, worst fall −37%.

**Checks**
- Too-good rule (an account more than doubling in a year): explained by the asset, not the rule.
  Holding the coins made +397% in 2020; the benchmark's yearly numbers match the known moves of BTC and
  ETH; the unit tests confirm trades happen one day after the signal and that changing later prices
  does not change earlier signals.
- Placebo (the same signals moved to a random other time, 200 draws): Sharpe averages 0.5–0.7 against
  1.4–1.6 for the real signals; 0.5–2% of placebo runs match the real Sharpe. The timing adds something
  in this period beyond holding crypto part of the time.
- Where it comes from (M100-V vs holding): the rule sat out the second half of 2018 (−7% vs −65%) and
  halved every drawdown; in 2020 and 2021 it made far **less** than holding (+133% vs +397%, +58% vs +206%).
  Over 2019–2021 alone the Sharpe ratios are close (1.82 vs 1.68).

**What this does and does not show.** ⚠️ The period is one crash and one bull market, so two episodes
decide it (lesson 11: the leveraged equity trend rule passed 28 years on the same logic and then failed).
Beating SPY here comes from owning crypto in 2019–2021, not from the rule; the rule's own contribution
is lower risk. The final period (2022 →) contains a crash and a recovery and is the real test.

**Proposed freeze:** M100-V (highest Sharpe, shallowest fall, beat SPY in all three years, fewest
assumptions). The final test runs once, after the user agrees: `backtest.crypto_trend final --final`
prints all variants; only the frozen one counts.

## FROZEN for the final test (2026-11-16, user agreed)
**M100-V:** each of BTC and ETH is held only while its close is above its 100-day average, at a weight
of min(50%, 50% × 40% ÷ 30-day volatility), traded one day after the signal, 0.45% cost each way.
Run ONCE on 2022-01-01 → latest with `backtest.crypto_trend final --final`. Pass = beats SPY in total
return, worst fall no deeper than 50%, and a higher Sharpe ratio than holding 50/50 BTC/ETH. The other
variants are printed for information only.

## FINAL TEST RESULT (2022-01-01 → 2026-11-15, run once, 2026-11-16): FAILS
| | Total return | Sharpe | Worst fall | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|
| **M100-V (frozen)** | **+22%** | 0.29 | −36% | −32% | +40% | +24% | +11% | −9% |
| SPY | +69% | 0.71 | −25% | −18% | +26% | +25% | +18% | +12% |
| Hold 50/50 BTC/ETH | −13% | 0.24 | −68% | −66% | +121% | +74% | −6% | −33% |

M100-V made +22% against +69% for SPY, so it fails the pass rule (beat SPY). It did what a trend rule
does: it lost half as much as holding the coins and ended ahead of them, but crypto itself went nowhere
over these five years, and 168 trading days cost $1,887 on a $6,000 account. For information: the other
variants made +19% to +26%, all below SPY; BTC alone +35% (M100-V) against +38% for holding 50% BTC.
The choose-period pass came from owning crypto in a boom, as warned. The final years are now spent.
