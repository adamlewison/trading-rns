"""Backup round-trips against a LocalStore, for both markets: push -> wipe -> pull restores
everything, a second push only uploads what changed, raw pruning (US only) keeps files
restorable, and bulk files survive a chunked round trip."""
import io
import tarfile

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"""
paths: {{data_dir: {tmp_path}/data, log_dir: {tmp_path}/data/logs}}
http: {{timeout_s: 5, max_retries: 1, backoff_base_s: 0, respect_robots: false}}
backup: {{store: local, local_dir: {tmp_path}/store}}
uk: {{db: {tmp_path}/data/uk.duckdb, raw_dir: {tmp_path}/data/raw/uk, backup_prefix: trading-rns/v1,
      user_agent: test, announcements: {{min_interval_s: 0}}}}
us: {{db: {tmp_path}/data/us.duckdb, raw_dir: {tmp_path}/data/raw/us, backup_prefix: trading-rns/us/v1,
      disk_warn_gb: 20}}
""")
    monkeypatch.setenv("RNS_CONFIG", str(cfg))
    import core.config
    core.config.load_config.cache_clear()
    import core.backup
    yield tmp_path, core.backup, core.backup.LocalStore(tmp_path / "store")
    core.config.load_config.cache_clear()


def _tar_names(store, prefix):
    names = []
    for n in store.list(prefix):
        with tarfile.open(fileobj=io.BytesIO(store.get(n))) as t:
            names += t.getnames()
    return names


def test_uk_roundtrip_and_incremental(env):
    tmp, backup, store = env
    from ingest.uk.common import session
    with session() as con:
        con.execute("INSERT INTO universe (tidm, name) VALUES ('ABC', 'Abc plc')")
        con.execute("INSERT INTO ann_bodies VALUES ('s:1', 'text one', 8, 'ok', TIMESTAMP '2026-01-01 10:00')")
    raw = tmp / "data/raw/uk/html/x/ab"
    raw.mkdir(parents=True)
    (raw / "a.gz").write_bytes(b"page-a")

    backup.push(store, profile="uk")
    n1 = len(store.list("trading-rns/v1/"))
    assert _tar_names(store, "trading-rns/v1/raw/") == ["html/x/ab/a.gz"]

    # second push with one new body: only a new body part + manifest change
    with session() as con:
        con.execute("INSERT INTO ann_bodies VALUES ('s:2', 'text two', 8, 'ok', TIMESTAMP '2026-01-02 10:00')")
    backup.push(store, profile="uk")
    names = store.list("trading-rns/v1/")
    assert len(names) == n1 + 1
    assert len([n for n in names if "/ann_bodies/" in n]) == 2

    # wipe the machine, restore
    (tmp / "data/uk.duckdb").unlink()
    (tmp / "data/backup_manifest_uk.json").unlink()
    (raw / "a.gz").unlink()
    backup.pull(store, profile="uk")
    with session() as con:
        assert con.execute("SELECT count(*) FROM ann_bodies").fetchone()[0] == 2
        assert con.execute("SELECT name FROM universe").fetchone()[0] == "Abc plc"
    assert (raw / "a.gz").read_bytes() == b"page-a"


def test_markets_are_separate(env):
    tmp, backup, store = env
    from ingest.uk.common import session as uk_session
    from ingest.us.common import session as us_session
    with uk_session(), us_session():
        pass                                               # create both (empty) databases
    (tmp / "data/raw/uk/html/a").mkdir(parents=True)
    (tmp / "data/raw/uk/html/a/uk.gz").write_bytes(b"uk")
    (tmp / "data/raw/us/sec/x").mkdir(parents=True)
    (tmp / "data/raw/us/sec/x/us.gz").write_bytes(b"us")
    backup.push(store, profile="uk")
    backup.push(store, profile="us")
    assert _tar_names(store, "trading-rns/v1/raw/") == ["html/a/uk.gz"]
    assert _tar_names(store, "trading-rns/us/v1/raw/") == ["x/us.gz"]


def test_prune_raw_only_for_us_and_restorable(env):
    tmp, backup, store = env
    with pytest.raises(SystemExit):
        backup.push(store, profile="uk", prune_raw=True)   # the UK cache is never pruned

    from ingest.us.common import session
    with session() as con:
        con.execute("INSERT INTO us_progress VALUES ('k', 'done', 1, now())")
    f = tmp / "data/raw/us/sec/www.sec.gov/ab/page.gz"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"sec-page")
    backup.push(store, profile="us", prune_raw=True)
    assert not f.exists()                                  # pruned locally ...
    backup.push(store, profile="us", prune_raw=True)       # ... and a re-push doesn't choke

    (tmp / "data/us.duckdb").unlink()
    backup.pull(store, profile="us", skip_raw=True)
    assert not f.exists()
    with session() as con:
        assert con.execute("SELECT count(*) FROM us_progress").fetchone()[0] == 1
    backup.pull(store, profile="us", force=True)
    assert f.read_bytes() == b"sec-page"                   # ... but restorable from the backup


def test_bulk_chunked_roundtrip(env, monkeypatch):
    tmp, backup, store = env
    monkeypatch.setattr(backup, "BULK_CHUNK", 10)
    bulk = tmp / "data/raw/us/bulk"
    bulk.mkdir(parents=True)
    payload = bytes(range(256)) * 3
    (bulk / "submissions.zip").write_bytes(payload)
    backup.push_bulk(store)
    n = len(store.list("trading-rns/us/v1/bulk/"))
    assert n == -(-len(payload) // 10)
    backup.push_bulk(store)                                # already complete: nothing re-uploaded
    assert len(store.list("trading-rns/us/v1/bulk/")) == n
    (bulk / "submissions.zip").unlink()
    backup.pull_bulk(store)
    assert (bulk / "submissions.zip").read_bytes() == payload


def test_push_never_backs_up_a_missing_or_emptied_db(env):
    tmp, backup, store = env
    with pytest.raises(SystemExit):
        backup.push(store, profile="uk")                   # no DB yet: refuse, don't create one
    assert not (tmp / "data/uk.duckdb").exists()

    from ingest.uk.common import session
    with session() as con:
        con.execute("INSERT INTO universe (tidm, name) VALUES ('ABC', 'Abc plc')")
    backup.push(store, profile="uk")
    good = store.get("trading-rns/v1/db/universe.parquet")
    with session() as con:
        con.execute("DELETE FROM universe")
    backup.push(store, profile="uk")
    assert store.get("trading-rns/v1/db/universe.parquet") == good


def test_large_backlog_goes_up_in_parts_and_restores(env, monkeypatch):
    """A long backlog of new rows is pushed in several small parts (one big COPY ran the 1GB
    droplet out of memory); rows sharing a timestamp stay together; pull restores them all once."""
    tmp, backup, store = env
    monkeypatch.setattr(backup, "INCR_PART_ROWS", 2)
    from ingest.uk.common import session
    with session() as con:
        for i, ts in enumerate(["01", "02", "02", "02", "03"]):
            con.execute(f"INSERT INTO ann_bodies VALUES ('s:{i}', 't', 1, 'ok', TIMESTAMP '2026-01-{ts} 10:00')")
    backup.push(store, profile="uk")
    parts = [n for n in store.list("trading-rns/v1/") if "/ann_bodies/" in n]
    assert len(parts) == 2                      # [01, 02, 02, 02] then [03]
    backup.push(store, profile="uk")            # nothing new: no extra part
    assert len([n for n in store.list("trading-rns/v1/") if "/ann_bodies/" in n]) == 2
    (tmp / "data/uk.duckdb").unlink()
    (tmp / "data/backup_manifest_uk.json").unlink()
    backup.pull(store, profile="uk")
    with session() as con:
        assert con.execute("SELECT count(*) FROM ann_bodies").fetchone()[0] == 5
