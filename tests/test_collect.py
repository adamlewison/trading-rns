import time

import pandas as pd

from collect.run import common_stocks, parse_ibkr, parse_symdir, trim_ud

SYMDIR = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
QQQ|Invesco QQQ Trust|G|N|N|100|Y|N
ABCDW|Abcd Corp - Warrants|G|N|N|100|N|N
ZXZZT|Test Co|G|Y|N|100|N|N
File Creation Time: 1122202618:00|||||||"""
OTHER = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
BRK.B|Berkshire Hathaway Inc. Class B|N|BRK.B|N|100|N|BRK.B
XYZ.U|XYZ Acquisition Units|N|XYZ.U|N|100|N|XYZ.U"""


def test_symdir_and_common_stocks():
    d = pd.concat([parse_symdir(SYMDIR, "nasdaqlisted"), parse_symdir(OTHER, "otherlisted")])
    assert common_stocks(d) == ["AAPL", "BRK-B"]


def test_parse_ibkr():
    t = """#BOF|2026.11.22|18:00:10
#SYM|CUR|NAME|CON|ISIN|REBATERATE|FEERATE|AVAILABLE|FIGI|
GME|USD|GAMESTOP|36285627|US36467W1099|3.1|0.75|>10000000|BBG000BB5BF6|
XYZ|USD|XYZ CO|1|US0000000000|-45.2|49.9|100|BBG000000000|
#EOF|2"""
    d = parse_ibkr(t, "usa")
    assert d.SYM.tolist() == ["GME", "XYZ"] and d.AVAILABLE.tolist() == [10000000, 100]
    assert d.FEERATE.iloc[1] == 49.9 and d.file_time.iloc[0] == "2026.11.22|18:00:10"


def test_trim_ud_keeps_recent_only():
    now = time.time()
    r = trim_ud({"upgradeDowngradeHistory": {"history": [{"epochGradeDate": now - 86400}, {"epochGradeDate": now - 90 * 86400}]}}, 45)
    assert len(r["upgradeDowngradeHistory"]["history"]) == 1
