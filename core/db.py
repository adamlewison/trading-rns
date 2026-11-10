"""DuckDB connections, shared by both markets. Each market has its own database file and schema
(ingest/uk/schema.py, ingest/us/schema.py) and wraps these in its own `session()`."""
from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

import duckdb

# Small row groups (DuckDB's default is 122,880 rows). When a row group fills, its checkpoint
# compresses the whole group at once: with ~25KB of text per filing that took 135s on a 16GB machine
# and ran the 1GB droplet out of scratch space ("failed to offload data block", 2026-11-10). At 8,192
# rows the worst save measured 7s. DuckDB doesn't store this in the file, and a checkpoint under the
# default merges small groups back into big ones, so EVERY connection must open the file this way.
ROW_GROUP_SIZE = 8192


def connect(db, schema: str, read_only: bool = False, wait_s: float = 600,
            memory_limit: str | None = None) -> duckdb.DuckDBPyConnection:
    """DuckDB allows one writer *process* at a time. Long-running jobs therefore use short-lived
    connections (see `session`), and we wait for the lock instead of crashing. On a writable
    connection the market's schema (CREATE TABLE IF NOT EXISTS ...) is applied first."""
    path = db
    # caps passed at open time, so they also hold while DuckDB replays a leftover WAL
    cfg = ({"memory_limit": memory_limit, "threads": 1, "preserve_insertion_order": False}
           if memory_limit else {})
    cfg["temp_directory"] = str(path) + ".tmp"
    opts = f"ROW_GROUP_SIZE {ROW_GROUP_SIZE}" + (", READ_ONLY" if read_only else "")
    alias = Path(path).stem
    deadline = time.monotonic() + wait_s
    while True:
        con = duckdb.connect(config=cfg)            # in-memory host; the market file is attached below
        try:
            con.execute(f"ATTACH '{path}' AS \"{alias}\" ({opts}); USE \"{alias}\"")
            break
        except duckdb.Error as e:
            con.close()
            if "lock" not in str(e).lower() or time.monotonic() > deadline:
                raise
            time.sleep(1.0)
    if not read_only:
        con.execute(schema)
    return con


@contextmanager
def session(db, schema: str, read_only: bool = False, memory_limit: str | None = None):
    con = connect(db, schema, read_only=read_only, memory_limit=memory_limit)
    try:
        yield con
    finally:
        con.close()
