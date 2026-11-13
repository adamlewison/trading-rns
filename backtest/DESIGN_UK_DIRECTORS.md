# Design: UK director buying (written 2026-11-13, BEFORE any result was looked at)

Fixed before running; later changes are added at the end with the reason. Testing rules in `CLAUDE.md`.

## The idea
Directors buying their own company's shares with their own money predicts above-market returns over
the following months (UK evidence: Fidrmuc, Goergen & Renneboog 2006; Gregory et al.). UK small caps are
less watched than US ones, and the US test failed mainly in liquid stocks. Holds are long, so costs
are paid rarely.

## Data
The Luna labels of pre-open announcements (`uk-rns-full-v1-20261113T064938`): an event is a company-day
whose announcements include a director/PDMR **purchase** (`director_dealing_side = buy`), with the
total value in £k where stated. ⚠️ Dealings announced during the trading day were not labelled, so they
are not in this test. Prices `prices_daily`; benchmark **^FTSE All-Share** (what the user would hold
otherwise; its price index plus 3.5%/yr for dividends, accrued per day).

## Rules
- Tradable: prior close ≥ 5p, 20-day average turnover ≥ £50,000, clean prices (as in the news test).
- Entry: that day's 08:00 open.
- **S0:** every director purchase event. **S1:** purchases worth ≥ £20,000 in total (real money at stake).
- Exits: **H20, H60** (close of day 20 / 60) and **T60** (trailing stop 3 × ATR20 below the highest close,
  checked on closes, sold at the next open, else day 60).
- No model: two fixed rules × three exits = 6 variants.

## Costs and account
UK cost table (full spread by £ turnover: ≥£5m 0.4%, £1–5m 0.8%, £250k–1m 1.5%, £50k–250k 3.0%; half each
side), slippage 0.25% each side, 0.5% stamp duty on Main Market buys. £120 per position, at most 40 open,
largest purchase first within a day, skipped when full.

## Years and pass rule
Choose 2019–2021 (2012–2018 seen only to check the data); final 2022 → latest once, frozen.
**Pass to the final test:** the account beats the FTSE All-Share (with dividends) after costs in total over
2019–2021 and in at least 2 of the 3 years. **Final pass:** the same, in total over 2022 → latest.

## Known risks
⚠️ Survivorship (27% of UK news from delisted companies without prices), worst for small caps.
Few events (a few hundred a year), so results are noisy.
