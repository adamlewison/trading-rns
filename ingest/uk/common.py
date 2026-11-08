"""UK plumbing: config, the UK DuckDB (data/uk.duckdb) and HTTP clients. Mirrors ingest/us/common.py."""
from __future__ import annotations

from contextlib import contextmanager

from core import db as _db
from core.config import market
from core.http import PoliteClient
from ingest.uk.schema import SCHEMA


def uk_cfg() -> dict:
    return market("uk")


def connect(read_only: bool = False):
    return _db.connect(uk_cfg()["db"], SCHEMA, read_only=read_only)


@contextmanager
def session(read_only: bool = False):
    with _db.session(uk_cfg()["db"], SCHEMA, read_only=read_only) as con:
        yield con


def client(cache_subdir: str, min_interval_s: float | None = None) -> PoliteClient:
    """Polite client for UK sources; responses cached under data/raw/uk/<cache_subdir>."""
    c = uk_cfg()
    return PoliteClient(cache_root=c["raw_dir"], user_agent=c["user_agent"], cache_subdir=cache_subdir,
                        min_interval_s=c["announcements"]["min_interval_s"] if min_interval_s is None
                        else min_interval_s)
