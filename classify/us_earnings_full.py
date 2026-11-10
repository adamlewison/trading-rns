"""Phase 2 full run, US earnings releases: the v2 prompt (piloted on 500) over every release we
could trade. Approved by the user 2026-11-10 ("go £71").

Which releases: Item 2.02 earnings releases with text (>= 800 chars), released outside market hours
(pre-market or after the close: the trade is at the next open), from a company we have daily prices
for on that date. Intraday releases and companies without prices are left out (they can't be
backtested yet); they can be labelled later at the same cost per release.

    here (cloud session):  prepare            -> chunks of CHUNK requests, gzipped, uploaded to B2
    droplet (has the key): run NAME [--max-gbp 85]
                           sends chunk after chunk as Anthropic batches (at most MAX_IN_FLIGHT waiting),
                           collects each batch's raw results to B2, keeps state.json in B2 so a rerun
                           continues where it stopped, and never sends a chunk twice.
    anywhere:              status NAME        (reads state.json from B2)

Spending guard: the run refuses to start if the estimate is above --max-gbp, and stops sending new
chunks once measured spend + the estimate of what is in flight would pass it."""
from __future__ import annotations

import argparse
import gzip
import json
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from classify import us_earnings as ue
from classify.anonymise import anonymise
from core.log import get_logger
from ingest.us.common import session

log = get_logger("llm")

CHUNK = 2000            # requests per batch: ~50MB, small enough for the 1GB droplet (batch limit 256MB)
MAX_IN_FLIGHT = 10      # batches waiting at Anthropic at once (~20k requests)
POLL_S = 120
B2_ROOT = "trading-rns/llm/v1"
# measured on pilot v2 (500 releases, same prompt and 25k-char cap): input chars per token, output per release
PILOT_CHARS_PER_TOKEN = None    # filled from the pilot run at prepare time
PILOT_OUT_TOKENS = 472


def select() -> pd.DataFrame:
    """The tradeable earnings releases (no text yet)."""
    with session(read_only=True, memory_limit="2GB") as con:
        return con.execute("""
            WITH t AS (SELECT DISTINCT cik, ticker FROM sec_ticker_map),
            pr AS (SELECT ticker, min(date) d0, max(date) d1 FROM us_prices_daily GROUP BY 1),
            cov AS (SELECT DISTINCT t.cik, pr.d0, pr.d1 FROM t JOIN pr USING (ticker))
            SELECT d.accession, d.cik, d.acceptance_et, d.session, d.chars
            FROM us_filing_docs d
            WHERE d.item_group = 'earnings' AND d.status = 'ok' AND d.chars >= 800
              AND d.acceptance_et IS NOT NULL AND d.session <> 'intraday'
              AND EXISTS (SELECT 1 FROM cov WHERE cov.cik = d.cik
                          AND d.acceptance_et::DATE BETWEEN cov.d0 AND cov.d1)
            ORDER BY d.acceptance_et, d.accession""").df()


def _pilot_ratio() -> float:
    """Input chars per token, measured on the v2 pilot (system prompt + release text)."""
    run = ue.RUNS_DIR / "us-earnings-pilot-v2-20261109T233820"
    chars = sum(len(r["params"]["system"]) + len(r["params"]["messages"][0]["content"])
                for r in map(json.loads, (run / "requests.jsonl").open()) if r["custom_id"].startswith("e-"))
    _, _, u = ue.parse_results(run)
    return chars / u["input"]


def estimate(chars_in: int, n: int, ratio: float) -> dict:
    c = ue.llm_cfg()
    in_tok, out_tok = chars_in / ratio, n * PILOT_OUT_TOKENS
    usd = (in_tok * c["price_in_per_mtok"] + out_tok * c["price_out_per_mtok"]) / 1e6 * 0.5   # batch: half price
    return {"requests": n, "input_tokens_est": int(in_tok), "output_tokens_est": out_tok,
            "usd_est": round(usd, 2), "gbp_est": round(usd * c["usd_gbp"], 2)}


def cost_gbp(input_tokens: int, output_tokens: int) -> float:
    c = ue.llm_cfg()
    return (input_tokens * c["price_in_per_mtok"] + output_tokens * c["price_out_per_mtok"]) / 1e6 * 0.5 * c["usd_gbp"]


def prepare(limit: int | None = None) -> Path:
    from core.backup import get_store
    store = get_store()
    df = select()
    if limit:
        df = df.head(limit)
    ratio = _pilot_ratio()
    name = f"us-earnings-full-{ue.PROMPT_VERSION[-2:]}-{datetime.now():%Y%m%dT%H%M%S}"
    run = ue.RUNS_DIR / name
    (run / "chunks").mkdir(parents=True)
    ids = ue.identities([int(c) for c in df.cik.unique()])
    index, chars_in, n_chunks = [], 0, 0
    for k, start in enumerate(range(0, len(df), CHUNK)):
        part = df.iloc[start:start + CHUNK]
        with session(read_only=True) as con:
            texts = dict(con.execute("SELECT accession, text FROM us_filing_docs WHERE list_contains(?, accession)",
                                     [part.accession.tolist()]).fetchall())
        lines = []
        for row in part.itertuples():
            names, tickers = ids.get(int(row.cik), ([], []))
            t = anonymise(texts[row.accession], names, tickers)
            cut = len(t) > ue.MAX_CHARS
            t = t[:ue.MAX_CHARS] + ("\n[... release truncated ...]" if cut else "")
            p = ue.params(ue.SYSTEM, ue.SCHEMA, t)
            chars_in += len(p["system"]) + len(t) + 21
            lines.append(json.dumps({"custom_id": f"e-{row.accession}", "params": p}))
            index.append((row.accession, int(row.cik), row.acceptance_et, row.session, int(row.chars), cut, k))
        body = gzip.compress(("\n".join(lines) + "\n").encode())
        (run / "chunks" / f"chunk-{k:04d}.jsonl.gz").write_bytes(body)
        store.put(f"{B2_ROOT}/{name}/chunks/chunk-{k:04d}.jsonl.gz", body)
        n_chunks = k + 1
        log.info("prepared chunk %d (%d requests, %.1f MB gz)", k, len(part), len(body) / 1e6)
    idx = pd.DataFrame(index, columns=["accession", "cik", "acceptance_et", "session", "chars", "truncated", "chunk"])
    idx.to_parquet(run / "index.parquet")
    est = estimate(chars_in, len(idx), ratio)
    meta = {"prompt_version": ue.PROMPT_VERSION, "model": ue.llm_cfg()["model"], "n": len(idx),
            "chunks": n_chunks, "chunk_size": CHUNK, "truncated": int(idx.truncated.sum()),
            "chars_per_token": round(ratio, 3), "created": datetime.now().isoformat(), "estimate": est,
            "selection": "earnings, chars>=800, pre_market/after_close, priced on the date"}
    (run / "run.json").write_text(json.dumps(meta, indent=1))
    for f in ("run.json", "index.parquet"):
        store.put(f"{B2_ROOT}/{name}/{f}", (run / f).read_bytes())
    log.info("prepared %s: %d requests in %d chunks, %d truncated; estimate $%.2f (£%.2f)",
             name, len(idx), n_chunks, meta["truncated"], est["usd_est"], est["gbp_est"])
    return run


# ---------------------------------------------------------------- droplet: send, wait, collect
def _load_state(store, name: str) -> dict:
    raw = store.get(f"{B2_ROOT}/{name}/state.json")
    return json.loads(raw) if raw else {"chunks": {}, "spent_gbp": 0.0, "input_tokens": 0, "output_tokens": 0,
                                        "succeeded": 0, "errored": 0, "test_call": False}


def _save_state(store, name: str, state: dict) -> None:
    state["updated"] = datetime.now().isoformat()
    store.put(f"{B2_ROOT}/{name}/state.json", json.dumps(state, indent=1).encode())


def _collect(client, store, name: str, k: str, st: dict, state: dict) -> None:
    """Raw results of one ended batch to B2 (exactly as returned), then the usage into the state."""
    lines, inp, out, ok, err = [], 0, 0, 0, 0
    for r in client.messages.batches.results(st["batch_id"]):
        lines.append(r.to_json(indent=None))
        if r.result.type == "succeeded":
            ok += 1
            inp += r.result.message.usage.input_tokens
            out += r.result.message.usage.output_tokens
        else:
            err += 1
    store.put(f"{B2_ROOT}/{name}/results/{k}.jsonl.gz", gzip.compress(("\n".join(lines) + "\n").encode()))
    st.update(status="collected", collected=datetime.now().isoformat(), succeeded=ok, errored=err,
              input_tokens=inp, output_tokens=out, cost_gbp=round(cost_gbp(inp, out), 4))
    state["spent_gbp"] = round(state["spent_gbp"] + st["cost_gbp"], 4)
    state["input_tokens"] += inp
    state["output_tokens"] += out
    state["succeeded"] += ok
    state["errored"] += err
    log.info("collected %s: %d ok, %d errored, £%.3f (total £%.2f)", k, ok, err, st["cost_gbp"], state["spent_gbp"])


def run(name: str, max_gbp: float, client=None, store=None, poll_s: int = POLL_S) -> dict:
    import anthropic
    if store is None:
        from core.backup import get_store
        store = get_store()
    meta = json.loads(store.get(f"{B2_ROOT}/{name}/run.json"))
    est_total = meta["estimate"]["gbp_est"]
    if est_total > max_gbp:
        raise SystemExit(f"estimate £{est_total:.2f} is above the cap £{max_gbp:.2f}: not starting")
    per_req, size = est_total / meta["n"], meta["chunk_size"]
    keys = [f"chunk-{i:04d}" for i in range(meta["chunks"])]
    state = _load_state(store, name)
    client = client or ue._client()

    if not state["test_call"]:     # one normal call first: a bad parameter fails here, not in a whole batch
        first = json.loads(gzip.decompress(store.get(f"{B2_ROOT}/{name}/chunks/{keys[0]}.jsonl.gz")).split(b"\n", 1)[0])
        test = client.messages.create(**first["params"])
        if test.stop_reason != "end_turn" or ue._json_text(test) is None:
            raise SystemExit(f"test call stopped with {test.stop_reason}; not sending anything")
        json.loads(ue._json_text(test))
        state["test_call"] = True
        state["spent_gbp"] = round(state["spent_gbp"] + cost_gbp(test.usage.input_tokens, test.usage.output_tokens) * 2, 4)
        _save_state(store, name, state)
        log.info("test call ok (%d in / %d out tokens)", test.usage.input_tokens, test.usage.output_tokens)

    capped = False
    while True:
        # 1. collect every batch that has ended
        for k in keys:
            st = state["chunks"].get(k)
            if st and st["status"] == "sent":
                b = client.messages.batches.retrieve(st["batch_id"])
                if b.processing_status == "ended":
                    _collect(client, store, name, k, st, state)
                    _save_state(store, name, state)
        # 2. send more while under the in-flight limit and the spending cap
        in_flight = [k for k in keys if state["chunks"].get(k, {}).get("status") == "sent"]
        for k in keys:
            if k in state["chunks"] or len(in_flight) >= MAX_IN_FLIGHT:
                continue
            n_req = size if k != keys[-1] else meta["n"] - size * (len(keys) - 1)
            projected = state["spent_gbp"] + per_req * (len(in_flight) * size + n_req)
            if projected > max_gbp:
                if not capped:
                    log.error("spending cap: £%.2f spent + in flight would pass £%.2f; not sending %s or later",
                              state["spent_gbp"], max_gbp, k)
                capped = True
                break
            reqs = [json.loads(line) for line in
                    gzip.decompress(store.get(f"{B2_ROOT}/{name}/chunks/{k}.jsonl.gz")).decode().splitlines() if line]
            try:
                batch = client.messages.batches.create(requests=reqs)
            except anthropic.RateLimitError as e:          # queue full: try again next round
                log.warning("batch queue full (%s); waiting", str(e)[:120])
                break
            del reqs
            state["chunks"][k] = {"batch_id": batch.id, "status": "sent", "sent": datetime.now().isoformat(), "n": n_req}
            _save_state(store, name, state)      # the batch id is in B2 before anything else happens
            in_flight.append(k)
            log.info("sent %s as %s (%d requests); %d in flight", k, batch.id, n_req, len(in_flight))
        done = sum(1 for k in keys if state["chunks"].get(k, {}).get("status") == "collected")
        log.info("progress: %d/%d chunks collected, %d in flight, £%.2f spent", done, len(keys), len(in_flight),
                 state["spent_gbp"])
        if done == len(keys) or (capped and not in_flight):
            break
        time.sleep(poll_s)
    state["finished"] = datetime.now().isoformat() if done == len(keys) else None
    _save_state(store, name, state)
    log.info("FULL RUN %s: %d ok, %d errored, £%.2f", "COMPLETE" if done == len(keys) else "STOPPED AT CAP",
             state["succeeded"], state["errored"], state["spent_gbp"])
    return state


def status(name: str) -> str:
    from core.backup import get_store
    store = get_store()
    meta = json.loads(store.get(f"{B2_ROOT}/{name}/run.json"))
    s = _load_state(store, name)
    by = pd.Series([c["status"] for c in s["chunks"].values()]).value_counts().to_dict()
    return (f"{name}: {meta['n']:,} requests in {meta['chunks']} chunks; estimate £{meta['estimate']['gbp_est']:.2f}\n"
            f"chunks: {by or 'none sent yet'} (of {meta['chunks']})\n"
            f"results: {s['succeeded']:,} ok, {s['errored']:,} errored; spent £{s['spent_gbp']:.2f}; "
            f"updated {s.get('updated')}; finished {s.get('finished')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--limit", type=int)
    r = sub.add_parser("run"); r.add_argument("name"); r.add_argument("--max-gbp", type=float, default=85.0)
    s = sub.add_parser("status"); s.add_argument("name")
    a = ap.parse_args()
    if a.cmd == "prepare":
        print(prepare(a.limit))
    elif a.cmd == "run":
        run(a.name, a.max_gbp)
    else:
        print(status(a.name))


if __name__ == "__main__":
    main()
