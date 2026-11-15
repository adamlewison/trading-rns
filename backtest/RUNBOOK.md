# Runbook: how to run any test in this project without fooling yourself

Written 2026-11-15 for whichever agent runs the tests next. Read `CLAUDE.md` first, then this, then the
strategy's `DESIGN_*.md`. The design says **what** to test; this says **how**. If the two disagree,
the design wins and you note the disagreement under "Changes" in the design.

The single most likely way to get this wrong is to find a result that looks good and believe it.
Nine strategies have failed here. A tenth that "passes" is far more likely to be a bug or a
flattering assumption than a discovery. Treat every good result as a bug until you have ruled bugs out.

## 1. Order of work (do not skip or reorder)
1. Read the design to the end. Do not start coding from the summary.
2. **Step 0 of the design: data checks.** Count what the design says to count. If a number is far from
   what the design expects (say half or double), stop and report; do not adapt the rules yourself.
3. Write the code with every rule as a named constant at the top of the file, copied from the design.
4. Write unit tests on tiny hand-made data where you know the right answer (section 4).
5. Run the checks in section 5 on the choose period. Fix bugs. Bugs fixed here are not "changes".
6. Run the choose period once for all variants. Write the result into the design file ("Choose-years
   result") and one row in `CLAUDE.md` section 6. Commit and push.
7. **Stop.** Report to the user. The final test runs only after the user says so, on one frozen variant,
   once, behind a `--final` flag that the code refuses to run without.
8. After the final test: write the result, pass or fail, in the design and in `CLAUDE.md`. Commit, push, stop.

## 2. Rules that are never broken
- **Never look at final-period outcomes before the final test.** The code must filter the final period
  out *before* computing anything, not compute everything and print only part. Checking row counts and
  date ranges of the final period is fine; returns, win rates and prices after entry are not.
- **Never change a rule because of a result.** If a rule turns out to be impossible or ambiguous, choose
  the reading that makes returns **lower**, write it under "Changes" with the date and reason, and say in
  the report that the change was made after seeing data.
- **Never add a filter to rescue a failing strategy** ("it works if we drop 2022", "it works for small
  deals"). That is a new strategy: it needs its own design, and its final years are already spent.
- **Never drop a variant from the report.** All variants in the design are reported, best and worst.
- **Never invent a number.** If something could not be measured, write "not measured".
- **No real money, no paid data, no LLM run over £5** without the user's go-ahead. Scraping rules are in
  `CLAUDE.md` section 2: on 403 or 429 slow down, then stop; never work around a block.

## 3. Look-ahead: the checklist (go through every item, for every test)
A backtest may only use what was knowable at the moment of the trade.
- **Timestamps.** Each signal has a time it became public. The entry price is the first price obtainable
  *after* it: next open for news after the close, same-day open only for news before the open.
- **Fields that are rewritten later.** Anything describing "now" is suspect when used for the past:
  today's index membership, today's share count, a market's final close time, a "status" column, the
  latest version of an amended filing. Use the value as it was first published, or do not use it.
- **Adjusted prices.** `adj_close` is for measuring returns only. To compare with a price written in a
  document (a tender price, an issue price), use the actual price of that day: unadjusted close, and
  for US stocks close × later split ratios (`us_splits`, `CLAUDE.md` section 8).
- **Fitting.** Any mean, standard deviation, percentile or model used in a signal is computed from data
  strictly before the signal date. No full-sample z-scores, no full-sample winsorising.
- **Selection by outcome.** Do not select events using anything that happens after entry (for example
  "offers that completed"). Failed, cancelled and voided events stay in, with their real outcome.
- **LLM labels.** Use them only for facts copied from the document. A model may know how things turned
  out; never ask it whether something is a good trade when testing dates before its training cutoff.

## 4. Unit tests to write (small, hand-made data)
- One winning and one losing trade with known prices: the return, fee and cost come out to the cent.
- A signal exactly on a boundary (threshold, window edge, band edge): included or excluded as written.
- An event with a missing price on the entry day: skipped and counted, not filled from another day.
- The account: a third trade when only two slots are free is skipped; stakes follow the sizing rule.
- The period filter: a final-period event never appears in a choose run.

## 5. Checks before believing any result
Run these on the choose period and put the outputs in the report.
1. **Print 10 trades end to end** (5 best, 5 worst): the raw source rows, signal time, entry time and
   price, exit time and price, costs, return. Read them. Most bugs are visible here.
2. **The too-good rule.** If any variant shows a mean net return per trade above 2%, a t-statistic
   above 6, a win rate above 95% (above 99.5% for the prediction-market test) or an account more than
   doubling in a year: assume a bug. Re-check section 3 line by line before reporting, and say what you checked.
3. **Placebo.** Move every signal to a random other date for the same instrument (or shuffle outcomes
   across events). The result must be about zero before costs and negative after. If the placebo also
   "works", the code is leaking.
4. **Benchmark check.** Buying everything in the universe on the same dates should give roughly the
   index return. If not, the price data or the return calculation is wrong.
5. **Costs applied on both sides**, and the benchmark includes dividends and covers the same days.
6. **Count the losses in the data.** Report how many events were dropped and why (no price, stale price,
   unparsed document, delisted). If more than 20% were dropped, say so in the first line of the report.
7. **Concentration.** Report the result without the best 5 trades and without the best year. If the
   result disappears, say so. A result carried by 2020 is not a result (lesson 14).

## 6. Statistics (use exactly these)
- One observation per **independent bet**. Trades in the same company, the same event or the same day's
  burst are one cluster. Compute the mean return per trade `m`, and its standard error as
  `sqrt(sum over clusters of (sum of (r_i − m) within the cluster)²) / N`, where N is the number of
  trades. t = m / standard error.
- Each design gives a **minimum count**. Below it the verdict is "too small to judge", never pass or fail.
- Report medians and the share of winners next to means; a mean made by three trades is not an edge.
- Compare with the benchmark over the same days, per trade and for the account.

## 7. The only verdicts allowed
- **Failed** (say at which stage and why), **passed to the final test**, **passed the final test**,
  or **too small to judge**. Do not write "promising", "encouraging" or "nearly passed".
- A pass leads to paper trading, then one small real trade. It never leads straight to real money.
- Survivorship, capacity and broker handling are stated with every verdict, with their likely direction.

## 8. Reporting format (design file and chat)
First line: the verdict. Then a table with one row per variant: trades, clusters, win share, mean and
median net return per trade, t, account result, benchmark result, worst drawdown. Then the checks from
section 5 with their outputs. Then what was dropped. Then what would flatter the result. No adjectives.

## 9. Housekeeping
- Design file committed **before** the first run; every later edit goes under "Changes" with a date.
- `uv run pytest` before every push. Push after each step (the container is temporary).
- Update `CLAUDE.md` in the same commit: section 1 (status), 6 (result row), 4 (a lesson, if any), 8 (data
  findings), 9 (new commands). Keep it short; details live in the design file.
- New data goes to B2 under its own prefix; nothing scraped is ever deleted (`CLAUDE.md` section 2).
- If you are unsure what a rule means and both readings change the result, stop and ask the user.
