"""Compare cheaper non-Anthropic models with Claude Haiku on the same 500 earnings releases, through
the Vercel AI Gateway batch API (half price; the instructions come first in every request, so the
providers' automatic prompt caching applies). Asked for by the user 2026-11-10.

    uv run python -W ignore -m classify.gateway_test run gemini|nano [--base RUN]

Input: the exact requests of a Haiku pilot run (default: v4A), so only the model differs.
Output: a run folder data/llm/<base>-<arm>-<ts>/ whose results.jsonl uses the Anthropic batch result
shape, so `classify.us_earnings report` and the comparisons work unchanged. The gateway credential
is injected by the session's network proxy (no key in code or env)."""
from __future__ import annotations

import argparse
import io
import json
import shutil
import time
from datetime import datetime
from pathlib import Path

from classify import us_earnings as ue
from core.log import get_logger

log = get_logger("llm")
GATEWAY = "https://ai-gateway.vercel.sh"
BASE = "us-earnings-pilot-v4A-20261110T121705"
MAX_BODY = 3_800_000          # gateway limit: 4.5MB per Messages batch call, 4 MiB per uploaded file
ARMS = {
    # model, API format, batch price $/MTok (input, cached input, output) from the gateway's model list
    "gemini": {"model": "google/gemini-2.5-flash-lite", "format": "messages", "price": (0.05, 0.01, 0.20)},
    # OpenAI's own batch format isn't enabled for this gateway team (403), so nano also goes through the
    # Messages format; its reasoning effort is passed as an OpenAI provider option (checked: 263 output
    # tokens instead of ~4,000 with the default, same answer).
    "nano": {"model": "openai/gpt-5-nano", "format": "messages", "price": (0.025, 0.0025, 0.20),
             "extra": {"providerOptions": {"openai": {"reasoningEffort": "minimal"}}}},
}


def _groups(lines: list[str]) -> list[list[str]]:
    out, cur, size = [], [], 0
    for line in lines:
        if cur and size + len(line) > MAX_BODY:
            out.append(cur)
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    return out + ([cur] if cur else [])


def _requests(base: Path) -> list[dict]:
    reqs = []
    for line in (base / "requests.jsonl").open():
        r = json.loads(line)
        if r["custom_id"].startswith("e-"):
            p = r["params"]
            reqs.append({"custom_id": r["custom_id"], "system": ue.system_text(p),
                         "text": p["messages"][0]["content"], "schema": p["output_config"]["format"]["schema"]})
    return reqs


def submit(arm: str, base: Path, run: Path) -> list[str]:
    a = ARMS[arm]
    reqs = _requests(base)
    ids = []
    if a["format"] == "messages":
        import anthropic
        client = anthropic.Anthropic(api_key="proxy-injected", base_url=GATEWAY)
        lines = [json.dumps({"custom_id": r["custom_id"], "params": {
            "model": a["model"], "max_tokens": 4000, "system": r["system"],
            "messages": [{"role": "user", "content": r["text"]}],
            "output_config": {"format": {"type": "json_schema", "schema": r["schema"]}},
            **a.get("extra", {})}}) for r in reqs]
        for k, g in enumerate(_groups(lines)):
            b = client.messages.batches.create(requests=[json.loads(x) for x in g],
                                               extra_headers={"Idempotency-Key": f"{run.name}-{k}"})
            ids.append(b.id)
            log.info("%s: sent group %d (%d requests) as %s", arm, k, len(g), b.id)
    else:
        import openai
        client = openai.OpenAI(api_key="proxy-injected", base_url=GATEWAY + "/v1")
        lines = [json.dumps({"custom_id": r["custom_id"], "method": "POST", "url": "/v1/chat/completions", "body": {
            "model": a["model"], "max_completion_tokens": 4000, "reasoning_effort": a["reasoning_effort"],
            "messages": [{"role": "system", "content": r["system"]}, {"role": "user", "content": r["text"]}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "earnings", "strict": True,
                                                                        "schema": r["schema"]}}}}) for r in reqs]
        for k, g in enumerate(_groups(lines)):
            f = client.files.create(file=(f"{run.name}-{k}.jsonl", io.BytesIO("\n".join(g).encode())),
                                    purpose="batch", extra_headers={"Idempotency-Key": f"{run.name}-{k}-file"})
            b = client.batches.create(input_file_id=f.id, endpoint="/v1/chat/completions", completion_window="24h",
                                      extra_headers={"Idempotency-Key": f"{run.name}-{k}-batch"})
            ids.append(b.id)
            log.info("%s: sent group %d (%d requests) as %s", arm, k, len(g), b.id)
            time.sleep(7)                     # gateway: at most 10 file uploads a minute
    return ids


def collect(arm: str, ids: list[str], poll_s: int = 60) -> list[dict]:
    """Wait for every batch, then return results in the Anthropic batch result shape."""
    a, out = ARMS[arm], []
    if a["format"] == "messages":
        import anthropic
        client = anthropic.Anthropic(api_key="proxy-injected", base_url=GATEWAY)
        for bid in ids:
            while client.messages.batches.retrieve(bid).processing_status != "ended":
                time.sleep(poll_s)
            out += [json.loads(r.to_json(indent=None)) for r in client.messages.batches.results(bid)]
    else:
        import openai
        client = openai.OpenAI(api_key="proxy-injected", base_url=GATEWAY + "/v1")
        for bid in ids:
            while (b := client.batches.retrieve(bid)).status not in ("completed", "failed", "expired", "cancelled"):
                log.info("%s %s: %s %s", arm, bid, b.status, b.request_counts)
                time.sleep(poll_s)
            for fid in (b.output_file_id, b.error_file_id):
                if not fid:
                    continue
                for line in client.files.content(fid).text.splitlines():
                    if not line.strip():
                        continue
                    x = json.loads(line)
                    resp = (x.get("response") or {})
                    if x.get("error") or resp.get("status_code") != 200:
                        out.append({"custom_id": x["custom_id"], "result": {"type": "errored", "error": x.get("error") or resp}})
                        continue
                    body = resp["body"]
                    u = body.get("usage") or {}
                    cached = ((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
                    out.append({"custom_id": x["custom_id"], "result": {"type": "succeeded", "message": {
                        "content": [{"type": "text", "text": body["choices"][0]["message"]["content"]}],
                        "stop_reason": "end_turn" if body["choices"][0]["finish_reason"] == "stop" else body["choices"][0]["finish_reason"],
                        "usage": {"input_tokens": u.get("prompt_tokens", 0) - cached, "cache_read_input_tokens": cached,
                                  "output_tokens": u.get("completion_tokens", 0),
                                  "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens")}}}})
    return out


def run_arm(arm: str, base_name: str = BASE) -> Path:
    base = ue.RUNS_DIR / base_name
    run = ue.RUNS_DIR / f"{base_name}-{arm}-{datetime.now():%Y%m%dT%H%M%S}"
    run.mkdir(parents=True)
    shutil.copy(base / "sample.parquet", run / "sample.parquet")
    meta = json.loads((base / "run.json").read_text())
    meta.update(model=ARMS[arm]["model"], arm=arm, base=base_name, created=datetime.now().isoformat())
    meta["batch_ids"] = submit(arm, base, run)
    (run / "run.json").write_text(json.dumps(meta, indent=1))
    results = collect(arm, meta["batch_ids"])
    with (run / "results.jsonl").open("w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    meta["fetched"] = datetime.now().isoformat()
    (run / "run.json").write_text(json.dumps(meta, indent=1))
    ue.backup_run(run)
    log.info("%s done: %d results in %s", arm, len(results), run)
    return run


def cost_usd(arm: str, u: dict) -> float:
    pin, pcache, pout = ARMS[arm]["price"]
    return (u["input"] * pin + u.get("cache_read", 0) * pcache + u["output"] * pout) / 1e6


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("arm", choices=list(ARMS)); r.add_argument("--base", default=BASE)
    a = ap.parse_args()
    print(run_arm(a.arm, a.base))


if __name__ == "__main__":
    main()
