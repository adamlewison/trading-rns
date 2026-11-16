"""Net asset values from UK investment-trust "Net Asset Value" announcements, read by GPT-6 Luna.
Design: backtest/DESIGN_UK_TRUST_DISCOUNTS.md. Facts copied from the text only.

    uv run python -W ignore -m classify.uk_nav run --max-gbp 5     # resumable, direct API
    uv run python -W ignore -m classify.uk_nav report

Output: data/labels/uk_nav.jsonl, copied to B2 `trading-rns/llm/v1/uk-nav/labels.jsonl`.
"""
from __future__ import annotations

import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from classify import openai_full as of
from ingest.uk.common import session

HEADLINE_RE = "^net asset value"
OUT = Path("data/labels/uk_nav.jsonl")
B2_NAME = "trading-rns/llm/v1/uk-nav/labels.jsonl"
MAX_CHARS = 6000
PROMPT_VERSION = "uk-nav-v1"

_N = {"type": ["number", "null"]}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "company_found": {"type": "boolean"},
        "nav_date": {"type": ["string", "null"]},
        "unit": {"type": "string", "enum": ["pence", "pounds", "us_dollars", "us_cents", "euros", "euro_cents", "other", "not_stated"]},
        "cum_income_debt_fair": _N, "cum_income_debt_par": _N,
        "ex_income_debt_fair": _N, "ex_income_debt_par": _N,
        "cum_income_basis_unstated_debt": _N, "ex_income_basis_unstated_debt": _N,
        "nav_basis_not_stated": _N,
        "share_class": {"type": ["string", "null"]},
    },
    "required": ["company_found", "nav_date", "unit", "cum_income_debt_fair", "cum_income_debt_par",
                 "ex_income_debt_fair", "ex_income_debt_par", "cum_income_basis_unstated_debt",
                 "ex_income_basis_unstated_debt", "nav_basis_not_stated", "share_class"],
}
SYSTEM = """You extract the net asset value (NAV) per share of ONE named investment company from a UK
stock-exchange announcement. The announcement may list several companies or share classes: use only the
figures for the company named in the first line of the message, and for its ordinary shares (if several
classes, the sterling ordinary class; say which in share_class). Copy numbers exactly as printed, without
converting units. If something is not stated, answer null. Never calculate, guess or use outside knowledge.

- company_found: false if the text gives no NAV per share for the named company.
- nav_date: the date the NAV refers to ("as at close of business on ..."), as YYYY-MM-DD. Not the
  publication date unless the text says the NAV is as at that date.
- unit: the unit the NAV per share is printed in (625.1p or 625.1 pence -> pence; £3.10 -> pounds).
- The NAV figures, each null unless the text gives that basis:
  cum_income_debt_fair: including current-year income (cum income), debt at fair or market value;
  cum_income_debt_par: including income, debt at par or nominal value;
  ex_income_debt_fair / ex_income_debt_par: excluding income (capital only), debt at fair / at par;
  cum_income_basis_unstated_debt / ex_income_basis_unstated_debt: cum or ex income stated, but nothing
  said about debt (common for trusts with no debt);
  nav_basis_not_stated: a single NAV with neither income nor debt basis stated.
  Diluted figures are ignored when an undiluted figure is given."""


def select() -> pd.DataFrame:
    with session(read_only=True) as con:
        ids = con.execute(f"""SELECT ann_id, tidm, company, headline, published_uk FROM ann_index i
            WHERE regexp_matches(lower(i.headline), '{HEADLINE_RE}')""").df()
        con.register("ids", ids[["ann_id"]])
        body = con.execute("""SELECT b.ann_id, b.body_text FROM ann_bodies b SEMI JOIN ids USING (ann_id)
            WHERE b.status = 'ok'""").df()
    return ids.merge(body, on="ann_id").sort_values("published_uk").reset_index(drop=True)


def done() -> dict[str, dict]:
    if not OUT.exists():
        return {}
    return {r["ann_id"]: r for r in map(json.loads, OUT.read_text().splitlines()) if r.get("label")}


def run(max_gbp: float, workers: int = 16) -> None:
    df = select()
    have = done()
    todo = df[~df.ann_id.isin(have)]
    est = of.cost_gbp(int(todo.body_text.str.len().clip(upper=MAX_CHARS).sum() / 3.4) + 650 * len(todo), 0, 110 * len(todo)) * 2
    print(f"{len(df)} announcements with text, {len(todo)} to label, estimate £{est:.2f} (direct API, before caching)", flush=True)
    if est > max_gbp:
        raise SystemExit(f"estimate £{est:.2f} is above the cap £{max_gbp:.2f}")
    client = of._client()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    spent = [0.0, 0]

    def one(r) -> None:
        text = f"Company: {r.company} (ticker {r.tidm})\nPublished: {r.published_uk}\n\n{r.body_text[:MAX_CHARS]}"
        try:
            resp = client.chat.completions.create(
                model=of.MODEL, reasoning_effort=of.EFFORT, max_completion_tokens=1500,
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": f"<announcement>\n{text}\n</announcement>"}],
                response_format={"type": "json_schema", "json_schema": {"name": "nav", "strict": True, "schema": SCHEMA}})
            raw = resp.choices[0].message.content
            u = resp.usage
            cached = (getattr(u, "prompt_tokens_details", None) and u.prompt_tokens_details.cached_tokens) or 0
            cost = of.cost_gbp(u.prompt_tokens - cached, cached, u.completion_tokens) * 2
            row = {"ann_id": r.ann_id, "prompt": PROMPT_VERSION, "label": json.loads(raw)}
        except Exception as e:                                   # no label recorded: retried next run
            row, cost = {"ann_id": r.ann_id, "error": str(e)[:300]}, 0.0
        with lock:
            with OUT.open("a") as f:
                f.write(json.dumps(row) + "\n")
            spent[0] += cost
            spent[1] += 1
            if spent[1] % 2000 == 0:
                print(f"{spent[1]}/{len(todo)} £{spent[0]:.2f}", flush=True)
            if spent[0] > max_gbp:
                raise SystemExit("cost cap reached")

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, todo.itertuples()))
    print(f"labelled {len(done())} of {len(df)}; this run £{spent[0]:.2f}", flush=True)
    of._store().put(B2_NAME, OUT.read_bytes())


def labels() -> pd.DataFrame:
    d = pd.DataFrame([{"ann_id": k, **v["label"]} for k, v in done().items()])
    d["nav_date"] = pd.to_datetime(d.nav_date, errors="coerce")
    return d


def report() -> None:
    d = labels()
    print(len(d), "labelled; company found", round(d.company_found.mean(), 3), "; nav_date", round(d.nav_date.notna().mean(), 3))
    print(d.unit.value_counts().to_string())
    cols = [c for c in SCHEMA["properties"] if c.startswith(("cum_", "ex_", "nav_basis"))]
    print(d[cols].notna().mean().round(3).to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "report"])
    ap.add_argument("--max-gbp", type=float, default=5.0)
    a = ap.parse_args()
    run(a.max_gbp) if a.cmd == "run" else report()
