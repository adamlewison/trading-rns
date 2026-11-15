# Design: prediction-market near-certain contracts (written 2026-11-15, BEFORE any outcome was looked at)

Fixed before running; later changes are added at the end with the reason. Testing rules in `CLAUDE.md`.
Only the shape of the data (fields, endpoints, speed) was inspected before this was written.

## The idea
A contract that pays $1 if an event happens and trades at 95–99c with days to go offers 1–5% in days.
Research on betting and prediction markets finds a favourite–longshot bias: longshots are overpriced, so
favourites are slightly underpriced. If that holds after the spread and fees, many small independent
bets on near-certain contracts compound quickly, which is what the user asked for. A wrong bet loses
its whole stake, so the test is whether the wins outweigh the rare total losses.

## ⚠️ Access (checked 2026-11-15, the user is in the UK)
Kalshi lists the UK as restricted and Polymarket blocks UK addresses; neither holds a UK Gambling
Commission licence. **The user cannot trade either today, and we never work around a block.** This test
therefore answers "does the effect exist where free history is good?". If it does, the next step is a
UK-legal venue (Betfair, Smarkets, Matchbook), which needs its own data and test: Betfair's free price
files returned 403 to this session and Smarkets' public API lists no settled markets, so that data would
come from the user's own Betfair account download.

## Data
Kalshi's public market-data API (no account, no key; polite rate, stop on 403/429).
- All series, then every settled binary market per series from both the live and the historical tier.
  Multi-leg combination markets (`KXMVE…`) are excluded: they are parlays, not single events.
- Hourly candles (yes bid and ask) for the last 8 days before each market's scheduled end, for markets
  that pass the size filter below. If more than 150,000 markets pass, a fixed random sample of 150,000
  is used (hash of the ticker), and that is reported.
- Stored under `data/pm/kalshi/` and copied to B2 `trading-rns/pm/v1/`.

## Rules
- **Scheduled end** = `expected_expiration_time` (else `latest_expiration_time`), never the actual close:
  markets that close early when the event happens would otherwise leak the outcome into the entry time.
  ⚠️ Assumes Kalshi does not rewrite these fields after an early close; checked on the data and reported.
- **Eligible market:** binary, settled yes or no (voided or "fair price" settlements are counted and
  reported, and treated as returning the settlement value), lifetime volume ≥ 1,000 contracts.
- **Signal:** the first hourly candle, inside the entry window, where the ask of either side closes
  inside the price band. Yes ask as quoted; no ask = 1 − yes bid.
- **Fill:** the ask at the open of the next hourly candle, only if it is still below 1.00 and not more
  than 1c above the signal price; otherwise no trade. One entry per market, held to settlement.
- **One bet per event:** markets sharing an `event_ticker` are the same bet (strikes on one number), so
  only the first to signal is taken. Statistics are clustered by event, and also reported clustered
  by series-and-day as the stricter check.
- **Variants (4, nothing else tried):** band **B95** ask 0.95–0.98 inclusive or **B90** ask 0.90–0.94;
  window **W2** scheduled end is 6 hours to 2 days away, or **W7** 6 hours to 7 days away.
- No model. Reported for information only, not chosen on: results by Kalshi category, by year, by
  days to end, and the same bets entered on the longshot side (the mirror image, as a check on the data).

## Costs and account
- Kalshi taker fee per order: 0.07 × contracts × price × (1 − price), rounded up to the cent; the series'
  own fee multiplier is used where the API gives one.
- The fill is at the ask, so the spread is paid in full. No depth history exists, so fills are assumed
  for the stake below; that is why low-volume markets are excluded. ⚠️ Could flatter thin markets.
- Account: $6,000; each bet stakes **2% of current equity** (so gains compound); at most one bet per
  event, at most 10% of equity in one series on one day, at most 50 bets open, first signal first.
  Idle cash earns nothing. Compared with $6,000 in SPY (with dividends) over the same dates.

## Years and pass rule
Choose: markets with a scheduled end up to 2025-12-31. Final: 2026-01-01 → latest, **once**, one variant frozen.
- **Pass to the final test:** a variant has ≥ 300 events, mean return per bet after fees > 0 with
  t ≥ 3 (clustered by event), positive in both halves of the period, and its account beats SPY.
- **Final pass:** the frozen variant's mean return per bet after fees > 0 with t ≥ 2, and its account beats SPY.
- Whatever happens, report: number of bets, share won, mean win, number of total losses, return per bet,
  average days held, account path, worst drawdown, and every variant.

## Known risks
- ⚠️ Not tradable from the UK today (above). A pass here is evidence, not a strategy.
- ⚠️ A market at 97c can already be decided and waiting for settlement; those are real trades but
  the ask may show almost no size. Reported separately where the last trade before entry is ≥ 0.99.
- ⚠️ Bets on the same day in related series (all crypto strikes, all games of one night) are not
  independent; the series-and-day clustering is the guard.
- Kalshi's volume before 2024 is thin; most choose-period bets will come from 2024–2025.
- An LLM screen of the losers is **not** part of this test (rule 6: baseline first), and it could only be
  judged on markets after the model's training cutoff.
