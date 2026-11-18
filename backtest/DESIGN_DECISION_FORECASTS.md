# Design: decision-model forecasts against market prices (written 2026-11-18, BEFORE any model answer or price was seen)

Fixed before running; later changes go under "Changes" with a date. How to run it: `backtest/RUNBOOK.md`.

## The idea
Decision models (launched autumn 2026) answer a yes/no question with a probability, for fractions of a
cent. A prediction-market price is also a probability. If any model's probabilities are better than the
market's on events it cannot already know, betting where the two differ would pay. This test asks
which model, if any, beats the market, and by how much.

Prior: low. A model without web access knows nothing after its training cutoff, while the market price
contains every piece of news up to the moment of the snapshot. Published work finds language models
forecast about as well as crowds, not better.

## Models (Vercel AI Gateway, `POST /v1/decisions`, `predicate` questions)
Every decision model the project's gateway key can call **without web search** (14):
`baseten/elephant-1`, `baseten/joey-1`, `baseten/mammoth-1`, `convaiinnovations/laya`,
`heygen/heygen-decision-1`, `heygen/heygen-decision-1-turbo`, `liquid/d1-ultra`, `liquid/d1.2`,
`liquid/d1.2-ultra`, `microsoft/decision-1.1`, `nace-ai/drex-max-finance`, `nace-ai/drex-max-v1.1`,
`nace-ai/drex-v2.0`, `scaledown/scaledown-classify-1`.
- **Excluded from the historical test:** Parallel Decisions and Perplexity Decider, because they search
  the web and would find how settled events turned out. They can only be judged going forward.
- **Not available on the free key:** Gemini Decision 1, GPT-6 Luna Decisions, Microsoft Decision-1,
  Liquid d1, Jev. If the user adds paid credits they are run under exactly the same rules.

## Events
- Kalshi binary markets (`data/pm/kalshi/markets`), settled yes or no, lifetime volume ≥ 1,000, **scheduled
  end from 2026-10-20 to 2026-11-15** (after the models' likely training cutoffs; most were released
  September–November 2026). One market per event, chosen by hash of the ticker; then a fixed sample of
  **2,000 events** by hash. Multi-leg combinations (`KXMVE…`) excluded.
- **Choose half:** scheduled end 2026-10-20 → 2026-11-02. **Confirm half:** 2026-11-03 → 2026-11-15.
- **Hindsight check sample:** 500 events (same rules) with scheduled end 2026-08-01 → 2026-08-31.
- **Snapshot:** T = scheduled end − 24 hours. Market price = mid of the last hourly candle at or before T
  with both bid and ask present and a gap of at most 10c; otherwise the event is dropped and counted.
  The ask at T is kept for the trading rule.

## Question (identical for every model)
Input text: the market title, its rules text (`rules_primary`, first 1,500 characters), and
"Today's date is <T, UTC>. The event resolves on <scheduled end>." Question:
`{"type": "predicate", "name": "yes", "instructions": "Based only on the information above and what you know, will this market resolve Yes?"}`.
- **Mode B (blind):** as above.
- **Mode M (with market):** the input also says "The market currently prices Yes at <mid>%." Same question.
Probabilities are clipped to [0.01, 0.99] for scoring.

## Scoring
- Brier score per event (squared error); the market's Brier uses the mid. **Skill** = 1 − Brier(model) /
  Brier(market). 95% interval by bootstrap over series-days (1,000 resamples, seed 7).
- **Trading rule** (fixed): bet the side the model favours when |p − mid| ≥ 0.10, at that side's ask
  at T, $10 per bet, Kalshi taker fee, hold to settlement. Mean return per bet and t clustered by
  series-day, as in `backtest/pm_favourites.py`.

## Procedure and pass rule
1. Run all 14 models in both modes on the **choose half** and the August sample.
2. **Pick one model and one mode** on the choose half: highest skill. Report all 28 combinations.
3. **Confirm** with that one combination only on the confirm half. Pass if skill > 0 with the 95%
   interval above 0, **and** the trading rule has mean return > 0 with t ≥ 2 and at least 100 bets.
4. **Hindsight check:** if the chosen model's skill on the August sample is more than twice its skill
   on the choose half, assume it knows August outcomes and report that.
A pass leads to a forward test (paper bets recorded on open markets, settled later), including the two
web-search models, and then to UK-legal venues (Betfair, Smarkets). Kalshi itself is closed to UK residents.

## Known risks
- ⚠️ The models' training cutoffs are not published; the August comparison is only a rough check.
- ⚠️ Many Kalshi markets are numeric strikes (weather, prices, scores) where the market sees live data
  and a model without web access cannot; results are reported by category, but the verdict uses all.
- ⚠️ Mode M can only beat the market by disagreeing with it; a model that copies the price scores skill ≈ 0.
- Trap: using `close_time` or `settlement_ts` for T (they leak early closes). Use the scheduled end.

## Changes
- 2026-11-18, before any model answer was collected: the user added paid credits, so the five paid
  models (Gemini Decision 1, GPT-6 Luna Decisions, Microsoft Decision-1, Liquid d1, Jev 1.14) join the
  test under the same rules: 19 models, 38 model-and-mode combinations on the choose half.
- 2026-11-18, after the first download and **on counts only** (no model answer, no outcome looked at):
  only 235 of 1,000 choose-half events had a price 24 hours before the end, 96% of them sports. Most
  Kalshi markets live for 15 minutes to a day (crypto and commodity "up in the next 15 minutes",
  same-day sports) and did not exist at the snapshot. New sample: markets **open at least 2 days before
  the scheduled end**; up to 70% of each half from the judgement categories (Economics, Politics,
  Elections, Entertainment, Science and Technology, Mentions, Financials: about 650 events in the choose
  half), the rest from Sports, Weather, Crypto and Commodities, each by hash. Results are still
  reported by category; the verdict uses all events.

## Choose-half result (run 2026-11-18): no model beats the market
Usable events (a quoted price with a gap of at most 10c, 24 hours before the scheduled end): choose 356
of 1,000 (242 sports), August 169 of 500. 19,976 answers cost $0.28 in total.

**Every one of the 38 model-and-mode combinations scored worse than the market price** (negative skill),
on the choose half and on August. Market Brier score 0.190 (choose), 0.160 (August).

| Best five, choose half | Mode | Brier | Skill vs market (95% interval) | Correlation with market |
|---|---|---|---|---|
| Liquid d1.2-Ultra | told the price | 0.192 | −0.9% (−6.1% to +2.6%) | 0.98 |
| Baseten Mammoth-1 | told the price | 0.192 | −1.1% (−5.7% to +2.2%) | 0.96 |
| GPT-6 Luna Decisions | told the price | 0.200 | −4.8% (−10.0% to −0.7%) | 0.94 |
| Microsoft Decision-1.1 | told the price | 0.201 | −5.8% | 0.94 |
| Nace Drex Max Finance | told the price | 0.203 | −6.7% | 0.97 |

- **Blind (not told the price), every model was 20–37% worse than the market**; Gemini Decision 1 −28%,
  GPT-6 Luna Decisions −31%. Their forecasts correlate only 0.4–0.6 with the price.
- **Told the price, the best models mostly repeat it** (correlation 0.96–0.98) and lose a little where they
  move away from it. A model that disagrees with the market is, on average, wrong.
- Convai Laya and ScaleDown Classify-1 gave answers unrelated to the question (correlation with the market near zero).
- The trading rule's mean returns are noise (t below 1.3 for every model with a positive mean; it bets
  mostly on cheap long shots, a few of which won).
- Hindsight check: no model did better on August than on the choose half, so no sign they knew outcomes.

**Chosen for the confirm half:** Liquid d1.2-Ultra, told the price (highest skill, still negative). Its
pass requires skill above zero with the interval above zero on the confirm half; given −0.9% here, a
pass is very unlikely. The confirm run waits for the user's go-ahead.
