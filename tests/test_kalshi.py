from ingest.pm import kalshi as k


def test_batches_group_by_end_and_size():
    rows = [("a", "s", 0.0), ("b", "s", 10.0), ("c", "s", 1e6)]
    assert [[r[0] for r in b] for b in k.batches(rows)] == [["a", "b"], ["c"]]
    many = [(str(i), "s", float(i)) for i in range(85)]
    assert [len(b) for b in k.batches(many, size=40)] == [40, 40, 5]


def test_scheduled_end_prefers_expected_and_never_uses_close():
    m = {"expected_expiration_time": "2026-01-02T00:00:00Z", "latest_expiration_time": "2026-01-09T00:00:00Z",
         "close_time": "2025-12-30T00:00:00Z"}
    assert k.scheduled_end(m) == k.ts("2026-01-02T00:00:00Z")
    assert k.scheduled_end({"latest_expiration_time": "2026-01-09T00:00:00Z"}) == k.ts("2026-01-09T00:00:00Z")
    assert k.scheduled_end({"close_time": "2025-12-30T00:00:00Z"}) is None
