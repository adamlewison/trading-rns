"""Phase 2 full run on GPT-6 Luna (OpenAI Batch API), chosen by the user 2026-11-10 after the model test:
same EPS accuracy as Claude Haiku (98% vs XBRL, 99% identical), cleaner buyback tags, ~35% cheaper.

It reuses the prepared chunks of the Haiku run (identical anonymised inputs, prompt v4), converted to
OpenAI Chat Completions requests on the fly. Runs from the cloud session (the OpenAI key is injected
by the session's proxy); all state lives in B2, so `run` can be restarted any time and continues.

    uv run python -W ignore -m classify.openai_full estimate            # cost estimate, no API calls
    uv run python -W ignore -m classify.openai_full run [--max-gbp 55]   # send, wait, collect; resumable
    uv run python -W ignore -m classify.openai_full status

Safety:
- never sends a chunk twice: state.json is saved right after each batch is created, and on start any
  batch OpenAI has for this run (matched by metadata) but the state lacks is adopted, not re-sent;
- spending cap: refuses to start if the estimate is above it, and stops sending once measured spend
  plus the estimate of what is in flight would pass it;
- stalls and stragglers: a batch whose progress hasn't moved for STALL_H, or that sits at >=99% for
  STRAGGLER_H, is cancelled (finished requests are kept and billed, the rest re-sent in a retry batch)."""
from __future__ import annotations

import argparse
import gzip
import io
import json
import time
from datetime import datetime, timedelta

from classify import us_earnings as ue
from classify.us_earnings_full import B2_ROOT
from core.log import get_logger

log = get_logger("llm")

SOURCE = "us-earnings-full-v4-20261110T123421"        # prepared chunks (Haiku format)
MODEL, EFFORT = "gpt-6-luna", "none"
PRICE = (0.05, 0.005, 0.25)                           # batch $/MTok: input, cached input, output
PILOT = "us-earnings-pilot-v4A-20261110T121705-luna-20261110T145454"
MAX_IN_FLIGHT = 400    # "fill the queue": send until OpenAI's queued-token limit rejects a batch, then hold
                       # at that level (user's call 2026-11-11: send it all, let OpenAI take its 24h)
JOB_SIZE = 500         # requests per batch (~3M tokens), so a new account's queue limit isn't exceeded
STRAGGLER_H = 3.0
STRAGGLER_DONE = 0.99
STALL_H = 30.0         # effectively off: cancelling stalled batches (tried 2026-11-10) only restarted
                       # their 24h clock; OpenAI finishes or expires every batch within 24h anyway.
                       # (Was 1.0: a batch whose completed count hasn't moved for this long is cancelled and its
                       # unfinished requests re-sent: OpenAI often stops mid-batch for hours (seen 2026-11-10:
                       # four batches stuck at ~45% for over an hour), while fresh batches start quickly
POLL_S = 120
TERMINAL = ("completed", "failed", "expired", "cancelled")


def _prefix() -> str:
    return f"{B2_ROOT}/{SOURCE}/openai-{MODEL}"


def _store():
    from core.backup import get_store
    return get_store()


def _client():
    """On the droplet the key comes from .env (OPENAI_API_KEY); in a cloud session the network proxy
    injects it, so a placeholder is enough."""
    import os
    import openai
    from core.config import load_config
    load_config()                                   # loads .env
    return openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY") or "proxy-injected")


def to_openai(line: str) -> str:
    """One prepared request (Anthropic shape) -> one OpenAI batch line."""
    r = json.loads(line)
    p = r["params"]
    return json.dumps({"custom_id": r["custom_id"], "method": "POST", "url": "/v1/chat/completions", "body": {
        "model": MODEL, "reasoning_effort": EFFORT, "max_completion_tokens": 4000,
        "messages": [{"role": "system", "content": ue.system_text(p)},          # identical prefix -> cached
                     {"role": "user", "content": p["messages"][0]["content"]}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "earnings", "strict": True, "schema": p["output_config"]["format"]["schema"]}}}})


def to_result(x: dict) -> dict:
    """One OpenAI batch output line -> the Anthropic batch result shape the reports read."""
    resp = x.get("response") or {}
    if x.get("error") or resp.get("status_code") != 200:
        return {"custom_id": x["custom_id"], "result": {"type": "errored", "error": x.get("error") or resp}}
    body = resp["body"]
    u = body.get("usage") or {}
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    ch = body["choices"][0]
    return {"custom_id": x["custom_id"], "result": {"type": "succeeded", "message": {
        "content": [{"type": "text", "text": ch["message"]["content"]}],
        "stop_reason": "end_turn" if ch["finish_reason"] == "stop" else ch["finish_reason"],
        "usage": {"input_tokens": u.get("prompt_tokens", 0) - cached, "cache_read_input_tokens": cached,
                  "output_tokens": u.get("completion_tokens", 0)}}}}


def cost_gbp(inp: int, cached: int, out: int) -> float:
    return (inp * PRICE[0] + cached * PRICE[1] + out * PRICE[2]) / 1e6 * ue.llm_cfg()["usd_gbp"]


def estimate(store=None) -> dict:
    """Luna pilot cost per release, scaled by the full run's text size relative to the pilot's."""
    from pathlib import Path
    store = store or _store()
    meta = json.loads(store.get(f"{B2_ROOT}/{SOURCE}/run.json"))
    _, _, u = ue.parse_results(ue.RUNS_DIR / PILOT)
    pilot_gbp = cost_gbp(u["input"], u["cache_read"], u["output"]) / u["succeeded"]
    base = ue.RUNS_DIR / PILOT.rsplit("-luna", 1)[0]
    pilot_chars = sum(len(json.loads(l)["params"]["messages"][0]["content"])
                      for l in (base / "requests.jsonl").open()) / 500
    full_chars = meta["estimate"]["input_tokens_est"] * 2.5756 / meta["n"]     # text chars per release (v4 prepare)
    per = pilot_gbp * full_chars / pilot_chars
    return {"releases": meta["n"], "chunks": meta["chunks"], "chunk_size": meta["chunk_size"],
            "gbp_per_release": round(per, 6), "gbp_est": round(per * meta["n"], 2), "pilot_gbp_per_release": round(pilot_gbp, 6)}


def _load(store) -> dict:
    raw = store.get(f"{_prefix()}/state.json")
    return json.loads(raw) if raw else {"jobs": {}, "spent_gbp": 0.0, "succeeded": 0, "errored": 0,
                                        "input": 0, "cached": 0, "output": 0}


def _save(store, st: dict) -> None:
    st["updated"] = datetime.now().isoformat()
    store.put(f"{_prefix()}/state.json", json.dumps(st, indent=1).encode())


def _lines(store, chunk: str, only: set | None = None) -> list[str]:
    raw = gzip.decompress(store.get(f"{B2_ROOT}/{SOURCE}/chunks/{chunk}.jsonl.gz")).decode()
    out = [l for l in raw.splitlines() if l]
    if only is not None:
        out = [l for l in out if json.loads(l)["custom_id"] in only]
    return out


def _send(client, store, st: dict, job: str) -> None:
    j = st["jobs"][job]
    lines = [to_openai(l) for l in _lines(store, j["chunk"], set(j["ids"]))]
    f = client.files.create(file=(f"{job}.jsonl", io.BytesIO("\n".join(lines).encode())), purpose="batch")
    b = client.batches.create(input_file_id=f.id, endpoint="/v1/chat/completions", completion_window="24h",
                              metadata={"run": SOURCE, "job": job})
    j.update(status="sent", batch_id=b.id, sent=datetime.now().isoformat(), n=len(lines))
    _save(store, st)                                   # the batch id is in B2 before anything else
    log.info("sent %s (%d requests) as %s", job, len(lines), b.id)


def _collect(client, store, st: dict, job: str, b) -> None:
    j = st["jobs"][job]
    results = []
    for fid in (b.output_file_id, b.error_file_id):
        if fid:
            results += [to_result(json.loads(l)) for l in client.files.content(fid).text.splitlines() if l.strip()]
    store.put(f"{_prefix()}/results/{job}.jsonl.gz", gzip.compress("\n".join(json.dumps(r) for r in results).encode()))
    ok = [r for r in results if r["result"]["type"] == "succeeded"]
    inp = sum(r["result"]["message"]["usage"]["input_tokens"] for r in ok)
    cac = sum(r["result"]["message"]["usage"]["cache_read_input_tokens"] for r in ok)
    out = sum(r["result"]["message"]["usage"]["output_tokens"] for r in ok)
    cost = cost_gbp(inp, cac, out)
    st["spent_gbp"] = round(st["spent_gbp"] + cost, 4)
    for k, v in (("succeeded", len(ok)), ("errored", len(results) - len(ok)), ("input", inp), ("cached", cac), ("output", out)):
        st[k] += v
    j.update(status="collected", collected=datetime.now().isoformat(), succeeded=len(ok),
             errored=len(results) - len(ok), cost_gbp=round(cost, 4), batch_status=b.status)
    # anything not returned (cancelled straggler, expired, failed request) goes into one retry job
    want = set(j["ids"])
    missing = sorted(want - {r["custom_id"] for r in ok})
    if missing and j.get("retries", 0) < 6:
        rid = f"{job.split('-retry')[0]}-retry{j.get('retries', 0) + 1}"
        st["jobs"][rid] = {"chunk": j["chunk"], "ids": missing, "status": "queued", "retries": j.get("retries", 0) + 1}
        log.info("%s: %d requests not returned, queued as %s", job, len(missing), rid)
    _save(store, st)
    log.info("collected %s: %d ok, %d not ok, £%.3f (total £%.2f)", job, len(ok), len(results) - len(ok), cost, st["spent_gbp"])


def _plan(store, est: dict) -> dict:
    """Jobs of at most JOB_SIZE requests, from the run's index (accession -> chunk)."""
    import pandas as pd
    idx = pd.read_parquet(io.BytesIO(store.get(f"{B2_ROOT}/{SOURCE}/index.parquet")), columns=["accession", "chunk"]) \
        if store.get(f"{B2_ROOT}/{SOURCE}/index.parquet") else None
    jobs = {}
    for i in range(est["chunks"]):
        chunk = f"chunk-{i:04d}"
        ids = (["e-" + a for a in idx[idx.chunk == i].accession] if idx is not None
               else [json.loads(l)["custom_id"] for l in _lines(store, chunk)])
        for p in range(0, len(ids), JOB_SIZE):
            jobs[f"{chunk}-p{p // JOB_SIZE}"] = {"chunk": chunk, "ids": ids[p:p + JOB_SIZE], "status": "queued"}
    return jobs


def _adopt(client, st: dict) -> None:
    """A batch created just before a crash (state not yet saved) is taken over, never sent again."""
    known = {j.get("batch_id") for j in st["jobs"].values()}
    for b in client.batches.list(limit=100):
        md = b.metadata or {}
        if md.get("run") == SOURCE and b.id not in known and md.get("job") in st["jobs"] \
                and st["jobs"][md["job"]]["status"] == "queued":
            j = st["jobs"][md["job"]]
            j.update(status="sent", batch_id=b.id, sent=datetime.now().isoformat(), adopted=True, n=len(j["ids"]))
            log.warning("adopted %s for %s (created before the last state save)", b.id, md["job"])


def run(max_gbp: float = 55.0, client=None, store=None, poll_s: int = POLL_S, once: bool = False) -> dict:
    store, client = store or _store(), client or _client()
    st = _load(store)
    # the estimate is computed once (it needs the pilot files, which only the cloud session has) and
    # kept in the state, so the droplet can resume without them
    est = st.get("estimate") or estimate(store)
    if est["gbp_est"] > max_gbp:
        raise SystemExit(f"estimate £{est['gbp_est']:.2f} is above the cap £{max_gbp:.2f}: not starting")
    if not st["jobs"]:
        st["jobs"] = _plan(store, est)
        st["estimate"], st["max_in_flight"] = est, MAX_IN_FLIGHT
        _save(store, st)
    _adopt(client, st)
    per_req, capped = est["gbp_per_release"], False
    if not any(j.get("failures") for j in st["jobs"].values()):   # no queue-limit rejections so far
        st["max_in_flight"] = MAX_IN_FLIGHT
    while True:
        changed = False
        for job, j in list(st["jobs"].items()):
            if j["status"] != "sent":
                continue
            try:
                b = client.batches.retrieve(j["batch_id"])
                if b.status in TERMINAL and not (b.status == "failed" and not (b.request_counts and b.request_counts.completed)):
                    _collect(client, store, st, job, b)
                    continue
            except Exception as e:                    # network hiccup etc.: try this job again next pass
                log.warning("%s: %s: %s; will retry", job, type(e).__name__, str(e)[:200])
                continue
            if b.status == "failed" and not (b.request_counts and b.request_counts.completed):
                # rejected as a whole (typically the account's queued-token limit): send it again later,
                # with fewer batches in flight
                errs = "; ".join(e.message for e in ((b.errors and b.errors.data) or []))[:300]
                j.update(status="queued", failures=j.get("failures", 0) + 1)
                # the queue is full at this level: hold there (one fewer), try again in 30 minutes
                n_sent = sum(1 for x in st["jobs"].values() if x["status"] == "sent")
                st["max_in_flight"] = max(1, min(st.get("max_in_flight", MAX_IN_FLIGHT), n_sent) - 1)
                st["pause_until"] = (datetime.now() + timedelta(minutes=30)).isoformat()
                _save(store, st)
                log.warning("%s rejected (%s); requeued, %d in flight from now", job, errs, st["max_in_flight"])
                if j["failures"] > 5:
                    raise SystemExit(f"{job} rejected {j['failures']} times: {errs}")
                continue
            if b.status in TERMINAL:
                _collect(client, store, st, job, b)
            elif b.status in ("validating", "in_progress", "finalizing") and b.request_counts and b.request_counts.total:
                now = datetime.now()
                comp = b.request_counts.completed
                done = comp / b.request_counts.total
                age = now - datetime.fromisoformat(j["sent"])
                if (j.get("progress") or {}).get("completed") != comp:
                    j["progress"] = {"completed": comp, "at": now.isoformat()}
                    changed = True
                still = now - datetime.fromisoformat(j["progress"]["at"])
                straggling = done >= STRAGGLER_DONE and age > timedelta(hours=STRAGGLER_H)
                stalled = b.status == "in_progress" and still > timedelta(hours=STALL_H) and age > timedelta(hours=STALL_H)
                if (straggling or stalled) and not j.get("cancel_asked"):
                    try:
                        client.batches.cancel(j["batch_id"])
                    except Exception as e:            # e.g. 409 "Cannot cancel a batch with status 'finalizing'"
                        log.warning("%s: cancel refused (%s); leaving it to finish", job, str(e)[:200])
                    j["cancel_asked"] = True
                    changed = True
                    log.info("%s: %d/%d done, no progress for %.1fh; cancelling and re-sending the rest",
                             job, comp, b.request_counts.total, still.total_seconds() / 3600)
        if changed:
            _save(store, st)
        in_flight = [k for k, j in st["jobs"].items() if j["status"] == "sent"]
        paused = st.get("pause_until") and datetime.now() < datetime.fromisoformat(st["pause_until"])
        for job, j in st["jobs"].items():
            if paused or j["status"] != "queued" or len(in_flight) >= st.get("max_in_flight", MAX_IN_FLIGHT):
                continue
            n = len(j["ids"])
            projected = st["spent_gbp"] + per_req * (sum(st["jobs"][k]["n"] for k in in_flight) + n)
            if projected > max_gbp:
                if not capped:
                    log.error("spending cap: £%.2f spent + in flight would pass £%.2f; not sending %s or later",
                              st["spent_gbp"], max_gbp, job)
                capped = True
                break
            try:
                _send(client, store, st, job)
            except Exception as e:
                log.warning("sending %s failed (%s: %s); will retry", job, type(e).__name__, str(e)[:200])
                break
            in_flight.append(job)
        queued = [k for k, j in st["jobs"].items() if j["status"] == "queued"]
        log.info("progress: %d jobs collected, %d in flight, %d queued; %d ok; £%.2f spent",
                 sum(j["status"] == "collected" for j in st["jobs"].values()), len(in_flight), len(queued),
                 st["succeeded"], st["spent_gbp"])
        if once or (not in_flight and (not queued or capped)):
            break
        time.sleep(poll_s)
    if not any(j["status"] in ("sent", "queued") for j in st["jobs"].values()):
        st["finished"] = datetime.now().isoformat()
        _save(store, st)
        log.info("OPENAI FULL RUN COMPLETE: %d ok, %d not ok, £%.2f", st["succeeded"], st["errored"], st["spent_gbp"])
    return st


def status() -> str:
    store = _store()
    st = _load(store)
    by: dict[str, int] = {}
    for j in st["jobs"].values():
        by[j["status"]] = by.get(j["status"], 0) + 1
    return (f"{SOURCE} on {MODEL}: jobs {by or 'not started'}; {st['succeeded']:,} ok, {st['errored']:,} not ok; "
            f"spent £{st['spent_gbp']:.2f}; updated {st.get('updated')}; finished {st.get('finished')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("estimate")
    r = sub.add_parser("run"); r.add_argument("--max-gbp", type=float, default=55.0)
    sub.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "estimate":
        print(json.dumps(estimate(), indent=1))
    elif a.cmd == "run":
        run(a.max_gbp)
    else:
        print(status())


if __name__ == "__main__":
    main()
