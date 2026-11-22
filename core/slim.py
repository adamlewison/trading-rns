"""Keep only what the droplet uses; everything else lives in B2 (the user's rule, 2026-11-23).

    uv run python -W ignore -m core.slim plan --profile us|uk        # what would go, checked against B2
    uv run python -W ignore -m core.slim run  --profile us|uk        # delete those rows, then rebuild the file

What goes (only rows B2 already holds, checked against the backup manifest):
  us: 8-K texts (us_filing_docs) fetched up to the backup watermark; archived tables (us_insider_tx)
      whose snapshot in B2 has the same row count.
  uk: announcement bodies up to the watermark for announcements published before KEEP_UK_SINCE (the
      retail-offers trial reads recent ones); the derived `announcements` tables (rebuild with
      `ingest.uk.announcements.run build`).
DuckDB never shrinks a file after deletes, so `run` copies the tables into a fresh file and swaps it in
(the old file is kept as <db>.old until the new one opens; delete it by hand). Stop the market's backup
loop first. A cloud session that needs any of it gets it back with `core.backup pull`."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from core.backup import _content_hash, get_profile, get_store
from core.db import session
from core.log import get_logger

log = get_logger("slim")
KEEP_UK_SINCE = "2026-11-01"
DERIVED_UK = ("announcements", "announcements_admin")


def _manifest(pf, store) -> dict:
    return json.loads(store.get(f"{pf.prefix}/manifest.json") or b"{}")


def statements(profile: str, store=None) -> list[tuple[str, str]]:
    """(description, SQL) for everything safe to remove; each checked against what B2 holds."""
    pf, store = get_profile(profile), store or get_store()
    m = _manifest(pf, store)
    wm = m.get("bodies_watermark")
    out = []
    with session(pf.db, pf.schema, read_only=True) as con:
        tables = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
        if wm:
            if profile == "uk":
                cond = (f"fetched_at <= TIMESTAMP '{wm}' AND ann_id IN "
                        f"(SELECT ann_id FROM ann_index WHERE published_uk < TIMESTAMP '{KEEP_UK_SINCE}')")
            else:
                cond = f"fetched_at <= TIMESTAMP '{wm}'"
            n = con.execute(f"SELECT count(*) FROM {pf.incr_table} WHERE {cond}").fetchone()[0]
            if n:
                out.append((f"{pf.incr_table}: {n:,} rows already in B2", f"DELETE FROM {pf.incr_table} WHERE {cond}"))
        for t in pf.archived_tables:
            if t not in tables:
                continue
            n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            if not n:
                continue
            same = (m.get("table_rows", {}).get(t) == n and m.get("tables", {}).get(t) == _content_hash(con, t)
                    and f"{pf.prefix}/db/{t}.parquet" in store.list(f"{pf.prefix}/db/"))
            if not same:
                log.warning("%s: B2 snapshot missing or different (%s rows locally); kept", t, n)
                continue
            out.append((f"{t}: {n:,} rows, identical snapshot in B2", f"DELETE FROM {t}"))
        if profile == "uk":
            for t in DERIVED_UK:
                if t in tables:
                    out.append((f"{t}: derived table (rebuild with announcements.run build)", f"DROP TABLE {t}"))
    return out


def compact(db: Path, schema: str, memory_limit: str = "450MB") -> None:
    """Copy every table into a fresh file (schema first, so keys survive), then swap it in."""
    new = db.with_suffix(".new.duckdb")
    for p in (new, Path(str(new) + ".wal")):
        p.unlink(missing_ok=True)
    with session(new, schema, memory_limit=memory_limit) as con:
        con.execute(f"ATTACH '{db}' AS src (READ_ONLY)")
        src = [r[0] for r in con.execute("SELECT table_name FROM duckdb_tables() WHERE database_name='src'").fetchall()]
        have = {r[0] for r in con.execute(f"SELECT table_name FROM duckdb_tables() WHERE database_name='{new.stem}'").fetchall()}
        for t in src:
            if t in have:
                con.execute(f'INSERT INTO "{t}" BY NAME SELECT * FROM src."{t}"')
            else:
                con.execute(f'CREATE TABLE "{t}" AS SELECT * FROM src."{t}"')
            a = con.execute(f'SELECT count(*) FROM src."{t}"').fetchone()[0]
            b = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            if a != b:
                raise SystemExit(f"compact: {t} copied {b} of {a} rows; old file untouched")
            log.info("compact: %s %d rows", t, b)
        con.execute("DETACH src")
        con.execute("CHECKPOINT")
    old = db.with_suffix(".old.duckdb")
    os.replace(db, old)
    os.replace(new, db)
    log.info("compact: %s %.2f GB -> %.2f GB (old file kept as %s)", db.name, old.stat().st_size / 1e9,
             db.stat().st_size / 1e9, old.name)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "run"])
    ap.add_argument("--profile", choices=["us", "uk"], required=True)
    a = ap.parse_args()
    st = statements(a.profile)
    for d, _ in st:
        print("-", d)
    if a.cmd == "run":
        pf = get_profile(a.profile)
        with session(pf.db, pf.schema) as con:
            for d, sql in st:
                con.execute(sql)
                log.info("slim: %s", d)
            con.execute("CHECKPOINT")
        compact(pf.db, pf.schema)


if __name__ == "__main__":
    main()
