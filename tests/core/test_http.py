"""core.http: refusals must never be cached or mistaken for 'no data'."""
import httpx
import pytest

from core.http import Blocked, PoliteClient


def test_blocked_responses_are_not_cached_or_treated_as_empty(tmp_path, monkeypatch):
    c = PoliteClient(cache_root=tmp_path, user_agent="test", min_interval_s=0)
    c.respect_robots = False
    c.max_retries = 1
    c.backoff = 0
    c.client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(429, text="slow down")))
    with pytest.raises(Blocked):
        c.get("https://example.test/x")
    assert not c.cached("https://example.test/x")


def test_rate_limit_holds_across_threads_and_workers_overlap_latency(tmp_path):
    """Two workers sharing a client: request starts stay >= min_interval apart (SEC's cap holds),
    but slow replies overlap, so it finishes faster than one worker would."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    starts, lock = [], threading.Lock()

    def slow(req):
        with lock:
            starts.append(time.monotonic())
        time.sleep(0.12)                     # server latency > min_interval
        return httpx.Response(200, text="ok")

    c = PoliteClient(cache_root=tmp_path, user_agent="test", min_interval_s=0.05)
    c.respect_robots = False
    c.client = httpx.Client(transport=httpx.MockTransport(slow))
    t0 = time.monotonic()
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda i: c.get(f"https://example.test/{i}"), range(12)))
    elapsed = time.monotonic() - t0
    starts.sort()
    assert min(b - a for a, b in zip(starts, starts[1:])) >= 0.05 - 0.005
    assert elapsed < 12 * 0.12 * 0.75        # one worker would need >= 1.44s
    assert c.n_network == 12


def test_a_retry_backs_off_every_thread(tmp_path):
    """A 503 seen by one worker pushes back the next request slot for all of them."""
    import time
    c = PoliteClient(cache_root=tmp_path, user_agent="test", min_interval_s=0.01)
    c._pause("example.test", 0.3)
    t0 = time.monotonic()
    c._wait("example.test")
    assert time.monotonic() - t0 >= 0.25
