"""Backup round-trip against a LocalStore: push -> wipe -> pull restores everything,
and a second push only uploads what changed."""
import importlib

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"""
paths: {{data_dir: {tmp_path}/data, db: {tmp_path}/data/rns.duckdb, raw_dir: {tmp_path}/data/raw, log_dir: {tmp_path}/data/logs}}
backup: {{store: local, local_dir: {tmp_path}/store, prefix: t/v1}}
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
    n1 = len(store.list("t/v1/"))

    # second push with one new body: only a new body part + manifest change
    with session() as con:
        con.execute("INSERT INTO ann_bodies VALUES ('s:2', 'text two', 8, 'ok', TIMESTAMP '2026-01-02 10:00')")
    backup.push(store)
    names = store.list("t/v1/")
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
    for n in store.list("t/v1/raw/"):
        with tarfile.open(fileobj=io.BytesIO(store.get(n))) as t:
            names += t.getnames()
    assert names == ["html/a/uk.gz"]
