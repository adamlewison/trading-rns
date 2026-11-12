# Design: insider buying, US (written 2026-11-12, BEFORE any result was looked at)

Fixed before running. Every change after a result is seen is added at the end as a change, with
the reason. Testing rules and the account are those in `CLAUDE.md` (sections 3 and 8).

## The idea and the evidence
Company insiders buying their own shares with their own money, at market prices, predicts
above-market returns over the following months (Lakonishok & Lee 2001; Jeng, Metrick & Zeckhauser
2003). The effect is strongest for several insiders buying at once, for CEOs/CFOs, for purchases
that break the insider's usual pattern ("opportunistic", Cohen, Malloy & Pomorski 2012), and in
small companies. Insider sales carry much less information (many reasons to sell) and are not used.

## Data
- `us_insider_tx` (SEC Form 4 data sets). Prices: `us_prices_daily` (2012 on) and `us_splits`.
- **Timing:** only the filing DATE is known, so the earliest trade is the **open of the next trading
  day after the filing date** (as if every filing came after the close).

## Which purchases count (all must hold)
1. Form 4 or 4/A, transaction code **P** (purchase), acquired (A), non-derivative common stock
   (security title containing "common" or "ordinary" or "class a"), shares > 0, price > 0.
2. Bought by an **officer or director** (not a 10%-owner fund alone).
3. Not flagged as a pre-planned 10b5-1 trade (`aff10b5one`, only reported from 2023).
4. **At the market, not a private placement:** price within ±10% of the stock's close on the
   transaction date, and the company filed no 8-K with item 3.02 (unregistered sale of equity) in
   the 10 days before the filing.
5. Filed within 10 calendar days of the transaction (stale reports carry old news).
6. The issuer maps to a priced ticker (by CIK through the SEC ticker map, else the filing's symbol).

## Events
One event per company per filing date: all qualifying purchases filed that day. Features (all
known by the trade): total dollars bought; number of distinct insiders buying in the last 30 days
(including today); CEO/CFO/Chair among them (title text); dollars bought / dollar value of the
buyer's holding after (size of the bet); the buyer bought in the same calendar month in each of the
past 3 years ("routine", Cohen et al.); price context (5/20/60-day return, 20-day volatility, dollar
volume), size (market cap, as in `features/us_earnings_labels.py`), 52-week high/low distance,
book-to-market and earnings yield from filings before the trade.

## Universes (both reported)
- **U1 tradable small:** prior close ≥ $1 and 20-day dollar volume ≥ $2m.
- **U2 liquid:** 20-day dollar volume ≥ $20m.

## Selections (all reported, no tuning)
- **S0 every event** in the universe.
- **S1 cluster:** 2+ distinct insiders bought in the last 30 days, or a CEO/CFO bought ≥ $50,000.
- **S2 model:** LightGBM (same fixed settings as the earnings test) predicting the 60-day excess
  return from the features; buy above the previous year's top-quintile prediction.

## Exits (fixed)
- **H20, H60:** hold 20 / 60 trading days from the entry open, sell at the close.
- **T60:** up to 60 days with a trailing stop at 3× the 20-day average true range below the highest
  close (exit idea B), checked on closes, sold at the next open.

## Costs and account
Trading 212 (primary) and IBKR, as in `CLAUDE.md`. Spread table extended for smaller stocks
(estimate): ≥$500m/day 0.05%, $100–500m 0.10%, $20–100m 0.20%, $5–20m 0.40%, $2–5m 0.80% (full).
The $6,000 account: $150 per position, at most 40 open, best first within a day (S2 by prediction,
S0/S1 by dollars bought), skipped when full. Benchmark: SPY if market cap ≥ $10bn, else IWM.

## Years
Fit 2012–2018; choose 2019–2021 (walk-forward as before); final test 2022–2026 **once**, frozen.
Report per year: events, trades taken, gross excess, net, the account's $ vs the market, and the
universe's average (to separate the pick from general drift).

## Known risks
- ⚠️ Survivorship: small companies that later failed have no prices, and insider buying is
  concentrated in small caps. Results are flattered; report the share of events with no price.
- Many events cluster in sell-offs (insiders buy dips): March 2020 will weigh heavily.
