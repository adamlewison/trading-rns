"""DuckDB connections, shared by both markets. Each market has its own database file and schema
(ingest/uk/schema.py, ingest/us/schema.py) and wraps these in its own `session()`."""
from __future__ import annotations

import time
from contextlib import contextmanager

import duckdb


def connect(db, schema: str, read_only: bool = False, wait_s: float = 600,
            memory_limit: str | None = None) -> duckdb.DuckDBPyConnection:
    """DuckDB allows one writer *process* at a time. Long-running jobs therefore use short-lived
    connections (see `session`), and we wait for the lock instead of crashing. On a writable
    connection the market's schema (CREATE TABLE IF NOT EXISTS ...) is applied first."""
    path = db
    # caps passed at open time, so they also hold while DuckDB replays a leftover WAL
    cfg = ({"memory_limit": memory_limit, "threads": 1, "preserve_insertion_order": False}
           if memory_limit else {})
    deadline = time.monotonic() + wait_s
    while True:
        try:
            con = duckdb.connect(str(path), read_only=read_only, config=cfg)
            break
        except duckdb.IOException as e:
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
