import io

import pandas as pd

from ingest.uk_trading_service import parse_mm_file, parse_service_map


def _xlsx(frames: dict[str, pd.DataFrame], startrow=0) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, df in frames.items():
            df.to_excel(w, sheet_name=name, index=False, startrow=startrow)
    return buf.getvalue()


def test_parse_service_map():
    df = pd.DataFrame({"Trading Service": ["SETS", "SETSqx", "SETSqx"], "Segment Code": ["SET1", "ASQ1", "ASQ1"],
                       "Segment Name": ["FTSE 100", "AIM", "AIM"], "Sector Code": ["FE10", "AMQ1", "AMQ1"],
                       "Sector Name": ["x", "y", "y"]})
    m = parse_service_map(_xlsx({"Sector Breakdown": df}, startrow=4))
    assert len(m) == 2
    assert m.set_index(["segment_code", "sector_code"]).loc[("ASQ1", "AMQ1"), "trading_service"] == "SETSqx"


def test_parse_mm_file_one_row_per_pair(tmp_path):
    df = pd.DataFrame({"TIDM": ["ABC", "ABC", "XYZ"], "ISIN": ["GB1", "GB1", "GB2"], "Market Maker": ["WINS", "PEEL", "WINS"]})
    p = tmp_path / "mm.xlsx"
    p.write_bytes(_xlsx({"Sheet1": df}, startrow=3))
    out = parse_mm_file(p)
    assert sorted(out[out.tidm == "ABC"]["market_maker"]) == ["PEEL", "WINS"]
