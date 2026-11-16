# Design: passive orders in wide-spread AIM shares (written 2026-11-16, BEFORE any quote data was seen)

Fixed before running; later changes go under "Changes" with a date. How to run it: `backtest/RUNBOOK.md`.
There is no order-book history, so this test is live observation first, then paper, then tiny size.

## The idea
In a thinly traded share the gap between the best bid and the best offer is 2–3% or more. Whoever
crosses it pays; whoever has an order resting in the book when they arrive is paid. Our UK tests failed
because we paid that gap on every trade (2.4–2.8% a round trip). This test asks whether a small account
can sit on the other side: rest a buy order at the best bid, and once it fills, a sell order at the
best offer, staying out on days with company news.

Why it could work: AIM shares pay no stamp duty; an order of a few hundred pounds is a normal size in
these books; professionals concentrate on larger names; our announcement feed can pull orders on news days.
Why it might not: the people who hit a resting bid often know something (the share then keeps falling);
fills may be rare; a filled position can take days to sell; commissions have a minimum per order.

## Universe (counted from our data on 2026-11-16, no prices used beyond turnover)
AIM shares on **SETS**, the continuous order book, are the only ones where a private order can rest
between market makers' quotes all day: **99** current tickers, of which 37 trade £20k–250k a day, 27
£250k–1m and 21 above £1m. The 503 AIM shares on SETSqx (market makers plus four daily auctions) are out.
Working universe: the 64 AIM/SETS shares with £20k–£1m daily turnover (from `uk_trading_service`,
`universe`, `prices_daily`), re-counted at the start of each phase.

## Phase 0: the user checks with IBKR (any "no" stops the track)
1. The monthly fee for live London Stock Exchange quotes (level 1) on the account, and that the API may use them.
2. That limit orders in AIM shares on SETS rest on the exchange's book (not internalised), with the
   commission per order (assumed below: 0.05%, minimum £1 each way; check the current schedule).
3. Whether the user is willing to keep IBKR's gateway program logged in during market hours on a
   computer or the droplet (it needs the account login and periodic two-factor approval; the
   droplet's 1GB of memory may not be enough).

## Phase 1: record, trade nothing (4 weeks)
Through the IBKR API, for each universe share during continuous trading (08:00–16:30 UK): every change
of best bid, best offer and their sizes, and every trade (time, price, size). Stored under
`data/aim_quotes/` and copied to B2. Reported at the end, before any simulation: per share, the median
gap as a % of the mid price, trades per day, and the share of trades at the bid, at the offer and inside.

## Phase 2: simulate on the recorded data (rules fixed now)
- **Eligible on a day:** median gap over the previous 5 days ≥ 1.5%; at least 3 trades a day on
  average over those days; price ≥ 5p; **no announcement from the company that day or the previous
  day** (any non-admin RNS item); not in an offer period.
- **Buy order:** £300 at the best bid at 08:15, re-placed at the new best bid when the bid moves
  (never above the previous day's close × 1.02). Cancelled at 16:20 if unfilled.
- **Fill rule (deliberately harsh):** the order fills only when a later trade prints **below** our
  price, or at our price for more than the size shown ahead of us when we joined. Joining the bid puts
  us last in the queue.
- **Sell order:** once filled, the whole position at the best offer, re-placed as the offer moves, never
  below our purchase price + 0.5% for the first 3 trading days; from day 4 at the best offer whatever it
  is; on day 6, or at once if the company announces anything, sell at the best bid (cross the gap).
- At most 6 positions open, one per share; no new buy in a share for 5 days after a loss in it.
- **Costs:** 0.05% with a £1 minimum each way; no stamp duty (AIM); nothing for cancelled orders.
- **Variants (2, nothing else tried):** as above (**P**), and **P-wide** with the gap threshold at 3%.

## Pass rule
- Phase 2 passes if a variant has ≥ 60 completed round trips, a mean net return per round trip above
  zero with t ≥ 2 (clustered by day), and positive total money after counting every position still open
  at its last best bid. Fewer than 60 round trips: "too small to judge"; extend the recording, do not relax rules.
- Reported whatever happens: fills per day, share of fills followed by a fall of more than the gap
  within 2 days (adverse selection), days held, the result split into gap earned and price drift, and
  the same without the best 5 trades.
- **Phase 3 (only after a pass, with the user's go-ahead):** the same rules with real orders of £100 for
  4 weeks, to compare real fills with the simulated ones; then £300.

## Known risks and traps
- ⚠️ The fill rule decides everything. Any rule that fills us when a trade merely touches our price
  flatters the result; the harsh rule above is the conservative reading and must not be softened.
- ⚠️ A passive buyer is filled most easily when the share is about to fall. Report the drift after
  fills first; if it exceeds the gap earned, the idea is dead whatever the totals say.
- ⚠️ Four weeks is one market mood. A pass leads to tiny real size, not to scaling up.
- Trap: using the day's later announcements to stay out. Only announcements published before the
  order time count; an announcement during the day triggers the exit rule from its timestamp.
- Trap: counting the opening and closing auctions as continuous trading; orders rest 08:15–16:20 only.
- Trap: valuing open positions at the mid price. Use the best bid.
