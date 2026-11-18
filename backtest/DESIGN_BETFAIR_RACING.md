# Design: Betfair Exchange racing, Australia and New Zealand (written 2026-11-18, BEFORE any result was looked at)

Fixed before running; later changes go under "Changes" with a date. How to run it: `backtest/RUNBOOK.md`.
Only the column layout and three rows of one 2024 file were seen before this was written.

## Why the exchange
Bookmakers restrict winning customers; the exchange does not (Betfair charges heavy winners a higher fee
instead, irrelevant at this size). An edge here could keep paying, which is what the user asked for.

## Data
Betfair's own free files (betfair-datascientists.github.io, "Data Listing"): Aus & NZ thoroughbreds, one
row per runner, 2020 → October 2026: Betfair Starting Price for win and place (`WIN_BSP`, `PLACE_BSP`),
best back and lay in the win market at the scheduled off, traded volumes, and results. No UK racing, no
tick data. Stored in `data/betfair/` (gitignored), copied to B2 `trading-rns/betfair/v1/`.

## What can be known when
A bet at BSP is placed **before** the off and filled at the price set at the off. So a signal may use
only what is visible just before the off: the win market's best back and lay at the scheduled off.
Signals never use `WIN_BSP` or `PLACE_BSP` of the same race; those are only fill prices.

## Ideas (5 variants in total: A1, A2, B1, B2, B4; nothing else tried)
**A. Favourite–longshot bias.** Betting markets overprice longshots. Signal: win mid price at the
scheduled off (`(best back + best lay) / 2`). Fill at `WIN_BSP`.
- **A1:** back every runner whose mid is ≤ 2.5. **A2:** lay every runner whose mid is 50–200 (lay at BSP).

**B. Place market against the win market.** The place market is thinner and may be priced less
carefully. From the win mids at the off, normalise implied win probabilities in each race (sum to 1)
and compute each runner's chance of finishing in the places with the Harville formula. Places paid: 2 if
5–7 runners took part, 3 if 8 or more; no bet with 4 or fewer (runners = those with a `WIN_BSP`).
The place price is not known before the off, so the rules use the model probability and the win mid only.
- **B1:** back the place for runners with model place probability ≥ 0.5 and win mid ≥ 4.0 (rated likely
  to place by the win market but not to win).
- **B2:** lay the place for runners with model place probability ≤ 0.15 and win mid ≤ 15.
- **B4:** back the place for every runner whose model place probability is at least 0.10 above the
  place probability implied by its win mid under a flat rule (2 places: 2.0 × win probability; 3 places:
  3.0 × win probability, capped at 0.95). This bets where Harville says the place chance is much higher
  than a naive "places × win chance".
- **B3 (diagnostic only, not a strategy):** calibration of `PLACE_BSP` against Harville probabilities from
  `WIN_BSP` of the same race, by bucket. It uses the same race's BSPs, so it describes the market, it
  cannot be traded, and it is never used to choose a variant.

## Costs and account
- Commission on each bet's winnings: **5%** (Betfair UK basic rate) and, as a check, **8%**. Losing bets pay none.
- Every bet £10 stake (back) or £10 liability (lay). Filled in full at BSP: runners with `WIN_BSP_VOLUME`
  under 1,000 (A) or `PLACE_BSP_VOLUME` under 500 (B) are skipped.
- ⚠️ Not checked yet: whether a UK Betfair account can bet on Australian racing, and at what commission.

## Years and pass rule
Choose: 2020–2023. Final: 2024 → October 2026, **once**, one variant frozen.
- **Pass to the final test:** ≥ 2,000 bets, mean return per £ staked (or per £ of liability) after 5%
  commission > 0 with t ≥ 3 clustered by meeting-day, positive in at least 3 of the 4 years, and still
  positive at 8% commission.
- **Final pass:** the frozen variant's mean return after 5% commission > 0 with t ≥ 2, positive in at
  least 2 of the 3 years.
- Report for every variant: bets, win rate, mean return, t, by year, by price band, with 5% and 8%.

## Known risks
- ⚠️ Australian markets, not British: tradability and commission for a UK customer must be checked.
- ⚠️ Price at the scheduled off is not the price at the real off (often a minute later); BSP is used to fill,
  so this only affects the signal, not the fill.
- Favourite–longshot bias at the exchange is well documented and small; commission may remove it.
- Trap: using `WIN_BSP`, `PLACE_BSP` or results to choose bets. Trap: counting places from results.
