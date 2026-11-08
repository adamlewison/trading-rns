"""Build the research universe: AIM companies + a FTSE SmallCap proxy.

Sources (LSE public reports, docs.londonstockexchange.com):
  * Instrument list (current; has TIDMs, market, FTSE index sector codes)
  * Monthly issuer-list snapshots back to 1999 (company name, market, market cap; NO TIDM)
Delisted companies are added later from the announcement index (TIDMs seen in RNS listings
that are not in today's instrument list), matched by name to the historic snapshots.

    uv run python -m ingest.uk.universe build
    uv run python -m ingest.uk.universe add-delisted     # after announcements index exists
    uv run python -m ingest.uk.universe report
"""
from __future__ import annotations

import argparse
import io
import json
import re
import zipfile
from datetime import date, datetime

import pandas as pd

from core.http import PoliteClient
from core.log import get_logger
from ingest.uk.common import client as uk_client
from ingest.uk.common import connect, uk_cfg
from ingest.uk.names import normalise_name, tidm_to_yahoo

log = get_logger("universe")

KEEP_FCA_MAIN = {
    "Equity shares (commercial companies)",
    "Equity shares (transition)",
}


def _download(client: PoliteClient, url: str, cache: bool) -> bytes:
    status, body = client.get(url, use_cache=cache)
    if status != 200:
        raise RuntimeError(f"HTTP {status} for {url}")
    return body


def _s(col: pd.Series) -> pd.Series:
    """String column with NaN -> '' (pandas 3 keeps NaN through astype(str))."""
    return col.map(lambda v: "" if pd.isna(v) else str(v).strip())


def _header_row(raw: pd.DataFrame, must_contain: str) -> int:
    for i in range(min(30, len(raw))):
        if raw.iloc[i].map(lambda v: str(v).strip()).eq(must_contain).any():
            return i
    raise ValueError(f"header row with {must_contain!r} not found")


def _as_at(raw: pd.DataFrame) -> date | None:
    for i in range(min(10, len(raw))):
        for v in raw.iloc[i].map(str):
            m = re.search(r"As at (\d{1,2} \w+ \d{4})", v)
            if m:
                return datetime.strptime(m.group(1), "%d %B %Y").date()
    return None


# ---------------------------------------------------------------- instrument list
def load_instruments(client: PoliteClient) -> pd.DataFrame:
    cfg = uk_cfg()["universe"]
    # "latest" changes monthly: bypass cache, but the response is also cached for audit.
    body = _download(client, cfg["instrument_list_url"], cache=False)
    raw = pd.read_excel(io.BytesIO(body), sheet_name="1.1 Shares", header=None, engine="calamine")
    asat = _as_at(raw) or date.today()
    h = _header_row(raw, "TIDM")
    df = pd.read_excel(io.BytesIO(body), sheet_name="1.1 Shares", header=h, engine="calamine")
    df.columns = [str(c).strip() for c in df.columns]
    out = pd.DataFrame({
        "snapshot_date": asat,
        "tidm": _s(df["TIDM"]),
        "issuer_name": _s(df["Issuer Name"]),
        "instrument_name": df["Instrument Name"],
        "isin": df["ISIN"],
        "icb_industry": _s(df["ICB Industry"]),
        "icb_supersector": _s(df["ICB Super-Sector Name"]),
        "start_date": pd.to_datetime(df["Start Date"], dayfirst=True, errors="coerce").dt.date,
        "country": df["Country of Incorporation"],
        "currency": df["Trading Currency"],
        "lse_market": _s(df["LSE Market"]),
        "fca_category": df["FCA Listing Category"],
        "segment_code": df["Market Segment Code"],
        "sector_code": _s(df["Market Sector Code"]),
    })
    return out


# ---------------------------------------------------------------- issuer snapshots
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _sheet_as_at(raw: pd.DataFrame, sheet: str, file_year: int | None) -> date | None:
    """'As at 31 January 2024' text, or an 'At' cell followed by a date, or sheet name + file year."""
    d = _as_at(raw)
    if d:
        return d
    for i in range(min(6, len(raw))):
        row = list(raw.iloc[i])
        for j, v in enumerate(row[:-1]):
            if str(v).strip() == "At" and isinstance(row[j + 1], (datetime, pd.Timestamp)):
                return pd.Timestamp(row[j + 1]).date()
    m = _MONTHS.get(sheet.strip()[:3].lower())
    if m and file_year:
        return (pd.Timestamp(file_year, m, 1) + pd.offsets.MonthEnd(0)).date()
    return None


def _parse_issuer_workbook(body: bytes, source_file: str, file_year: int | None = None) -> list[pd.DataFrame]:
    """Handles both the 2017+ layout ('Company Name', 'Admission Date', 'Company Market Cap (£m)')
    and the older 'LISTDATE HISTORIC' layout ('Company', 'List Date', 'Mkt Cap £m')."""
    xl = pd.ExcelFile(io.BytesIO(body), engine="calamine")
    frames = []
    for sheet in xl.sheet_names:
        if "note" in sheet.lower() or "disclaimer" in sheet.lower():
            continue
        raw = pd.read_excel(xl, sheet_name=sheet, header=None)
        h = None
        for key in ("Company Name", "Company"):
            try:
                h = _header_row(raw, key)
                break
            except ValueError:
                pass
        if h is None:
            continue
        asat = _sheet_as_at(raw, sheet, file_year)
        if asat is None:
            (log.debug if sheet.lower().startswith("sheet") else log.warning)("no as-at date for %s / %s", source_file, sheet)
            continue
        df = pd.read_excel(xl, sheet_name=sheet, header=h)
        df.columns = [str(c).strip() for c in df.columns]
        name_col = "Company Name" if "Company Name" in df else "Company"
        adm_col = next((c for c in ("Admission Date", "List Date") if c in df), None)
        mcap_col = next((c for c in df.columns if "Market Cap" in c or "Mkt Cap" in c), None)
        ind_col = next((c for c in ("ICB Industry", "Sector") if c in df), None)
        f = pd.DataFrame({
            "snapshot_date": asat,
            "company_name": _s(df[name_col]),
            "market": _s(df["Market"]).str.upper() if "Market" in df else "",
            "admission_date": pd.to_datetime(df[adm_col], dayfirst=True, errors="coerce").dt.date if adm_col else None,
            "icb_industry": _s(df[ind_col]) if ind_col else "",
            "country": _s(df["Country of Incorporation"]) if "Country of Incorporation" in df else "",
            "mcap_gbp_m": pd.to_numeric(df[mcap_col], errors="coerce") if mcap_col else float("nan"),
            "source_file": source_file,
        })
        f = f[f["company_name"].ne("") & f["company_name"].ne("nan")]
        frames.append(f)
    return frames


def load_issuer_snapshots(client: PoliteClient) -> pd.DataFrame:
    cfg = uk_cfg()["universe"]
    min_year = cfg["issuer_archive_min_year"]
    entries = []
    for idx_url in (cfg["issuer_historic_index"], cfg["issuer_latest_index"]):
        entries += json.loads(_download(client, idx_url, cache=False))["Data"]
    frames = []
    for e in entries:
        m = re.search(r"(\d{4})", e["fileName"])
        year = int(m.group(1)) if m else date.today().year
        if year < min_year:
            continue
        # historic archives are immutable -> cache; "latest" ones are refreshed
        immutable = e["reportType"].startswith("historic")
        body = _download(client, e["downloadLink"], cache=immutable)
        if e["fileType"] == "zip":
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                for name in z.namelist():
                    if name.lower().endswith((".xlsx", ".xls")):
                        frames += _parse_issuer_workbook(z.read(name), f"{e['fileName']}/{name}", year)
        else:
            frames += _parse_issuer_workbook(body, e["fileName"], year)
        log.info("parsed %s", e["fileName"])
    df = pd.concat(frames, ignore_index=True)
    df["name_norm"] = df["company_name"].map(normalise_name)
    df = df.drop_duplicates(["snapshot_date", "company_name", "market"])
    return df


# ---------------------------------------------------------------- universe
def classify_instruments(inst: pd.DataFrame) -> pd.DataFrame:
    """Return rows that belong in the universe with grp = aim | smallcap_proxy."""
    cfg = uk_cfg()["universe"]
    ftse350 = set(cfg["ftse100_sector_codes"]) | set(cfg["ftse250_sector_codes"])
    is_aim = inst["lse_market"].eq("AIM")
    is_main_small = (
        inst["lse_market"].eq("MAIN MARKET")
        & inst["fca_category"].isin(KEEP_FCA_MAIN)
        & ~inst["sector_code"].isin(ftse350)
    )
    out = inst[(is_aim & cfg["include_aim"]) | (is_main_small & cfg["include_smallcap_proxy"])].copy()
    out["grp"] = out["lse_market"].map(lambda m: "aim" if m == "AIM" else "smallcap_proxy")
    out["market"] = out["lse_market"].map(lambda m: "AIM" if m == "AIM" else "MAIN")
    return out


def build() -> None:
    client = uk_client("lse", min_interval_s=1.0)
    con = connect()
    inst = load_instruments(client)
    con.execute("DELETE FROM lse_instruments WHERE snapshot_date = ?", [inst["snapshot_date"].iloc[0]])
    con.register("inst", inst)
    con.execute("INSERT INTO lse_instruments SELECT * FROM inst")
    log.info("instrument list: %d share lines as at %s", len(inst), inst["snapshot_date"].iloc[0])

    snaps = load_issuer_snapshots(client)
    con.execute("DELETE FROM lse_issuer_snapshots")
    con.register("snaps", snaps)
    con.execute("""INSERT INTO lse_issuer_snapshots
        SELECT snapshot_date, company_name, name_norm, market, admission_date, icb_industry,
               country, mcap_gbp_m, source_file FROM snaps""")
    log.info("issuer snapshots: %d rows, %d months", len(snaps), snaps["snapshot_date"].nunique())

    u = classify_instruments(inst)
    sfx = uk_cfg()["prices"]["yahoo_suffix"]
    rows = pd.DataFrame({
        "tidm": u["tidm"], "name": u["issuer_name"], "name_norm": u["issuer_name"].map(normalise_name),
        "isin": u["isin"], "currency": u["currency"], "market": u["market"], "grp": u["grp"],
        "is_current": True, "source": "lse_instrument_list",
        "first_seen": u["start_date"], "last_seen": u["snapshot_date"],
        "yahoo_ticker": u["tidm"].map(lambda t: tidm_to_yahoo(t, sfx)), "notes": None,
    }).drop_duplicates("tidm")
    con.execute("DELETE FROM universe WHERE source = 'lse_instrument_list'")
    con.register("rows", rows)
    con.execute("INSERT OR REPLACE INTO universe SELECT * FROM rows")
    # The instrument "Start Date" resets on consolidations / market moves; prefer the company-level
    # admission date from the most recent issuer snapshot with the same normalised name.
    con.execute("""
        UPDATE universe u SET first_seen = s.admission_date,
               notes = 'first_seen = issuer-list admission date'
        FROM (SELECT name_norm, arg_max(admission_date, snapshot_date) AS admission_date
              FROM lse_issuer_snapshots WHERE admission_date IS NOT NULL GROUP BY name_norm) s
        WHERE u.name_norm = s.name_norm AND u.source = 'lse_instrument_list'""")
    log.info("universe (current): %s", rows["grp"].value_counts().to_dict())
    client.close()


def add_delisted() -> None:
    """TIDMs seen in the announcement index but not in today's instrument list are candidate
    delisted/renamed companies. Keep them if their name matches an AIM company (or a Main
    Market company ranked outside the top 350 by market cap) in a historic monthly snapshot."""
    con = connect()
    sfx = uk_cfg()["prices"]["yahoo_suffix"]
    cand = con.execute("""
        SELECT tidm, any_value(company) AS company,
               min(published_uk)::DATE AS first_seen, max(published_uk)::DATE AS last_seen
        FROM ann_index WHERE tidm IS NOT NULL AND tidm NOT IN (SELECT tidm FROM universe)
        GROUP BY tidm""").df()
    if cand.empty:
        log.info("no candidate delisted tidms")
        return
    cand["name_norm"] = cand["company"].map(normalise_name)
    con.register("cand", cand)
    matched = con.execute("""
        WITH ranked AS (
            SELECT *, CASE WHEN market LIKE '%MAIN%' THEN
                       rank() OVER (PARTITION BY snapshot_date, market LIKE '%MAIN%'
                                    ORDER BY mcap_gbp_m DESC NULLS LAST) END AS main_rank
            FROM lse_issuer_snapshots),
        hits AS (
            SELECT c.tidm, c.company, c.name_norm, c.first_seen, c.last_seen,
                   r.snapshot_date, r.market, r.main_rank,
                   row_number() OVER (PARTITION BY c.tidm ORDER BY r.snapshot_date DESC) AS rn
            FROM cand c JOIN ranked r ON c.name_norm = r.name_norm AND c.name_norm <> ''
            WHERE r.snapshot_date BETWEEN c.first_seen - INTERVAL 60 DAY AND c.last_seen + INTERVAL 60 DAY)
        SELECT * FROM hits WHERE rn = 1""").df()
    keep = matched[(matched["market"].str.strip() == "AIM") | (matched["main_rank"] > 350)].copy()
    if keep.empty:
        log.info("no delisted matches")
        return
    keep["grp"] = keep["market"].str.strip().map(lambda m: "aim" if m == "AIM" else "smallcap_proxy")
    keep["mkt"] = keep["grp"].map(lambda g: "AIM" if g == "aim" else "MAIN")
    rows = pd.DataFrame({
        "tidm": keep["tidm"], "name": keep["company"], "name_norm": keep["name_norm"], "isin": None,
        "currency": None, "market": keep["mkt"], "grp": keep["grp"], "is_current": False,
        "source": "announcement_index", "first_seen": keep["first_seen"], "last_seen": keep["last_seen"],
        "yahoo_ticker": keep["tidm"].map(lambda t: tidm_to_yahoo(t, sfx)),
        "notes": "matched by name to LSE snapshot " + keep["snapshot_date"].astype(str),
    })
    con.register("rows", rows)
    con.execute("INSERT OR REPLACE INTO universe SELECT * FROM rows")
    log.info("added %d non-current tidms (of %d candidates, %d name matches)",
             len(rows), len(cand), len(matched))


def report() -> None:
    con = connect(read_only=True)
    print(con.execute("""SELECT grp, is_current, count(*) n FROM universe GROUP BY ALL ORDER BY ALL""").df())
    print(con.execute("""SELECT year(snapshot_date) yr, count(DISTINCT snapshot_date) n_months,
        count(*) FILTER (WHERE market='AIM') aim_rows FROM lse_issuer_snapshots GROUP BY 1 ORDER BY 1""").df())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "add-delisted", "report"])
    a = ap.parse_args()
    {"build": build, "add-delisted": add_delisted, "report": report}[a.cmd]()
