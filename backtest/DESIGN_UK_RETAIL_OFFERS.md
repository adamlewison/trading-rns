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
