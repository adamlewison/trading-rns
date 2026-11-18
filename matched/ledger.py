"""Your matched-betting record (data/matched_ledger.csv: private, never committed).

    uv run python -W ignore -m matched.ledger add --book "William Hill" --offer "Bet £10 get £10" --kind qualifying --result -0.36
    uv run python -W ignore -m matched.ledger add --book "William Hill" --offer "£10 free bet" --kind free --result 7.80
    uv run python -W ignore -m matched.ledger summary
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

LEDGER = Path("data/matched_ledger.csv")
COLS = ["date", "bookmaker", "offer", "kind", "result_gbp", "note"]


def load() -> pd.DataFrame:
    return pd.read_csv(LEDGER) if LEDGER.exists() else pd.DataFrame(columns=COLS)


def add(book: str, offer: str, kind: str, result: float, note: str = "", when: str | None = None) -> None:
    d = load()
    row = {"date": when or date.today().isoformat(), "bookmaker": book, "offer": offer, "kind": kind,
           "result_gbp": round(result, 2), "note": note}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    pd.concat([d, pd.DataFrame([row])], ignore_index=True)[COLS].to_csv(LEDGER, index=False)


def summary() -> str:
    d = load()
    if d.empty:
        return "No bets recorded yet."
    d["month"] = d.date.str[:7]
    lines = [f"Total profit: £{d.result_gbp.sum():,.2f} from {len(d)} bets",
             d.groupby("month").result_gbp.agg(["count", "sum"]).rename(columns={"sum": "profit_gbp"}).round(2).to_string(),
             "", d.groupby("bookmaker").result_gbp.agg(["count", "sum"]).rename(columns={"sum": "profit_gbp"}).round(2).to_string()]
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--book", required=True)
    a.add_argument("--offer", required=True)
    a.add_argument("--kind", choices=["qualifying", "free", "other"], required=True)
    a.add_argument("--result", type=float, required=True, help="net £ from both sides together")
    a.add_argument("--note", default="")
    a.add_argument("--date")
    sub.add_parser("summary")
    x = ap.parse_args()
    if x.cmd == "add":
        add(x.book, x.offer, x.kind, x.result, x.note, x.date)
    print(summary())
