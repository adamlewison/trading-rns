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


def test_save_waits_out_a_memory_shortage(us_env, monkeypatch):
    """2026-11-10: the downloader died when the backup took the droplet's memory. A save that runs
    out of memory is retried after a pause; the rows still land and nothing is fetched twice."""
    import duckdb
    common, documents = us_env
    monkeypatch.setattr(documents, "FLUSH_RETRY_S", 0)
    real, fails = common.session, {"n": 2}

    def flaky(read_only=False, memory_limit=None):
        assert read_only or memory_limit == documents.FLUSH_MEMORY_LIMIT
        if not read_only and fails["n"]:
            fails["n"] -= 1
            raise duckdb.OutOfMemoryException("failed to allocate")
        return real(read_only=read_only, memory_limit=memory_limit)
    monkeypatch.setattr(documents, "session", flaky)
    _use_transport(monkeypatch, documents, lambda req: httpx.Response(
        200, text=INDEX_HTM if str(req.url).endswith("-index.htm") else "<p>ok</p>"))
    documents.docs("earnings", limit=None, assume_yes=True)
    with common.session(read_only=True) as con:
        assert con.execute("SELECT count(*) FROM us_filing_docs").fetchone()[0] == 120


def test_memory_cap_is_applied(tmp_path):
    from core.db import session
    with session(tmp_path / "x.duckdb", "CREATE TABLE IF NOT EXISTS t (a INT)", memory_limit="200MB") as con:
        lim, th = con.execute("SELECT current_setting('memory_limit'), current_setting('threads')").fetchone()
    assert lim.startswith("190") or "MiB" in lim or "MB" in lim
    assert th == 1
