"""US filing downloader with 2 workers: every filing is fetched once, rows land correctly,
and 3 refusals in a row still stop the run."""
import httpx
import pytest

from tests.us.test_parse import INDEX_HTM


@pytest.fixture
def us_env(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"""
paths: {{data_dir: {tmp_path}/data, log_dir: {tmp_path}/data/logs}}
http: {{timeout_s: 5, max_retries: 0, backoff_base_s: 0, respect_robots: false}}
us: {{db: {tmp_path}/data/us.duckdb, raw_dir: {tmp_path}/data/raw/us, min_interval_s: 0, workers: 2,
      earnings_items: ["2.02"], news_items: ["8.01"], min_free_gb: 0, disk_warn_gb: 20}}
""")
    monkeypatch.setenv("RNS_CONFIG", str(cfg))
    monkeypatch.setenv("SEC_USER_AGENT", "Test test@example.com")
    import core.config
    core.config.load_config.cache_clear()
    from ingest.us import common, documents
    with common.session() as con:
        for i in range(120):   # more than one BATCH
            con.execute("INSERT INTO sec_filings VALUES (?, 320193, '8-K', DATE '2020-01-02', NULL, NULL, '2.02', 'a.htm', false)",
                        [f"0000000000-20-{i:06d}"])
    yield common, documents
    core.config.load_config.cache_clear()


def _use_transport(monkeypatch, documents, handler):
    real = documents.sec_client

    def client():
        c = real()
        c.respect_robots = False
        c.client = httpx.Client(transport=httpx.MockTransport(handler))
        return c
    monkeypatch.setattr(documents, "sec_client", client)


def test_two_workers_fetch_everything_once(us_env, monkeypatch):
    common, documents = us_env
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if "000099" in str(req.url):
            return httpx.Response(404)
        if str(req.url).endswith("-index.htm"):
            return httpx.Response(200, text=INDEX_HTM)
        return httpx.Response(200, text="<p>Revenue up 10%</p>")
    _use_transport(monkeypatch, documents, handler)
    documents.docs("earnings", limit=None, assume_yes=True)
    assert len(seen) == len(set(seen)) == 119 * 2 + 1
    with common.session(read_only=True) as con:
        rows = dict(con.execute("SELECT status, count(*) FROM us_filing_docs GROUP BY 1").fetchall())
        text = con.execute("SELECT text FROM us_filing_docs WHERE status='ok' LIMIT 1").fetchone()[0]
    assert rows == {"ok": 119, "not_found": 1}
    assert "Revenue up 10%" in text


def test_three_refusals_in_a_row_still_stop(us_env, monkeypatch):
    common, documents = us_env
    _use_transport(monkeypatch, documents, lambda req: httpx.Response(403))
    with pytest.raises(SystemExit) as e:
        documents.docs("earnings", limit=None, assume_yes=True)
    assert e.value.code == 3
    with common.session(read_only=True) as con:
        assert con.execute("SELECT count(*) FROM us_filing_docs").fetchone()[0] == 0
