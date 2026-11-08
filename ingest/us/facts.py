"""Company facts: selected XBRL concepts from SEC's bulk companyfacts.zip."""
from __future__ import annotations

import json
import zipfile

import pandas as pd

from ingest.us.common import (
    FACTS_ZIP,
    bulk_download,
    log,
    mark_progress,
    session,
    us_cfg,
)


# ---------------------------------------------------------------- 5. company facts
def facts(refresh: bool = False) -> None:
    c = us_cfg()
    want = {tax: set(cs) for tax, cs in c["fact_concepts"].items()}
    zpath = bulk_download(FACTS_ZIP, c["raw_dir"] / "bulk" / "companyfacts.zip", refresh)
    with session() as con:
        ciks = {r[0] for r in con.execute("SELECT DISTINCT cik FROM sec_filings").fetchall()}
        con.execute("DELETE FROM us_facts")
    rows, n, n_rows = [], 0, 0

    def flush():
        nonlocal n_rows
        if rows:
            df = pd.DataFrame(rows, columns=["cik", "taxonomy", "concept", "unit", "val", "start_date",
                                             "end_date", "fy", "fp", "form", "filed", "accn", "frame"])
            for col in ("start_date", "end_date", "filed"):
                df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
            with session() as con:
                con.register("df", df)
                con.execute("INSERT INTO us_facts SELECT * FROM df")
            n_rows += len(rows)
            rows.clear()

    with zipfile.ZipFile(zpath) as z:
        for nm in z.namelist():
            try:
                cik = int(nm[3:13])
            except ValueError:
                continue
            if cik not in ciks:
                continue
            d = json.loads(z.read(nm))
            for tax, concepts in want.items():
                for concept in concepts:
                    node = d.get("facts", {}).get(tax, {}).get(concept)
                    if not node:
                        continue
                    for unit, vals in node.get("units", {}).items():
                        for v in vals:
                            rows.append((cik, tax, concept, unit, v.get("val"), v.get("start"), v.get("end"),
                                         v.get("fy"), v.get("fp"), v.get("form"), v.get("filed"),
                                         v.get("accn"), v.get("frame")))
            n += 1
            if len(rows) > 300_000:
                flush()
                log.info("  facts: %d CIKs, %d rows", n, n_rows)
    flush()
    with session() as con:
        mark_progress(con, "facts", "done", n_rows)
    log.info("facts: %d CIKs, %d rows", n, n_rows)
