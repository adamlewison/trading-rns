"""UK addition A: LSE trading service (SETS / SETSqx / other) per ticker, plus registered market makers.

Run AFTER the UK scrape has finished (it writes to the UK DB).

    uv run python -m ingest.uk_trading_service build            # service map + per-ticker service
    uv run python -m ingest.uk_trading_service import-mm FILE   # "Registered Market Makers by Security"
    uv run python -m ingest.uk_trading_service report

Sources (official, LSE):
  * Instrument list (docs.londonstockexchange.com/reports/instruments): each line's Market Segment
    Code + Market Sector Code. We keep every monthly snapshot we download (lse_instruments), so the
    per-ticker history grows from the first download onward; LSE publishes no back history.
  * "Millennium Exchange and TRADEcho Business Parameters" workbook, sheet "Sector Breakdown":
    maps (segment, sector) -> Trading Service. Versions are stored by download date.
  * "Registered Market Makers by Security" (xls) is only linked from LSE's JavaScript page, whose
    API refuses scripted access, so it is downloaded by hand and imported with `import-mm`.
"""
from __future__ import annotations

import argparse
import io
import re
from datetime import date
from pathlib import Path

import pandas as pd

from core.db import session
from core.http import PoliteClient
from core.log import get_logger

log = get_logger("uk_trading_service")

BP_URL = ("https://www.londonstockexchange.com/content/dam/lse/en_us/documents/technical-library/"
          "millennium-exchange-and-tradecho-business-parameters.xlsx")

SCHEMA = """
CREATE TABLE IF NOT EXISTS lse_service_map (
    version_date DATE, trading_service VARCHAR, segment_code VARCHAR, segment_name VARCHAR,
    sector_code VARCHAR, sector_name VARCHAR
);
CREATE TABLE IF NOT EXISTS uk_trading_service (       -- one row per ticker per instrument-list snapshot
    snapshot_date DATE, tidm VARCHAR, segment_code VARCHAR, sector_code VARCHAR,
    trading_service VARCHAR, map_version DATE,
    PRIMARY KEY (snapshot_date, tidm)
);
CREATE TABLE IF NOT EXISTS uk_market_makers (         -- long format: one row per (security, market maker)
    as_of DATE, tidm VARCHAR, isin VARCHAR, market_maker VARCHAR, source_file VARCHAR
);
"""


def parse_service_map(xlsx: bytes) -> pd.DataFrame:
    raw = pd.read_excel(io.BytesIO(xlsx), sheet_name="Sector Breakdown", header=None, engine="calamine")
    h = next(i for i in range(30) if raw.iloc[i].astype(str).str.strip().eq("Segment Code").any())
    d = pd.read_excel(io.BytesIO(xlsx), sheet_name="Sector Breakdown", header=h, engine="calamine")
    d.columns = [str(c).strip() for c in d.columns]
    d = d.dropna(subset=["Segment Code", "Sector Code"])
    return pd.DataFrame({
        "trading_service": d["Trading Service"].astype(str).str.strip(),
        "segment_code": d["Segment Code"].astype(str).str.strip(),
        "segment_name": d["Segment Name"].astype(str).str.strip(),
        "sector_code": d["Sector Code"].astype(str).str.strip(),
        "sector_name": d["Sector Name"].astype(str).str.strip(),
    }).drop_duplicates(["segment_code", "sector_code"])


def build() -> None:
    client = PoliteClient(cache_subdir="lse", min_interval_s=1.0)
    status, body = client.get(BP_URL, use_cache=False)
    if status != 200:
        raise SystemExit(f"business parameters download failed: HTTP {status}")
    m = parse_service_map(body)
    v = date.today()
    with session() as con:
        con.execute(SCHEMA)
        con.execute("DELETE FROM lse_service_map WHERE version_date = ?", [v])
        m.insert(0, "version_date", v)
        con.register("m", m)
        con.execute("INSERT INTO lse_service_map SELECT * FROM m")
        # every instrument-list snapshot we hold, mapped with the newest map version available
        con.execute("""
            INSERT OR REPLACE INTO uk_trading_service
            SELECT i.snapshot_date, i.tidm, i.segment_code, i.sector_code,
                   coalesce(s.trading_service, 'UNMAPPED'), s.version_date
            FROM lse_instruments i
            LEFT JOIN (SELECT * FROM lse_service_map
                       WHERE version_date = (SELECT max(version_date) FROM lse_service_map)) s
              ON s.segment_code = i.segment_code AND s.sector_code = i.sector_code
            WHERE i.tidm IN (SELECT tidm FROM universe)""")
        print(con.execute("""SELECT u.grp, t.trading_service, count(*) n FROM uk_trading_service t
            JOIN universe u USING (tidm) WHERE t.snapshot_date = (SELECT max(snapshot_date) FROM uk_trading_service)
            GROUP BY ALL ORDER BY ALL""").df().to_string(index=False))
    log.info("service map version %s: %d (segment, sector) rows", v, len(m))


def parse_mm_file(path: Path) -> pd.DataFrame:
    """Robust to layout: finds the header row containing a TIDM/ISIN column; market makers are either
    one column ('Market Maker'/'Mnemonic'/'Firm') with one row per pair, or several MM columns."""
    raw = pd.read_excel(path, sheet_name=0, header=None, engine="calamine")
    h = next(i for i in range(min(40, len(raw)))
             if raw.iloc[i].astype(str).str.contains(r"^\s*(TIDM|ISIN|Mnemonic|Security)", case=False, regex=True).any())
    d = pd.read_excel(path, sheet_name=0, header=h, engine="calamine")
    d.columns = [str(c).strip() for c in d.columns]
    pick = lambda pat: next((c for c in d.columns if re.search(pat, c, re.I)), None)
    tidm_c, isin_c = pick(r"^(tidm|security mnemonic|ticker)"), pick(r"isin")
    mm_cols = [c for c in d.columns if re.search(r"market ?maker|mm ?code|firm|participant", c, re.I)]
    if not mm_cols or not (tidm_c or isin_c):
        raise SystemExit(f"Unrecognised layout; columns: {list(d.columns)}")
    long = d.melt(id_vars=[c for c in (tidm_c, isin_c) if c], value_vars=mm_cols, value_name="market_maker")
    long = long.dropna(subset=["market_maker"])
    long["market_maker"] = long["market_maker"].astype(str).str.strip()
    long = long[long["market_maker"].ne("")]
    return pd.DataFrame({
        "tidm": long[tidm_c].astype(str).str.strip() if tidm_c else None,
        "isin": long[isin_c].astype(str).str.strip() if isin_c else None,
        "market_maker": long["market_maker"],
    }).drop_duplicates()


def import_mm(path: str, as_of: str | None) -> None:
    p = Path(path)
    df = parse_mm_file(p)
    df.insert(0, "as_of", date.fromisoformat(as_of) if as_of else date.today())
    df["source_file"] = p.name
    with session() as con:
        con.execute(SCHEMA)
        con.execute("DELETE FROM uk_market_makers WHERE as_of = ?", [df["as_of"].iloc[0]])
        con.register("df", df)
        con.execute("INSERT INTO uk_market_makers SELECT as_of, tidm, isin, market_maker, source_file FROM df")
        # ISIN -> TIDM via the instrument list when the file has no TIDMs
        con.execute("""UPDATE uk_market_makers m SET tidm = i.tidm FROM lse_instruments i
                       WHERE m.tidm IS NULL AND m.isin = i.isin""")
    log.info("imported %d (security, market maker) rows from %s", len(df), p.name)
    report()


def report() -> None:
    with session(read_only=True) as con:
        print(con.execute("""
            WITH s AS (SELECT * FROM uk_trading_service WHERE snapshot_date = (SELECT max(snapshot_date) FROM uk_trading_service)),
                 mm AS (SELECT tidm, count(DISTINCT market_maker) n_mm FROM uk_market_makers
                        WHERE as_of = (SELECT max(as_of) FROM uk_market_makers) GROUP BY 1)
            SELECT u.grp, s.trading_service, count(*) tickers,
                   count(*) FILTER (WHERE coalesce(mm.n_mm, 0) = 0) no_market_makers,
                   round(avg(coalesce(mm.n_mm, 0)), 1) avg_market_makers
            FROM s JOIN universe u USING (tidm) LEFT JOIN mm USING (tidm) GROUP BY ALL ORDER BY ALL""").df().to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "import-mm", "report"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--as-of", help="import-mm: date the file is valid for (default today)")
    a = ap.parse_args()
    if a.cmd == "build":
        build()
    elif a.cmd == "import-mm":
        import_mm(a.file, a.as_of)
    else:
        report()
