# Assumptions and known biases

Reference for the data and every choice that affects results. Where something was uncertain, the
conservative option was picked. ⚠️ marks anything that could make results look **better** than reality.

## Biases that affect every test
- **⚠️ Survivorship (biggest).** Yahoo has almost no prices for delisted companies. US: prices only
  for today's NYSE/Nasdaq tickers (EDGAR has dead companies' filings, but they can't be traded in a
  backtest). UK: of 420 delisted companies with announcements, 43 have prices. Failures drop out,
  so backtests see mostly survivors. Paid fixes are listed in the README; nothing bought.
- **⚠️ LLM hindsight.** Anonymising doesn't hide the company: the model named it in 45 of 50
  stripped releases. Its judgements (tone, outlook) may use what it knows happened later. Use LLMs
  for facts copied from the text; a judgement must not predict clearly better before the model's
  training cutoff than after it.
- **⚠️ Many tries.** The best of many variants on the same years is flattered. Count every try;
  keep a final test untouched (rules in `PLAN.md`).
- **Capacity:** a backtest without an account limit assumes every trade is funded. Use the
  account simulation ($150 × 40 slots).

## Costs
- **Brokers:** Trading 212: no commission, FX 0.15% each way. IBKR Pro fixed: $0.005/share, min $1,
  max 1% of the trade, FX 0.03% each way. (Check current price lists before going live.)
- **Spreads:** no historical quotes. Corwin–Schultz (from daily high/low) badly **overstates**
  spreads for liquid stocks (Apple 0.42%, real ~0.01%), so it's only the pessimistic check. The
  main assumption is a table by 20-day dollar volume: ≥$500m 0.05%, $100–500m 0.10%, $20–100m 0.20%
  full spread. It is an estimate, not measured; real quote data would settle it.
- **Slippage:** 0.10% each side (0.15% for Trading 212, which can't use closing-auction orders).
- **UK extras:** 0.5% stamp duty on main-market buys (not AIM); spreads of 2–3% on small AIM stocks.

## US data (SEC EDGAR + Yahoo)
- **Filings:** every 8-K / 8-K/A from EDGAR's quarterly index, 2012 on; items and metadata from the
  bulk submissions file. Document = Ex-99.1, else the first EX-99.x, else the 8-K itself. An 8-K with
  both an earnings item (2.02) and a news item is filed under earnings.
- **Acceptance time** comes from each filing's index page (Eastern time → UTC). The submissions
  JSON's `acceptanceDateTime` is wrong by 4–5 hours for many older filings: never use it for timing.
- **⚠️ The news is often public before the 8-K** (newswire first, filing minutes to hours later).
  So trades are only counted from the next price we could actually get after the filing.
- **Tickers:** the SEC ticker map covers current companies only.
- **Prices:** Yahoo daily OHLC, split-adjusted (`adj_close` also dividend-adjusted), NYSE + Nasdaq,
  plus SPY and IWM. `is_stale` = no volume or O=H=L=C; `is_suspect` = impossible bar or ≥30× jump.
  Both are flagged, never corrected, and excluded from outcomes.
- **Split histories** (`us_splits`, Yahoo) undo the split adjustment: price then = close × product of
  later split ratios. Yahoo records some spin-offs as fractional splits; undoing them is still right
  because it reverses exactly what Yahoo applied.
- **Company facts (XBRL):** a fixed list of concepts, using the `filed` date (point in time) and the
  first filed value per period (restatements ignored). The whole `companyfacts.zip` is kept.
- **Market cap** = price then × shares. Shares: median of the last 3 cover-page counts filed before
  the trade, carried through splits. Filers often get the scale wrong, so: if it disagrees >3× with
  net income ÷ diluted EPS from the release, the release's figure wins; >30bn shares dropped; caps
  outside $100k–$6tn dropped.
- **Ratio caps** are fixed numbers, never sample percentiles (those use future data).

## US earnings labels (GPT-6 Luna, prompt v4)
- 150,099 of 150,518 releases labelled, for $49.12. Diluted EPS matches the later XBRL filing within
  1 cent for 97.0% of 100,404 checkable releases (96–98% every year). 37% of releases were cut at
  25,000 characters (headline numbers come first).
- "Beat/missed consensus" is mentioned in only 2% of releases and we have no analyst data, so
  surprise is measured against the company's own history (SUE), which is weaker than true surprise.
- **SUE** = (EPS − prior-year EPS, both from the release) / std of the company's last 8 seasonal EPS
  changes filed before the trade (needs ≥4); clipped ±10.
- **Guidance change** is against the same company's previous guidance for the same period and basis.
- Raw responses are stored, so labels never need regenerating (models get retired).

## UK data (LSE + Investegate + Yahoo)
- **Universe:** AIM + a FTSE SmallCap proxy (Main Market commercial companies outside the FTSE 350),
  from LSE's instrument list, which is current companies only. ⚠️ About half of 2012's AIM companies
  have since delisted. Delisted names are added when they appear in announcements (daily-mode
  scrape), but usually have no prices. Index membership is as of today, not point in time.
- **⚠️ Yahoo's UK open/close are often auction or mid prices**, not trades (close sits outside the
  day's high-low on ~19% of days). Entering "at the open" is optimistic for illiquid names: always
  add half-spread and slippage. Since ~Feb 2024 some forward-filled days are in pounds instead of
  pence (100× too low); treat a whole stale stretch after a suspect row as unusable.
- **TIDMs get reused** by different companies; drop prices outside a company's listing window.
- **Announcements (Investegate):** personal research use only, never published or committed.
  2 requests a second, honest User-Agent, stops on blocks. Times are UK wall clock → UTC;
  pre-open = before 08:00. No official RNS category: `headline_category` is a rough regex label.
  Admin notices (whole headline is an admin phrase) are kept separately without bodies.
  "Director/PDMR Shareholding" is **not** admin (insider dealing is a strategy input).
  Replacements and non-regulatory announcements are kept and flagged.
- **Trading service (SETS/SETSqx):** from LSE's official files, current only, so today's service is a
  proxy for the past. The market-maker file has to be downloaded by hand.
