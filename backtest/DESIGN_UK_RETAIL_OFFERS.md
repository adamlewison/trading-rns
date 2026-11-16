# Design: UK retail offers (written 2026-11-15, BEFORE any data was looked at)

Fixed before running; later changes go under "Changes" with a date. How to run it: `backtest/RUNBOOK.md`.

## The idea
When a UK company raises money by a placing, institutions buy new shares at a discount to the market
price (often 5–10%). Since about 2019 many placings include a **retail offer** at the same price through
PrimaryBid, RetailBook, Winterflood's WRAP, Peel Hunt's REX or BookBuild. A private investor applies
for a few hundred pounds of shares, receives them on admission days or weeks later, and can sell them.
There is no commission and no stamp duty on new shares. The trade earns the discount if the price holds.

Why it could work: the discount is a payment for taking stock quickly, normally reserved for
institutions. Why it might not: the price usually falls towards the issue price; popular offers are
scaled back, so you get all of the bad ones and little of the good ones; companies raising money at a
discount are often in trouble; the wait until admission can be weeks.

## Phase 0: checks by the user (no code depends on them, but live use does)
Which platform the user can use (PrimaryBid app; RetailBook through AJ Bell, Hargreaves Lansdown or
interactive investor), the minimum application (often £250), and how fast shares can be sold after admission.

## Data
- `ann_index` (all announcements, with `headline`, `published_uk`, `tidm`) and `ann_bodies`; daily prices
  `prices_daily`; benchmark the FTSE All-Share with dividends, built as in `backtest/uk_directors.py`.
- **Candidate announcements:** headline matches (case-insensitive) `retail offer`, `primarybid`,
  `retailbook`, `wrap retail`, `rex retail`, `bookbuild`, or `offer for subscription`. Exclude headlines
  that are only `open offer` (existing holders only) and `result of`-type headlines from the launch
  list (they are used for outcomes).
- If bodies for these announcements are missing (evening announcements may not have been fetched),
  fetch them from Investegate at the usual 2 requests a second.

## Step 0: data checks (no outcomes)
Report per year 2015–2026: candidate launch announcements, how many have a body, how many are for a
ticker with prices on the announcement date. Expect very few before 2019 and roughly 50–200 a year
from 2020. If there are fewer than 150 priced launches in total up to 2021, report and stop.

## Labels (facts copied from the text; regex first, Luna for the rest; estimate under £2)
Per launch: `issue_price_p` (pence), `platform`, `offer_close` (date and time), `existing_holders_only`
(bool), `conditional_on_gm` (bool), `expected_admission` (date), `min_application_gbp`, `retail_size_m`.
Per offer, from later announcements by the same company within 60 days (headlines such as `result of
retail offer`, `result of placing`, `result of general meeting`, `admission`, `total voting rights`):
`actual_admission` (date), `cancelled` (bool), `retail_raised_m`.
Checks: `issue_price_p` is 50–105% of the close before the announcement for at least 95% of offers
(otherwise a pence/pounds error: fix the parser, do not drop silently); print 30 random offers for the user.

## Rules
- **Offer included if:** not for existing holders only; the company has a clean price (not stale or
  suspect) on the last trading day before the announcement; prior close ≥ 5p; 20-day average turnover ≥ £25,000.
- **Decision time:** the user applies before `offer_close`, knowing only prices up to the last close
  before the launch announcement (launches after 16:30) or up to the previous close (launches before 08:00).
- **Discount** = 1 − issue price ÷ that last close.
- **Variants (4, nothing else tried):** apply to **A** every included offer, or **D5** only those with a
  discount ≥ 5%; sell at the **open** or at the **close** of the admission day.
- **Admission day** = `actual_admission`; if missing, `expected_admission`; if both are missing the
  offer is dropped and counted. If that day has no clean price, use the next clean day within 5 days,
  else drop and count.
- **Cancelled offers** return 0% and tie the money up until the cancellation date.
- **Allocation (the main uncertainty).** Report both, judge on the second:
  - *Full:* every application is filled in full.
  - *Adverse:* filled in full if the close on the first trading day after the launch is at or below
    issue price × 1.03; otherwise filled 25%. This approximates being scaled back in popular offers.
- Stake £500 per offer applied for; at most 6 applications outstanding; money is committed from the
  application to the admission day. Return per offer = filled fraction × (sale price ÷ issue price − 1 − costs).

## Costs
Nothing on the purchase. On the sale: half the spread from the UK cost table (`CLAUDE.md` section 8 and
`backtest/uk_news.py`) plus 0.25% slippage. Yahoo's UK open and close are often auction or mid prices,
which is why the half-spread and slippage are always charged.

## Years and pass rule
Choose: launches up to 2021-12-31. Final: 2022-01-01 → latest, once, one variant frozen.
- **Pass to the final test:** under *adverse* allocation a variant has ≥ 100 offers, a mean net return per
  offer above the FTSE All-Share's over the same days with t ≥ 3 (clustered by launch week), and it is
  still positive with 2020 removed (lesson 14).
- **Final pass:** the frozen variant, adverse allocation: mean net return above the index's with t ≥ 2,
  and positive money made in total.
- Below 100 offers in the choose period: "too small to judge".

## Report (besides the runbook's table)
Discount at launch, price move on the first day after launch, days from launch to admission, share of
offers where the admission-day price was below the issue price, results under both allocations, results
by platform and by year, and the offers dropped for missing prices.

## Known risks and traps
- ⚠️ **Survivorship, strongly flattering.** Companies that raised money at a discount and later failed
  have no prices and drop out. Report how many launches were dropped for missing prices, by year.
- ⚠️ **Allocation is assumed, not observed.** A pass under *full* but not *adverse* is a **fail**.
- ⚠️ The sale price is a Yahoo open or close, not a quote. Small AIM stocks can gap on admission day.
- Trap: comparing the issue price with an adjusted price. Use unadjusted `close`, in pence.
- Trap: using the market price *after* the launch to decide whether to apply. The decision uses the
  last close before the launch only (the adverse-allocation rule is a penalty, not a signal).
- Trap: treating the announcement of results as the launch (the offer is already closed by then).
- Trap: an offer conditional on a general meeting that then fails is a cancelled offer, not a missing one.

## Step 0 result (2026-11-15): STOPPED, too few offers before 2022 (no outcome was looked at)
Candidate launch announcements (headline match, "result" headlines excluded) and how many are for a
ticker with a clean price on or just before the announcement day:

| Year | 2012–15 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Launches | 153 | 40 | 82 | 33 | 61 | 87 | 142 | 95 | 123 | 185 | 213 | 212 |
| Priced | 7 | 13 | 31 | 8 | 13 | 28 | 46 | 24 | 58 | 107 | 130 | 122 |

- Priced launches up to 2021: **146**, below the 150 the design requires, and that count is inflated:
  the headline match also catches VCT "offer for subscription" notices and institutional "accelerated
  bookbuild" notices, which are not retail offers. Real retail offers before 2022 are probably under 100.
- Most activity is in 2024–2026, which the design reserves for the final test.
- Only about a fifth of launches have their text stored (evening announcements were not fetched), so
  about 1,000 pages would need fetching from Investegate, from the droplet (the only machine that scrapes).
- Most launches have no price because the company is outside our universe (larger Main Market
  companies, trusts, VCTs).

**Proposed change, awaiting the user (made on counts only):** move the split so the choose period is
launches up to 2024-12-31 and the final period is 2025-01-01 → latest; drop "offer for subscription"
and "bookbuild" from the headline match; fetch the missing texts on the droplet. Without this change
the verdict is "too small to judge".

## Changes (2026-11-16, agreed by the user, made on counts only: no price outcome has been looked at)
- **Split:** choose = launches up to 2024-12-31; final = 2025-01-01 → latest.
- **Headline match:** `retail offer|primarybid|retailbook` only (this still covers WRAP and REX retail
  offers). "Offer for subscription" (mostly VCTs) and "bookbuild" (institutional) are dropped.
- **Texts:** every announcement matching that pattern, launches and results, is fetched on the droplet
  (`ingest.uk.announcements.run bodies --headline-re ...`). Admission day = the date in the "result"
  announcement if it gives one, else the expected date in the launch announcement.
- Pass rule unchanged (≥ 100 offers, adverse allocation, t ≥ 3 by launch week); "still positive with 2020
  removed" stays.

## State (2026-11-16)
- 1,134 announcements match the new headline pattern (362 are results); 457 have text, 677 do not.
- The fetch on the droplet (unit `retail-bodies`, run from `/opt/trading-rns-pm` with `data/uk.duckdb` and
  `data/raw/uk` linked to the UK checkout) was stopped: every Investegate page timed out, from the droplet
  and from a cloud session, while robots.txt answered. Retry later with the same command; if pages still
  time out, stop and report. After a successful fetch the UK backup loop carries the texts to B2
  (`core.backup pull --profile uk --force --skip-raw` in a cloud session).
- `classify/uk_retail_offers.py` (Luna, facts only) is written and was tried on five stored
  announcements: it returned kind, price in pence, platform, close time, existing-holders-only,
  general-meeting condition and admission date correctly formed. Many WRAP offers are for existing
  holders only, which the design excludes, so the usable count will be lower than the launch count.
- Not written yet: `backtest/uk_retail_offers.py`. Intraday launches (08:00–16:30) use the previous
  day's close as the last known price (the conservative reading of "Decision time").

## Choose-period result (launches up to 2024-12-31, run 2026-11-16): TOO SMALL TO JUDGE
All 1,134 texts were fetched (Investegate answered again) and labelled by Luna for £0.38: 687 launches,
405 results, 42 other. Final period (2025 →) not looked at.

**From 399 choose-period launch announcements to 50 usable offers** (100 required):
72 state no price yet, 140 are for existing holders only, 23 repeat an earlier announcement of the same
offer → 164 offers. Of those: 61 have no prices (company outside our universe or delisted), 27 have an
issue price that cannot be compared with our price data (below), 18 are too small or illiquid, 6 have
no clean last close, 2 no admission date → **50 used**.

| Variant | Offers | Winners | Mean net | Median net | Excess, full allocation (t) | Excess, adverse allocation (t) | Adverse, without 2020 | Adverse, without best 5 |
|---|---|---|---|---|---|---|---|---|
| A-open | 50 | 60% | +5.2% | +1.4% | +4.7% (2.9) | +0.7% (1.0) | +0.3% | −0.3% |
| A-close | 50 | 58% | +5.3% | +3.0% | +4.7% (2.5) | +0.4% (0.6) | −0.2% | −0.6% |
| D5-open | 40 | 60% | +4.4% | +0.8% | +4.0% (2.5) | +0.7% (0.8) | +0.4% | −0.5% |
| D5-close | 40 | 58% | +4.4% | +2.6% | +4.0% (2.0) | +0.3% (0.3) | −0.2% | −0.9% |

- Excess = net return minus the FTSE All-Share over the same days. Average discount at launch 12%;
  the share price falls about 6% the next day but stays above the issue price; average wait 11 days;
  about a third of offers trade below the issue price at admission.
- **The whole question is allocation.** With every application filled in full the offers made about
  +4.7% each over the index in 11 days (t 2.5–2.9, below the 3 required). Under the adverse rule (a
  quarter filled when the price holds more than 3% above the issue price, which was the case for about
  two thirds of offers) it is +0.7% (t 1.0), and negative without the five best offers. The backtest
  cannot tell which is closer to the truth: only real applications can.
- Even if the count reached 100, the adverse-allocation result as it stands would fail the pass rule.
- ⚠️ 61 of 164 offers have no prices, many of them companies that later failed: the result flatters.
- **Data finding:** Yahoo's UK `close` is adjusted for later share consolidations. 19 of the 27
  "issue price cannot be compared" offers are companies that later consolidated their shares 10–700 to 1
  (issue price 0.5p against an adjusted close of 6.5p). Any test comparing a price stated in a document
  with UK prices needs the consolidation history, or the last close stated in the same document.
- Checks: the 10 best and worst offers were printed and read (prices and dates consistent); no variant
  trips the too-good rule. No placebo was run (an event test with a fixed purchase price has none).

**What would settle it:** a live record of real allocations. Apply for the minimum (often £100–250)
in each open retail offer on PrimaryBid or through a RetailBook broker for a few months and note the
amount received; that measures the one thing the backtest has to assume.

## FROZEN for the final test (2026-11-16, by the user's decision although the choose period was too small)
**A-open:** apply to every included retail offer (not existing-holders-only, clean last close ≥ 5p,
20-day turnover ≥ £25,000), sell at the open of the admission day; £500 per offer, at most 6 outstanding.
Run ONCE on launches from 2025-01-01 with `backtest.uk_retail_offers final --final`.
**Final pass (unchanged):** under adverse allocation, mean net return above the FTSE All-Share's over
the same days with t ≥ 2 (clustered by launch week), and positive money made in total; fewer than 100
offers = too small to judge. The full-allocation figure and the other three variants are printed for
information only.

## FINAL TEST RESULT (launches 2025-01-01 → 2026-11, run once, 2026-11-16): TOO SMALL TO JUDGE
288 launch announcements → 90 offers after removing 32 without a price, 151 for existing holders only
and 15 repeats → **18 usable** (31 too small or illiquid, 19 without prices, 13 without a clean last
close, 9 with a price that cannot be compared). 100 were required.

| A-open (frozen), 18 offers | |
|---|---|
| Winners after costs | 56% |
| Mean net return per offer (median) | +7.3% (+2.8%) |
| Excess over the index, full allocation (t) | +6.2% (1.1) |
| **Excess over the index, adverse allocation (t)** | **−0.3% (−0.2)** |
| Adverse, without the best 5 offers | −3.7% |
| £500 an offer, adverse allocation | +£22 against +£52 for the index on the same money |

For information, the other variants under adverse allocation: A-close +0.5%, D5-open +0.2%, D5-close +0.3%
(t all below 0.2). Average discount 16%, average wait 11–14 days, a third below the issue price at admission.

The picture is the same as in the choose period: filled in full the offers look good on average
(+4.7% and +6.2% over the index) but the average rests on a few large winners and is not
statistically reliable at these counts; with popular offers scaled back to a quarter there is nothing
(+0.7% and −0.3%). More offers than expected were for existing holders only, so the final period was
smaller than the choose period, not larger. The final period is now spent. No further backtest can
settle this: only a record of real allocations can.

## Live trial (started 2026-11-16, the user's decision; small real money, outside the backtest rules)
**Purpose:** measure the one thing the backtests had to assume, the share of each application actually received.
- **The user applies by hand** (no broker API covers this, and IBKR and Trading 212 do not offer retail
  offers): PrimaryBid's app, or a broker that carries RetailBook, WRAP or REX offers. Apply for the
  minimum (usually £100–250) in **every** offer open to new investors, without picking; keep no more
  than about £1,000 outstanding; sell at the open on the admission day.
- **The user reports** each application to an agent (ticker, amount applied, amount received, sale
  price); the agent adds a line to `paper/retail_offers_log.csv` and pushes.
- **The droplet records everything else** twice each weekday (07:30 and 17:30 UK, timer
  `trading-rns-retail`, `paper.retail_offers daily`): new retail offers from Investegate, their terms
  (Luna, pennies), and what each would have made if filled in full. Status: `paper.retail_offers status`
  or B2 `trading-rns/paper/retail_offers.md`.
- **Judge after 20 applications or six months,** whichever comes first: average share received, and
  money made against the same money in the index. Skipping offers by feel breaks the test.
- ⚠️ Offers often close within hours; the platforms' own alerts are the prompt to apply. The droplet's
  record is for measuring, not for alerting.

## Practical facts for the live trial (from the labelled launches open to new investors, checked 2026-11-16)
- **PrimaryBid no longer appears:** 20–28 offers a year in 2022–2024, none in 2025–2026. Current
  platforms: Winterflood's WRAP (34 in 2025, 36 in 2026), RetailBook (23, 30), BookBuild (3, 8).
- About 7 offers a month open to new investors. Launch time: 07:00–08:00 (57%) or 16:30–18:30 (43%).
- Time open: median 3 days; a quarter close within 24 hours, 14% within 6 hours; brokers may close earlier.
- Minimum application: £100 (most common) or £250; a third depend on a general meeting.
- Applications go through a participating broker (announcements: "contact your broker"; Hargreaves
  Lansdown is named publicly for WRAP). Which brokers carry RetailBook and BookBuild offers is not
  published in one place: the user must confirm with their broker. IBKR and Trading 212 do not take part.
