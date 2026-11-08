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
