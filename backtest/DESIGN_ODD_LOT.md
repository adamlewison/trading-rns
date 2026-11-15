# Design: US odd-lot tender offers (written 2026-11-15, BEFORE any data was pulled or result seen)

Fixed before running; later changes are added at the end with the reason. Testing rules in `CLAUDE.md`.

## The idea
When a listed company buys back its own shares by tender offer and more shares are tendered than it
wants, holders are scaled back pro rata. Most offers exempt **odd lots**: a holder of fewer than 100
shares who tenders all of them is bought in full. Because of the scale-back, the market price during the
offer usually sits below the tender price. Someone holding ≤ 99 shares collects the whole gap.

Why it could survive: the prize is capped at 99 shares a deal (a few hundred dollars), so no fund can
take it, and it needs a manual election at the broker. Why it might not: it is well known among small
investors, some companies have dropped the odd-lot clause, and deals are few.

## Phase 0: checks with IBKR before any code (the user asks IBKR; any "no" stops the track)
1. **Withholding tax (the biggest risk).** For a non-US holder, self-tender proceeds can be treated as a
   dividend, and offer documents say 30% (15% under the UK treaty) of the **gross** payment may be
   withheld unless the holder certifies the sale ends their holding. That would be several times the
   edge. Ask: *"For an issuer self-tender (Schedule TO-I), do you withhold US tax on the proceeds for
   my account, and can I certify a complete termination of interest to avoid it?"*
2. Does IBKR pass on the **odd-lot priority** election, and is there a fee per voluntary corporate action?
3. IBKR's election **cut-off** relative to the offer's expiry (assumed below: 2 trading days before).
4. Can shares bought with unsettled status be tendered (US settlement is T+1)?

## Data (phase 1)
- EDGAR quarterly index, forms `SC TO-I` and `SC TO-I/A` (and any renamed equivalent found in the
  index), 2012 → now. The current `sec_form_index` keeps 8-Ks only, so the ingest is extended; SEC
  rate limit and User-Agent rules as before.
- Kept: filers on our ticker map with daily prices (NYSE/Nasdaq common stock). Dropped and counted:
  unlisted funds, REITs and BDCs with no price, and third-party offers (`SC TO-T`, takeovers).
- Per offer: the Offer to Purchase (exhibit (a)(1)(A) or (a)(1)(i)), every amendment, the final
  results press release, and each filing's acceptance time from its index page.

## Labels (phase 2; facts copied from the text only, no judgements)
From the offer: fixed price, or the low and high of a Dutch-auction range; shares sought; expiry date;
whether odd lots are exempt from scale-back; conditions (financing, minimum tender). From amendments:
extensions, price changes, termination, final price, scale-back factor. Regex first; GPT-6 Luna where
regex fails (estimate under $5 for the first ~30,000 characters of ~2,000 documents; the run stops and
asks if the estimate exceeds £5). Check: the labelled final price must match the results release, and
50 random offers are printed for the user to eyeball.

## Rules (phase 3)
- **Floor price** = the fixed price, or the **low end** of a Dutch-auction range (an odd-lot holder
  tenders "at the price the offer sets" and receives the final price, which is never below the low end).
- **Gap** = floor ÷ entry price − 1 − costs. Entry price is the then-actual price (close × later split
  ratios, section 8), never the split-adjusted one.
- **Entry timing: E1** the next open after the offer's first filing; **E2** the open 5 trading days
  before expiry. Never later than 3 trading days before expiry.
- **Threshold: G1** gap ≥ 1%; **G3** gap ≥ 3%. Four variants (E1/E2 × G1/G3); nothing else is tried.
- Only offers with the odd-lot exemption and a price on the entry day (not stale or suspect).
- Size: min(99 shares, $2,000 ÷ price), at least 1 share; at most 3 deals open; first filed first.
- **Outcome:** completed → the final price in cash, 7 trading days after the last expiry (extensions
  followed). Terminated, or the odd-lot clause removed → sold at the next open after that filing.
  Outcome not found → sold at the close on expiry day, and the deal is flagged and counted.
- No model, no exits to tune.

## Costs and account
IBKR Pro fixed: $0.005 a share, $1 minimum, on the buy (and on the sale if a deal fails); half the
spread plus 0.10% slippage on each market trade (section 8 table; below $20m daily dollar volume a full
spread of 0.60%); FX 0.03% each way; any tender fee found in phase 0. No trade on exit when the tender
completes. Account: $6,000 held in SPY (with dividends); each deal's cash leaves SPY at entry and
returns on the payment day. Every deal is also measured against SPY over its own days.

## Years and pass rule
Choose 2012–2021 (deals are too few for three years); final 2022 → latest, **once**, with one variant frozen.
- **Pass to the final test:** a variant has ≥ 30 deals, a mean return per deal after costs above SPY's
  over the same days with t ≥ 3, and ≥ 80% of deals profitable. With fewer than 30 deals in every
  variant the test is declared too small to judge rather than passed or failed.
- **Final pass:** the frozen variant's account ends above the all-SPY account, and its mean deal return
  after costs is positive with t ≥ 2.
- Reported whatever happens: deals per year, dollars per year on the $6,000 account, every failed deal,
  and whether deal counts or gaps shrink over time (a sign the edge is being competed away).

## Known risks
- ⚠️ Withholding tax and broker handling (phase 0): the backtest cannot see them.
- ⚠️ Survivorship: issuers later delisted have no prices and drop out; the count is reported.
- ⚠️ Opening prices on the first day after the filing may already include the jump; E2 is the check.
- Few deals, and one terminated offer can cost several deals' profit: a few failures decide the result.
- Dutch auctions that clear above the low end add profit the entry rule did not count on; reported apart.

## After the backtest (only if it passes)
A daily droplet job that reads new `SC TO-I` filings, labels them and reports open deals with their gap;
paper trading first, then one small real deal to confirm IBKR's handling.

## Execution steps (detail added 2026-11-15, still before any data; follow `backtest/RUNBOOK.md`)
Phase 0 must be answered by the user first. If IBKR withholds tax on the proceeds for a UK holder and
no exemption is available, record the track as blocked in `CLAUDE.md` and stop: do not build anything.

**Step 0, data checks (no outcomes).**
1. In the EDGAR quarterly index (`form.idx`), list the form types beginning `SC TO` or `SCHEDULE TO` and
   count filings per year for each. Expect a few hundred `SC TO-I` a year, most from unlisted funds.
2. Join to the ticker map and `us_prices_daily`. Report per year: filings, distinct offers (group
   `SC TO-I` plus its `/A` amendments by CIK and a gap of more than 120 days between filings), and offers
   with a price on the filing date. If fewer than 15 priced offers a year on average, report and stop.

**Step 1, documents.** For each priced offer download the filing index page (acceptance time, as for
8-Ks: never the submissions JSON), the Offer to Purchase from the first filing, and every amendment's
main document and press-release exhibit. Text only. Extend `ingest/us/` (new table, e.g. `us_tenders`).

**Step 2, labels.** One row per offer with these fields, each either copied from the text or null:
`offer_type` (fixed | dutch), `price` or `price_low` and `price_high`, `shares_sought`, `expiry_date`
(the first one), `odd_lot_priority` (true only if the text says odd-lot holders who tender all their
shares are not subject to proration), `security` (must be common stock; drop preferred, units, notes),
`conditions`; from amendments: `extended_to` (list), `terminated` (date), `final_price`, `proration_pct`,
`results_date`. Regex first; Luna only for what regex leaves null. Checks before using the labels:
- `final_price` lies inside the range (dutch) or equals `price` (fixed) for at least 95% of completed offers;
- `expiry_date` is 20–60 calendar days after the first filing for at least 90% of offers;
- print 50 random offers (company, dates, prices, odd-lot flag, link) into the design file for the user.

**Step 3, backtest (`backtest/us_odd_lot.py`).** Traps specific to this test:
- The entry price and the floor are compared in **actual dollars of that day**. `us_prices_daily` is
  split-adjusted: multiply by later split ratios first. A unit test must cover an offer followed by a split.
- E1 enters at the first regular open **after the acceptance time** of the first `SC TO-I` (a filing at
  17:00 ET enters the next day; one at 07:00 ET enters the same day).
- An offer is tradable only if its entry day is at least 3 trading days before the expiry known **at
  entry** (the original one for E1, the latest announced one for E2).
- Dutch auctions: the entry test uses `price_low`; the payoff uses `final_price`. Never the reverse.
- A failed deal is still a trade: sell at the next open after the termination filing, with full costs.
- Cash is out of SPY from the entry day to the payment day (last expiry + 7 trading days).
- Returns are per deal on the money invested (shares × entry price), not on the $6,000.

**Step 4, report** as in the runbook, plus: deals per year, dollars per year, the list of failed
deals, and the mean gap by year (is the edge shrinking?). The too-good rule applies with a twist: a win
rate above 95% is plausible here, so instead verify 10 completed deals by hand against the results
press release (final price and that odd lots were not prorated).

**What would make the conclusion wrong:** (a) counting offers without odd-lot priority, where the
holder is scaled back and left with shares that fall after expiry; (b) using the split-adjusted price;
(c) dropping terminated offers; (d) ignoring withholding tax, which the backtest cannot see.

## Phase 0 research (2026-11-16, public sources only; IBKR itself has not answered)
- **Withholding (the blocker).** Offer documents say the same thing in each case checked (Triumph
  Bancorp 2022, PHH 2017, Santander Consumer 2020): 30% of the **gross** proceeds paid to a non-US
  holder is withheld, or the treaty rate with a valid W-8BEN (15% for a UK resident). It can be avoided
  only if the withholding agent accepts a certificate that the holder meets a Section 302 test (for
  example "complete termination": all shares sold); holders at a broker are told to ask the broker
  whether it offers that certificate. Otherwise the holder must claim a refund from the IRS.
- **Whether IBKR offers that certificate could not be found** in its public pages or in user reports.
  If it does not, a UK holder loses 15% of the proceeds up front to earn a gap of a few percent, with
  a refund only through a US tax filing: not workable. This one question decides the track.
- **Deadline:** IBKR asks for elections by 12:00 ET on the day before the offer expires (earlier in
  some regions); later requests are best-efforts. The design's "2 trading days before expiry" stands.
- **Odd-lot election and fee:** not stated publicly. Offer documents say a broker "may charge a fee".
- Odd-lot terms confirmed in a real offer: fewer than 100 shares owned, all of them tendered, and the
  odd-lot box completed; modified Dutch auctions give a price range (for example $51–58).
