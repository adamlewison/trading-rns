"""core.slim removes only what B2 holds, rebuilds the file, and `pull` brings it all back."""
from tests.core.test_backup import env  # noqa: F401  (fixture)


def test_us_slim_keeps_unbacked_rows_and_pull_restores(env, monkeypatch):  # noqa: F811
    tmp, backup, store = env
    import dataclasses

    import ingest.us.backup as usb
    current = usb.profile
    monkeypatch.setattr(usb, "profile", lambda: dataclasses.replace(       # as before: insider table snapshotted
        current(), snapshot_tables=[*current().snapshot_tables, "us_insider_tx"], archived_tables=()))
    from core import slim
    from ingest.us.common import session
    with session() as con:
        con.execute("INSERT INTO us_filing_docs (accession, fetched_at) VALUES ('a1', TIMESTAMP '2026-11-01 00:00')")
        con.execute("INSERT INTO us_insider_tx (accession, quarter) VALUES ('f1', '2024q1')")
    backup.push(store, profile="us")
    monkeypatch.setattr(usb, "profile", current)                  # now: archived
    with session() as con:                                        # written after the push: not in B2 yet
        con.execute("INSERT INTO us_filing_docs (accession, fetched_at) VALUES ('a2', TIMESTAMP '2026-11-20 00:00')")

    st = slim.statements("us", store)
    assert [d.split(":")[0] for d, _ in st] == ["us_filing_docs", "us_insider_tx"]
    pf = backup.get_profile("us")
    with session() as con:
        for _, sql in st:
            con.execute(sql)
    slim.compact(pf.db, pf.schema, memory_limit="200MB")
    with session(read_only=True) as con:
        assert con.execute("SELECT list(accession) FROM us_filing_docs").fetchone()[0] == ["a2"]
        assert con.execute("SELECT count(*) FROM us_insider_tx").fetchone()[0] == 0

    backup.push(store, profile="us")                              # archived table is not pushed empty
    backup.pull(store, force=True, profile="us")
    with session(read_only=True) as con:
        assert sorted(con.execute("SELECT list(accession) FROM us_filing_docs").fetchone()[0]) == ["a1", "a2"]
        assert con.execute("SELECT count(*) FROM us_insider_tx").fetchone()[0] == 1
