"""Decision-model forecasts against Kalshi prices (backtest/DESIGN_DECISION_FORECASTS.md).

    uv run python -W ignore -m backtest.decision_forecasts sample     # events, rules text, snapshot prices
    uv run python -W ignore -m backtest.decision_forecasts ask [--models a,b] [--halves choose,august|confirm]
    uv run python -W ignore -m backtest.decision_forecasts choose     # score all models on the choose half
    uv run python -W ignore -m backtest.decision_forecasts confirm MODEL MODE   # ONCE, on the confirm half

Files: data/decisions/events.parquet, data/decisions/answers.jsonl (every answer, resumable).
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import duckdb
import httpx
import numpy as np
import pandas as pd

from backtest.pm_favourites import fee, tstat
from ingest.pm.kalshi import Client, batches, ts

D = Path("data/decisions")
MARKETS = "data/pm/kalshi/markets/*.jsonl.gz"
HALVES = {"choose": ("2026-10-20 00:00:00", "2026-11-02 23:59:59"), "confirm": ("2026-11-03 00:00:00", "2026-11-15 23:59:59"),
          "august": ("2026-08-01 00:00:00", "2026-08-31 23:59:59")}
N_EVENTS = {"choose": 1000, "confirm": 1000, "august": 500}
MIN_VOLUME, MAX_GAP, LEAD_S = 1000.0, 0.10, 24 * 3600
MIN_OPEN_DAYS = 2                      # change 2026-11-18: the market must exist well before the snapshot
NUMERIC_CATS = ["Sports", "Crypto", "Commodities", "Climate and Weather"]   # live-data markets: filled after the rest
MAX_JUDGEMENT_SHARE = 0.7
MODELS = ["baseten/elephant-1", "baseten/joey-1", "baseten/mammoth-1", "convaiinnovations/laya",
          "heygen/heygen-decision-1", "heygen/heygen-decision-1-turbo", "liquid/d1-ultra", "liquid/d1.2",
          "liquid/d1.2-ultra", "microsoft/decision-1.1", "nace-ai/drex-max-finance", "nace-ai/drex-max-v1.1",
          "nace-ai/drex-v2.0", "scaledown/scaledown-classify-1",
          "google/gemini-decision-1", "openai/gpt-6-luna-decisions", "microsoft/microsoft-decision-1",
          "liquid/d1", "typesafe-ai/jev-1.14.0"]
QUESTION = {"type": "predicate", "name": "yes",
            "instructions": "Based only on the information above and what you know, will this market resolve Yes?"}
GATEWAY = "https://ai-gateway.vercel.sh/v1/decisions"
EDGE, STAKE = 0.10, 10.0
WORKERS = 64


def h(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()


def sample() -> None:
    """Pick events by hash, fetch each market's rules and hourly candles, keep the snapshot at T."""
    D.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"""CREATE TABLE m AS SELECT DISTINCT ON (ticker) ticker, event_ticker, series, title, result,
        CAST(volume_fp AS DOUBLE) volume, coalesce(expected_expiration_time, latest_expiration_time) AS sched,
        CAST(open_time AS TIMESTAMPTZ) AS opened
        FROM read_json_auto('{MARKETS}', union_by_name=true, sample_size=-1)
        WHERE market_type='binary' AND result IN ('yes','no') AND CAST(volume_fp AS DOUBLE) >= {MIN_VOLUME}
          AND NOT starts_with(ticker, 'KXMVE')""")
    cats = pd.DataFrame(json.loads(Path("data/pm/kalshi/series.json").read_text()))[["series", "category"]]
    rows = []
    for half, (a, b) in HALVES.items():
        d = con.execute(f"""SELECT * FROM m WHERE CAST(sched AS TIMESTAMPTZ) BETWEEN TIMESTAMPTZ '{a}+00' AND TIMESTAMPTZ '{b}+00'
            AND opened <= CAST(sched AS TIMESTAMPTZ) - INTERVAL {MIN_OPEN_DAYS} DAY""").df().merge(cats, on="series", how="left")
        d["k"] = d.ticker.map(h)
        d = d.sort_values("k").drop_duplicates("event_ticker")          # one market per event, chosen by hash
        d["ek"] = d.event_ticker.map(h)
        d = d.sort_values("ek")
        n = N_EVENTS[half]
        other = d[~d.category.isin(NUMERIC_CATS)].head(int(n * MAX_JUDGEMENT_SHARE))   # judgement events first
        rest = d[d.category.isin(NUMERIC_CATS)].head(n - len(other))
        rows.append(pd.concat([other, rest]).assign(half=half))
    ev = pd.concat(rows, ignore_index=True)
    ev["end"] = ev.sched.map(lambda s: ts(str(s)))
    ev["t_snap"] = ev.end - LEAD_S
    cl = Client()
    # rules text: up to 100 markets per request (live tier first, then the historical tier)
    rules: dict[str, str] = {}
    tick = list(ev.ticker)
    for k in range(0, len(tick), 100):
        chunk = tick[k:k + 100]
        for path in ("/markets", "/historical/markets"):
            todo = [t for t in chunk if t not in rules]
            if not todo:
                break
            for m in (cl.get(path, {"tickers": ",".join(todo), "limit": 100}) or {}).get("markets", []):
                rules[m["ticker"]] = (m.get("rules_primary") or "")[:1500]
        print(f"rules {len(rules)}/{len(tick)} ({cl.n} requests, {cl.n429} x 429)", flush=True)
    ev["rules"] = ev.ticker.map(rules).fillna("")
    # candles: 40 recent markets per request, one at a time from the historical tier for the rest
    candles: dict[str, list] = {}
    win = {"period_interval": 60}
    for b in batches([(r.ticker, r.series, r.t_snap) for r in ev.itertuples()], size=40):
        ends = {t: e for t, _, e in b}
        d = cl.get("/markets/candlesticks", {**win, "market_tickers": ",".join(ends),
                                              "start_ts": int(min(ends.values()) - 3 * 86400),
                                              "end_ts": int(max(ends.values()) + 1)}) or {}
        for m in d.get("markets", []):
            if m.get("candlesticks"):
                candles[m["market_ticker"]] = m["candlesticks"]
    print(f"candles by batch: {len(candles)}/{len(ev)} ({cl.n} requests, {cl.n429} x 429)", flush=True)
    for n, r in enumerate(ev[~ev.ticker.isin(candles)].itertuples(), 1):
        enc = quote(r.ticker, safe="")
        d = cl.get(f"/historical/markets/{enc}/candlesticks",
                   {**win, "start_ts": int(r.t_snap - 3 * 86400), "end_ts": int(r.t_snap + 1)}) or {}
        candles[r.ticker] = d.get("candlesticks", [])
        if n % 100 == 0:
            print(f"single candles {n} ({cl.n} requests, {cl.n429} x 429)", flush=True)
    snaps = {}
    for r in ev.itertuples():
        snap = None
        for x in sorted(candles.get(r.ticker, []), key=lambda x: x["end_period_ts"]):
            a, bb = x.get("yes_ask") or {}, x.get("yes_bid") or {}
            ask, bid = a.get("close_dollars", a.get("close")), bb.get("close_dollars", bb.get("close"))
            if x["end_period_ts"] > r.t_snap or ask is None or bid is None:
                continue
            ask, bid = float(ask), float(bid)
            # the last candle with both sides quoted decides: kept only if its gap is at most 10c
            snap = (x["end_period_ts"], bid, ask) if 0 < bid <= ask < 1 and ask - bid <= MAX_GAP + 1e-9 else None
        snaps[r.ticker] = snap
    ev["snap_t"] = ev.ticker.map(lambda t: snaps[t] and snaps[t][0])
    ev["bid"] = ev.ticker.map(lambda t: snaps[t] and snaps[t][1])
    ev["ask"] = ev.ticker.map(lambda t: snaps[t] and snaps[t][2])
    print(ev.groupby("half").agg(events=("ticker", "size"), with_rules=("rules", lambda s: (s.str.len() > 0).sum()),
                                 with_snapshot=("bid", lambda s: s.notna().sum())).to_string())
    ev.to_parquet(D / "events.parquet")


def input_text(r, mode: str) -> str:
    t = pd.Timestamp(r.t_snap, unit="s", tz="UTC").strftime("%Y-%m-%d %H:%M UTC")
    e = pd.Timestamp(r.end, unit="s", tz="UTC").strftime("%Y-%m-%d %H:%M UTC")
    s = f"Prediction market: {r.title}\nRules: {r.rules}\nToday's date is {t}. The event resolves on {e}."
    if mode == "M":
        s += f"\nThe market currently prices Yes at {round(100 * (r.bid + r.ask) / 2)}%."
    return s


def ask(models: list[str], halves: list[str]) -> None:
    ev = pd.read_parquet(D / "events.parquet")
    ev = ev[ev.half.isin(halves) & ev.bid.notna() & (ev.rules.str.len() > 0)]
    path = D / "answers.jsonl"
    done = set()
    if path.exists():
        for line in path.read_text().splitlines():
            x = json.loads(line)
            if x.get("p") is not None:
                done.add((x["model"], x["mode"], x["ticker"]))
    jobs = [(m, mode, r) for m in models for mode in ("B", "M") for r in ev.itertuples() if (m, mode, r.ticker) not in done]
    print(f"{len(jobs)} questions to ask", flush=True)
    c = httpx.Client(headers={"Authorization": "Bearer proxy-injected", "Content-Type": "application/json"}, timeout=120)
    lock = threading.Lock()
    cost = [0.0, 0, 0]

    def one(job):
        m, mode, r = job
        x = {"model": m, "mode": mode, "ticker": r.ticker, "p": None}
        for attempt in range(4):
            try:
                resp = c.post(GATEWAY, json={"model": m, "input": input_text(r, mode), "questions": [QUESTION]})
                d = resp.json()
                if "answers" in d:
                    x["p"] = float(d["answers"][0]["probability"])
                    x["cost"] = float(d["provider_metadata"]["gateway"].get("cost") or 0)
                    break
                x["error"] = str(d.get("error", d))[:200]
                if resp.status_code in (402, 403):
                    break
            except Exception as e:
                x["error"] = str(e)[:200]
            time.sleep(2 ** attempt)
        with lock:
            with path.open("a") as f:
                f.write(json.dumps(x) + "\n")
            cost[0] += x.get("cost", 0)
            cost[1] += 1
            cost[2] += x["p"] is None
            if cost[1] % 2000 == 0:
                print(f"{cost[1]}/{len(jobs)} ${cost[0]:.3f} errors {cost[2]}", flush=True)

    with ThreadPoolExecutor(WORKERS) as ex:      # paid tier: Vercel's own limits are lifted; 429s retry with backoff
        list(ex.map(one, jobs))
    print(f"done: {cost[1]} answers, ${cost[0]:.3f}, errors {cost[2]}")


def load(half: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    ev = pd.read_parquet(D / "events.parquet")
    ev = ev[(ev.half == half) & ev.bid.notna() & (ev.rules.str.len() > 0)].copy()
    ev["mid"] = (ev.bid + ev.ask) / 2
    ev["y"] = (ev.result == "yes").astype(float)
    ev["cluster"] = ev.series + "|" + pd.to_datetime(ev.end, unit="s").dt.strftime("%Y-%m-%d")
    a = pd.DataFrame([json.loads(x) for x in (D / "answers.jsonl").read_text().splitlines()])
    a = a[a.p.notna()].drop_duplicates(["model", "mode", "ticker"], keep="last")
    return ev, a


def skill_ci(y, p, q, cl, n=1000, seed=7) -> tuple[float, float, float]:
    bm, bq = (p - y) ** 2, (q - y) ** 2
    s = 1 - bm.sum() / bq.sum()
    g = pd.DataFrame({"m": bm, "q": bq, "c": cl}).groupby("c").sum()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(g), (n, len(g)))
    sk = 1 - g.m.to_numpy()[idx].sum(1) / g.q.to_numpy()[idx].sum(1)
    return float(s), float(np.percentile(sk, 2.5)), float(np.percentile(sk, 97.5))


def trades(e: pd.DataFrame) -> pd.Series:
    """Bet the side the model favours when it differs from the mid by >= EDGE, at that side's ask."""
    out = {}
    for r in e.itertuples():
        if abs(r.p - r.mid) < EDGE:
            continue
        side_yes = r.p > r.mid
        price = r.ask if side_yes else 1 - r.bid
        if not 0 < price < 1:
            continue
        n = math.floor(STAKE / price)
        win = r.y == 1.0 if side_yes else r.y == 0.0
        out[r.Index] = (n * win - n * price - fee(n, price)) / (n * price)
    return pd.Series(out, dtype=float)


def score(half: str, only: tuple[str, str] | None = None) -> pd.DataFrame:
    ev, a = load(half)
    rows = []
    for (m, mode), g in a.groupby(["model", "mode"]):
        if only and (m, mode) != only:
            continue
        e = ev.join(g.set_index("ticker").p, on="ticker", how="inner")
        if len(e) < 50:
            continue
        e["p"] = e.p.clip(0.01, 0.99)
        s, lo, hi = skill_ci(e.y.to_numpy(), e.p.to_numpy(), e.mid.to_numpy(), e.cluster.to_numpy())
        r = trades(e)
        rows.append({"model": m, "mode": mode, "events": len(e), "brier_model": ((e.p - e.y) ** 2).mean(),
                     "brier_market": ((e.mid - e.y) ** 2).mean(), "skill": s, "ci_lo": lo, "ci_hi": hi,
                     "corr_with_market": np.corrcoef(e.p, e.mid)[0, 1], "bets": len(r),
                     "mean_ret": r.mean() if len(r) else np.nan,
                     "t": tstat(r, e.loc[r.index, "cluster"]) if len(r) > 1 else np.nan})
    return pd.DataFrame(rows).sort_values("skill", ascending=False)


def by_category(half: str, model: str, mode: str) -> pd.DataFrame:
    ev, a = load(half)
    g = a[(a.model == model) & (a["mode"] == mode)].set_index("ticker").p
    e = ev.join(g, on="ticker", how="inner")
    e["p"] = e.p.clip(0.01, 0.99)
    return e.groupby(e.category.fillna("?")).apply(lambda x: pd.Series({
        "events": len(x), "brier_model": ((x.p - x.y) ** 2).mean(), "brier_market": ((x.mid - x.y) ** 2).mean(),
        "skill": 1 - ((x.p - x.y) ** 2).sum() / ((x.mid - x.y) ** 2).sum()})).sort_values("events", ascending=False)


if __name__ == "__main__":
    pd.set_option("display.width", 250, "display.max_rows", 100, "display.float_format", lambda x: f"{x:,.4f}")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "choose"
    arg = dict(a.split("=", 1) for a in sys.argv[2:] if "=" in a)
    if cmd == "sample":
        sample()
    elif cmd == "ask":
        ask(arg.get("--models", ",".join(MODELS)).split(","), arg.get("--halves", "choose,august").split(","))
    elif cmd == "choose":
        for half in ("choose", "august"):
            print(f"\n== {half} ==")
            print(score(half).to_string(index=False))
    elif cmd == "confirm":
        if "--final" not in sys.argv:
            raise SystemExit("the confirm half runs once, with --final, for the one chosen model and mode")
        model, mode = sys.argv[2], sys.argv[3]
        ask([model], ["confirm"])
        print(score("confirm", (model, mode)).to_string(index=False))
        print(by_category("confirm", model, mode).to_string())
