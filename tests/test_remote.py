import json
from datetime import datetime, timedelta, timezone

from core import remote
from core.backup import LocalStore


def test_job_runs_once_and_result_is_stored(tmp_path):
    store = LocalStore(tmp_path)
    jid = remote.submit("echo hello; echo oops >&2; exit 3", cwd=str(tmp_path), store=store)
    assert remote.serve_once(store) == 1
    r = remote.result(jid, store)
    assert r["exit"] == 3 and r["stdout"] == "hello\n" and "oops" in r["stderr"]
    assert remote.serve_once(store) == 0                       # never run twice


def test_old_job_expires_and_timeout_is_enforced(tmp_path):
    store = LocalStore(tmp_path)
    old = {"id": "old", "cmd": "touch ran", "cwd": str(tmp_path), "timeout_s": 5,
           "created": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()}
    store.put(f"{remote.ROOT}/jobs/old.json", json.dumps(old).encode())
    jid = remote.submit("sleep 5", cwd=str(tmp_path), timeout_s=1, store=store)
    remote.serve_once(store)
    assert remote.result("old", store)["exit"] is None and not (tmp_path / "ran").exists()
    assert remote.result(jid, store)["exit"] == 124
