"""Trading 212 practice account: the four paper factor portfolios placed as real (practice) orders.

    uv run python -W ignore -m paper.t212 check      # key works? account currency and cash; ticker coverage
    uv run python -W ignore -m paper.t212 daily      # paper.factors daily calls this after its own work
    uv run python -W ignore -m paper.t212 status     # print the practice-account record

One account holds all four portfolios (V, Q, M, QVM). Each portfolio has its own virtual book: $6,000 to
start, shares per ticker, cash. On each rebalance day (the day paper.factors records new picks):
  1. every portfolio's target is its value / 30 per pick, in shares at today's close;
  2. each portfolio's change per ticker is target minus held; the four are added up per ticker;
  3. only the net per ticker is sent as a market order (sells first), queued for the next US open;
  4. on later runs each order's fills are read back and every portfolio's change on that ticker is
     booked at the fill price, scaled by the share of the net order that filled (so the four books
     always add up to what the account holds).
Each portfolio is also charged the design's FX cost (0.15%) on its own full trades. Fills already carry
the spread and slippage. Practice host only: this module never talks to the live account.
Keys: T212_API_KEY + T212_API_SECRET (droplet .env); in a cloud session the proxy adds them instead."""
from __future__ import annotations

import argparse
import base64
import os
import time
from datetime import date, datetime

import httpx
import numpy as np
import pandas as pd

from core.log import get_logger

log = get_logger("t212")
BASE = "https://demo.trading212.com/api/v0"          # practice account only, by design
START_USD = 6000.0
N_HOLD = 30
FX_SIDE = 0.0015                                     # Trading 212 FX fee each way (design costs)
MIN_TRADE_USD = 1.0                                  # skip dust changes per portfolio and ticker
SETTLE_GIVE_UP_DAYS = 5                              # an order not found after this long counts as unfilled
ACTIVE = {"LOCAL", "UNCONFIRMED", "CONFIRMED", "NEW", "CANCELLING", "PARTIALLY_FILLED", "REPLACING"}
STATUS_KEY = "trading-rns/paper/t212.md"



# ---------------------------------------------------------------- pure logic (tested)

def ticker_map(instruments: list[dict]) -> dict[str, str]:
    """Our (Yahoo) US ticker -> Trading 212 ticker, from the instrument list (US stocks only)."""
    out: dict[str, str] = {}
    for i in instruments:
        t = i.get("ticker") or ""
        if i.get("type") != "STOCK" or i.get("currencyCode") != "USD" or not t.endswith("_US_EQ"):
            continue
        keys = {t[: -len("_US_EQ")].replace("_", "-").upper()}
        if i.get("shortName"):
            keys.add(i["shortName"].upper().replace(".", "-").replace("/", "-"))
        for k in keys:
            out.setdefault(k, t)
    return out


def plan(held: pd.DataFrame, cash: dict[str, float], picks: pd.DataFrame, close: pd.Series,
         min_trade: float = MIN_TRADE_USD) -> pd.DataFrame:
    """Each portfolio's share change per ticker to reach equal weight in its new picks.
    held: variant, ticker, shares; picks: variant, ticker; close: price per ticker (today)."""
    rows = []
    for v in sorted(set(picks.variant) | set(held.variant)):
        h = held[held.variant == v].groupby("ticker").shares.sum()
        h = h[h.abs() > 1e-9]
        value = cash.get(v, START_USD) + float((h * close.reindex(h.index)).fillna(0).sum())
        names = list(picks[picks.variant == v].ticker)
        target = pd.Series({t: value / N_HOLD / close[t] for t in names if t in close and close[t] > 0})
        for t in sorted(set(target.index) | set(h.index)):
            d = float(target.get(t, 0.0) - h.get(t, 0.0))
            if t in close and abs(d) * close[t] >= min_trade:
                rows.append((v, t, d, float(close[t])))
    return pd.DataFrame(rows, columns=["variant", "ticker", "delta", "ref_price"])


def allocate(intents: pd.DataFrame, orders: pd.DataFrame) -> pd.DataFrame:
    """Book each portfolio's change at its ticker's fill. orders: ticker, qty, filled_qty, fill_price
    (qty 0 / missing = the four changes cancelled out: booked in full at the reference price)."""
    o = orders.set_index("ticker")
    rows = []
    for r in intents.itertuples():
        if r.ticker in o.index and abs(o.at[r.ticker, "qty"]) > 0:
            q, f, p = o.at[r.ticker, "qty"], o.at[r.ticker, "filled_qty"], o.at[r.ticker, "fill_price"]
            frac = 0.0 if not f or pd.isna(f) else min(1.0, abs(f / q))
            price = p if frac > 0 and pd.notna(p) else r.ref_price
            src = "fill" if frac > 0 else "unfilled"
        else:
            frac, price, src = 1.0, r.ref_price, "netted"
        shares = r.delta * frac
        rows.append((r.variant, r.ticker, shares, float(price), abs(shares * price) * FX_SIDE, src))
    return pd.DataFrame(rows, columns=["variant", "ticker", "shares", "price", "cost_usd", "source"])


def net_orders(intents: pd.DataFrame, tmap: dict[str, str]) -> pd.DataFrame:
    n = intents.groupby("ticker").delta.sum().reset_index().rename(columns={"delta": "qty"})
    n["t212"] = n.ticker.map(tmap)
    n = n[n.qty.abs() > 1e-6]
    return n.sort_values("qty").reset_index(drop=True)          # sells first


# ---------------------------------------------------------------- client

def _utc(s: str) -> pd.Timestamp:
    t = pd.Timestamp(s)
    return t.tz_convert("UTC").tz_localize(None) if t.tzinfo else t


class Client:
    def __init__(self):
        h = {"User-Agent": "trading-rns-paper/1.0"}
        k, s = os.environ.get("T212_API_KEY"), os.environ.get("T212_API_SECRET")
        if k and s:                               # droplet; in a cloud session the proxy adds the header
            h["Authorization"] = "Basic " + base64.b64encode(f"{k}:{s}".encode()).decode()
        self.http = httpx.Client(base_url=BASE, headers=h, timeout=30)

    def _call(self, method: str, path: str, retry: bool = True, **kw):
        for attempt in range(5):
            r = self.http.request(method, path, **kw)
            if r.status_code == 429 and retry:
                reset = float(r.headers.get("x-ratelimit-reset", time.time() + 10))
                time.sleep(min(70, max(2, reset - time.time() + 1)))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"T212 {method} {path}: {r.status_code} {r.text[:200]}")
            return r.json()
        raise RuntimeError(f"T212 {method} {path}: still rate-limited")

    def summary(self):
        return self._call("GET", "/equity/account/summary")

    def instruments(self):
        return self._call("GET", "/equity/metadata/instruments")

    def positions(self):
        return self._call("GET", "/equity/positions")

    def order(self, oid: int):
        return self._call("GET", f"/equity/orders/{oid}")

    def market(self, t212: str, qty: float):
        # not idempotent: a 429 is safe to repeat (refused), anything else is not retried
        return self._call("POST", "/equity/orders/market", json={"ticker": t212, "quantity": qty, "extendedHours": False})

    def history(self, since: datetime) -> list[dict]:
        out, path, params = [], "/equity/history/orders", {"limit": 50}
        while path:
            j = self._call("GET", path, params=params)
            items = j.get("items", [])
            out += items
            oldest = min((_utc(i["order"]["createdAt"]) for i in items), default=None)
            nxt = j.get("nextPagePath")
            if not items or not nxt or (oldest is not None and oldest < pd.Timestamp(since)):
                break
            path, params = nxt.replace("/api/v0", ""), None
            time.sleep(3.1)
        return out


# ---------------------------------------------------------------- database steps

def _session(read_only=False):
    from ingest.us.common import session
    return session(read_only=read_only, memory_limit="300MB")


def holdings(con) -> tuple[pd.DataFrame, dict[str, float]]:
    """Booked shares per portfolio (adjusted for splits since each trade) and cash."""
    b = con.execute("""SELECT b.variant, b.ticker, b.shares * coalesce(
                           (SELECT product(s.ratio) FROM us_splits s WHERE s.ticker = b.ticker
                              AND s.date > b.rebalance AND s.date <= current_date), 1) AS shares,
                           b.shares * b.price + b.cost_usd AS spent
                       FROM t212_book b""").df()
    cash = {v: START_USD - float(g.spent.sum()) for v, g in b.groupby("variant")}
    return b[["variant", "ticker", "shares"]], cash


def settle(c: Client, con) -> int:
    pend = con.execute("SELECT * FROM t212_orders WHERE settled_at IS NULL AND order_id IS NOT NULL").df()
    if pend.empty:
        return 0
    hist = c.history(pd.to_datetime(pend.placed_at).min() - pd.Timedelta(hours=1))
    fills: dict[int, list] = {}
    for it in hist:
        if it.get("fill"):
            fills.setdefault(int(it["order"]["id"]), []).append(it)
    n = 0
    for r in pend.itertuples():
        got = fills.get(int(r.order_id))
        if got:
            qty = sum(abs(g["fill"]["quantity"]) for g in got)
            px = sum(abs(g["fill"]["quantity"]) * g["fill"]["price"] for g in got) / qty if qty else None
            fees = sum(t.get("quantity", 0) for g in got for t in (g["fill"].get("walletImpact") or {}).get("taxes") or [])
            status = got[0]["order"].get("status", "FILLED")
            if status in ACTIVE:
                continue
            con.execute("""UPDATE t212_orders SET status=?, filled_qty=?, fill_price=?, fees_acct=?, settled_at=now()
                           WHERE order_id=?""", [status, float(np.sign(r.qty) * qty), px, float(fees), int(r.order_id)])
            n += 1
            continue
        try:
            st = c.order(int(r.order_id)).get("status")
        except RuntimeError:
            st = None                                   # no longer pending and not in history (yet)
        if st in ACTIVE:
            continue
        if st or (datetime.now() - pd.Timestamp(r.placed_at)).days >= SETTLE_GIVE_UP_DAYS:
            con.execute("UPDATE t212_orders SET status=?, filled_qty=0, settled_at=now() WHERE order_id=?",
                        [st or "NOT_FOUND", int(r.order_id)])
            n += 1
        time.sleep(1.1)
    # book every rebalance whose orders are all settled and not yet booked
    for (reb,) in con.execute("""SELECT DISTINCT rebalance FROM t212_intents WHERE rebalance NOT IN
                                 (SELECT DISTINCT rebalance FROM t212_book)""").fetchall():
        o = con.execute("SELECT * FROM t212_orders WHERE rebalance=?", [reb]).df()
        if (o.settled_at.isna() & o.order_id.notna()).any():
            continue
        b = allocate(con.execute("SELECT * FROM t212_intents WHERE rebalance=?", [reb]).df(), o)
        b.insert(1, "rebalance", reb)
        con.register("b", b)
        con.execute("INSERT INTO t212_book SELECT variant, rebalance, ticker, shares, price, cost_usd, source FROM b")
        log.info("t212: booked rebalance %s (%d portfolio trades)", reb, len(b))
    return n


def rebalance(c: Client, con, day: date) -> None:
    if con.execute("SELECT count(*) FROM t212_intents WHERE rebalance=?", [day]).fetchone()[0]:
        log.info("t212: orders for %s already sent", day)
        return
    unbooked = con.execute("""SELECT count(*) FROM t212_intents WHERE rebalance < ? AND rebalance NOT IN
                              (SELECT DISTINCT rebalance FROM t212_book)""", [day]).fetchone()[0]
    if unbooked:
        log.warning("t212: the previous rebalance is not booked yet (orders unsettled); not trading %s", day)
        return
    picks = con.execute("SELECT variant, ticker FROM paper_picks WHERE entry_date=?", [day]).df()
    if picks.empty:
        return
    held, cash = holdings(con)
    close = con.execute("SELECT ticker, close FROM us_prices_daily WHERE date=? AND NOT is_stale", [day]).df() \
        .set_index("ticker").close
    intents = plan(held, cash, picks, close)
    tmap = ticker_map(c.instruments())
    pos = {p["instrument"]["ticker"]: p["quantity"] for p in c.positions()}
    net = net_orders(intents, tmap)
    missing = sorted(set(net[net.t212.isna()].ticker))
    if missing:                                          # not on Trading 212: those slots stay in cash
        log.warning("t212: not tradable on Trading 212, kept as cash: %s", ", ".join(missing))
        intents = intents[~intents.ticker.isin(missing)]
        net = net[net.t212.notna()]
    intents.insert(0, "rebalance", day)
    con.register("i", intents)
    con.execute("INSERT INTO t212_intents SELECT rebalance, variant, ticker, delta, ref_price FROM i")
    for r in net.itertuples():
        qty = float(r.qty)
        if qty < 0:                                       # never sell more than the account holds
            qty = -min(-qty, float(pos.get(r.t212, 0.0)))
        oid, status, note = None, "SKIPPED", ""
        for dp in (4, 2, 0):
            q = round(qty, dp)
            if q == 0:
                note = "rounds to zero"
                break
            try:
                o = c.market(r.t212, q)
                oid, status, qty = int(o["id"]), o.get("status", "NEW"), q
                break
            except RuntimeError as e:
                note = str(e)[:200]
                if " 400 " not in note:                    # only a validation refusal is safe to retry
                    status = "ERROR"
                    break
        con.execute("INSERT INTO t212_orders VALUES (?,?,?,?,?,?,NULL,NULL,NULL,now(),?,?)",
                    [day, r.ticker, r.t212, qty, oid, status, None if oid else datetime.now(), note])
        time.sleep(1.3)                                   # 50 market orders a minute
    log.info("t212: %d net orders for %s (%d portfolio changes)", len(net), day, len(intents))


def mark(c: Client, con) -> pd.DataFrame:
    held, cash = holdings(con)
    if held.empty:
        return pd.DataFrame()
    pos = c.positions()
    price = {p["instrument"]["ticker"]: p["currentPrice"] for p in pos}
    qty = {p["instrument"]["ticker"]: p["quantity"] for p in pos}
    last = con.execute("SELECT ticker, arg_max(close, date) AS px FROM us_prices_daily GROUP BY 1").df().set_index("ticker").px
    tmap = ticker_map(c.instruments())
    held["t212"] = held.ticker.map(tmap)
    held["px"] = [price.get(t, last.get(k, np.nan)) for t, k in zip(held.t212, held.ticker)]
    book = held.groupby("t212").shares.sum()
    gap = float(sum(abs(book.get(t, 0) - qty.get(t, 0)) * price.get(t, 0) for t in set(book.index) | set(qty)))
    rows = [(date.today(), v, cash.get(v, START_USD) + float((g.shares * g.px).sum()), cash.get(v, START_USD), gap,
             datetime.now()) for v, g in held.groupby("variant")]
    m = pd.DataFrame(rows, columns=["date", "variant", "value_usd", "cash_usd", "recon_gap", "updated_at"])
    con.execute("DELETE FROM t212_marks WHERE date=?", [date.today()])
    con.register("m", m)
    con.execute("INSERT INTO t212_marks SELECT * FROM m")
    return m


def status_text(con) -> str:
    m = con.execute("SELECT * FROM t212_marks WHERE date=(SELECT max(date) FROM t212_marks)").df()
    o = con.execute("""SELECT rebalance, count(*) n, count(*) FILTER (WHERE status='FILLED') filled,
                              count(*) FILTER (WHERE settled_at IS NULL) pending FROM t212_orders GROUP BY 1 ORDER BY 1""").df()
    lines = ["# Trading 212 practice account: the four factor portfolios", ""]
    if m.empty:
        lines.append("Nothing booked yet (first orders go in on the first rebalance day, filled at the next open).")
    else:
        lines += [f"Values on {str(m.date.iloc[0])[:10]} (each started at ${START_USD:,.0f}; FX cost charged per portfolio):", "",
                  "| Portfolio | Value | Return | Cash |", "|---|---|---|---|"]
        for r in m.sort_values("variant").itertuples():
            lines.append(f"| {r.variant} | ${r.value_usd:,.0f} | {r.value_usd / START_USD - 1:+.1%} | ${r.cash_usd:,.0f} |")
        lines += ["", f"Account check: books vs account positions differ by ${m.recon_gap.iloc[0]:,.2f}."]
    for r in o.itertuples():
        lines.append(f"- Rebalance {str(r.rebalance)[:10]}: {r.n} net orders, {r.filled} filled, {r.pending} pending")
    lines += ["", f"Updated {datetime.now():%Y-%m-%d %H:%M} UTC. Practice account only; rules: backtest/DESIGN_FACTORS.md."]
    return "\n".join(lines) + "\n"


def daily(day: date | None = None) -> None:
    from core.backup import get_store
    day = day or date.today()
    c = Client()
    with _session() as con:
        settle(c, con)
        rebalance(c, con, day)
        mark(c, con)
        txt = status_text(con)
    get_store().put(STATUS_KEY, txt.encode())
    log.info("t212: status updated")


def check() -> None:
    c = Client()
    s = c.summary()
    print(f"account {s.get('id')} ({s.get('currency')}): total {s.get('totalValue')}, cash {s.get('cash')}")
    tmap = ticker_map(c.instruments())
    print(f"{len(tmap)} US stock tickers mapped")
    with _session(read_only=True) as con:
        t = con.execute("""SELECT DISTINCT ticker FROM paper_picks""").df().ticker
        if t.empty:
            t = con.execute("""SELECT ticker FROM us_prices_daily WHERE date >= current_date - INTERVAL 10 DAY
                               GROUP BY 1 ORDER BY avg(close*volume) DESC LIMIT 500""").df().ticker
    miss = [x for x in t if x not in tmap]
    print(f"of {len(t)} candidate tickers, {len(t) - len(miss)} are on Trading 212; missing: {', '.join(miss[:40]) or 'none'}")
    print(f"positions held now: {len(c.positions())}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "daily", "status"])
    a = ap.parse_args()
    if a.cmd == "check":
        check()
    elif a.cmd == "daily":
        daily()
    else:
        with _session() as con:
                print(status_text(con))


if __name__ == "__main__":
    main()
