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

## Changes
- 2026-11-18 (before any result): some files write the meeting date day-first ("1/04/2025"); both formats are read.

## Choose-period result (2020–2023, run 2026-11-18): FAILED, every variant
77,576 races, 761,727 runners; 648,818 with a price at the scheduled off. Final years (2024 →) not run.

| Variant | Bets | Hit rate | Mean return per £, 5% commission (t) | Years positive | At 8% commission |
|---|---|---|---|---|---|
| A1 back favourites (mid ≤ 2.5) at win BSP | 18,845 | 50.3% | −2.8% (−3.9) | 0 of 4 | −4.3% |
| A2 lay longshots (mid 50–200) at win BSP, per £ of liability | 5,572 | 1.4% won | **+0.27% (1.7)** | 4 of 4 | +0.22% |
| B1 back place, Harville ≥ 0.5 and win mid ≥ 4 | 42,437 | 53.8% | −2.9% (−7.1) | 0 of 4 | −4.3% |
| B2 lay place, Harville ≤ 0.15 and win mid ≤ 15 | 80 | 31.3% placed | −9.6% (−1.4) | 1 of 4 | −10.3% |
| B4 back place where Harville ≥ naive + 0.10 | 14,810 | 45.7% | −1.9% (−2.5) | 0 of 4 | −3.5% |

- **A2 is the only positive one and it fails the pass rule** (t 1.7, 3 required): laying longshots earns
  about 0.27p per £1 risked, every year, which confirms a favourite–longshot bias exists at the exchange
  but at a size commission almost erases. On £10 of liability per bet, 5,572 bets over four years made about £150.
- **The place market is better than the model.** B3 diagnostic: place BSPs are almost exactly calibrated
  (implied 5.4% → 4.9% placed, 25% → 25%, 55% → 55%, 75% → 75%), while Harville overstates favourites'
  place chances and understates longshots'. Betting the model against the market loses.
- Untested here: anything needing second-by-second prices (in-running trading, making markets), which
  needs Betfair's paid historical data or a live recording through the API with the user's account.
