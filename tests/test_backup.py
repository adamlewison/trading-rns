"""Backup round-trip against a LocalStore: push -> wipe -> pull restores everything,
and a second push only uploads what changed."""
import importlib

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"""
paths: {{data_dir: {tmp_path}/data, db: {tmp_path}/data/rns.duckdb, raw_dir: {tmp_path}/data/raw, log_dir: {tmp_path}/data/logs}}
backup: {{store: local, local_dir: {tmp_path}/store, prefix: trading-rns/v1}}
us: {{db: {tmp_path}/data/us.duckdb, raw_dir: {tmp_path}/data/raw/us, disk_warn_gb: 20}}
""")
    monkeypatch.setenv("RNS_CONFIG", str(cfg))
    import core.config
    core.config.load_config.cache_clear()
    import core.backup
    importlib.reload(core.backup)
    yield tmp_path, core.backup
    core.config.load_config.cache_clear()


def test_roundtrip_and_incremental(env):
    tmp, backup = env
    from core.db import session
    with session() as con:
        con.execute("INSERT INTO universe (tidm, name) VALUES ('ABC', 'Abc plc')")
        con.execute("INSERT INTO ann_bodies VALUES ('s:1', 'text one', 8, 'ok', TIMESTAMP '2026-01-01 10:00')")
    raw = tmp / "data/raw/html/x/ab"
    raw.mkdir(parents=True)
    (raw / "a.gz").write_bytes(b"page-a")

    store = backup.LocalStore(tmp / "store")
    backup.push(store)
    n1 = len(store.list("trading-rns/v1/"))

    # second push with one new body: only a new body part + manifest change
    with session() as con:
        con.execute("INSERT INTO ann_bodies VALUES ('s:2', 'text two', 8, 'ok', TIMESTAMP '2026-01-02 10:00')")
    backup.push(store)
    names = store.list("trading-rns/v1/")
    assert len(names) == n1 + 1
    assert len([n for n in names if "/ann_bodies/" in n]) == 2

    # wipe the machine, restore
    (tmp / "data/rns.duckdb").unlink()
    (tmp / "data/backup_manifest.json").unlink()
    (raw / "a.gz").unlink()
    backup.pull(store)
    with session() as con:
        assert con.execute("SELECT count(*) FROM ann_bodies").fetchone()[0] == 2
        assert con.execute("SELECT name FROM universe").fetchone()[0] == "Abc plc"
    assert (raw / "a.gz").read_bytes() == b"page-a"


def test_uk_backup_excludes_us_raw_cache(env):
    tmp, backup = env
    (tmp / "data/raw/html/a").mkdir(parents=True)
    (tmp / "data/raw/html/a/uk.gz").write_bytes(b"uk")
    (tmp / "data/raw/us/sec/x").mkdir(parents=True)
    (tmp / "data/raw/us/sec/x/us.gz").write_bytes(b"us")
    store = backup.LocalStore(tmp / "store")
    backup.push(store)
    import io
    import tarfile
    names = []
    for n in store.list("trading-rns/v1/raw/"):
        with tarfile.open(fileobj=io.BytesIO(store.get(n))) as t:
            names += t.getnames()
    assert names == ["html/a/uk.gz"]


def test_prune_raw_only_for_us_and_restorable(env, monkeypatch):
    tmp, backup = env
    store = backup.LocalStore(tmp / "store")
    with pytest.raises(SystemExit):
        backup.push(store, profile="uk", prune_raw=True)   # UK cache is never pruned

    monkeypatch.setenv("US_DB", str(tmp / "data/us.duckdb"))
    monkeypatch.setenv("US_RAW_DIR", str(tmp / "data/raw/us"))
    from core.db import session
    from ingest.us.common import US_SCHEMA
    with session(db=tmp / "data/us.duckdb", schema=US_SCHEMA) as con:
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
    with session(db=tmp / "data/us.duckdb", schema=US_SCHEMA) as con:
        assert con.execute("SELECT count(*) FROM us_progress").fetchone()[0] == 1
    backup.pull(store, profile="us", force=True)
    assert f.read_bytes() == b"sec-page"                   # ... but restorable from the backup


def test_bulk_chunked_roundtrip(env, monkeypatch):
    tmp, backup = env
    monkeypatch.setattr(backup, "BULK_CHUNK", 10)
    bulk = tmp / "data/raw/us/bulk"
    bulk.mkdir(parents=True)
    payload = bytes(range(256)) * 3
    (bulk / "submissions.zip").write_bytes(payload)
    store = backup.LocalStore(tmp / "store")
    backup.push_bulk(store)
    n = len(store.list("trading-rns/us/v1/bulk/"))
    assert n == -(-len(payload) // 10)
    backup.push_bulk(store)                                # already complete: nothing re-uploaded
    assert len(store.list("trading-rns/us/v1/bulk/")) == n
    (bulk / "submissions.zip").unlink()
    backup.pull_bulk(store)
    assert (bulk / "submissions.zip").read_bytes() == payload
