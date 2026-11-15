"""Facts from UK retail-offer announcements (launches and results), read by GPT-6 Luna.
Design: backtest/DESIGN_UK_RETAIL_OFFERS.md. Facts copied from the text only, no judgements.

    uv run python -W ignore -m classify.uk_retail_offers run --max-gbp 3    # resumable, direct API
    uv run python -W ignore -m classify.uk_retail_offers report

Output: data/labels/uk_retail_offers.jsonl (one line per announcement, raw model reply kept), copied
to B2 `trading-rns/llm/v1/uk-retail-offers/labels.jsonl`.
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

HEADLINE_RE = "retail offer|primarybid|retailbook"
OUT = Path("data/labels/uk_retail_offers.jsonl")
B2_NAME = "trading-rns/llm/v1/uk-retail-offers/labels.jsonl"
MAX_CHARS = 30_000
PROMPT_VERSION = "uk-retail-offers-v1"

_N = {"type": ["number", "null"]}
_S = {"type": ["string", "null"]}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "enum": ["launch", "result", "other"]},
        "issue_price_pence": _N,
        "platform": {"type": "string", "enum": ["PrimaryBid", "RetailBook", "WRAP", "REX", "BookBuild", "other", "not_stated"]},
        "offer_close_date": _S, "offer_close_time": _S,
        "existing_holders_only": {"type": ["boolean", "null"]},
        "conditional_on_general_meeting": {"type": ["boolean", "null"]},
        "admission_date": _S,
        "min_application_gbp": _N,
        "retail_size_gbp_m": _N,
        "retail_raised_gbp_m": _N,
        "cancelled": {"type": "boolean"},
    },
    "required": ["kind", "issue_price_pence", "platform", "offer_close_date", "offer_close_time",
                 "existing_holders_only", "conditional_on_general_meeting", "admission_date",
                 "min_application_gbp", "retail_size_gbp_m", "retail_raised_gbp_m", "cancelled"],
}
SYSTEM = """You extract facts from a UK stock-exchange announcement about a retail offer: an offer of new
shares to private investors (through PrimaryBid, RetailBook, Winterflood's WRAP, Peel Hunt's REX or
BookBuild), usually alongside a placing with institutions. Copy facts from the text only. If the text
does not state something, answer null. Never guess and never use outside knowledge.

- kind: "launch" if the announcement opens or announces the retail offer; "result" if it reports the
  outcome (amount raised, close, admission); "other" for anything else (including announcements that
  only mention a retail offer in passing).
- issue_price_pence: the price per new share in the retail offer, in pence (a price of £1.20 is 120;
  0.5p is 0.5). If the price is still to be set (for example by a bookbuild), null.
- platform: the retail platform named.
- offer_close_date (YYYY-MM-DD) and offer_close_time (HH:MM, 24-hour, UK time): when the retail offer
  closes to applications, as stated. Null if not stated.
- existing_holders_only: true if only existing shareholders may apply; false if new investors may too.
- conditional_on_general_meeting: true if the retail offer (or the shares issued under it) depends on
  shareholder approval at a general meeting.
- admission_date (YYYY-MM-DD): the date the new retail-offer shares are expected to be, or were,
  admitted to trading. If several tranches, the date for the retail-offer shares; if still unclear, the first.
- min_application_gbp: the minimum application in pounds.
- retail_size_gbp_m: the maximum or target size of the retail offer, in £ million (launch).
- retail_raised_gbp_m: the amount raised by the retail offer, in £ million (result).
- cancelled: true only if the text says the retail offer was withdrawn, cancelled or did not proceed."""


def select() -> pd.DataFrame:
    with session(read_only=True) as con:
        return con.execute(f"""SELECT i.ann_id, i.tidm, i.headline, i.published_uk, b.body_text
            FROM ann_index i JOIN ann_bodies b USING (ann_id)
            WHERE regexp_matches(lower(i.headline), '{HEADLINE_RE}') AND b.status = 'ok'
            ORDER BY i.published_uk""").df()


def done() -> dict[str, dict]:
    if not OUT.exists():
        return {}
    return {r["ann_id"]: r for r in map(json.loads, OUT.read_text().splitlines()) if r.get("label")}


def run(max_gbp: float, workers: int = 8) -> None:
    df = select()
    have = done()
    todo = df[~df.ann_id.isin(have)]
    est = of.cost_gbp(int(todo.body_text.str.len().clip(upper=MAX_CHARS).sum() / 3.4) + 700 * len(todo), 0, 150 * len(todo)) * 2
    print(f"{len(df)} announcements with text, {len(todo)} to label, estimate £{est:.2f} (direct API)")
    if est > max_gbp:
        raise SystemExit(f"estimate £{est:.2f} is above the cap £{max_gbp:.2f}")
    client = of._client()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    spent = [0.0, 0]

    def one(r) -> None:
        text = f"Published: {r.published_uk}\nHeadline: {r.headline}\n\n{r.body_text[:MAX_CHARS]}"
        try:
            resp = client.chat.completions.create(
                model=of.MODEL, reasoning_effort=of.EFFORT, max_completion_tokens=2000,
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": f"<announcement>\n{text}\n</announcement>"}],
                response_format={"type": "json_schema", "json_schema": {"name": "retail_offer", "strict": True, "schema": SCHEMA}})
            raw = resp.choices[0].message.content
            u = resp.usage
            cached = (getattr(u, "prompt_tokens_details", None) and u.prompt_tokens_details.cached_tokens) or 0
            cost = of.cost_gbp(u.prompt_tokens - cached, cached, u.completion_tokens) * 2
            row = {"ann_id": r.ann_id, "prompt": PROMPT_VERSION, "label": json.loads(raw), "raw": raw}
        except Exception as e:                                   # recorded without a label: retried next run
            row, cost = {"ann_id": r.ann_id, "error": str(e)[:300]}, 0.0
        with lock:
            with OUT.open("a") as f:
                f.write(json.dumps(row) + "\n")
            spent[0] += cost
            spent[1] += 1
            if spent[1] % 100 == 0:
                print(f"{spent[1]}/{len(todo)} £{spent[0]:.2f}", flush=True)

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, todo.itertuples()))
    print(f"labelled {len(done())} of {len(df)}; this run £{spent[0]:.2f}")
    of._store().put(B2_NAME, OUT.read_bytes())


def labels() -> pd.DataFrame:
    d = pd.DataFrame([{"ann_id": k, **v["label"]} for k, v in done().items()])
    for c in ("offer_close_date", "admission_date"):
        d[c] = pd.to_datetime(d[c], errors="coerce")
    return d


def report() -> None:
    d = labels()
    print(len(d), "labelled")
    print(d.kind.value_counts().to_string())
    L = d[d.kind == "launch"]
    print("launches: price stated", L.issue_price_pence.notna().mean().round(3), "| admission date", L.admission_date.notna().mean().round(3),
          "| close date", L.offer_close_date.notna().mean().round(3), "| existing holders only", (L.existing_holders_only == True).mean().round(3),  # noqa: E712
          "| needs general meeting", (L.conditional_on_general_meeting == True).mean().round(3))  # noqa: E712
    print(L.platform.value_counts().to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "report"])
    ap.add_argument("--max-gbp", type=float, default=3.0)
    a = ap.parse_args()
    run(a.max_gbp) if a.cmd == "run" else report()
