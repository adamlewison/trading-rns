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
