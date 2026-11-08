from datetime import datetime

from ingest.uk.announcements.base import IndexRow
from ingest.uk.announcements.store import is_pre_open, to_records, uk_to_utc


def test_uk_to_utc_handles_dst():
    # winter: GMT == UTC
    assert uk_to_utc(datetime(2024, 1, 15, 7, 0)) == datetime(2024, 1, 15, 7, 0)
    # summer: BST = UTC+1, so a 07:00 RNS is 06:00 UTC
    assert uk_to_utc(datetime(2024, 7, 15, 7, 0)) == datetime(2024, 7, 15, 6, 0)
    # day after clocks go forward (31 Mar 2024)
    assert uk_to_utc(datetime(2024, 4, 1, 7, 0)) == datetime(2024, 4, 1, 6, 0)


def test_pre_open():
    assert is_pre_open(datetime(2024, 7, 15, 7, 0))        # Monday 07:00
    assert is_pre_open(datetime(2024, 7, 15, 7, 59))
    assert not is_pre_open(datetime(2024, 7, 15, 8, 0))    # at the open: not pre-open
    assert not is_pre_open(datetime(2024, 7, 15, 16, 30))
    assert not is_pre_open(datetime(2024, 7, 13, 7, 0))    # Saturday


def test_to_records_derives_fields():
    r = IndexRow(source="investegate", source_id="123", url="u", headline="Total Voting Rights",
                 published_uk=datetime(2024, 7, 15, 7, 0), tidm="ABC")
    df = to_records([r])
    row = df.iloc[0]
    assert row.ann_id == "investegate:123"
    assert bool(row.is_admin) and row.admin_rule == "total_voting_rights"
    assert bool(row.pre_open)
    assert row.published_utc == datetime(2024, 7, 15, 6, 0)
