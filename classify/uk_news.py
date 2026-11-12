"""UK RNS announcements -> GPT-6 Luna labels (OpenAI Batch API), reusing classify/openai_full.py.

    uv run python -W ignore -m classify.uk_news prepare pilot|full    # anonymise, chunk, upload to B2, estimate
    uv run python -W ignore -m classify.uk_news run NAME --max-gbp 55 # send/collect (resumable; cloud or droplet)
    uv run python -W ignore -m classify.uk_news status NAME
    uv run python -W ignore -m classify.uk_news report NAME           # fill rates, distributions, real cost

Scope: pre-open, regulatory, non-admin announcements of tradable UK events (features.uk_news, priced).
Pilot: 500 random ones from 2012–2021. Text anonymised (company name, TIDM, URLs, emails, phones)."""
from __future__ import annotations

import argparse
import gzip
import io
import json
import re
from datetime import datetime

import numpy as np
import pandas as pd

from classify import openai_full as of
from classify import us_earnings as ue
from classify.anonymise import anonymise
from classify.us_earnings_full import B2_ROOT
from core.config import ROOT
from core.log import get_logger
from ingest.uk.common import session

log = get_logger("llm")
PROMPT_VERSION = "uk-rns-v1"
MAX_CHARS = 20_000
CHUNK = 2000
CHARS_PER_TOKEN = 3.4          # OpenAI tokenizer on these texts (US runs: ~40% fewer tokens than Anthropic's count)
OUT_TOKENS = 260
EVENTS = ROOT / "data" / "features" / "uk_news_events.parquet"
LABELS = ROOT / "data" / "labels"

SYSTEM = """You read UK stock exchange announcements (RNS) from small London-listed companies and extract
facts for a quantitative trading study. The company's name and ticker are replaced by [COMPANY] and
[TICKER]. Use only what the announcement says; never guess from outside knowledge. If something is not
stated, use null or "not_stated".

- category: the main subject.
- vs_expectations: only if the text compares results, revenue or profit with market / analyst / consensus
  expectations ("ahead of", "in line with", "below", "at the upper end of"...). Otherwise not_stated.
- outlook_change: whether full-year guidance or outlook is raised, maintained, lowered or withdrawn.
- profit_direction: profit (or loss) versus the comparable prior period.
- revenue_growth_pct: revenue change vs the prior comparable period in percent, if stated or computable.
- fundraise_*: for placings / subscriptions / open offers: gross amount in GBP millions and the issue
  price discount to the previous close in percent (positive = discount), if stated.
- contract_value_m: value of a contract / order in GBP millions (convert only if a rate is given).
- director_dealing_side / value_k: for director/PDMR dealings: buy or sell, total value in GBP thousands.
- funding_risk: true if the text mentions going-concern doubt, cash running low, covenant breach,
  needing new funding soon, or administration.
- news_for_shareholders: is this, on balance, good or bad news for the share price, judged only from
  the text: positive, negative, mixed, neutral.
- materiality: 0 routine, 1 minor, 2 notable, 3 major (likely to move the share price a lot).
- tone: -2 very negative .. 2 very positive.
- key_points: at most 30 words."""

_N = {"type": ["number", "null"]}
CATEGORIES = ["full_year_results", "interim_results", "trading_update", "profit_warning", "contract_or_order",
              "fundraise", "acquisition", "disposal", "director_dealing", "board_change", "trial_or_regulatory_outcome",
              "drilling_or_resource_update", "operational_update", "dividend", "takeover_or_offer", "strategic_review",
              "debt_or_refinancing", "other"]
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "vs_expectations": {"type": "string", "enum": ["ahead", "in_line", "below", "mixed", "not_stated"]},
        "outlook_change": {"type": "string", "enum": ["raised", "maintained", "lowered", "withdrawn", "not_stated"]},
        "profit_direction": {"type": "string", "enum": ["improved", "flat", "worse", "not_stated"]},
        "revenue_growth_pct": _N,
        "fundraise_amount_m": _N, "fundraise_discount_pct": _N,
        "contract_value_m": _N,
        "director_dealing_side": {"type": "string", "enum": ["buy", "sell", "none"]},
        "director_dealing_value_k": _N,
        "funding_risk": {"type": "boolean"},
        "news_for_shareholders": {"type": "string", "enum": ["positive", "negative", "mixed", "neutral"]},
        "materiality": {"type": "integer", "enum": [0, 1, 2, 3]},
        "tone": {"type": "integer", "enum": [-2, -1, 0, 1, 2]},
        "key_points": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}
SCHEMA["required"] = list(SCHEMA["properties"])


def select(scope: str, n: int = 500, seed: int = 7) -> pd.DataFrame:
    ev = pd.read_parquet(EVENTS, columns=["tidm", "day", "skip"])
    ev = ev[ev.skip.isna()][["tidm", "day"]]
    with session(read_only=True) as con:
        con.register("ev", ev)
        df = con.execute("""
            SELECT a.ann_id, a.tidm, a.company, a.headline, a.published_uk, a.body_text, u.name AS uni_name
            FROM announcements a JOIN ev ON ev.tidm = a.tidm AND CAST(a.published_uk AS DATE) = ev.day
            LEFT JOIN universe u ON u.tidm = a.tidm
            WHERE a.pre_open AND a.is_regulatory AND NOT a.is_admin AND NOT coalesce(a.is_replacement, false)
              AND a.body_status = 'ok' AND coalesce(a.body_chars, 0) >= 200""").df()
    if scope == "pilot":
        df = df[pd.to_datetime(df.published_uk).dt.year <= 2021].sample(n, random_state=seed)
    return df.reset_index(drop=True)


_SELF_NAME = re.compile(r"([A-Z][\w&'.-]*(?:\s+[A-Z][\w&'.-]*){0,5})\s+(?:plc|PLC|Plc|Limited|Ltd|LIMITED)\b")
_SHORT_NAME = re.compile(r"\(\s*[\"“'‘]([^\"”'’]{2,30})[\"”'’]")
_UK_TICKER = re.compile(r"\b((?:AIM|LSE|LON|Epic|EPIC|TIDM|Ticker)\s*[:\-/]\s*)[A-Z0-9][A-Z0-9.]{1,5}\b")
_GENERIC = {"the company", "the group", "company", "group", "the board", "board", "the fund", "the bank"}


def self_names(text: str) -> list[str]:
    """Names the announcement uses for itself in its opening (former names included): "IGas Energy plc
    ("IGas" or "the Company")"."""
    head = text[:2500]
    names = {m.group(1).strip() for m in _SELF_NAME.finditer(head)}
    names |= {m.group(1).strip() for m in _SHORT_NAME.finditer(head) if m.group(1).strip().lower() not in _GENERIC}
    return [n for n in names if len(n) >= 3]


def text_for(r) -> tuple[str, bool]:
    body = f"Headline: {r.headline}\n\n{r.body_text}"
    names = [x for x in {r.company, r.uni_name} if isinstance(x, str) and x.strip()] + self_names(body)
    t = anonymise(body, names, [r.tidm])
    t = _UK_TICKER.sub(lambda m: m.group(1) + "[TICKER]", t)
    return t[:MAX_CHARS], len(t) > MAX_CHARS


def prepare(scope: str) -> str:
    df = select(scope)
    name = f"uk-rns-{scope}-{PROMPT_VERSION[-2:]}-{datetime.now():%Y%m%dT%H%M%S}"
    store = of._store()
    index, chars, k = [], 0, 0
    for start in range(0, len(df), CHUNK):
        part = df.iloc[start:start + CHUNK]
        lines = []
        for r in part.itertuples():
            text, cut = text_for(r)
            chars += len(text)
            lines.append(json.dumps({"custom_id": f"e-{r.ann_id}", "params": ue.params(SYSTEM, SCHEMA, text, cache=True)}))
            index.append((r.ann_id, r.tidm, r.published_uk, cut, k))
        store.put(f"{B2_ROOT}/{name}/chunks/chunk-{k:04d}.jsonl.gz", gzip.compress("\n".join(lines).encode()))
        log.info("prepared %s chunk %d (%d)", name, k, len(lines))
        k += 1
    idx = pd.DataFrame(index, columns=["accession", "tidm", "published_uk", "truncated", "chunk"])
    buf = io.BytesIO(); idx.to_parquet(buf)
    store.put(f"{B2_ROOT}/{name}/index.parquet", buf.getvalue())
    sys_tokens = len(SYSTEM + json.dumps(SCHEMA)) / CHARS_PER_TOKEN
    in_tok = chars / CHARS_PER_TOKEN
    usd = (in_tok * of.PRICE[0] + sys_tokens * len(idx) * of.PRICE[1] + OUT_TOKENS * len(idx) * of.PRICE[2]) / 1e6
    gbp = usd * ue.llm_cfg()["usd_gbp"]
    est = {"releases": len(idx), "chunks": k, "chunk_size": CHUNK, "gbp_per_release": round(gbp / max(len(idx), 1), 6),
           "gbp_est": round(gbp, 2), "basis": "chars/3.4 tokens + 260 output tokens, Luna batch price"}
    meta = {"prompt_version": PROMPT_VERSION, "model": of.MODEL, "n": len(idx), "chunks": k,
            "truncated": int(idx.truncated.sum()), "created": datetime.now().isoformat(), "estimate": est}
    store.put(f"{B2_ROOT}/{name}/run.json", json.dumps(meta, indent=1).encode())
    print(f"{name}: {len(idx):,} announcements, {k} chunks, {int(idx.truncated.sum())} cut at {MAX_CHARS:,} chars; "
          f"estimate ${usd:.2f} (£{gbp:.2f})")
    return name


def _use(name: str) -> None:
    """Point the shared runner at this run (its state lives under <run>/openai-gpt-6-luna/)."""
    store = of._store()
    meta = json.loads(store.get(f"{B2_ROOT}/{name}/run.json"))
    of.SOURCE = name
    of.estimate = lambda store=None, _e=meta["estimate"]: _e


def report(name: str) -> str:
    from classify.labels import parse
    _use(name)
    store = of._store()
    results = []
    for key in sorted(store.list(f"{of._prefix()}/results/")):
        results += [json.loads(l) for l in gzip.decompress(store.get(key)).decode().splitlines() if l.strip()]
    labels, n = parse(results)
    df = pd.DataFrame([{"ann_id": k, **v} for k, v in labels.items()])
    st = of._load(store)
    LABELS.mkdir(parents=True, exist_ok=True)
    df.to_parquet(LABELS / f"{name}.parquet")
    meta = json.loads(store.get(f"{B2_ROOT}/{name}/run.json"))
    usd = st["spent_gbp"] / ue.llm_cfg()["usd_gbp"]
    per = st["spent_gbp"] / max(len(df), 1)
    lines = [f"# {name}", f"labelled {len(df):,} of {meta['n']:,}; errored {n['errored']}, unparseable {n['bad_json']}",
             f"cost ${usd:.3f} (£{st['spent_gbp']:.3f}) = £{per * 1000:.3f} per 1,000; estimate was £{meta['estimate']['gbp_per_release'] * 1000:.3f} per 1,000"]
    for k in ["category", "vs_expectations", "outlook_change", "profit_direction", "news_for_shareholders",
              "materiality", "tone", "director_dealing_side", "funding_risk", "confidence"]:
        if k in df:
            lines.append(f"{k}: " + json.dumps({str(a): int(b) for a, b in df[k].value_counts().items()}))
    for k in ["revenue_growth_pct", "fundraise_amount_m", "fundraise_discount_pct", "contract_value_m", "director_dealing_value_k"]:
        if k in df:
            lines.append(f"{k}: filled {df[k].notna().mean():.0%}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare"); p.add_argument("scope", choices=["pilot", "full"])
    r = sub.add_parser("run"); r.add_argument("name"); r.add_argument("--max-gbp", type=float, default=55.0)
    s = sub.add_parser("status"); s.add_argument("name")
    q = sub.add_parser("report"); q.add_argument("name")
    a = ap.parse_args()
    if a.cmd == "prepare":
        prepare(a.scope)
    elif a.cmd == "run":
        _use(a.name)
        of.run(max_gbp=a.max_gbp)
    elif a.cmd == "status":
        _use(a.name)
        print(of.status())
    else:
        print(report(a.name))


if __name__ == "__main__":
    main()
