"""The full run's GPT-6 Luna results -> one labels table, plus a quality and cost report.

    uv run python -W ignore -m classify.labels build     # B2 results -> data/labels/us_earnings_labels.parquet
    uv run python -W ignore -m classify.labels report    # fill rates, EPS vs XBRL by year, errors, cost

A release can appear in several result files (a batch that expired, then its retry): a successful,
parseable answer wins; otherwise the release is listed as missing. Read-only on B2."""
from __future__ import annotations

import argparse
import gzip
import io
import json

import pandas as pd

from classify import openai_full as of
from classify import us_earnings as ue
from core.config import ROOT

OUT = ROOT / "data" / "labels" / "us_earnings_labels.parquet"
SCALARS = [k for k, v in ue.SCHEMA["properties"].items() if k != "events"]


def parse(results: list[dict]) -> tuple[dict, dict]:
    """-> ({accession: label dict}, counts). Later successful answers never replace earlier ones."""
    out, n = {}, {"lines": 0, "errored": 0, "bad_json": 0, "stops": {}}
    for r in results:
        n["lines"] += 1
        cid, res = r["custom_id"], r["result"]
        if not cid.startswith("e-"):
            continue
        acc = cid[2:]
        if res["type"] != "succeeded":
            n["errored"] += 1
            continue
        msg = res["message"]
        n["stops"][msg["stop_reason"]] = n["stops"].get(msg["stop_reason"], 0) + 1
        try:
            data = json.loads(msg["content"][0]["text"])
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            n["bad_json"] += 1
            continue
        out.setdefault(acc, data)
    return out, n


def to_frame(labels: dict) -> pd.DataFrame:
    rows = []
    for acc, d in labels.items():
        ev = d.get("events") or []
        row = {"accession": acc, **{k: d.get(k) for k in SCALARS},
               "events": json.dumps(ev), "n_events": len(ev)}
        types = {e.get("type") for e in ev if isinstance(e, dict)}
        row.update({f"ev_{t}": int(t in types) for t in ue.EVENTS})
        rows.append(row)
    return pd.DataFrame(rows)


def build(store=None) -> pd.DataFrame:
    store = store or of._store()
    results = []
    for key in sorted(store.list(f"{of._prefix()}/results/")):
        raw = store.get(key)
        results += [json.loads(l) for l in gzip.decompress(raw).decode().splitlines() if l.strip()]
    labels, n = parse(results)
    idx = pd.read_parquet(io.BytesIO(store.get(f"{of.B2_ROOT}/{of.SOURCE}/index.parquet")))
    df = idx.merge(to_frame(labels), on="accession", how="left")
    df["labelled"] = df.is_earnings_release.notna()
    df["model"], df["prompt_version"] = of.MODEL, ue.PROMPT_VERSION
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT)
    (OUT.with_suffix(".counts.json")).write_text(json.dumps(n, indent=1))
    print(f"{df.labelled.sum():,} of {len(df):,} releases labelled -> {OUT}  ({n})")
    return df


def report() -> str:
    df = pd.read_parquet(OUT)
    n = json.loads(OUT.with_suffix(".counts.json").read_text())
    st = of._load(of._store())
    lab = df[df.labelled].copy()
    usd = st["spent_gbp"] / ue.llm_cfg()["usd_gbp"]
    lines = [f"# US earnings labels ({of.MODEL}, {ue.PROMPT_VERSION})",
             f"labelled {len(lab):,} of {len(df):,} ({len(lab) / len(df):.2%}); missing {len(df) - len(lab):,}",
             f"result lines {n['lines']:,}: errored {n['errored']:,} (expired/cancelled, re-sent), "
             f"unparseable {n['bad_json']}; stop reasons {n['stops']}",
             f"cost ${usd:.2f} (£{st['spent_gbp']:.2f}); tokens {st['input']:,} in + {st['cached']:,} cached, "
             f"{st['output']:,} out; ${usd / max(len(lab), 1) * 1000:.3f} per 1,000 releases",
             f"truncated input: {int(df.truncated.sum()):,} releases cut at {ue.MAX_CHARS:,} chars", ""]
    fill_cols = ["period_end_date", "revenue_m", "revenue_prior_year_m", "eps_diluted_gaap",
                 "eps_diluted_gaap_prior_year", "eps_adjusted", "guidance_eps_low", "guidance_revenue_low_m"]
    lines += ["filled: " + json.dumps({k: f"{lab[k].notna().mean():.0%}" for k in fill_cols})]
    for k in ["is_earnings_release", "fiscal_period", "vs_consensus", "guidance_given", "guidance_change_stated",
              "guidance_eps_basis", "tone", "confidence"]:
        lines.append(f"{k}: " + json.dumps({str(a): int(b) for a, b in lab[k].value_counts().items()}))
    ev = {t: int(lab[f"ev_{t}"].sum()) for t in ue.EVENTS}
    lines += ["events: " + json.dumps(dict(sorted(ev.items(), key=lambda x: -x[1]))), ""]
    x = ue.xbrl_check(lab, df[["accession", "cik"]])
    if len(x):
        x["year"] = pd.to_datetime(x.period_end).dt.year
        by = x.groupby("year").match.agg(["mean", "size"])
        lines += [f"EPS vs later XBRL filing: {x.match.sum():,} of {len(x):,} match within 1 cent ({x.match.mean():.1%}); "
                  f"{lab.eps_diluted_gaap.notna().sum() - len(x):,} with EPS had no comparable XBRL figure",
                  "by year: " + ", ".join(f"{y} {m:.0%} (n={s:,})" for y, (m, s) in by.iterrows()), ""]
        x.to_parquet(OUT.with_name("us_earnings_eps_check.parquet"))
    lab["year"] = pd.to_datetime(lab.acceptance_et).dt.year
    lines += ["labelled per year: " + ", ".join(f"{y} {c:,}" for y, c in lab.year.value_counts().sort_index().items())]
    text = "\n".join(lines)
    OUT.with_name("us_earnings_labels_report.md").write_text(text)
    return text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "report"])
    a = ap.parse_args()
    if a.cmd == "build":
        build()
    else:
        print(report())


if __name__ == "__main__":
    main()
