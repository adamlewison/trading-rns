"""Daily snapshots of data that is free today but not kept (or sold) as history. Droplet timers run each job.

    uv run python -W ignore -m collect.run listings    # Nasdaq symbol directories + S&P 500/400/600 holdings (SPDR)
    uv run python -W ignore -m collect.run prices      # last 5 daily bars for every listed US stock (survivorship-free from now)
    uv run python -W ignore -m collect.run borrow      # IBKR short-borrow fees and availability, US + UK
    uv run python -W ignore -m collect.run options     # option chains near the money, S&P 500, nearest expiries
    uv run python -W ignore -m collect.run analysts    # Yahoo analyst forecasts, revisions, earnings dates, S&P 1500
    uv run python -W ignore -m collect.run status      # what has been collected

Files: data/collect/<job>/<YYYY-MM-DD>[THHMM].<ext>, each uploaded to B2 under trading-rns/collect/v1/<job>/.
Nothing is ever deleted. Polite: one request at a time with pauses; a run stops after repeated refusals."""
from __future__ import annotations

import argparse
import ftplib
import gzip
import io
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd

from core.log import get_logger

log = get_logger("collect")
ROOT = Path("data/collect")
B2_PREFIX = "trading-rns/collect/v1"
UA = "trading-rns research (personal, non-commercial)"
NASDAQ = {"nasdaqlisted": "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt",
          "otherlisted": "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"}
SPDR = "https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{}.xlsx"
SPDR_FUNDS = {"spy": "sp500", "mdy": "sp400", "spsm": "sp600"}
IBKR_FTP, IBKR_FILES = "ftp2.interactivebrokers.com", ["usa.txt", "british.txt"]
QS_MODULES = ["earningsTrend", "calendarEvents", "financialData", "recommendationTrend", "upgradeDowngradeHistory",
              "defaultKeyStatistics", "earningsHistory"]
UD_KEEP_DAYS = 45                    # rating changes kept per day after the first full copy
OPT_EXPIRIES, OPT_BAND = 4, 0.25     # nearest 4 expiries, strikes within ±25% of the price
MAX_FAILS = 25                       # consecutive refusals before a run stops (never push through a block)


# ---------------------------------------------------------------- storage

def save(job: str, name: str, data: bytes) -> Path:
    p = ROOT / job / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    try:
        from core.backup import get_store
        get_store().put(f"{B2_PREFIX}/{job}/{name}", data)
    except Exception as e:                    # kept locally; `upload` retries anything missing
        log.warning("collect: B2 upload of %s/%s failed: %s", job, name, e)
        (ROOT / "upload_pending.txt").open("a").write(f"{job}/{name}\n")
    log.info("collect: saved %s/%s (%d KB)", job, name, len(data) // 1024)
    return p


def parquet(df: pd.DataFrame) -> bytes:
    b = io.BytesIO()
    df.to_parquet(b, index=False, compression="zstd")
    return b.getvalue()


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M")


def latest(job: str, pattern: str) -> Path | None:
    files = sorted((ROOT / job).glob(pattern))
    return files[-1] if files else None


# ---------------------------------------------------------------- parsers (tested)

def parse_symdir(text: str, source: str) -> pd.DataFrame:
    lines = [l for l in text.splitlines() if l and not l.startswith("File Creation Time")]
    df = pd.read_csv(io.StringIO("\n".join(lines)), sep="|", dtype=str, keep_default_na=False)
    df = df.rename(columns={"ACT Symbol": "Symbol"})
    df["source"] = source
    return df


def common_stocks(listings: pd.DataFrame) -> list[str]:
    """Listed US symbols worth daily prices: not ETFs, not test issues, not warrants/units/rights/notes."""
    d = listings[(listings.ETF != "Y") & (listings["Test Issue"] != "Y")]
    name = d["Security Name"].str.lower()
    bad = name.str.contains(r"\bwarrants?\b|\bunits?\b|\brights?\b|\bnotes? due\b|debentures|depositary shares? representing.*preferred")
    syms = d[~bad].Symbol.str.replace(".", "-", regex=False).str.replace("$", "-P", regex=False)
    return sorted(s for s in set(syms) if s and " " not in s)


def parse_spdr(raw: bytes, fund: str) -> pd.DataFrame:
    x = pd.read_excel(io.BytesIO(raw), header=None)
    asof = str(x.iloc[2, 1]).replace("As of ", "").strip()
    hdr = x.index[x.iloc[:, 0].astype(str).str.strip().eq("Name")][0]
    df = x.iloc[hdr + 1:].copy()
    df.columns = [str(c).strip() for c in x.iloc[hdr]]
    df = df[df["Ticker"].notna() & df["Name"].notna()]
    df = df[df["Ticker"].astype(str).str.match(r"^[A-Z][A-Z0-9.\-]*$")]
    df.insert(0, "fund", fund)
    df.insert(1, "as_of", pd.to_datetime(asof, format="%d-%b-%Y", errors="coerce"))
    return df.astype({c: str for c in df.columns if df[c].dtype == object})


def parse_ibkr(text: str, market: str) -> pd.DataFrame:
    lines = text.splitlines()
    hdr = next(l for l in lines if l.startswith("#SYM"))[1:].rstrip("|").split("|")
    rows = [l.rstrip("|").split("|") for l in lines if l and not l.startswith("#")]
    df = pd.DataFrame([r[:len(hdr)] for r in rows], columns=hdr)
    for c in ("REBATERATE", "FEERATE"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["AVAILABLE"] = pd.to_numeric(df["AVAILABLE"].str.replace(">", "", regex=False), errors="coerce")
    df.insert(0, "market", market)
    df.insert(1, "file_time", lines[0].split("|", 1)[1] if lines and lines[0].startswith("#BOF") else "")
    return df


def trim_ud(res: dict, keep_days: int) -> dict:
    h = (res.get("upgradeDowngradeHistory") or {}).get("history")
    if h:
        cut = time.time() - keep_days * 86400
        res["upgradeDowngradeHistory"]["history"] = [e for e in h if e.get("epochGradeDate", 0) >= cut]
    return res


# ---------------------------------------------------------------- jobs

def _http() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA}, timeout=60, follow_redirects=True)


def listings() -> None:
    with _http() as h:
        frames = []
        for src, url in NASDAQ.items():
            r = h.get(url)
            r.raise_for_status()
            frames.append(parse_symdir(r.text, src))
            time.sleep(2)
        save("listings", f"{today()}.parquet", parquet(pd.concat(frames, ignore_index=True)))
        hold = []
        for fund, index in SPDR_FUNDS.items():
            r = h.get(SPDR.format(fund))
            r.raise_for_status()
            hold.append(parse_spdr(r.content, index))
            time.sleep(3)
        save("index_holdings", f"{today()}.parquet", parquet(pd.concat(hold, ignore_index=True)))


def _universe_listed() -> list[str]:
    p = latest("listings", "*.parquet")
    if p is None:
        listings()
        p = latest("listings", "*.parquet")
    return common_stocks(pd.read_parquet(p))


def _universe_index(indexes: tuple[str, ...]) -> list[str]:
    p = latest("index_holdings", "*.parquet")
    if p is None:
        listings()
        p = latest("index_holdings", "*.parquet")
    d = pd.read_parquet(p)
    return sorted(set(d[d.fund.isin(indexes)].Ticker.str.replace(".", "-", regex=False)))


def prices() -> None:
    import yfinance as yf
    syms = _universe_listed()
    frames, fails = [], 0
    for i in range(0, len(syms), 200):
        chunk = syms[i:i + 200]
        try:
            d = yf.download(chunk, period="5d", interval="1d", auto_adjust=False, actions=True, group_by="ticker",
                            threads=False, progress=False)
            if d is None or d.empty:
                raise RuntimeError("empty")
            d = d.stack(level=0, future_stack=True).reset_index().rename(columns={"level_1": "Ticker"})
            frames.append(d.dropna(subset=["Close"]))
            fails = 0
        except Exception as e:
            fails += 1
            log.warning("collect: prices chunk %d failed: %s", i, str(e)[:120])
            if fails >= 3:
                raise SystemExit("collect: prices refused 3 times in a row; stopping")
        time.sleep(2)
    df = pd.concat(frames, ignore_index=True)
    df.columns = [str(c) for c in df.columns]
    save("prices", f"{today()}.parquet", parquet(df))
    log.info("collect: prices for %d of %d symbols", df.Ticker.nunique(), len(syms))


def borrow() -> None:
    f = ftplib.FTP(IBKR_FTP, timeout=60)
    f.login("shortstock", "")
    frames = []
    for n in IBKR_FILES:
        b = io.BytesIO()
        f.retrbinary(f"RETR {n}", b.write)
        frames.append(parse_ibkr(b.getvalue().decode("latin1"), n.removesuffix(".txt")))
        time.sleep(2)
    f.quit()
    save("borrow", f"{stamp()}.parquet", parquet(pd.concat(frames, ignore_index=True)))


def options() -> None:
    import yfinance as yf
    syms = _universe_index(("sp500",))
    taken = datetime.now(timezone.utc)
    frames, fails = [], 0
    for s in syms:
        try:
            t = yf.Ticker(s)
            exps = list(t.options)[:OPT_EXPIRIES]
            time.sleep(0.7)
            for e in exps:
                ch = t.option_chain(e)
                spot = (ch.underlying or {}).get("regularMarketPrice")
                for side, df in (("C", ch.calls), ("P", ch.puts)):
                    if spot:
                        df = df[(df.strike >= spot * (1 - OPT_BAND)) & (df.strike <= spot * (1 + OPT_BAND))]
                    frames.append(df.assign(ticker=s, expiry=e, side=side, spot=spot, taken_utc=taken))
                time.sleep(0.7)
            fails = 0
        except Exception as e:
            fails += 1
            log.warning("collect: options %s failed: %s", s, str(e)[:120])
            if fails >= MAX_FAILS:
                raise SystemExit("collect: options refused repeatedly; stopping")
            time.sleep(5)
    df = pd.concat(frames, ignore_index=True)
    df = df.drop(columns=[c for c in ("contractSize", "currency", "change", "percentChange") if c in df])
    save("options", f"{today()}.parquet", parquet(df))
    log.info("collect: options for %d of %d tickers, %d rows", df.ticker.nunique(), len(syms), len(df))


def analysts() -> None:
    import yfinance as yf
    syms = _universe_index(("sp500", "sp400", "sp600"))
    first = latest("analysts", "*.jsonl.gz") is None          # the first file keeps the full rating history
    out, fails, ok = io.BytesIO(), 0, 0
    with gzip.GzipFile(fileobj=out, mode="wb") as gz:
        for s in syms:
            try:
                r = yf.Ticker(s)._quote._fetch(modules=QS_MODULES)
                res = (r or {}).get("quoteSummary", {}).get("result") or []
                if not res:
                    raise RuntimeError("no result")
                rec = trim_ud(res[0], 100000 if first else UD_KEEP_DAYS)
                gz.write((json.dumps({"ticker": s, "taken_utc": datetime.now(timezone.utc).isoformat(), **rec}) + "\n").encode())
                ok, fails = ok + 1, 0
            except Exception as e:
                fails += 1
                log.warning("collect: analysts %s failed: %s", s, str(e)[:120])
                if fails >= MAX_FAILS:
                    raise SystemExit("collect: analysts refused repeatedly; stopping")
                time.sleep(5)
            time.sleep(0.8)
    save("analysts", f"{today()}{'-full' if first else ''}.jsonl.gz", out.getvalue())
    log.info("collect: analysts for %d of %d tickers", ok, len(syms))


def upload() -> None:
    p = ROOT / "upload_pending.txt"
    if not p.exists():
        return
    from core.backup import get_store
    left = []
    for rel in sorted(set(p.read_text().split())):
        try:
            get_store().put(f"{B2_PREFIX}/{rel}", (ROOT / rel).read_bytes())
        except Exception:
            left.append(rel)
    p.write_text("".join(f"{r}\n" for r in left))


def status() -> str:
    lines = ["# Data collection", "", "| Job | Files | First | Latest | Size |", "|---|---|---|---|---|"]
    for job in ("listings", "index_holdings", "prices", "borrow", "options", "analysts"):
        fs = sorted(p for p in (ROOT / job).glob("*") if p.is_file())
        if fs:
            mb = sum(p.stat().st_size for p in fs) / 1e6
            lines.append(f"| {job} | {len(fs)} | {fs[0].name.split('.')[0]} | {fs[-1].name.split('.')[0]} | {mb:.0f} MB |")
        else:
            lines.append(f"| {job} | 0 | | | |")
    pend = ROOT / "upload_pending.txt"
    n = len(pend.read_text().split()) if pend.exists() else 0
    lines += ["", f"Uploads pending: {n}. Updated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC."]
    return "\n".join(lines) + "\n"


JOBS = {"listings": listings, "prices": prices, "borrow": borrow, "options": options, "analysts": analysts}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=[*JOBS, "upload", "status"])
    a = ap.parse_args()
    if a.job == "status":
        print(status())
        return
    if a.job != "upload":
        JOBS[a.job]()
    upload()
    try:
        from core.backup import get_store
        get_store().put("trading-rns/collect/status.md", status().encode())
    except Exception as e:
        log.warning("collect: status upload failed: %s", e)


if __name__ == "__main__":
    main()
