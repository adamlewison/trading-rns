"""Live trial of UK retail offers (backtest/DESIGN_UK_RETAIL_OFFERS.md, "Live trial").

    uv run python -W ignore -m paper.retail_offers daily     # droplet timer: scan, read, label, status
    uv run python -W ignore -m paper.retail_offers status    # print the latest status (from B2)

Records every retail offer launched since the trial began, what an application would have made if filled
in full, and what the user actually applied for and received (paper/retail_offers_log.csv, edited by an
agent when the user reports an application). Nothing is traded by this code: applications are made by
hand on PrimaryBid or a RetailBook / WRAP / REX broker.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.uk_news import spread_uk
from backtest.uk_retail_offers import SAME_OFFER_DAYS, SLIP, adverse_fill, known_and_next
from classify import uk_retail_offers as lab
from ingest.uk.common import session

TRIAL_START = date(2026, 11, 16)
LOG = Path("paper/retail_offers_log.csv")
B2_STATUS = "trading-rns/paper/retail_offers.md"
SCAN_DAYS = 3


def scan() -> None:
    """Index the last few days of announcements (fresh pages, not the cache) and fetch new retail-offer texts."""
    from ingest.uk.announcements.run import get_source
    from ingest.uk.announcements.store import insert_index
    src = get_source()
    for k in range(SCAN_DAYS, -1, -1):
        d = date.today() - timedelta(days=k)
        rows = []
        for page in range(1, 201):
            lp = src.day_page(d, page, use_cache=False)       # recent days' pages keep growing: never the cache
            rows += lp.rows
            if not lp.has_next or not lp.rows:
                break
        with session() as con:
            n = insert_index(con, rows)
        print(f"{d}: {len(rows)} announcements, {n} new", flush=True)
    with session(read_only=True) as con:
        todo = con.execute(f"""SELECT i.ann_id, i.url FROM ann_index i LEFT JOIN ann_bodies b USING (ann_id)
            WHERE b.ann_id IS NULL AND i.published_uk >= '{TRIAL_START - timedelta(days=1)}'
              AND regexp_matches(lower(i.headline), '{lab.HEADLINE_RE}')""").fetchall()
    for ann_id, url in todo:
        status, text = src.body(url)
        with session() as con:
            con.execute("INSERT OR REPLACE INTO ann_bodies VALUES (?,?,?,?,?)",
                        [ann_id, text or None, len(text or ""), status, datetime.now()])
    print(f"{len(todo)} retail-offer texts fetched", flush=True)


def label() -> None:
    from core.backup import get_store
    if not lab.OUT.exists():                      # earlier labels live in B2: never pay for them twice
        lab.OUT.parent.mkdir(parents=True, exist_ok=True)
        old = get_store().get(lab.B2_NAME)
        if old:
            lab.OUT.write_bytes(old)
    lab.run(max_gbp=0.5)


def prices(tidm: str) -> pd.DataFrame:
    import yfinance as yf
    with session(read_only=True) as con:
        r = con.execute("SELECT yahoo_ticker FROM universe WHERE tidm = ?", [tidm]).fetchone()
    df = yf.Ticker((r and r[0]) or f"{tidm}.L").history(period="6mo", auto_adjust=False, actions=False)
    if df.empty:
        return df
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df[df.Volume > 0]


def offers() -> pd.DataFrame:
    L = lab.labels()
    with session(read_only=True) as con:
        idx = con.execute(f"""SELECT ann_id, tidm, headline, published_uk FROM ann_index
            WHERE published_uk >= '{TRIAL_START}' AND regexp_matches(lower(headline), '{lab.HEADLINE_RE}')""").df()
    d = L.merge(idx, on="ann_id").sort_values("published_uk")
    d["published_uk"] = pd.to_datetime(d.published_uk)
    res = d[d.kind == "result"]
    rows, last = [], {}
    for r in d[d.kind == "launch"].itertuples():
        if r.tidm in last and (r.published_uk - last[r.tidm]).days <= SAME_OFFER_DAYS:
            continue
        last[r.tidm] = r.published_uk
        rr = res[(res.tidm == r.tidm) & (res.published_uk > r.published_uk)]
        adm = rr.admission_date.dropna()
        admission = adm.iloc[-1] if len(adm) else r.admission_date
        row = {"tidm": r.tidm, "launch": r.published_uk, "platform": r.platform, "issue_p": r.issue_price_pence,
               "closes": f"{'' if pd.isna(r.offer_close_date) else r.offer_close_date.date()} {r.offer_close_time or ''}".strip(),
               "existing_only": r.existing_holders_only, "needs_gm": r.conditional_on_general_meeting,
               "min_gbp": r.min_application_gbp, "admission": admission, "cancelled": bool(rr.cancelled.any())}
        try:
            px = prices(r.tidm)
        except Exception as e:
            px = pd.DataFrame()
            row["note"] = f"no prices: {str(e)[:60]}"
        if not px.empty and r.issue_price_pence:
            dates = px.index.to_numpy().astype("datetime64[D]")
            k, nx = known_and_next(dates, r.published_uk)
            if k is not None:
                last_close = float(px.Close.iloc[k])
                row["last_close_p"] = last_close
                row["discount"] = 1 - r.issue_price_pence / last_close
                turnover = float((px.Close * px.Volume).iloc[max(0, k - 19):k + 1].mean() / 100)
                first = float(px.Close.iloc[nx]) if nx is not None else None
                if pd.notna(admission):
                    s = int(np.searchsorted(dates, np.datetime64(admission.date(), "D")))
                    if s < len(px):
                        net = float(px.Open.iloc[s]) / r.issue_price_pence - 1 - float(spread_uk(np.array([turnover]))[0]) / 2 - SLIP
                        row.update(sale_day=px.index[s].date(), sale_open_p=float(px.Open.iloc[s]), paper_net=net,
                                   adverse_fill=adverse_fill(first, r.issue_price_pence, net))
        rows.append(row)
    return pd.DataFrame(rows)


def status_text() -> str:
    o = offers()
    out = [f"# UK retail offers: live trial since {TRIAL_START} (updated {datetime.now():%Y-%m-%d %H:%M} UK)", ""]
    if o.empty:
        return "\n".join(out + ["No retail offer launched since the trial began."])
    now = pd.Timestamp.now()
    o["open_to_new"] = o.existing_only != True                                            # noqa: E712
    cols = ["tidm", "launch", "platform", "issue_p", "discount", "closes", "min_gbp", "needs_gm", "admission"]
    cur = o[o.open_to_new & (o.admission.isna() | (o.admission >= now.normalize()))]
    out += ["## Launched and not yet admitted (open to new investors)", "",
            cur[[c for c in cols if c in cur]].to_string(index=False) if len(cur) else "none", ""]
    done = o[o.get("paper_net").notna()] if "paper_net" in o else o.iloc[:0]
    out += [f"## Finished offers: {len(done)} ({int(done.open_to_new.sum()) if len(done) else 0} open to new investors)", ""]
    if len(done):
        d = done[done.open_to_new]
        out += [d[["tidm", "launch", "platform", "issue_p", "discount", "sale_day", "sale_open_p", "paper_net", "adverse_fill"]].to_string(index=False), "",
                f"Paper result if filled in full: mean {d.paper_net.mean():+.1%}, median {d.paper_net.median():+.1%}, "
                f"{(d.paper_net > 0).mean():.0%} winners. With the backtest's scale-back rule: {(d.paper_net * d.adverse_fill).mean():+.1%}.", ""]
    out += ["## The user's applications", ""]
    if LOG.exists() and len(pd.read_csv(LOG)):
        g = pd.read_csv(LOG)
        g["filled"] = g.received_gbp / g.applied_gbp
        g["profit_gbp"] = np.where(g.sold_price_p.notna(), g.received_gbp * (g.sold_price_p / g.issue_p - 1), np.nan)
        out += [g.to_string(index=False), "",
                f"{len(g)} applications; average share received {g.filled.mean():.0%}; "
                f"profit on sold positions £{g.profit_gbp.sum():,.2f} (before selling costs)."]
    else:
        out += ["none recorded yet"]
    n_excl = int((~o.open_to_new).sum())
    out += ["", f"Offers for existing holders only (not applied for): {n_excl}"]
    return "\n".join(out)


def daily() -> None:
    from core.backup import get_store
    scan()
    label()
    text = status_text()
    get_store().put(B2_STATUS, text.encode())
    print(text)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "daily":
        daily()
    else:
        from core.backup import get_store
        s = get_store().get(B2_STATUS)
        print(s.decode() if s else "no status yet: the droplet timer has not run")
