"""Phase 2 pilot, US earnings releases: Claude reads an anonymised 8-K earnings release (Ex-99.1)
and returns the numbers and labels the trading model needs, as validated JSON.

    uv run python -m classify.us_earnings prepare [--n 500] [--leak 50]   # sample, anonymise, cost estimate (no API)
    uv run python -m classify.us_earnings submit RUN_DIR [--yes-spend]    # 1 test call, then the batch
    uv run python -m classify.us_earnings fetch  RUN_DIR                  # waits for the batch, stores raw results
    uv run python -m classify.us_earnings report RUN_DIR                  # quality, leak check, real cost

The API key can't live in a cloud session, so the paid part runs on the droplet (key in its .env):
    here:     prepare -> backup RUN_DIR                  (run folder goes to B2)
    droplet:  run-remote RUN_NAME [--yes-spend]          (pulls the folder, test call, batch, waits, results to B2)
    here:     pull RUN_NAME -> report RUN_DIR

Everything for a run lives in data/llm/<run>/ and is copied to the B2 backup: the exact requests,
the raw API results (labels never need regenerating), and the parsed labels."""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from classify.anonymise import COMPANY, anonymise
from core.config import ROOT, load_config
from core.log import get_logger
from ingest.us.common import session

log = get_logger("llm")

PROMPT_VERSION = "us-earnings-v1"
MAX_CHARS = 40_000          # longer releases are cut here (flagged); the headline numbers come first
CHARS_PER_TOKEN = 3.3       # rough, for the estimate before any API call; the report gives the real count
RUNS_DIR = ROOT / "data" / "llm"

SYSTEM = f"""You read US company earnings press releases and extract facts for a quantitative trading study.
The company's name, tickers and web addresses have been replaced with {COMPANY}, [TICKER] and [URL].
Do not try to work out which company it is, and use nothing you may know about it from elsewhere:
judge only what this text says, as of the day it was published.

Rules:
- Numbers: copy them from the text, never compute or estimate. Money in millions of the reporting
  currency (e.g. "$1.2 billion" -> 1200, "$350,000" -> 0.35); per-share amounts as written; losses negative.
  Use null when the text doesn't state the number.
- "prior_year" means the same period one year earlier, as shown in the release.
- GAAP diluted EPS and adjusted (non-GAAP) EPS are different fields; don't put one in the other.
- vs_consensus: only if the text itself compares results with analyst expectations; otherwise not_mentioned.
- guidance: the company's outlook in this release compared with its previous outlook. "initiated" if it
  gives an outlook for the first time, "none" if there is no outlook.
- tone: how good the news is for shareholders overall, -2 (very bad) to +2 (very good).
- If the document is not an earnings release, set is_earnings_release false and fill what you can."""

_NUM = {"type": ["number", "null"]}
EVENTS = ["ceo_change", "cfo_change", "other_exec_change", "restatement_or_accounting_issue",
          "impairment_or_writedown", "going_concern_doubt", "restructuring_or_layoffs", "buyback",
          "dividend_increase", "dividend_cut_or_suspension", "acquisition_or_merger", "divestiture",
          "litigation_or_regulatory", "debt_financing", "equity_raise", "strategic_review", "other"]

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "is_earnings_release": {"type": "boolean"},
        "fiscal_period": {"type": "string", "enum": ["Q1", "Q2", "Q3", "Q4", "H1", "H2", "FY", "other", "unknown"]},
        "period_end_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "currency": {"type": ["string", "null"], "description": "ISO code, e.g. USD"},
        "revenue_m": _NUM, "revenue_prior_year_m": _NUM,
        "net_income_m": _NUM, "net_income_prior_year_m": _NUM,
        "eps_diluted_gaap": _NUM, "eps_diluted_gaap_prior_year": _NUM,
        "eps_adjusted": _NUM, "eps_adjusted_prior_year": _NUM,
        "vs_consensus": {"type": "string", "enum": ["beat", "in_line", "missed", "mixed", "not_mentioned"]},
        "guidance": {"type": "string", "enum": ["raised", "maintained", "lowered", "initiated", "withdrawn", "mixed", "none"]},
        "guidance_revenue_low_m": _NUM, "guidance_revenue_high_m": _NUM,
        "guidance_eps_low": _NUM, "guidance_eps_high": _NUM,
        "tone": {"type": "integer", "enum": [-2, -1, 0, 1, 2]},
        "events": {"type": "array", "items": {"type": "string", "enum": EVENTS}},
        "key_points": {"type": "string", "description": "at most 40 words"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}
SCHEMA["required"] = list(SCHEMA["properties"])

LEAK_SYSTEM = "You will see an anonymised company press release. Say which company you think it is."
LEAK_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["company_guess", "ticker_guess", "confidence"],
    "properties": {"company_guess": {"type": ["string", "null"]}, "ticker_guess": {"type": ["string", "null"]},
                   "confidence": {"type": "string", "enum": ["high", "medium", "low", "none"]}},
}


def llm_cfg() -> dict:
    return load_config()["llm"]


def params(system: str, schema: dict, text: str) -> dict:
    c = llm_cfg()
    return {
        "model": c["model"],
        "max_tokens": 4000,                         # room for (low-effort) thinking plus the JSON
        "system": system,
        "messages": [{"role": "user", "content": f"<release>\n{text}\n</release>"}],
        "output_config": {"effort": c.get("effort", "low"), "format": {"type": "json_schema", "schema": schema}},
    }


# ---------------------------------------------------------------- prepare (no API calls)
def sample(n: int, seed: int = 7) -> pd.DataFrame:
    """n earnings releases with text, spread evenly over the years, reproducible for a given seed."""
    with session(read_only=True) as con:
        years = [r[0] for r in con.execute("""SELECT DISTINCT year(acceptance_et) FROM us_filing_docs
            WHERE item_group='earnings' AND status='ok' AND acceptance_et IS NOT NULL ORDER BY 1""").fetchall()]
        per_year = -(-n // len(years))
        df = con.execute(f"""
            SELECT * FROM (
              SELECT d.accession, d.cik, d.acceptance_et, d.session, d.doc_type, d.chars, d.text,
                     row_number() OVER (PARTITION BY year(d.acceptance_et)
                                        ORDER BY hash(d.accession || '{int(seed)}')) rn
              FROM us_filing_docs d
              WHERE d.item_group='earnings' AND d.status='ok' AND d.chars >= 800 AND d.acceptance_et IS NOT NULL)
            WHERE rn <= {per_year} ORDER BY hash(accession || '{int(seed)}') LIMIT {int(n)}""").df()
    return df.drop(columns="rn")


def identities(ciks: list[int]) -> dict[int, tuple[list[str], list[str]]]:
    """Every name and ticker we know for each company: current, former, and as filed."""
    with session(read_only=True) as con:
        q = lambda sql: con.execute(sql, [ciks]).fetchall()
        out: dict[int, tuple[set, set]] = {c: (set(), set()) for c in ciks}
        for cik, name, tickers, former in q("SELECT cik, name, tickers, former_names FROM sec_companies WHERE list_contains(?, cik)"):
            out[cik][0].add(name)
            out[cik][1].update(t for t in re.split(r"[,;\s|\[\]\"']+", tickers or "") if t)
            try:                                       # [{"name": ..., "from": ..., "to": ...}, ...]
                out[cik][0].update(f.get("name") for f in json.loads(former or "[]") if isinstance(f, dict))
            except json.JSONDecodeError:
                pass
        for cik, name in q("SELECT DISTINCT cik, company FROM sec_form_index WHERE list_contains(?, cik)"):
            out[cik][0].add(name)
        for cik, name, t in q("SELECT DISTINCT cik, name, ticker FROM sec_ticker_map WHERE list_contains(?, cik)"):
            out[cik][0].add(name)
            out[cik][1].add(t)
    return {c: (sorted(x for x in n if x), sorted(t for t in tk if t)) for c, (n, tk) in out.items()}


def estimate_cost(requests: list[dict]) -> dict:
    """Pre-run estimate. Output (incl. thinking) is a guess until the pilot measures it."""
    c = llm_cfg()
    in_tok = sum(len(r["params"]["system"]) + len(r["params"]["messages"][0]["content"]) for r in requests) / CHARS_PER_TOKEN
    out_tok = len(requests) * 700
    disc = 0.5 if c.get("batch", True) else 1.0
    usd = (in_tok * c["price_in_per_mtok"] + out_tok * c["price_out_per_mtok"]) / 1e6 * disc
    return {"requests": len(requests), "input_tokens_est": int(in_tok), "output_tokens_est": out_tok,
            "usd_est": round(usd, 3), "gbp_est": round(usd * c["usd_gbp"], 3)}


def prepare(n: int, n_leak: int, seed: int = 7) -> Path:
    df = sample(n, seed)
    ids = identities([int(c) for c in df.cik.unique()])
    texts, truncated = [], []
    for row in df.itertuples():
        names, tickers = ids.get(int(row.cik), ([], []))
        t = anonymise(row.text, names, tickers)
        truncated.append(len(t) > MAX_CHARS)
        texts.append(t[:MAX_CHARS] + ("\n[... release truncated ...]" if len(t) > MAX_CHARS else ""))
    df["anon_text"], df["truncated"] = texts, truncated
    df["names"] = [json.dumps(ids.get(int(c), ([], []))[0]) for c in df.cik]
    df["tickers"] = [json.dumps(ids.get(int(c), ([], []))[1]) for c in df.cik]

    run = RUNS_DIR / f"us-earnings-pilot-{datetime.now():%Y%m%dT%H%M%S}"
    run.mkdir(parents=True)
    reqs = [{"custom_id": f"e-{a}", "params": params(SYSTEM, SCHEMA, t)}
            for a, t in zip(df.accession, df.anon_text)]
    leak = df.sample(min(n_leak, len(df)), random_state=seed)
    reqs += [{"custom_id": f"l-{a}", "params": params(LEAK_SYSTEM, LEAK_SCHEMA, t)}
             for a, t in zip(leak.accession, leak.anon_text)]
    with (run / "requests.jsonl").open("w") as f:
        for r in reqs:
            f.write(json.dumps(r) + "\n")
    df.drop(columns=["text"]).to_parquet(run / "sample.parquet")
    est = estimate_cost(reqs)
    meta = {"prompt_version": PROMPT_VERSION, "model": llm_cfg()["model"], "seed": seed, "n": len(df),
            "n_leak": len(leak), "truncated": int(sum(truncated)), "created": datetime.now().isoformat(),
            "estimate": est}
    (run / "run.json").write_text(json.dumps(meta, indent=1))
    log.info("prepared %s: %d extraction + %d leak-check requests, %d truncated; estimate $%.3f (£%.3f)",
             run.name, len(df), len(leak), sum(truncated), est["usd_est"], est["gbp_est"])
    return run


# ---------------------------------------------------------------- API (costs money)
def _client():
    import anthropic
    load_config()                                   # loads .env (ANTHROPIC_API_KEY on the droplet)
    return anthropic.Anthropic()


def _json_text(message) -> str | None:
    return next((b.text for b in message.content if b.type == "text"), None)


def submit(run: Path, yes_spend: bool = False) -> str:
    meta = json.loads((run / "run.json").read_text())
    if meta.get("batch_id"):
        raise SystemExit(f"already submitted: {meta['batch_id']}")
    limit = float(llm_cfg()["confirm_cost_above_gbp"])
    if meta["estimate"]["gbp_est"] > limit and not yes_spend:
        raise SystemExit(f"estimated £{meta['estimate']['gbp_est']:.2f} is over £{limit:.2f}: "
                         "needs the user's OK, then rerun with --yes-spend")
    reqs = [json.loads(line) for line in (run / "requests.jsonl").open()]
    client = _client()
    # one normal call first: catches a schema/parameter problem before a whole batch fails
    test = client.messages.create(**reqs[0]["params"])
    if test.stop_reason not in ("end_turn",) or _json_text(test) is None:
        raise SystemExit(f"test call stopped with {test.stop_reason}; not submitting")
    json.loads(_json_text(test))
    (run / "test_call.json").write_text(test.to_json())
    log.info("test call ok (%d in / %d out tokens)", test.usage.input_tokens, test.usage.output_tokens)
    batch = client.messages.batches.create(requests=reqs)
    meta.update(batch_id=batch.id, submitted=datetime.now().isoformat())
    (run / "run.json").write_text(json.dumps(meta, indent=1))
    log.info("submitted batch %s (%d requests)", batch.id, len(reqs))
    return batch.id


def fetch(run: Path, poll_s: int = 60) -> None:
    meta = json.loads((run / "run.json").read_text())
    client = _client()
    while True:
        b = client.messages.batches.retrieve(meta["batch_id"])
        if b.processing_status == "ended":
            break
        log.info("batch %s: %s (%s)", b.id, b.processing_status, b.request_counts)
        time.sleep(poll_s)
    n = 0
    with (run / "results.jsonl").open("w") as f:        # raw, exactly as returned: never regenerate
        for r in client.messages.batches.results(meta["batch_id"]):
            f.write(r.to_json(indent=None) + "\n")
            n += 1
    meta.update(fetched=datetime.now().isoformat(), counts=b.request_counts.to_dict())
    (run / "run.json").write_text(json.dumps(meta, indent=1))
    log.info("fetched %d results into %s", n, run / "results.jsonl")
    backup_run(run)


def pull_run(name: str) -> Path:
    """Download a run folder from B2 (files already here with the same size are kept)."""
    from core.backup import get_store
    store, prefix = get_store(), f"trading-rns/llm/v1/{name}/"
    run = RUNS_DIR / name
    run.mkdir(parents=True, exist_ok=True)
    names = store.list(prefix)
    if not names:
        raise SystemExit(f"no run {name} in the backup")
    for key in names:
        (run / key[len(prefix):]).write_bytes(store.get(key))
    log.info("pulled %s (%d files)", name, len(names))
    return run


def backup_run(run: Path) -> None:
    """Copy the run folder to B2 (its own prefix, so it never touches a market's backup)."""
    from core.backup import get_store
    store = get_store()
    for p in sorted(run.iterdir()):
        store.put(f"trading-rns/llm/v1/{run.name}/{p.name}", p.read_bytes())
    log.info("backed up %s to B2", run.name)


# ---------------------------------------------------------------- report (no API calls)
def parse_results(run: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    labels, leaks = [], []
    usage = {"input": 0, "output": 0, "succeeded": 0, "errored": 0, "bad_json": 0, "stops": {}}
    for line in (run / "results.jsonl").open():
        r = json.loads(line)
        cid, res = r["custom_id"], r["result"]
        acc = cid[2:]                                   # "e-<accession>" / "l-<accession>"
        if res["type"] != "succeeded":
            usage["errored"] += 1
            continue
        msg = res["message"]
        usage["succeeded"] += 1
        usage["input"] += msg["usage"]["input_tokens"]
        usage["output"] += msg["usage"]["output_tokens"]
        usage["stops"][msg["stop_reason"]] = usage["stops"].get(msg["stop_reason"], 0) + 1
        text = next((b["text"] for b in msg["content"] if b["type"] == "text"), None)
        try:
            data = json.loads(text) if text else None
        except json.JSONDecodeError:
            data = None
        if data is None:
            usage["bad_json"] += 1
            continue
        (labels if cid.startswith("e-") else leaks).append({"accession": acc, **data})
    return pd.DataFrame(labels), pd.DataFrame(leaks), usage


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower())


def leak_hits(leaks: pd.DataFrame, sample_df: pd.DataFrame) -> pd.DataFrame:
    """A guess counts as a leak if it names the company (any known name's first word) or a ticker."""
    m = leaks.merge(sample_df[["accession", "names", "tickers"]], on="accession")
    def hit(r):
        s = lambda v: v if isinstance(v, str) else ""
        g, t = _norm(s(r.company_guess)), s(r.ticker_guess).upper().strip()
        names = [_norm(n) for n in json.loads(r.names)]
        firsts = {n.split()[0] for n in names if n.split() and len(n.split()[0]) >= 3}
        return bool(t and t in json.loads(r.tickers)) or bool(g and any(f in g.split() for f in firsts))
    m["leaked"] = m.apply(hit, axis=1) if len(m) else []
    return m


def xbrl_check(labels: pd.DataFrame, sample_df: pd.DataFrame) -> pd.DataFrame:
    """Compare extracted GAAP diluted EPS with the figure the company later filed in XBRL (10-Q/10-K)
    for the same period end. Used only to measure extraction accuracy, never as a trading input."""
    d = labels.merge(sample_df[["accession", "cik"]], on="accession")
    d = d[d.eps_diluted_gaap.notna() & d.period_end_date.notna()].copy()
    d["period_end"] = pd.to_datetime(d.period_end_date, errors="coerce")
    d = d[d.period_end.notna()]
    if d.empty:
        return d
    with session(read_only=True) as con:
        con.register("ext", d[["accession", "cik", "period_end", "fiscal_period", "eps_diluted_gaap"]])
        out = con.execute("""
            SELECT e.*, arg_min(f.val, f.filed) xbrl_eps
            FROM ext e JOIN us_facts f ON f.cik = e.cik AND f.concept = 'EarningsPerShareDiluted'
             AND abs(date_diff('day', f.end_date, e.period_end::DATE)) <= 7
             AND date_diff('day', f.start_date, f.end_date) BETWEEN
                 (CASE WHEN e.fiscal_period = 'FY' THEN 350 ELSE 80 END) AND
                 (CASE WHEN e.fiscal_period = 'FY' THEN 380 ELSE 100 END)
            GROUP BY ALL""").df()
    out["match"] = (out.eps_diluted_gaap - out.xbrl_eps).abs() <= 0.011
    return out


def report(run: Path) -> str:
    c = llm_cfg()
    meta = json.loads((run / "run.json").read_text())
    sample_df = pd.read_parquet(run / "sample.parquet")
    labels, leaks, u = parse_results(run)
    labels.to_parquet(run / "labels.parquet")
    disc = 0.5 if c.get("batch", True) else 1.0
    usd = (u["input"] * c["price_in_per_mtok"] + u["output"] * c["price_out_per_mtok"]) / 1e6 * disc
    n_e = max(len(labels), 1)
    lines = [f"# {run.name}  ({meta['prompt_version']}, {meta['model']})",
             f"results: {u['succeeded']} ok, {u['errored']} errored, {u['bad_json']} unparseable; stop reasons {u['stops']}",
             f"tokens: {u['input']:,} in, {u['output']:,} out (incl. thinking) -> ${usd:.3f} (£{usd * c['usd_gbp']:.3f}); "
             f"per release ~{u['input'] / max(u['succeeded'], 1):,.0f} in / {u['output'] / max(u['succeeded'], 1):,.0f} out",
             f"truncated releases: {meta['truncated']} of {meta['n']}", ""]
    if len(labels):
        per_doc_usd = usd / max(u["succeeded"], 1)
        with session(read_only=True) as con:
            n_all = {g: n for g, n in con.execute("SELECT item_group, count(*) FROM us_filing_docs WHERE status='ok' GROUP BY 1").fetchall()}
        lines += [f"full-run estimate at this cost per document: earnings {n_all.get('earnings', 0):,} -> "
                  f"£{per_doc_usd * n_all.get('earnings', 0) * c['usd_gbp']:.0f}", ""]
        fill = {k: f"{labels[k].notna().mean():.0%}" for k in
                ["revenue_m", "revenue_prior_year_m", "eps_diluted_gaap", "eps_diluted_gaap_prior_year",
                 "eps_adjusted", "period_end_date", "guidance_eps_low", "guidance_revenue_low_m"]}
        lines += ["filled:", json.dumps(fill), "",
                  "is_earnings_release: " + json.dumps(labels.is_earnings_release.value_counts().to_dict()),
                  "tone: " + json.dumps(labels.tone.value_counts().sort_index().to_dict()),
                  "vs_consensus: " + json.dumps(labels.vs_consensus.value_counts().to_dict()),
                  "guidance: " + json.dumps(labels.guidance.value_counts().to_dict()),
                  "confidence: " + json.dumps(labels.confidence.value_counts().to_dict()),
                  "events: " + json.dumps(labels.events.explode().value_counts().head(10).to_dict()), ""]
        x = xbrl_check(labels, sample_df)
        if len(x):
            lines += [f"EPS check vs later XBRL filing: {x.match.sum()} of {len(x)} match within 1 cent "
                      f"({x.match.mean():.0%}); {n_e - len(x)} had no comparable XBRL figure", ""]
            x.to_parquet(run / "eps_check.parquet")
    if len(leaks):
        lk = leak_hits(leaks, sample_df)
        lines += [f"leak check: the model named the company in {lk.leaked.sum()} of {len(lk)} anonymised releases "
                  f"({lk.leaked.mean():.0%}); it gave any guess in {(lk.confidence != 'none').sum()}", ""]
        lk.to_parquet(run / "leak_check.parquet")
    text = "\n".join(lines)
    (run / "report.txt").write_text(text)
    return text


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--n", type=int, default=None); p.add_argument("--leak", type=int, default=50)
    p.add_argument("--seed", type=int, default=7)
    r = sub.add_parser("run-remote"); r.add_argument("name"); r.add_argument("--yes-spend", action="store_true")
    q = sub.add_parser("pull"); q.add_argument("name")
    for name in ("submit", "fetch", "report", "backup"):
        s = sub.add_parser(name); s.add_argument("run")
        if name == "submit":
            s.add_argument("--yes-spend", action="store_true")
    a = ap.parse_args()
    if a.cmd == "prepare":
        print(prepare(a.n or int(llm_cfg()["pilot_size"]), a.leak, a.seed))
        return
    if a.cmd == "run-remote":                       # on the droplet
        run = pull_run(a.name)
        meta = json.loads((run / "run.json").read_text())
        if not meta.get("batch_id"):
            submit(run, a.yes_spend)
            backup_run(run)                         # batch id is safe in B2 before the long wait
        fetch(run)
        return
    if a.cmd == "pull":
        print(pull_run(a.name))
        return
    run = Path(a.run)
    if a.cmd == "submit":
        submit(run, a.yes_spend)
    elif a.cmd == "fetch":
        fetch(run)
    elif a.cmd == "report":
        print(report(run))
    else:
        backup_run(run)


if __name__ == "__main__":
    main()
