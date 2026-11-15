"""Kalshi public market data (no account): series, settled binary markets, hourly bid/ask candles.

    uv run python -W ignore -m ingest.pm.kalshi series
    uv run python -W ignore -m ingest.pm.kalshi markets      # every settled market of every series
    uv run python -W ignore -m ingest.pm.kalshi candles      # last 8 days of hourly candles, eligible markets
    uv run python -W ignore -m ingest.pm.kalshi status

Every step is resumable: finished series / tickers are recorded and skipped. Polite: one shared rate
limit, back off on 429/5xx, stop on 403. Files under data/pm/kalshi/ (see backtest/DESIGN_PM_FAVOURITES.md).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE = "https://api.elections.kalshi.com/trade-api/v2"
UA = "trading-rns-research/0.1 (personal non-commercial research; python-httpx)"
ROOT = Path("data/pm/kalshi")
RATE_PER_S = 5.0
MIN_VOLUME = 1000.0            # contracts, lifetime (design: eligible market)
MAX_CANDLE_MARKETS = 150_000   # design: fixed random sample above this
WINDOW_DAYS = 8
KEEP = ("ticker", "event_ticker", "market_type", "open_time", "close_time", "expected_expiration_time",
        "latest_expiration_time", "settlement_ts", "result", "settlement_value_dollars", "volume_fp",
        "last_price_dollars", "can_close_early", "status", "title")


class Blocked(Exception):
    pass


class Client:
    """One shared, adaptive rate limit: halves on 429 (never below 1/s), creeps back up slowly.
    Stops (Blocked) on 403 or when 429s persist: we slow down, we never work around a limit."""

    def __init__(self):
        self.c = httpx.Client(headers={"User-Agent": UA}, timeout=60)
        self.lock = threading.Lock()
        self.next_at = 0.0
        self.rate = RATE_PER_S
        self.n = self.n429 = self.ok_run = 0

    def get(self, path: str, params: dict | None = None) -> dict | None:
        for attempt in range(9):
            with self.lock:
                now = time.monotonic()
                wait = max(0.0, self.next_at - now)
                self.next_at = max(now, self.next_at) + 1.0 / self.rate
            if wait:
                time.sleep(wait)
            try:
                r = self.c.get(BASE + path, params=params)
            except httpx.HTTPError:
                time.sleep(min(60, 2 ** attempt))
                continue
            self.n += 1
            if r.status_code == 200:
                with self.lock:
                    self.ok_run += 1
                    if self.ok_run >= 300:
                        self.ok_run, self.rate = 0, min(RATE_PER_S, self.rate * 1.1)
                return r.json()
            if r.status_code == 404:
                return None
            if r.status_code == 403:
                raise Blocked(f"403 for {path}: stopping, not evading")
            if r.status_code == 429:
                with self.lock:
                    self.n429 += 1
                    self.ok_run, self.rate = 0, max(1.0, self.rate * 0.5)
                    self.next_at = time.monotonic() + min(60, 3 * 2 ** attempt)   # everyone pauses
            else:
                time.sleep(min(60, 2 ** attempt))
        raise Blocked(f"gave up on {path} (rate limit or server errors persisted)")


def ts(s: str | None) -> float | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def scheduled_end(m: dict) -> float | None:
    return ts(m.get("expected_expiration_time")) or ts(m.get("latest_expiration_time"))


def _done(path: Path) -> set[str]:
    return set(path.read_text().split()) if path.exists() else set()


def cmd_series(cl: Client) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    d = cl.get("/series")
    rows = [{"series": s["ticker"], "category": s.get("category"), "frequency": s.get("frequency"),
             "fee_type": s.get("fee_type"), "fee_multiplier": s.get("fee_multiplier"), "title": s.get("title")}
            for s in d["series"]]
    (ROOT / "series.json").write_text(json.dumps(rows))
    print(f"{len(rows)} series")


def _series_markets(cl: Client, series: str) -> list[dict]:
    out: dict[str, dict] = {}
    for path, extra in (("/historical/markets", {}), ("/markets", {"status": "settled"})):
        cursor = None
        while True:
            p = {"series_ticker": series, "limit": 1000, **extra}
            if cursor:
                p["cursor"] = cursor
            d = cl.get(path, p) or {}
            for m in d.get("markets", []):
                if m.get("mve_collection_ticker") or m["ticker"].startswith("KXMVE"):
                    continue
                row = {k: m.get(k) for k in KEEP}
                row["series"] = series
                row["title"] = (row.get("title") or "")[:100]
                out[m["ticker"]] = row
            cursor = d.get("cursor")
            if not cursor or len(d.get("markets", [])) < 1000:
                break
    return list(out.values())


def cmd_markets(cl: Client) -> None:
    series = [s["series"] for s in json.loads((ROOT / "series.json").read_text())
              if not s["series"].startswith("KXMVE")]
    done_path = ROOT / "markets.done"
    done = _done(done_path)
    todo = [s for s in series if s not in done]
    print(f"{len(todo)} of {len(series)} series to list", flush=True)
    mdir = ROOT / "markets"
    mdir.mkdir(exist_ok=True)
    wlock = threading.Lock()
    shard = gzip.open(mdir / f"m-{int(time.time())}.jsonl.gz", "at")
    count = [0, 0, time.time()]

    def work(s: str) -> None:
        rows = _series_markets(cl, s)
        with wlock:
            for r in rows:
                shard.write(json.dumps(r) + "\n")
            with done_path.open("a") as f:
                f.write(s + "\n")
            shard.flush()                      # rows are on disk before the series is marked done
            count[0] += 1
            count[1] += len(rows)
            if time.time() - count[2] > 60:
                count[2] = time.time()
                print(f"{count[0]} series, {count[1]} markets, {cl.n} requests, {cl.n429} x 429, rate {cl.rate:.1f}/s", flush=True)

    try:
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(work, todo))
    finally:
        shard.close()
    print(f"done: {count[0]} series, {count[1]} markets")


def load_markets():
    import duckdb
    con = duckdb.connect()
    con.execute(f"""CREATE TABLE m AS SELECT DISTINCT ON (ticker) * FROM
        read_json_auto('{ROOT}/markets/*.jsonl.gz', union_by_name=true, sample_size=-1)""")
    return con


def eligible(con) -> list[tuple]:
    """Design: binary, settled, lifetime volume >= 1,000; fixed hash sample above the cap."""
    rows = con.execute(f"""SELECT ticker, series, expected_expiration_time, latest_expiration_time, open_time
        FROM m WHERE market_type='binary' AND CAST(volume_fp AS DOUBLE) >= {MIN_VOLUME}
        AND result IS NOT NULL AND result <> ''""").fetchall()
    if len(rows) > MAX_CANDLE_MARKETS:
        rows.sort(key=lambda r: hashlib.sha1(r[0].encode()).hexdigest())
        rows = rows[:MAX_CANDLE_MARKETS]
    return rows


def batches(rows: list[tuple], size: int = 40, span_s: float = 86400.0) -> list[list[tuple]]:
    """Group (ticker, series, end_ts) by scheduled end so one request covers up to `size` markets
    whose ends lie within `span_s` of each other (keeps each reply under the 10,000-candle cap)."""
    rows = sorted(rows, key=lambda r: r[2])
    out: list[list[tuple]] = []
    for r in rows:
        if out and len(out[-1]) < size and r[2] - out[-1][0][2] <= span_s:
            out[-1].append(r)
        else:
            out.append([r])
    return out


def cmd_candles(cl: Client) -> None:
    con = load_markets()
    rows = []
    for ticker, series, exp, latest, _ in eligible(con):
        end = ts(str(exp) if exp else None) or ts(str(latest) if latest else None)
        if end:
            rows.append((ticker, series, end))
    done_path = ROOT / "candles.done"
    done = _done(done_path)
    todo = batches([r for r in rows if r[0] not in done])
    print(f"{sum(map(len, todo))} of {len(rows)} eligible markets need candles ({len(todo)} requests)", flush=True)
    cdir = ROOT / "candles"
    cdir.mkdir(exist_ok=True)
    wlock = threading.Lock()
    shard = gzip.open(cdir / f"c-{int(time.time())}.jsonl.gz", "at")
    count = [0, 0, time.time()]

    def num(d: dict, k: str):
        v = d.get(k + "_dollars", d.get(k))
        return None if v is None else float(v)

    def work(batch: list[tuple]) -> None:
        ends = {t: e for t, _, e in batch}
        d = cl.get("/markets/candlesticks",
                   {"market_tickers": ",".join(ends), "start_ts": int(min(ends.values()) - WINDOW_DAYS * 86400),
                    "end_ts": int(max(ends.values()) + 3600), "period_interval": 60}) or {}
        out = []
        for m in d.get("markets", []):
            ticker = m.get("market_ticker")
            end = ends.get(ticker)
            if end is None:
                continue
            for c in m.get("candlesticks", []):
                if not end - WINDOW_DAYS * 86400 <= c["end_period_ts"] <= end + 3600:
                    continue
                a, b, p = c.get("yes_ask") or {}, c.get("yes_bid") or {}, c.get("price") or {}
                out.append({"ticker": ticker, "t": c["end_period_ts"],
                            "ask_o": num(a, "open"), "ask_c": num(a, "close"),
                            "bid_o": num(b, "open"), "bid_c": num(b, "close"),
                            "last": num(p, "close"), "vol": float(c.get("volume_fp", c.get("volume", 0)) or 0)})
        with wlock:
            for o in out:
                shard.write(json.dumps(o) + "\n")
            shard.flush()                      # rows are on disk before the markets are marked done
            with done_path.open("a") as f:
                f.write("".join(t + "\n" for t in ends))
            count[0] += len(ends)
            count[1] += len(out)
            if time.time() - count[2] > 60:
                count[2] = time.time()
                print(f"{count[0]} markets, {count[1]} candles, {cl.n} requests, {cl.n429} x 429", flush=True)

    try:
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(work, todo))
    finally:
        shard.close()
    print(f"done: {count[0]} markets, {count[1]} candles")


def cmd_status() -> None:
    con = load_markets()
    print(con.execute("""SELECT count(*) markets, count(DISTINCT series) series,
        count(*) FILTER (WHERE market_type='binary' AND CAST(volume_fp AS DOUBLE) >= 1000) eligible,
        min(close_time) first_close, max(close_time) last_close FROM m""").df().to_string(index=False))
    print(len(_done(ROOT / "candles.done")), "markets with candles fetched")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        cmd_status()
    else:
        {"series": cmd_series, "markets": cmd_markets, "candles": cmd_candles}[cmd](Client())
        print("finished", datetime.now(timezone.utc).isoformat(timespec="seconds"))
