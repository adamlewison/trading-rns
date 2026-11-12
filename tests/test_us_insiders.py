import io
import zipfile
from datetime import date

from ingest.us.insiders import parse_quarter, quarters


def _zip(tmp_path):
    files = {
        "SUBMISSION.tsv": "ACCESSION_NUMBER\tFILING_DATE\tDOCUMENT_TYPE\tISSUERCIK\tISSUERNAME\tISSUERTRADINGSYMBOL\tAFF10B5ONE\n"
                          "A1\t05-MAR-2024\t4\t0000000123\tACME\tacm\t0\nA2\t06-MAR-2024\t3\t0000000123\tACME\tACM\t0\n",
        "REPORTINGOWNER.tsv": "ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNERNAME\tRPTOWNER_RELATIONSHIP\tRPTOWNER_TITLE\n"
                              "A1\t1\tSmith Jo\tDirector,Officer\tCEO\nA1\t2\tFund LP\tTenPercentOwner\t\n",
        "NONDERIV_TRANS.tsv": "ACCESSION_NUMBER\tNONDERIV_TRANS_SK\tSECURITY_TITLE\tTRANS_DATE\tTRANS_CODE\tTRANS_ACQUIRED_DISP_CD\t"
                              "TRANS_SHARES\tTRANS_PRICEPERSHARE\tSHRS_OWND_FOLWNG_TRANS\tDIRECT_INDIRECT_OWNERSHIP\n"
                              "A1\t9\tCommon Stock\t01-MAR-2024\tP\tA\t1000.0\t12.5\t5000.0\tD\n"
                              "A2\t10\tCommon Stock\t01-MAR-2024\tP\tA\t1.0\t1.0\t1.0\tD\n",
    }
    p = tmp_path / "q.zip"
    with zipfile.ZipFile(p, "w") as z:
        for n, t in files.items():
            z.writestr(n, t)
    return p


def test_parse_keeps_form4_rows_with_owner_roles(tmp_path):
    df = parse_quarter(_zip(tmp_path), "2024q1")
    assert len(df) == 1                                      # the Form 3 row is dropped
    r = df.iloc[0]
    assert r.filing_date == date(2024, 3, 5) and r.trans_date == date(2024, 3, 1)
    assert r.issuer_ticker == "ACM" and r.issuer_cik == 123 and r.trans_code == "P"
    assert (r.is_director, r.is_officer, r.is_ten_pct, r.n_owners) == (1, 1, 1, 2)
    assert r.shares * r.price == 12500 and r.aff10b5one is False or r.aff10b5one == False


def test_quarters_run_from_2006_to_now():
    qs = quarters(date(2026, 11, 12))
    assert qs[0] == "2006q1" and qs[-1] == "2026q4" and len(qs) == 84
