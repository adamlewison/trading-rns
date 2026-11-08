"""Row counts, sessions, samples and storage for the US data."""
from __future__ import annotations

import pandas as pd

from ingest.us.common import (
    session,
    us_cfg,
)


# ---------------------------------------------------------------- report
def report() -> None:
    c = us_cfg()
    pd.set_option("display.width", 200)
    with session(read_only=True) as con:
        q = lambda s: con.execute(s).df()
        print("== 8-K / 8-K/A in EDGAR full-index, per year ==")
        print(q("SELECT year, count(*) filter (where form='8-K') n_8k, count(*) filter (where form='8-K/A') n_8ka FROM sec_form_index GROUP BY 1 ORDER BY 1").to_string(index=False))
        print("\n== filings per year by item (from submissions) ==")
        items = c["earnings_items"] + c["news_items"]
        cols = ", ".join(f"count(*) filter (where list_contains(string_split(items, ','), '{i}')) AS \"{i}\"" for i in items)
        print(q(f"SELECT year(filing_date) yr, count(*) n, {cols} FROM sec_filings GROUP BY 1 ORDER BY 1").to_string(index=False))
        print("\n== fetched documents ==")
        print(q("""SELECT item_group, status, count(*) n, round(avg(chars)) avg_chars FROM us_filing_docs GROUP BY ALL ORDER BY ALL""").to_string(index=False))
        print(q("""SELECT item_group, year(acceptance_et) yr, count(*) n,
                     round(100*avg((session='pre_market')::int),1) pre_pct, round(100*avg((session='intraday')::int),1) intra_pct,
                     round(100*avg((session='after_close')::int),1) after_pct
                   FROM us_filing_docs WHERE status='ok' GROUP BY ALL ORDER BY ALL""").to_string(index=False))
        print("\n== acceptance time: header (ET->UTC) vs submissions JSON ==")
        print(q("""SELECT count(*) n, count(*) filter (where abs(epoch(d.acceptance_utc) - epoch(f.acceptance_utc_sub)) <= 1) same,
                          count(*) filter (where abs(epoch(d.acceptance_utc) - epoch(f.acceptance_utc_sub)) between 3500 and 18100) off_by_hours
                   FROM us_filing_docs d JOIN sec_filings f USING (accession) WHERE d.acceptance_utc IS NOT NULL""").to_string(index=False))
        print("\n== companies / tickers / facts / prices ==")
        print(q("""SELECT (SELECT count(*) FROM sec_companies) companies,
                          (SELECT count(DISTINCT cik) FROM sec_ticker_map) ciks_with_ticker,
                          (SELECT count(*) FROM us_facts) fact_rows,
                          (SELECT count(DISTINCT ticker) FROM us_prices_daily) priced_tickers,
                          (SELECT count(*) FROM us_prices_daily) price_rows""").to_string(index=False))
        print(q("""WITH l AS (SELECT *, row_number() OVER (PARTITION BY ticker ORDER BY attempted_at DESC) rn FROM us_price_download_log)
                   SELECT status, count(*) FROM l WHERE rn=1 GROUP BY 1""").to_string(index=False))
        print("\n== 10 sample earnings releases ==")
        s = q("""SELECT d.cik, c.name, t.ticker, d.acceptance_et, d.acceptance_utc, d.session, d.items_header,
                        d.doc_type, d.chars, left(regexp_replace(d.text, '\\s+', ' ', 'g'), 200) AS start
                 FROM us_filing_docs d LEFT JOIN sec_companies c USING (cik)
                 LEFT JOIN (SELECT cik, any_value(ticker) ticker FROM sec_ticker_map GROUP BY 1) t USING (cik)
                 WHERE d.item_group='earnings' AND d.status='ok' ORDER BY hash(d.accession) LIMIT 10""")
        for _, r in s.iterrows():
            print(f"\n[{r.ticker or '-'} | CIK {r.cik}] {r['name']}")
            print(f"  accepted {r.acceptance_et} ET = {r.acceptance_utc} UTC  session={r.session}  items={r.items_header}  {r.doc_type} {r.chars} chars")
            print(f"  {r.start}...")
    import subprocess
    print("\n== storage ==")
    print(subprocess.run(["du", "-sh", str(c["db"]), str(c["raw_dir"] / "sec"), str(c["raw_dir"] / "bulk")],
                         capture_output=True, text=True).stdout)
