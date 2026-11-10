"""Full-run sender without the API: every chunk is sent once, a rerun resumes, and the spending cap
stops new batches."""
import gzip
import json
from types import SimpleNamespace as NS

import pytest

from classify import us_earnings_full as full
from core.backup import LocalStore

NAME = "us-earnings-full-v2-test"


def _setup(tmp_path, n=5, chunk=2, gbp_est=0.05):
    store = LocalStore(tmp_path)
    chunks = -(-n // chunk)
    for k in range(chunks):
        lines = [json.dumps({"custom_id": f"e-{k}-{i}", "params": {"model": "m", "messages": []}})
                 for i in range(min(chunk, n - k * chunk))]
        store.put(f"{full.B2_ROOT}/{NAME}/chunks/chunk-{k:04d}.jsonl.gz", gzip.compress("\n".join(lines).encode()))
    store.put(f"{full.B2_ROOT}/{NAME}/run.json", json.dumps(
        {"n": n, "chunks": chunks, "chunk_size": chunk, "estimate": {"gbp_est": gbp_est}}).encode())
    return store


class FakeClient:
    """Batches end on the second poll; every request succeeds with 1000 in / 400 out tokens."""

    def __init__(self, input_tokens=1000):
        self.sent, self.polls = {}, {}
        msg = lambda: NS(usage=NS(input_tokens=input_tokens, output_tokens=400), stop_reason="end_turn",
                         content=[NS(type="text", text="{}")])
        self.messages = NS(create=lambda **kw: msg(), batches=NS(
            create=self._create, retrieve=self._retrieve, results=self._results))
        self._msg = msg

    def _create(self, requests):
        bid = f"b{len(self.sent)}"
        self.sent[bid] = [r["custom_id"] for r in requests]
        return NS(id=bid)

    def _retrieve(self, bid):
        self.polls[bid] = self.polls.get(bid, 0) + 1
        return NS(processing_status="ended" if self.polls[bid] >= 2 else "in_progress")

    def _results(self, bid):
        for cid in self.sent[bid]:
            yield NS(custom_id=cid, to_json=lambda indent=None, c=cid: json.dumps({"custom_id": c}),
                     result=NS(type="succeeded", message=self._msg()))


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(full.ue, "llm_cfg", lambda: {"price_in_per_mtok": 0.10, "price_out_per_mtok": 0.50,
                                                       "usd_gbp": 0.79})


def test_every_chunk_sent_once_and_collected(tmp_path):
    store, client = _setup(tmp_path), FakeClient()
    state = full.run(NAME, max_gbp=1.0, client=client, store=store, poll_s=0)
    assert sorted(c for ids in client.sent.values() for c in ids) == sorted(
        [f"e-0-{i}" for i in range(2)] + [f"e-1-{i}" for i in range(2)] + ["e-2-0"])
    assert state["succeeded"] == 5 and state["finished"]
    assert len(store.list(f"{full.B2_ROOT}/{NAME}/results")) == 3
    # a rerun after completion sends nothing more
    full.run(NAME, max_gbp=1.0, client=client, store=store, poll_s=0)
    assert len(client.sent) == 3


def test_rerun_resumes_without_resending(tmp_path, monkeypatch):
    store, client = _setup(tmp_path), FakeClient()
    monkeypatch.setattr(full, "MAX_IN_FLIGHT", 1)
    calls = {"n": 0}
    real_sleep = full.time.sleep

    def stop_after_first_round(s):     # the droplet dies after the first chunk was sent
        calls["n"] += 1
        raise KeyboardInterrupt
    monkeypatch.setattr(full.time, "sleep", stop_after_first_round)
    with pytest.raises(KeyboardInterrupt):
        full.run(NAME, max_gbp=1.0, client=client, store=store, poll_s=1)
    assert len(client.sent) == 1
    monkeypatch.setattr(full.time, "sleep", real_sleep)
    full.run(NAME, max_gbp=1.0, client=client, store=store, poll_s=0)
    assert len(client.sent) == 3                      # chunk 0 was not sent again


def test_cap_stops_new_batches(tmp_path):
    # requests turn out far dearer than estimated (1M input tokens each, ~£0.04): after the first
    # chunk the measured spend passes the cap and nothing more is sent
    store, client = _setup(tmp_path, gbp_est=0.05), FakeClient(input_tokens=1_000_000)
    state = full.run(NAME, max_gbp=0.11, client=client, store=store, poll_s=0)
    assert len(client.sent) == 1 and not state["finished"] and state["succeeded"] == 2


def test_refuses_when_estimate_is_above_cap(tmp_path):
    store = _setup(tmp_path, gbp_est=90.0)
    with pytest.raises(SystemExit, match="above the cap"):
        full.run(NAME, max_gbp=85.0, client=FakeClient(), store=store, poll_s=0)
