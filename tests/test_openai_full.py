"""GPT-6 Luna full run without the API: each chunk sent once, a crash between create and save is
adopted (not re-sent), stragglers are cancelled and re-sent, and the cap stops new batches."""
import gzip
import json
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from classify import openai_full as of
from core.backup import LocalStore

SCHEMA = {"type": "object", "properties": {"x": {"type": "number"}}, "required": ["x"], "additionalProperties": False}


def _setup(tmp_path, chunks=3, size=2):
    store = LocalStore(tmp_path)
    for k in range(chunks):
        lines = [json.dumps({"custom_id": f"e-{k}-{i}", "params": {
            "system": [{"type": "text", "text": "instr", "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": f"text {k}-{i}"}],
            "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}}}}) for i in range(size)]
        store.put(f"{of.B2_ROOT}/{of.SOURCE}/chunks/chunk-{k:04d}.jsonl.gz", gzip.compress("\n".join(lines).encode()))
    return store


class FakeOpenAI:
    """Batches finish on the 2nd retrieve; `hold` keeps the last request of each batch unfinished."""

    def __init__(self, hold=False, prompt_tokens=1000, reject_first=False):
        self.reject_first = reject_first
        self.files_, self.batches_, self.polls, self.hold, self.pt = {}, {}, {}, hold, prompt_tokens
        self.files = NS(create=self._fcreate, content=lambda fid: NS(text=self.files_[fid]))
        self.batches = NS(create=self._bcreate, retrieve=self._retrieve, cancel=self._cancel,
                          list=lambda limit=100: list(self.batches_.values()))

    def _fcreate(self, file, purpose):
        fid = f"f{len(self.files_)}"
        self.files_[fid] = file[1].getvalue().decode()
        return NS(id=fid)

    def _bcreate(self, input_file_id, endpoint, completion_window, metadata):
        bid = f"b{len(self.batches_)}"
        reqs = [json.loads(l) for l in self.files_[input_file_id].splitlines()]
        self.batches_[bid] = NS(id=bid, status="in_progress", metadata=metadata, reqs=reqs, output_file_id=None,
                                error_file_id=None, request_counts=NS(total=len(reqs), completed=0), errors=None)
        if self.reject_first and len(self.batches_) == 1:      # the account's queue limit
            self.batches_[bid].status = "failed"
            self.batches_[bid].errors = NS(data=[NS(message="Enqueued token limit reached")])
        return self.batches_[bid]

    def _finish(self, b, n):
        out = [{"custom_id": r["custom_id"], "response": {"status_code": 200, "body": {
            "choices": [{"message": {"content": '{"x": 1}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": self.pt, "completion_tokens": 300, "prompt_tokens_details": {"cached_tokens": 400}}}}}
               for r in b.reqs[:n]]
        fid = f"out-{b.id}"
        self.files_[fid] = "\n".join(json.dumps(x) for x in out)
        b.output_file_id = fid

    def _retrieve(self, bid):
        b = self.batches_[bid]
        self.polls[bid] = self.polls.get(bid, 0) + 1
        if b.status == "in_progress" and self.hold and len(b.reqs) > 1:
            b.request_counts.completed = len(b.reqs) - 1       # stops one short, from the first look
        elif b.status == "in_progress" and self.polls[bid] >= (1 if len(b.reqs) == 1 else 2):
            b.status = "completed"
            self._finish(b, len(b.reqs))
        return b

    def _cancel(self, bid):
        b = self.batches_[bid]
        b.status = "cancelled"
        self._finish(b, b.request_counts.completed)


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(of, "JOB_SIZE", 2)
    monkeypatch.setattr(of.ue, "llm_cfg", lambda: {"usd_gbp": 0.79})
    monkeypatch.setattr(of, "estimate", lambda store=None: {"chunks": 3, "chunk_size": 2, "gbp_per_release": 0.001,
                                                           "gbp_est": 0.006})


def test_each_chunk_sent_once_and_collected(tmp_path):
    store, client = _setup(tmp_path), FakeOpenAI()
    st = of.run(max_gbp=1.0, client=client, store=store, poll_s=0)
    sent = sorted(r["custom_id"] for b in client.batches_.values() for r in b.reqs)
    assert sent == sorted(f"e-{k}-{i}" for k in range(3) for i in range(2))
    assert st["succeeded"] == 6 and st["finished"] and st["cached"] == 6 * 400
    body = client.batches_["b0"].reqs[0]["body"]
    assert body["model"] == "gpt-6-luna" and body["messages"][0] == {"role": "system", "content": "instr"}
    assert body["response_format"]["json_schema"]["strict"] is True
    of.run(max_gbp=1.0, client=client, store=store, poll_s=0)        # rerun after completion sends nothing
    assert len(client.batches_) == 3


def test_batch_created_before_a_crash_is_adopted_not_resent(tmp_path, monkeypatch):
    store, client = _setup(tmp_path), FakeOpenAI()
    real_save, calls = of._save, {"n": 0}

    def crash_after_create(store_, st):             # 1st save = initial job list; 2nd = after chunk 0 created
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        real_save(store_, st)
    monkeypatch.setattr(of, "_save", crash_after_create)
    with pytest.raises(KeyboardInterrupt):
        of.run(max_gbp=1.0, client=client, store=store, poll_s=0)
    monkeypatch.setattr(of, "_save", real_save)
    of.run(max_gbp=1.0, client=client, store=store, poll_s=0)
    assert len(client.batches_) == 3                 # chunk 0's batch was adopted, not created again


def test_stragglers_cancelled_and_resent(tmp_path, monkeypatch):
    store, client = _setup(tmp_path, chunks=1, size=4), FakeOpenAI(hold=True)
    monkeypatch.setattr(of, "estimate", lambda store=None: {"chunks": 1, "chunk_size": 4, "gbp_per_release": 0.001, "gbp_est": 0.004})
    monkeypatch.setattr(of, "STRAGGLER_H", -1.0)    # any age counts as stuck
    monkeypatch.setattr(of, "STRAGGLER_DONE", 0.7)  # 3 of 4 done = 75%
    monkeypatch.setattr(of, "JOB_SIZE", 4)
    st = of.run(max_gbp=1.0, client=client, store=store, poll_s=0)
    assert st["succeeded"] == 4 and st["finished"]
    assert any(k.endswith("retry1") for k in st["jobs"])


def test_cap_stops_new_batches(tmp_path, monkeypatch):
    # requests turn out far dearer than estimated (100M input tokens each): after the first batch
    # the measured spend passes the cap and nothing more is sent
    store, client = _setup(tmp_path), FakeOpenAI(prompt_tokens=100_000_000)
    monkeypatch.setattr(of, "MAX_IN_FLIGHT", 1)
    st = of.run(max_gbp=1.0, client=client, store=store, poll_s=0)
    assert len(client.batches_) == 1 and not st.get("finished") and st["spent_gbp"] > 1.0


def test_refuses_when_estimate_above_cap(tmp_path):
    with pytest.raises(SystemExit, match="above the cap"):
        of.run(max_gbp=0.001, client=FakeOpenAI(), store=_setup(tmp_path), poll_s=0)


def test_queue_limit_rejection_is_retried_with_fewer_in_flight(tmp_path, monkeypatch):
    store, client = _setup(tmp_path), FakeOpenAI(reject_first=True)
    of.run(max_gbp=1.0, client=client, store=store, poll_s=0, once=True)      # sends the first batches
    st = of.run(max_gbp=1.0, client=client, store=store, poll_s=0, once=True)  # sees the rejection
    assert st["max_in_flight"] == 1 and st.get("pause_until")   # 2 still queued at OpenAI when the 3rd was refused
    st["pause_until"] = None
    of._save(store, st)
    st = of.run(max_gbp=1.0, client=client, store=store, poll_s=0)
    assert st["succeeded"] == 6 and st["finished"]


def test_stalled_batch_is_cancelled_and_rest_resent(tmp_path, monkeypatch):
    """2026-11-10: OpenAI batches stopped at ~45% for over an hour. A batch whose progress doesn't
    move for STALL_H is cancelled; finished requests are kept, the rest go out in a retry batch."""
    store, client = _setup(tmp_path, chunks=1, size=4), FakeOpenAI(hold=True)   # stops at 3 of 4
    monkeypatch.setattr(of, "estimate", lambda store=None: {"chunks": 1, "chunk_size": 4, "gbp_per_release": 0.001, "gbp_est": 0.004})
    monkeypatch.setattr(of, "JOB_SIZE", 4)
    monkeypatch.setattr(of, "STALL_H", -1.0)        # any time without progress counts as a stall
    st = of.run(max_gbp=1.0, client=client, store=store, poll_s=0)
    assert st["succeeded"] == 4 and st["finished"]
    assert "chunk-0000-p0-retry1" in st["jobs"] and len(st["jobs"]["chunk-0000-p0-retry1"]["ids"]) == 1
    sent = [r["custom_id"] for b in client.batches_.values() for r in b.reqs]
    assert len(sent) == 5 and len(set(sent)) == 4    # only the unfinished one was sent again


def test_network_error_on_one_batch_is_retried_not_fatal(tmp_path):
    """2026-11-12: the droplet runner died twice mid-collection. One failing call must not stop the run."""
    store, client = _setup(tmp_path), FakeOpenAI()
    real, calls = client.batches.retrieve, {"n": 0}

    def flaky(bid):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConnectionError("reset by peer")
        return real(bid)
    client.batches.retrieve = flaky
    st = of.run(max_gbp=1.0, client=client, store=store, poll_s=0)
    assert st["succeeded"] == 6 and st["finished"] and len(client.batches_) == 3


def test_cancel_refused_while_finalizing_is_not_fatal(tmp_path, monkeypatch):
    """2026-11-12 crash: 409 'Cannot cancel a batch with status finalizing'."""
    store, client = _setup(tmp_path, chunks=1, size=4), FakeOpenAI()
    monkeypatch.setattr(of, "estimate", lambda store=None: {"chunks": 1, "chunk_size": 4, "gbp_per_release": 0.001, "gbp_est": 0.004})
    monkeypatch.setattr(of, "JOB_SIZE", 4)
    monkeypatch.setattr(of, "STALL_H", -1.0)

    def refuse(bid):
        raise RuntimeError("409 Cannot cancel a batch with status 'finalizing'")
    client.batches.cancel = refuse
    st = of.run(max_gbp=1.0, client=client, store=store, poll_s=0)
    assert st["succeeded"] == 4 and st["finished"]
