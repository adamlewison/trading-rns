"""Health check for a market database, safe to run while nothing else uses it.

    uv run python -W ignore -m core.dbcheck --profile us                 # read-only report
    uv run python -W ignore -m core.dbcheck --profile us --checkpoint    # also write pending changes (WAL) into the file

Why (2026-11-10): the US downloader died twice on the 1GB droplet, the second time with
"Failed to create checkpoint ... failed to offload data block (7.0 GiB/7.0 GiB used)": writing 50 rows
needed 7GB of scratch space, which points at a large backlog of un-checkpointed changes (WAL)
left by the earlier crash. The report shows the file, WAL and scratch sizes; --checkpoint folds the
WAL into the database with the memory cap applied from the start and a bounded scratch directory."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import duckdb

from core.backup import get_profile


def _sizes(db: Path) -> dict:
    out = {}
    for p in [db, Path(str(db) + ".wal"), Path(str(db) + ".tmp")]:
        if p.is_dir():
            out[p.name] = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        elif p.exists():
            out[p.name] = p.stat().st_size
    out["disk_free"] = shutil.disk_usage(db.parent).free
    return {k: f"{v / 1e9:.2f} GB" for k, v in out.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="us")
    ap.add_argument("--checkpoint", action="store_true")
    ap.add_argument("--memory", default="500MB")
    ap.add_argument("--max-temp", default="4GB")
    a = ap.parse_args()
    db = Path(get_profile(a.profile).db)
    print("files before:", _sizes(db))
    cfg = {"memory_limit": a.memory, "threads": 1, "preserve_insertion_order": False,
           "temp_directory": str(db) + ".tmp", "max_temp_directory_size": a.max_temp}
    con = duckdb.connect(str(db), read_only=not a.checkpoint, config=cfg)   # caps apply before WAL replay
    print(con.execute("PRAGMA database_size").df().T.to_string(header=False))
    for t, n in con.execute("SELECT table_name, estimated_size FROM duckdb_tables() ORDER BY 1").fetchall():
        print(f"  {t}: ~{n:,} rows")
    if a.checkpoint:
        con.execute("CHECKPOINT")
        print("checkpoint ok")
        print(con.execute("PRAGMA database_size").df().T.to_string(header=False))
    con.close()
    print("files after:", _sizes(db))


if __name__ == "__main__":
    main()
