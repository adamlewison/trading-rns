"""Join the Luna labels onto the UK events (design: backtest/DESIGN_UK_NEWS.md, labelled addendum).

    uv run python -W ignore -m features.uk_news_labels build   # -> data/features/uk_news_model.parquet"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from classify.uk_news import CATEGORIES
from core.config import ROOT
from core.log import get_logger
from features.uk_news import OUT as EVENTS
from ingest.uk.common import session

log = get_logger("features")
RUN = "uk-rns-full-v1-20261113T064938"
LABELS = ROOT / "data" / "labels" / f"{RUN}.parquet"
OUT = ROOT / "data" / "features" / "uk_news_model.parquet"


def aggregate(lab: pd.DataFrame) -> pd.DataFrame:
    """Announcement labels -> one row per company-day."""
    x = lab.copy()
    for v in ("ahead", "below", "in_line"):
        x[f"exp_{v}"] = (x.vs_expectations == v).astype(int)
    for v in ("raised", "lowered", "withdrawn"):
        x[f"outlook_{v}"] = (x.outlook_change == v).astype(int)
    x["profit_up"] = (x.profit_direction == "improved").astype(int)
    x["profit_down"] = (x.profit_direction == "worse").astype(int)
    x["pos"] = (x.news_for_shareholders == "positive").astype(float)
    x["neg"] = (x.news_for_shareholders == "negative").astype(int)
    x["funding"] = x.funding_risk.astype(int)
    x["dir_buy_k"] = np.where(x.director_dealing_side == "buy", x.director_dealing_value_k.fillna(0), 0.0)
    x["dir_sell_k"] = np.where(x.director_dealing_side == "sell", x.director_dealing_value_k.fillna(0), 0.0)
    for c in CATEGORIES:
        x[f"llm_{c}"] = (x.category == c).astype(int)
    agg = {**{c: "max" for c in ["exp_ahead", "exp_below", "exp_in_line", "outlook_raised", "outlook_lowered",
                                  "outlook_withdrawn", "profit_up", "profit_down", "neg", "funding"]
              + [f"llm_{c}" for c in CATEGORIES]},
           "pos": "mean", "materiality": "max", "tone": "mean", "dir_buy_k": "sum", "dir_sell_k": "sum",
           "fundraise_discount_pct": "max", "fundraise_amount_m": "max", "contract_value_m": "max",
           "revenue_growth_pct": "max", "ann_id": "size"}
    g = x.groupby(["tidm", "day"]).agg(agg).rename(columns={"ann_id": "n_labelled", "pos": "share_positive",
                                                             "tone": "tone_mean", "materiality": "materiality_max"})
    return g.reset_index()


def build() -> pd.DataFrame:
    lab = pd.read_parquet(LABELS)
    with session(read_only=True) as con:
        con.register("l", lab[["ann_id"]])
        keys = con.execute("""SELECT a.ann_id, a.tidm, CAST(a.published_uk AS DATE) AS day
                              FROM announcements a SEMI JOIN l USING (ann_id)""").df()
    lab = lab.merge(keys, on="ann_id")
    lab["day"] = pd.to_datetime(lab.day)
    ev = pd.read_parquet(EVENTS).merge(aggregate(lab), on=["tidm", "day"], how="left")
    ev["labelled"] = ev.n_labelled.notna()
    ev.to_parquet(OUT)
    log.info("UK model table: %d events, %d with labels -> %s", len(ev), int(ev.labelled.sum()), OUT)
    return ev


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build"])
    ap.parse_args()
    build()


if __name__ == "__main__":
    main()
