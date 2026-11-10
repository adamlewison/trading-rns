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


PILOT_RUN = "us-earnings-pilot-v4A-20261110T121705"   # same prompt and settings, 500 releases


def _pilot_model() -> dict:
    """Measured on the pilot with the same prompt/settings: release-text chars per uncached input
    token, and per-release cache-read, cache-write and output tokens."""
    run = ue.RUNS_DIR / PILOT_RUN
    chars = sum(len(r["params"]["messages"][0]["content"])
                for r in map(json.loads, (run / "requests.jsonl").open()) if r["custom_id"].startswith("e-"))
    _, _, u = ue.parse_results(run)
    n = u["succeeded"]
    return {"chars_per_token": chars / u["input"], "cache_read": u["cache_read"] / n,
            "cache_write": u["cache_write"] / n, "output": u["output"] / n}


def estimate(chars_in: int, n: int, m: dict) -> dict:
    """chars_in: release text only (the instructions are the cached part)."""
    u = {"input": chars_in / m["chars_per_token"], "cache_read": n * m["cache_read"],
         "cache_write": n * m["cache_write"], "output": n * m["output"]}
    usd = ue.usage_usd(u)
    return {"requests": n, **{f"{k}_tokens_est": int(v) for k, v in u.items()},
            "usd_est": round(usd, 2), "gbp_est": round(usd * ue.llm_cfg()["usd_gbp"], 2)}


def cost_gbp(input_tokens: int, output_tokens: int, cache_write: int = 0, cache_read: int = 0) -> float:
    u = {"input": input_tokens, "output": output_tokens, "cache_write": cache_write, "cache_read": cache_read}
    return ue.usage_usd(u) * ue.llm_cfg()["usd_gbp"]


def _prep_chunk(name: str, k: int, rows: list[tuple], ids: dict, run_dir: str) -> tuple[int, list, int]:
    """One chunk (runs in a worker process): anonymise, build the requests, gzip, upload to B2."""
    from core.backup import get_store
    lines, index, chars_in = [], [], 0
    for acc, cik, text in rows:
        names, tickers = ids.get(int(cik), ([], []))
        t, _ = ue.strip_boilerplate(anonymise(text, names, tickers))   # settings "A" of the cost test
        cut = len(t) > ue.MAX_CHARS
        t = t[:ue.MAX_CHARS] + ("\n[... release truncated ...]" if cut else "")
        p = ue.params(ue.SYSTEM, ue.SCHEMA, t, cache=True)
        chars_in += len(p["messages"][0]["content"])
        lines.append(json.dumps({"custom_id": f"e-{acc}", "params": p}))
        index.append((acc, cut, k))
    body = gzip.compress(("\n".join(lines) + "\n").encode())
    (Path(run_dir) / "chunks" / f"chunk-{k:04d}.jsonl.gz").write_bytes(body)
    get_store().put(f"{B2_ROOT}/{name}/chunks/chunk-{k:04d}.jsonl.gz", body)
    return k, index, chars_in


def prepare(limit: int | None = None, workers: int = 4) -> Path:
    """Reads the release texts in one pass in storage order (fetching scattered rows chunk by chunk
    took ~50s per chunk) and anonymises chunks in parallel processes."""
    from concurrent.futures import ProcessPoolExecutor
    from core.backup import get_store
    sel = select()
    if limit:
        sel = sel.head(limit)
    wanted = set(sel.accession)
    model = _pilot_model()
    name = f"us-earnings-full-{ue.PROMPT_VERSION[-2:]}-{datetime.now():%Y%m%dT%H%M%S}"
    run = ue.RUNS_DIR / name
    (run / "chunks").mkdir(parents=True)
    ids = ue.identities([int(c) for c in sel.cik.unique()])
    index, chars_in, futures, k, buf = [], 0, [], 0, []

    def drain(n_keep: int):
        nonlocal chars_in
        while len(futures) > n_keep:
            kk, idx, ch = futures.pop(0).result()
            index.extend(idx)
            chars_in += ch
            log.info("prepared chunk %d (%d requests)", kk, len(idx))

    with ProcessPoolExecutor(max_workers=workers) as pool, session(read_only=True) as con:
        cur = con.execute("""SELECT accession, cik, text FROM us_filing_docs
                             WHERE item_group = 'earnings' AND status = 'ok' AND chars >= 800""")
        while True:
            rows = cur.fetchmany(5000)
            buf.extend(r for r in rows if r[0] in wanted)
            while len(buf) >= CHUNK or (not rows and buf):
                part, buf = buf[:CHUNK], buf[CHUNK:]
                sub = {c: ids[c] for c in {int(r[1]) for r in part} if c in ids}
                futures.append(pool.submit(_prep_chunk, name, k, part, sub, str(run)))
                k += 1
                drain(workers * 2)              # bounds memory: at most ~8 chunks of text in hand
            if not rows:
                break
        drain(0)
    idx = pd.DataFrame(index, columns=["accession", "truncated", "chunk"]).merge(sel, on="accession")
    assert len(idx) == len(sel), f"{len(sel) - len(idx)} selected releases were not prepared"
    idx.sort_values(["chunk", "accession"]).to_parquet(run / "index.parquet")
    est = estimate(chars_in, len(idx), model)
    meta = {"prompt_version": ue.PROMPT_VERSION, "model": ue.llm_cfg()["model"], "n": len(idx),
            "chunks": k, "chunk_size": CHUNK, "truncated": int(idx.truncated.sum()),
            "pilot": PILOT_RUN, "settings": "prompt caching + disclaimers stripped", "created": datetime.now().isoformat(), "estimate": est,
            "selection": "earnings, chars>=800, pre_market/after_close, priced on the date"}
    (run / "run.json").write_text(json.dumps(meta, indent=1))
    store = get_store()
    for f in ("run.json", "index.parquet"):
        store.put(f"{B2_ROOT}/{name}/{f}", (run / f).read_bytes())
    log.info("prepared %s: %d requests in %d chunks, %d truncated; estimate $%.2f (£%.2f)",
             name, len(idx), k, meta["truncated"], est["usd_est"], est["gbp_est"])
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
    lines, inp, out, cw, cr, ok, err = [], 0, 0, 0, 0, 0, 0
    for r in client.messages.batches.results(st["batch_id"]):
        lines.append(r.to_json(indent=None))
        if r.result.type == "succeeded":
            ok += 1
            us = r.result.message.usage
            inp += us.input_tokens
            out += us.output_tokens
            cw += getattr(us, "cache_creation_input_tokens", 0) or 0
            cr += getattr(us, "cache_read_input_tokens", 0) or 0
        else:
            err += 1
    store.put(f"{B2_ROOT}/{name}/results/{k}.jsonl.gz", gzip.compress(("\n".join(lines) + "\n").encode()))
    st.update(status="collected", collected=datetime.now().isoformat(), succeeded=ok, errored=err,
              input_tokens=inp, output_tokens=out, cache_write=cw, cache_read=cr,
              cost_gbp=round(cost_gbp(inp, out, cw, cr), 4))
    state["spent_gbp"] = round(state["spent_gbp"] + st["cost_gbp"], 4)
    state["input_tokens"] += inp
    state["output_tokens"] += out
    state["cache_write"] = state.get("cache_write", 0) + cw
    state["cache_read"] = state.get("cache_read", 0) + cr
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
        state["spent_gbp"] = round(state["spent_gbp"] + cost_gbp(test.usage.input_tokens, test.usage.output_tokens,
                                                            getattr(test.usage, "cache_creation_input_tokens", 0) or 0,
                                                            getattr(test.usage, "cache_read_input_tokens", 0) or 0) * 2, 4)
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
